/* SPDX-License-Identifier: GPL-3.0-only
 * Derived from FuSa GAMEPAD, Andy_maN, commit 97a1fccb8cb561b14964de4d4dbab4fcd8618297.
 * PSP Dock adaptation: explicit lifecycle, immutable transfer buffers and
 * session-bound vendor FEATURE command. See README.md and LICENSE.
 */
#include <pspkernel.h>
#include <pspctrl.h>
#include <pspusb.h>
#include <pspusbbus.h>
#include <pspintrman.h>
#include <string.h>
#include "bridge_hid.h"
#include "protocol.h"
#include "descriptors.h"

PSP_MODULE_INFO("PSPDockHID", PSP_MODULE_KERNEL, 1, 0);

static BridgeProtocol protocol;
static SceUID lifecycle_sema = -1, input_thread = -1;
static volatile int running, hid_active, connected, driver_started, activated;
static volatile int input_busy, control_busy;
static int control_is_feature_receive;
static uint8_t idle_rate;
static int input_sent;
static uint32_t last_input_time;
static uint8_t latest_input[8];
static uint8_t input_buffer[64] __attribute__((aligned(64)));
static uint8_t control_buffer[256] __attribute__((aligned(64)));
static struct UsbdDeviceReq input_request, control_request;

static int input_done(struct UsbdDeviceReq *req, int a, int b) {
    (void)req; (void)a; (void)b;
    input_busy = 0;
    return 0;
}
static int control_done(struct UsbdDeviceReq *req, int a, int b) {
    int irq;
    (void)a; (void)b;
    /* The receive owns the complete cache lines until completion. */
    if (control_is_feature_receive && req->retcode == 0 &&
        req->recvsize == BRIDGE_FEATURE_SIZE) {
        sceKernelDcacheInvalidateRange(control_buffer, sizeof(control_buffer));
        irq = sceKernelCpuSuspendIntr();
        if (hid_active)
            bridge_protocol_receive(&protocol, control_buffer);
        sceKernelCpuResumeIntr(irq);
    }
    control_busy = 0;
    return 0;
}

/* Only a single EP0 request can own this buffer. No caller retains stack data. */
static int control_submit(const void *data, int size, int receive, int feature) {
    int irq = sceKernelCpuSuspendIntr();
    int result;
    if (!hid_active || control_busy || size < 0 || size > (int)sizeof(control_buffer)) {
        sceKernelCpuResumeIntr(irq);
        return -1;
    }
    control_busy = 1;
    control_is_feature_receive = feature;
    sceKernelCpuResumeIntr(irq);
    memset(&control_request, 0, sizeof(control_request));
    control_request.endp = &endpoints[0];
    control_request.data = control_buffer;
    control_request.size = size;
    control_request.func = control_done;
    if (receive) {
        sceKernelDcacheWritebackInvalidateRange(control_buffer, sizeof(control_buffer));
        result = sceUsbbdReqRecv(&control_request);
    } else {
        if (size) memcpy(control_buffer, data, size);
        sceKernelDcacheWritebackRange(control_buffer, sizeof(control_buffer));
        result = sceUsbbdReqSend(&control_request);
    }
    if (result < 0) control_busy = 0;
    return result;
}

int bridgeHidGetStatus(uint8_t status[BRIDGE_FEATURE_SIZE]) {
    int irq;
    if (!status) return -1;
    irq = sceKernelCpuSuspendIntr();
    bridge_protocol_status(&protocol, status, hid_active, connected);
    sceKernelCpuResumeIntr(irq);
    return 0;
}
int bridgeHidPollStorageRequest(uint32_t *sequence) {
    int irq, result;
    if (!sequence) return -1;
    irq = sceKernelCpuSuspendIntr();
    /* Callback must have released EP0 before the app can act on the command. */
    result = hid_active && !control_busy ? bridge_protocol_take(&protocol, sequence) : 0;
    sceKernelCpuResumeIntr(irq);
    return result;
}

