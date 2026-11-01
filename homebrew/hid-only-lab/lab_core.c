/*
 * PSP Dock HID-only LAB. Kernel-only, time-limited, no USBSTOR lifecycle.
 *
 * This is NOT USBSTOR -> HID handoff: only run from the GAME menu with
 * native XMB USB mode closed AND physical cable unplugged at startup.
 * The controller refuses to start if storage is running or USB appears
 * connected/active. It never mounts/reads/writes FAT or firmware.
 *
 * The existing GPL-3.0 PSPDockHid driver does actual input USB work.
 * Stop on START+SELECT, cable loss after first connection or timeout.
 */
#include <pspkernel.h>
#include <pspctrl.h>
#include <pspusb.h>
#include <pspusbstor.h>
#include <pspthreadman.h>
#include <stdint.h>
#include "bridge_hid.h"
#include "lab_gate.h"

PSP_MODULE_INFO("PSPDockHidOnlyCore", PSP_MODULE_KERNEL, 1, 0);

#define TIMEOUT_US 90000000u

static volatile int stop_requested = 0;
static volatile int hid_started = 0;
static volatile int bus_owned = 0;
static volatile int shutdown_failed = 0;
static SceUID thread_uid = -1;

static void make_session(uint8_t out[BRIDGE_SESSION_SIZE]) {
    unsigned int x = sceKernelGetSystemTimeLow() ^ 0x6a19b2e7u;
    int i;
    for (i = 0; i < BRIDGE_SESSION_SIZE; ++i) {
        x ^= x << 13;
        x ^= x >> 17;
        x ^= x << 5;
        out[i] = (uint8_t)x;
    }
    out[0] |= 1u; /* Fresh test identifier; not a cryptographic secret. */
}

static int stop_lab(void) {
    int ret;
    if (hid_started) {
        ret = bridgeHidStop();
        if (ret < 0) {
            shutdown_failed = 1;
            return ret; /* Do not stop the bus with active HID transfers. */
        }
        hid_started = 0;
    }
    if (bus_owned) {
        ret = sceUsbStop(PSP_USBBUS_DRIVERNAME, 0, NULL);
        if (ret < 0) {
            shutdown_failed = 1;
            return ret;
        }
        bus_owned = 0;
    }
    return 0;
}

static int lab_thread(SceSize args, void *argp) {
    uint8_t session[BRIDGE_SESSION_SIZE];
    uint32_t started_at;
    int bus, storage, usb, ret;
    int cable_seen = 0, exit_hold = 0;
    SceCtrlData pad;
    (void)args;
    (void)argp;

    /* The EBOOT load completes first, then we inspect role ownership. */
    sceKernelDelayThread(1500000);
    if (stop_requested) return 0;
    usb = sceUsbGetState();
    storage = sceUsbGetDrvState(PSP_USBSTOR_DRIVERNAME);
    bus = sceUsbGetDrvState(PSP_USBBUS_DRIVERNAME);
    if (!hid_lab_safe_to_start(usb, storage, bus))
        return 0; /* No operations if USB/storage/bus are ambiguous. */
    if (bus == 2) {
        ret = sceUsbStart(PSP_USBBUS_DRIVERNAME, 0, NULL);
        if (ret < 0) return 0;
        bus_owned = 1;
    } else if (bus != 1) {
        return 0;
    }
    if (stop_requested) {
        stop_lab();
        return 0;
    }
    make_session(session);
    ret = bridgeHidStart(session);
    if (ret < 0) {
        stop_lab();
        return 0;
    }
    hid_started = 1;
    started_at = sceKernelGetSystemTimeLow();
    for (;;) {
        if (stop_requested ||
            (uint32_t)(sceKernelGetSystemTimeLow() - started_at) >= TIMEOUT_US)
            break;
        usb = sceUsbGetState();
        if (usb >= 0 && (usb & PSP_USB_CABLE_CONNECTED))
            cable_seen = 1;
        else if (cable_seen)
            break;
        if (sceCtrlPeekBufferPositive(&pad, 1) > 0 &&
            (pad.Buttons & (PSP_CTRL_START | PSP_CTRL_SELECT)) ==
                (PSP_CTRL_START | PSP_CTRL_SELECT)) {
            if (++exit_hold >= 10) break; /* Hold for one second. */
        } else
            exit_hold = 0;
        sceKernelDelayThread(100000);
    }
    stop_lab();
    return 0;
}

int module_start(SceSize args, void *argp) {
    (void)args;
    (void)argp;
    stop_requested = 0;
    hid_started = 0;
    bus_owned = 0;
    shutdown_failed = 0;
    thread_uid = sceKernelCreateThread(
        "PSPDockHidOnlyTimer", lab_thread, 0x20, 0x3000, 0, NULL);
    if (thread_uid < 0) return 1;
    if (sceKernelStartThread(thread_uid, 0, NULL) < 0) {
        sceKernelDeleteThread(thread_uid);
        thread_uid = -1;
        return 1;
    }
    return 0;
}

int module_stop(SceSize args, void *argp) {
    int ret;
    (void)args;
    (void)argp;
    stop_requested = 1;
    if (thread_uid < 0) return 0;
    /* Do not unload while a USB transfer is owned by the driver. */
    ret = sceKernelWaitThreadEnd(thread_uid, NULL);
    if (ret < 0 || shutdown_failed || hid_started || bus_owned)
        return -1;
    sceKernelDeleteThread(thread_uid);
    thread_uid = -1;
    return 0;
}
