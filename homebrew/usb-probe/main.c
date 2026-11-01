/* UNSAFE, RETIRED: crashed on real PSP-2003 running ARK-5/6.61.
 * The unmodified historical source is in legacy-unsafe/main.c.
 * Never rebuild or reinstall this diagnostic on the console.
 */
#error "Retired: use homebrew/screen-check then homebrew/usb-steps; do not run PSPDockProbe 0.1"
/* PSP Dock USB observer, 2026.
 * USBSTOR startup follows the PSPSDK BSD storage sample by John Kelley/PspPet.
 * See LICENSE-PSPSDK.txt. No filesystem accesses while storage is active.
 */
#include <pspkernel.h>
#include <pspdebug.h>
#include <pspctrl.h>
#include <pspdisplay.h>
#include <pspusb.h>
#include <pspusbstor.h>
#include <pspthreadman.h>
#include <pspmodulemgr.h>
#include <stdio.h>
#include <string.h>

PSP_MODULE_INFO("PSPDockProbe", 0x1000, 0, 1);
PSP_MAIN_THREAD_ATTR(0);
PSP_MAIN_THREAD_STACK_SIZE_KB(48);
#define PID_STORAGE 0x1c8
#define CAPACITY 64

typedef struct {
    unsigned int seconds, state, notify;
} Observation;
static Observation history[CAPACITY];
static unsigned int count, previous = 0xffffffff, all_notify;
static unsigned long long start;
static SceUID event_id = -1;
static int registered, active, bus_started, storage_started;
static int startup_results[10];

static int load_module(const char *path) {
    int status = 0;
    SceUID id = sceKernelLoadModule(path, 0, NULL);
    if (id < 0) return id;
    return sceKernelStartModule(id, 0, NULL, &status, NULL) < 0 ? -1 : status;
}

static void observe(unsigned int state, unsigned int notify) {
    Observation *row = &history[count % CAPACITY];
    row->seconds = (unsigned int)((sceKernelGetSystemTimeWide() - start) / 1000000);
    row->state = state;
    row->notify = notify;
    ++count;
    previous = state;
    all_notify |= notify;
}

static int start_storage(void) {
    const char *modules[] = {"flash0:/kd/semawm.prx", "flash0:/kd/usbstor.prx",
        "flash0:/kd/usbstormgr.prx", "flash0:/kd/usbstorms.prx", "flash0:/kd/usbstorboot.prx"};
    unsigned int i;
    for (i = 0; i < sizeof(modules) / sizeof(modules[0]); ++i)
        startup_results[i] = load_module(modules[i]);
    /* Resident modules may report already-loaded: driver calls decide readiness. */
    startup_results[5] = sceUsbStart(PSP_USBBUS_DRIVERNAME, 0, NULL);
    bus_started = sceUsbGetDrvState(PSP_USBBUS_DRIVERNAME) == 1;
    startup_results[6] = sceUsbStart(PSP_USBSTOR_DRIVERNAME, 0, NULL);
    storage_started = sceUsbGetDrvState(PSP_USBSTOR_DRIVERNAME) == 1;
    if (!bus_started || !storage_started) return -1;
    startup_results[7] = sceUsbstorBootSetCapacity(0x800000);
    event_id = sceKernelCreateEventFlag("DockUsbNotify", 0, 0, NULL);
    startup_results[8] = event_id < 0 ? event_id : sceUsbstorBootRegisterNotify(event_id);
    registered = event_id >= 0 && startup_results[8] >= 0;
    startup_results[9] = sceUsbActivate(PID_STORAGE);
    active = startup_results[9] >= 0;
    return active ? 0 : -1;
}

static void stop_storage(void) {
    if (active) sceUsbDeactivate(PID_STORAGE);
    if (registered) sceUsbstorBootUnregisterNotify(event_id);
    if (event_id >= 0) sceKernelDeleteEventFlag(event_id);
    if (storage_started) sceUsbStop(PSP_USBSTOR_DRIVERNAME, 0, NULL);
    if (bus_started) sceUsbStop(PSP_USBBUS_DRIVERNAME, 0, NULL);
    active = 0;
}

static void draw(unsigned int state, int ready) {
    unsigned int i, first = count > 8 ? count - 8 : 0;
    pspDebugScreenSetXY(0, 0);
    pspDebugScreenPrintf("PSP DOCK / USB OBSERVER 0.1 / LAB ONLY\n\n");
    pspDebugScreenPrintf("USBSTOR: %s   STATE: 0x%08X\n", ready ? "ACTIVE" : "ERROR ", state);
    pspDebugScreenPrintf("Activated %u   Cable %u   Configured %u\n",
        !!(state & PSP_USB_ACTIVATED), !!(state & PSP_USB_CABLE_CONNECTED),
        !!(state & PSP_USB_CONNECTION_ESTABLISHED));
    pspDebugScreenPrintf("Changes: %u  Notify OR: 0x%08X\n\n", count, all_notify);
    pspDebugScreenPrintf("USB event bits are RAW, meaning unverified.\n");
    pspDebugScreenPrintf("No file reads/writes while USB is mounted.\n");
    pspDebugScreenPrintf("Does NOT switch to controller yet.\n\n");
    pspDebugScreenPrintf("Windows will test safe eject with cable attached.\n");
    pspDebugScreenPrintf("Read STATE and Changes before/after.\n\n");
    pspDebugScreenPrintf("Time    USB state    Raw notification\n");
    for (i = first; i < count; ++i) {
        Observation *row = &history[i % CAPACITY];
        pspDebugScreenPrintf("%5us  0x%08X   0x%08X\n", row->seconds, row->state, row->notify);
    }
    for (i = count - first; i < 8; ++i) pspDebugScreenPrintf("                                             \n");
    pspDebugScreenPrintf("\nExit: remove USB cable, then hold START+SELECT.\n");
    pspDebugScreenPrintf("Exit is blocked while a cable is detected.\n");
    pspDebugScreenPrintf("Do not unplug while Windows is writing.\n\n");
    pspDebugScreenPrintf("Init raw: %08X %08X %08X %08X %08X\n",
        startup_results[0], startup_results[1], startup_results[2], startup_results[3], startup_results[4]);
    pspDebugScreenPrintf("USB init: %08X %08X %08X %08X %08X\n",
        startup_results[5], startup_results[6], startup_results[7], startup_results[8], startup_results[9]);
}

int main(void) {
    SceCtrlData pad;
    int ready;
    unsigned int state;
    u32 notify;
    pspDebugScreenInit();
    pspDebugScreenClear();
    sceCtrlSetSamplingCycle(0);
    sceCtrlSetSamplingMode(PSP_CTRL_MODE_ANALOG);
    start = sceKernelGetSystemTimeWide();
    ready = start_storage() == 0;
    for (;;) {
        state = (unsigned int)sceUsbGetState();
        notify = 0;
        if (registered && sceKernelPollEventFlag(event_id, 0xffffffff,
            PSP_EVENT_WAITOR | PSP_EVENT_WAITCLEAR, &notify) < 0) notify = 0;
        if (state != previous || notify) observe(state, notify);
        draw(state, ready);
        sceCtrlPeekBufferPositive(&pad, 1);
        if (!(state & PSP_USB_CABLE_CONNECTED) &&
            (pad.Buttons & (PSP_CTRL_START | PSP_CTRL_SELECT)) == (PSP_CTRL_START | PSP_CTRL_SELECT)) {
            stop_storage();
            sceKernelExitGame();
            return 0;
        }
        sceKernelDelayThread(100000);
    }
}
