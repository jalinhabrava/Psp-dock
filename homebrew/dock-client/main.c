/* PSP Dock Client LAB. Not yet automatic: user acknowledges a verified host
 * safe eject before switching from USBSTOR to HID. Never accesses FAT while
 * USBSTOR is active. No flash or boot plugin changes.
 */
#include <pspkernel.h>
#include <pspsdk.h>
#include <pspdebug.h>
#include <pspctrl.h>
#include <pspusb.h>
#include <pspusbstor.h>
#include <pspthreadman.h>
#include "../bridge-hid/bridge_hid.h"

PSP_MODULE_INFO("PSPDockClient", 0, 0, 1);
PSP_MAIN_THREAD_ATTR(PSP_THREAD_ATTR_USER);
PSP_MAIN_THREAD_STACK_SIZE_KB(48);
#define PID_STORAGE 0x1c8

static SceUID notify_flag = -1;
static int storage_active, storage_running, has_notification;
static unsigned int changes, notify_total, last_state;
static int last_result;

static int load_module(const char *path) {
    int status = 0;
    SceUID module = sceKernelLoadModule(path, 0, 0);
    int result;
    if (module < 0) return module;
    result = sceKernelStartModule(module, 0, 0, &status, 0);
    return result < 0 ? result : status;
}
static int start_storage(void) {
    static const char *modules[] = {
        "flash0:/kd/semawm.prx", "flash0:/kd/usbstor.prx",
        "flash0:/kd/usbstormgr.prx", "flash0:/kd/usbstorms.prx",
        "flash0:/kd/usbstorboot.prx"
    };
    unsigned int i;
    for (i = 0; i < sizeof(modules)/sizeof(modules[0]); ++i)
        load_module(modules[i]); /* May already be loaded. */
    sceUsbStart(PSP_USBBUS_DRIVERNAME, 0, 0);
    sceUsbStart(PSP_USBSTOR_DRIVERNAME, 0, 0);
    storage_running = sceUsbGetDrvState(PSP_USBSTOR_DRIVERNAME) == 1;
    if (!storage_running || sceUsbGetDrvState(PSP_USBBUS_DRIVERNAME) != 1) return -1;
    sceUsbstorBootSetCapacity(0x800000);
    notify_flag = sceKernelCreateEventFlag("PSPDockClientUsb", 0, 0, 0);
    has_notification = notify_flag >= 0 && sceUsbstorBootRegisterNotify(notify_flag) >= 0;
    last_result = sceUsbActivate(PID_STORAGE);
    storage_active = last_result >= 0;
    return storage_active ? 0 : last_result;
}
static int stop_storage(void) {
    int result;
    if (storage_active) {
        result = sceUsbDeactivate(PID_STORAGE);
        if (result < 0) return result;
        storage_active = 0;
    }
    if (has_notification) sceUsbstorBootUnregisterNotify(notify_flag);
    has_notification = 0;
    if (notify_flag >= 0) sceKernelDeleteEventFlag(notify_flag);
    notify_flag = -1;
    if (storage_running) {
        result = sceUsbStop(PSP_USBSTOR_DRIVERNAME, 0, 0);
        if (result < 0) return result;
        storage_running = 0;
    }
    return 0;
}
static void create_token(unsigned char session[BRIDGE_SESSION_SIZE]) {
    unsigned int x = sceKernelGetSystemTimeLow() ^ notify_total ^ changes ^ 0x4f811923, i;
    for (i = 0; i < BRIDGE_SESSION_SIZE; ++i) {
        x ^= x << 13; x ^= x >> 17; x ^= x << 5;
        session[i] = (unsigned char)x;
    }
    session[0] |= 1; /* Fresh per activation; NOT a cryptographic key. */
}
static void screen(int mode, int error, unsigned int state, int was_up,
                   int release_candidate, unsigned int sequence) {
    pspDebugScreenSetXY(0, 0);
    pspDebugScreenPrintf("PSP DOCK CLIENT 0.1 / LAB ONLY\n\n");
    pspDebugScreenPrintf("Mode %-8s  err %08X\n",
        mode == 0 ? "STORAGE" : mode == 1 ? "HID" : "ERROR", error);
    pspDebugScreenPrintf("USB state 0x%08X  notify 0x%08X\n", state, notify_total);
    pspDebugScreenPrintf("Cable %d   Configured %d   Was up %d\n",
        !!(state & PSP_USB_CABLE_CONNECTED),
        !!(state & PSP_USB_CONNECTION_ESTABLISHED), was_up);
    pspDebugScreenPrintf("Changes %u   Release candidate %d\n", changes, release_candidate);
    pspDebugScreenPrintf("Return command sequence 0x%08X\n\n", sequence);
    if (mode == 0) {
        pspDebugScreenPrintf("Only after Windows safe eject SUCCESS:\n");
        pspDebugScreenPrintf("  X = enable HID while USB is unconfigured.\n");
        pspDebugScreenPrintf("If Windows vetoes eject: DO NOT press X.\n");
    } else if (mode == 1) {
        pspDebugScreenPrintf("HID input active; host RETURN_STORAGE restores USB.\n");
    } else {
        pspDebugScreenPrintf("No automatic recovery from driver errors.\n");
    }
    pspDebugScreenPrintf("\nRemove cable then START+SELECT to exit.\n");
    pspDebugScreenPrintf("Never unplug while Windows is copying files.\n");
}
int main(void) {
    SceCtrlData pad;
    u32 notify;
    unsigned int state, previous_buttons = 0;
    uint32_t sequence = 0;
    unsigned char token[BRIDGE_SESSION_SIZE];
    int mode = 0, error = 0, was_up = 0, release_candidate = 0, result;
    pspDebugScreenInit();
    sceCtrlSetSamplingCycle(0);
    sceCtrlSetSamplingMode(PSP_CTRL_MODE_ANALOG);
    result = pspSdkLoadStartModule(
        "ms0:/PSP/GAME/PSPDockClient/bridgehid.prx", PSP_MEMORY_PARTITION_KERNEL);
    if (result < 0) { mode = 2; error = result; }
    else if (start_storage() < 0) { mode = 2; error = -2; }
    last_state = (unsigned int)sceUsbGetState();
    for (;;) {
        state = (unsigned int)sceUsbGetState();
        notify = 0;
        if (has_notification && sceKernelPollEventFlag(notify_flag, 0xffffffff,
            PSP_EVENT_WAITOR | PSP_EVENT_WAITCLEAR, &notify) < 0) notify = 0;
        notify_total |= notify;
        if (state != last_state || notify) ++changes;
        last_state = state;
        if (mode == 0) {
            if (state & PSP_USB_CONNECTION_ESTABLISHED) {
                was_up = 1; release_candidate = 0;
            } else if (was_up && (state & PSP_USB_CABLE_CONNECTED))
                release_candidate = 1; /* Not proof of clean eject alone. */
        } else if (mode == 1) {
            result = bridgeHidPollStorageRequest(&sequence);
            if (result == 1 || !(state & PSP_USB_CABLE_CONNECTED)) {
                result = bridgeHidStop();
                if (result < 0) { mode = 2; error = result; }
                else if (start_storage() < 0) { mode = 2; error = -3; }
                else { mode = 0; was_up = 0; release_candidate = 0; }
            } else if (result < 0) { mode = 2; error = result; }
        }
        sceCtrlPeekBufferPositive(&pad, 1);
        if (mode == 0 && release_candidate &&
            (pad.Buttons & PSP_CTRL_CROSS) && !(previous_buttons & PSP_CTRL_CROSS)) {
            result = stop_storage();
            if (result < 0) { mode = 2; error = result; }
            else {
                create_token(token);
                result = bridgeHidStart(token);
                if (result < 0) { mode = 2; error = result; }
                else { mode = 1; sequence = 0; }
            }
        }
        screen(mode, error, state, was_up, release_candidate, sequence);
        if (!(state & PSP_USB_CABLE_CONNECTED) &&
            (pad.Buttons & (PSP_CTRL_START | PSP_CTRL_SELECT)) ==
            (PSP_CTRL_START | PSP_CTRL_SELECT)) {
            if (mode == 1) bridgeHidStop();
            if (mode == 0) stop_storage();
            sceUsbStop(PSP_USBBUS_DRIVERNAME, 0, 0);
            sceKernelExitGame();
            return 0;
        }
        previous_buttons = pad.Buttons;
        sceKernelDelayThread(100000);
    }
}