static int receive_control(int endpoint, int arg2, struct DeviceRequest *req) {
    uint8_t snapshot[BRIDGE_FEATURE_SIZE];
    int length, irq;
    (void)endpoint;
    if (!req || arg2 == -1 || req->wIndex != 0) return -1;
    /* GET_DESCRIPTOR, interface recipient, report-ID zero. */
    if (req->bmRequestType == 0x81 && req->bRequest == 6) {
        const void *descriptor;
        if (req->wValue == 0x2200) {
            descriptor = report_descriptor; length = sizeof(report_descriptor);
        } else if (req->wValue == 0x2100) {
            descriptor = hid_descriptor; length = sizeof(hid_descriptor);
        } else return -1;
        if (length > req->wLength) length = req->wLength;
        return control_submit(descriptor, length, 0, 0);
    }
    if (req->bmRequestType == 0xa1 && req->bRequest == 1) { /* GET_REPORT */
        if (req->wValue == 0x0300) {
            bridgeHidGetStatus(snapshot);
            length = BRIDGE_FEATURE_SIZE;
        } else if (req->wValue == 0x0100) {
            irq = sceKernelCpuSuspendIntr();
            memcpy(snapshot, latest_input, 8);
            sceKernelCpuResumeIntr(irq);
            length = 8;
        } else return -1;
        if (length > req->wLength) length = req->wLength;
        return control_submit(snapshot, length, 0, 0);
    }
    if (req->bmRequestType == 0x21 && req->bRequest == 9) { /* SET_REPORT */
        if (req->wValue == 0x0300 && req->wLength == BRIDGE_FEATURE_SIZE)
            return control_submit(NULL, BRIDGE_FEATURE_SIZE, 1, 1);
        /* Preserve FuSa's four output bytes; no effects/rumble are implemented. */
        if (req->wValue == 0x0200 && req->wLength == 4)
            return control_submit(NULL, 4, 1, 0);
        return -1;
    }
    if (req->bmRequestType == 0xa1 && req->bRequest == 2 &&
        req->wValue == 0 && req->wLength == 1)
        return control_submit(&idle_rate, 1, 0, 0); /* GET_IDLE */
    if (req->bmRequestType == 0x21 && req->bRequest == 10 &&
        !(req->wValue & 255) && req->wLength == 0) {
        idle_rate = req->wValue >> 8;
        return 0; /* SET_IDLE: zero means send only on change. */
    }
    return -1;
}
static int attach(int speed, void *a, void *b) {
    (void)speed; (void)a; (void)b;
    connected = 1;
    return 0;
}
static int detach(int a, int b, int c) {
    (void)a; (void)b; (void)c;
    connected = 0;
    return 0;
}
static int alternate(int a, int b, int c) {
    (void)a; (void)c;
    return b == 0 ? 0 : -1;
}
static int driver_start(int size, void *args) { (void)size; (void)args; return 0; }
static int driver_stop(int size, void *args) { (void)size; (void)args; return 0; }
static struct UsbDriver driver = {
    .name = BRIDGE_HID_DRIVER, .endpoints = 2, .endp = endpoints, .intp = &interface,
    .devp_hi = &device_hi, .confp_hi = &config_hi,
    .devp = &device_full, .confp = &config_full, .str = strings,
    .recvctl = receive_control, .func28 = alternate, .attach = attach, .detach = detach,
    .start_func = driver_start, .stop_func = driver_stop
};

/* FuSa default mapping, unchanged: four analog axes, hat and 12 buttons. */
static void encode_pad(const SceCtrlData *pad, uint8_t out[8]) {
    static const uint32_t buttons[12] = {
        PSP_CTRL_CROSS, PSP_CTRL_CIRCLE, PSP_CTRL_SQUARE, PSP_CTRL_TRIANGLE,
        PSP_CTRL_LTRIGGER, PSP_CTRL_RTRIGGER, PSP_CTRL_SELECT, PSP_CTRL_START,
        PSP_CTRL_HOME, PSP_CTRL_VOLDOWN, PSP_CTRL_VOLUP, PSP_CTRL_SCREEN
    };
    int i;
    uint32_t b = pad->Buttons;
    memset(out, 0, 8);
    memset(out, 0x7f, 4);
    if (b & PSP_CTRL_NOTE) { out[2] = pad->Ly; out[3] = pad->Lx; }
    else { out[0] = pad->Lx; out[1] = pad->Ly; }
    if (b & PSP_CTRL_UP) out[4] = 1;
    if (b & PSP_CTRL_RIGHT) out[4] = 3;
    if (b & PSP_CTRL_DOWN) out[4] = 5;
    if (b & PSP_CTRL_LEFT) out[4] = 7;
    if ((b & (PSP_CTRL_UP | PSP_CTRL_RIGHT)) == (PSP_CTRL_UP | PSP_CTRL_RIGHT)) out[4] = 2;
    if ((b & (PSP_CTRL_RIGHT | PSP_CTRL_DOWN)) == (PSP_CTRL_RIGHT | PSP_CTRL_DOWN)) out[4] = 4;
    if ((b & (PSP_CTRL_DOWN | PSP_CTRL_LEFT)) == (PSP_CTRL_DOWN | PSP_CTRL_LEFT)) out[4] = 6;
    if ((b & (PSP_CTRL_LEFT | PSP_CTRL_UP)) == (PSP_CTRL_LEFT | PSP_CTRL_UP)) out[4] = 8;
    for (i = 0; i < 12; ++i)
        if (b & buttons[i]) out[4 + (i + 4) / 8] |= 1 << ((i + 4) % 8);
}
static int sample_input(SceSize args, void *argp) {
    SceCtrlData pad;
    uint8_t sample[8];
    int irq, send, result;
    (void)args; (void)argp;
    sceCtrlSetSamplingCycle(0);
    sceCtrlSetSamplingMode(PSP_CTRL_MODE_ANALOG);
    while (running) {
        sceKernelWaitSema(lifecycle_sema, 1, NULL);
        if (hid_active && connected && sceCtrlPeekBufferPositive(&pad, 1) > 0) {
            encode_pad(&pad, sample);
            irq = sceKernelCpuSuspendIntr();
            memcpy(latest_input, sample, 8);
            send = !input_busy && (!input_sent || memcmp(input_buffer, sample, 8) ||
                (idle_rate && (uint32_t)(sceKernelGetSystemTimeLow() - last_input_time) >=
                 (uint32_t)idle_rate * 4000));
            if (send) {
                input_busy = 1;
                memcpy(input_buffer, sample, 8);
            }
            sceKernelCpuResumeIntr(irq);
            if (send) {
                memset(&input_request, 0, sizeof(input_request));
                input_request.endp = &endpoints[1];
                input_request.data = input_buffer;
                input_request.size = 8;
                input_request.func = input_done;
                sceKernelDcacheWritebackRange(input_buffer, sizeof(input_buffer));
                result = sceUsbbdReqSend(&input_request);
                if (result < 0) { input_busy = 0; input_sent = 0; }
                else { input_sent = 1; last_input_time = sceKernelGetSystemTimeLow(); }
            }
        }
        sceKernelSignalSema(lifecycle_sema, 1);
        sceKernelDelayThread(4000);
    }
    return 0;
}

int bridgeHidStart(const uint8_t session[BRIDGE_SESSION_SIZE]) {
    int result, irq, nonzero = 0, i;
    if (!session || lifecycle_sema < 0) return -1;
    for (i = 0; i < BRIDGE_SESSION_SIZE; ++i) nonzero |= session[i];
    if (!nonzero) return -1;
    sceKernelWaitSema(lifecycle_sema, 1, NULL);
    if (driver_started || input_busy || control_busy) { result = -1; goto out; }
    irq = sceKernelCpuSuspendIntr();
    memset(&protocol, 0, sizeof(protocol));
    memcpy(protocol.session, session, BRIDGE_SESSION_SIZE);
    memset(latest_input, 0, sizeof(latest_input));
    memset(latest_input, 0x7f, 4);
    connected = 0;
    idle_rate = 0;
    input_sent = 0;
    sceKernelCpuResumeIntr(irq);
    result = sceUsbStart(BRIDGE_HID_DRIVER, 0, NULL);
    if (result < 0) goto out;
    driver_started = 1;
    hid_active = 1;
    result = sceUsbActivate(BRIDGE_HID_PID);
    if (result < 0) {
        hid_active = 0;
        if (sceUsbStop(BRIDGE_HID_DRIVER, 0, NULL) >= 0) driver_started = 0;
    } else activated = 1;
out:
    sceKernelSignalSema(lifecycle_sema, 1);
    return result;
}

int bridgeHidStop(void) {
    int result = 0, tries;
    if (lifecycle_sema < 0) return -1;
    sceKernelWaitSema(lifecycle_sema, 1, NULL);
    hid_active = 0;
    if (activated) {
        result = sceUsbDeactivate(BRIDGE_HID_PID);
        if (result < 0) goto out;
        activated = 0;
    }
    if (driver_started) {
        /* Cancellation does not grant buffer ownership until callbacks finish. */
        sceUsbbdReqCancelAll(&endpoints[0]);
        sceUsbbdReqCancelAll(&endpoints[1]);
        for (tries = 0; (input_busy || control_busy) && tries < 1000; ++tries)
            sceKernelDelayThread(1000);
        if (input_busy || control_busy) { result = -1; goto out; }
        result = sceUsbStop(BRIDGE_HID_DRIVER, 0, NULL);
        if (result >= 0) driver_started = 0;
    }
    connected = 0;
out:
    sceKernelSignalSema(lifecycle_sema, 1);
    return result;
}

int module_start(SceSize args, void *argp) {
    int result;
    (void)args; (void)argp;
    lifecycle_sema = sceKernelCreateSema("PSPDockHidLock", 0, 1, 1, NULL);
    if (lifecycle_sema < 0) return lifecycle_sema;
    result = sceUsbbdRegister(&driver);
    if (result < 0) goto fail_sema;
    input_thread = sceKernelCreateThread("PSPDockHidInput", sample_input, 32, 0x2000, 0, NULL);
    if (input_thread < 0) { result = input_thread; goto fail_register; }
    running = 1;
    result = sceKernelStartThread(input_thread, 0, NULL);
    if (result >= 0) return 0;
    running = 0;
    sceKernelDeleteThread(input_thread);
fail_register:
    sceUsbbdUnregister(&driver);
fail_sema:
    sceKernelDeleteSema(lifecycle_sema);
    lifecycle_sema = -1;
    return result;
}
int module_stop(SceSize args, void *argp) {
    int result;
    (void)args; (void)argp;
    result = bridgeHidStop();
    if (result < 0) return result; /* Never unload memory still owned by USB. */
    running = 0;
    result = sceKernelWaitThreadEnd(input_thread, NULL);
    if (result < 0) return result;
    result = sceUsbbdUnregister(&driver);
    if (result < 0) return result;
    sceKernelDeleteThread(input_thread);
    sceKernelDeleteSema(lifecycle_sema);
    lifecycle_sema = -1;
    return 0;
}
