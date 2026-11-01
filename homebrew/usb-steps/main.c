/* RETIRADO: causó pantalla negra y apagado en PSP física ARK-5/6.61.
 * El código original está preservado en legacy-unsafe/main.c.
 * Esta versión no debe compilarse ni distribuirse.
 */
#error "PSP Dock USB Step Probe 1.0 RETIRADO: importaciones de kernel en EBOOT de usuario"
/*
 * PSP Dock USB diagnostic 1.0 — deliberately STEPPED and USER mode.
 * PSPDockProbe 0.1 tried USB startup before drawing anything; its kernel
 * main thread could fail and black-screen the console.
 *
 * This replacement displays first, then runs ONE operation for each CROSS
 * press, logging its raw result. It has no flash writes and never reads
 * or writes Memory Stick data. It is NOT an automatic Dock controller.
 */
#include <pspkernel.h>
#include <pspdebug.h>
#include <pspctrl.h>
#include <pspthreadman.h>
#include <pspusb.h>
#include <pspusbstor.h>
#include <pspmodulemgr.h>
#include <string.h>

PSP_MODULE_INFO("DockUsbSteps", PSP_MODULE_USER, 1, 0);
PSP_MAIN_THREAD_ATTR(PSP_THREAD_ATTR_USER);
PSP_MAIN_THREAD_STACK_SIZE_KB(64);

#define NUM_STEPS 10
#define PID_STORAGE 0x1c8

static const char *const names[NUM_STEPS] = {
    "Read initial USB state",
    "Load semawm.prx",
    "Load usbstor.prx",
    "Load usbstormgr.prx",
    "Load usbstorms.prx",
    "Load usbstorboot.prx",
    "Start USB bus",
    "Start USB storage driver",
    "Set storage capacity",
    "Activate USBSTOR device"
};
static const char *const modules[5] = {
    "flash0:/kd/semawm.prx",
    "flash0:/kd/usbstor.prx",
    "flash0:/kd/usbstormgr.prx",
    "flash0:/kd/usbstorms.prx",
    "flash0:/kd/usbstorboot.prx"
};
static int next_step, failed, last_result, queried, owned_bus, owned_storage, active;
static int raw_state, bus_state, storage_state;

static int load_start_module(const char *path) {
    int status = 0, started;
    SceUID loaded = sceKernelLoadModule(path, 0, 0);
    if (loaded < 0) return loaded;
    started = sceKernelStartModule(loaded, 0, 0, &status, 0);
    if (started < 0) return started;
    if (started != loaded) return -1;
    return 0;
}

static int perform_step(int index) {
    int result;
    switch (index) {
    case 0:
        raw_state = sceUsbGetState();
        bus_state = sceUsbGetDrvState(PSP_USBBUS_DRIVERNAME);
        storage_state = sceUsbGetDrvState(PSP_USBSTOR_DRIVERNAME);
        queried = 1;
        /* Refuse to take control of an already activated USB connection. */
        if (raw_state < 0) return raw_state;
        if (raw_state & PSP_USB_ACTIVATED) return -1;
        return 0;
    case 1: case 2: case 3: case 4: case 5:
        return load_start_module(modules[index - 1]);
    case 6:
        result = sceUsbStart(PSP_USBBUS_DRIVERNAME, 0, 0);
        if (result == 0) owned_bus = 1;
        return result;
    case 7:
        result = sceUsbStart(PSP_USBSTOR_DRIVERNAME, 0, 0);
        if (result == 0) owned_storage = 1;
        return result;
    case 8:
        return sceUsbstorBootSetCapacity(0x800000);
    case 9:
        result = sceUsbActivate(PID_STORAGE);
        if (result == 0) active = 1;
        return result;
    }
    return -1;
}

static int shutdown_usb(void) {
    int result;
    if (active) {
        result = sceUsbDeactivate(PID_STORAGE);
        if (result < 0) return result;
        active = 0;
    }
    if (owned_storage) {
        result = sceUsbStop(PSP_USBSTOR_DRIVERNAME, 0, 0);
        if (result < 0) return result;
        owned_storage = 0;
    }
    if (owned_bus) {
        result = sceUsbStop(PSP_USBBUS_DRIVERNAME, 0, 0);
        if (result < 0) return result;
        owned_bus = 0;
    }
    return 0;
}

static void draw(void) {
    pspDebugScreenSetXY(0, 0);
    pspDebugScreenPrintf("PSP DOCK / USB STEP PROBE 1.0\n");
    pspDebugScreenPrintf("USER MODE / MANUAL LAB TEST\n\n");
    pspDebugScreenPrintf("Steps complete: %u of %u\n", next_step, NUM_STEPS);
    pspDebugScreenPrintf("Last operation result: 0x%08X\n", last_result);
    if (queried) {
        pspDebugScreenPrintf("USB raw state:   0x%08X\n", raw_state);
        pspDebugScreenPrintf("Bus driver: %08X   MS driver: %08X\n", bus_state, storage_state);
        pspDebugScreenPrintf("Cable: %u  Configured: %u  Activated: %u\n",
            !!(raw_state & PSP_USB_CABLE_CONNECTED),
            !!(raw_state & PSP_USB_CONNECTION_ESTABLISHED),
            !!(raw_state & PSP_USB_ACTIVATED));
    } else {
        pspDebugScreenPrintf("USB has NOT been queried or started.\n\n");
    }
    pspDebugScreenPrintf("\n");
    if (failed) {
        pspDebugScreenPrintf("STOP: Previous step failed.\n");
        pspDebugScreenPrintf("DO NOT repeat or force the next step.\n");
    } else if (next_step < NUM_STEPS) {
        pspDebugScreenPrintf("NEXT: %s\n", names[next_step]);
        pspDebugScreenPrintf("CROSS = perform ONLY this next step\n");
    } else {
        pspDebugScreenPrintf("USBSTOR activation request succeeded.\n");
        pspDebugScreenPrintf("Inspect Windows and USB state; no HID here.\n");
    }
    pspDebugScreenPrintf("\nSTART+SELECT = exit when USB is safely off.\n");
    pspDebugScreenPrintf("After storage activation, eject on PC first,\n");
    pspDebugScreenPrintf("then physically remove USB cable to exit.\n");
    pspDebugScreenPrintf("No flash writes, no savegame writes.\n");
}

int main(void) {
    SceCtrlData pad;
    unsigned int previous = 0;
    int result;
    pspDebugScreenInit();
    pspDebugScreenClear();
    sceCtrlSetSamplingCycle(0);
    sceCtrlSetSamplingMode(PSP_CTRL_MODE_DIGITAL);
    last_result = 0;
    draw(); /* Visible BEFORE any USB API call. */
    for (;;) {
        sceCtrlPeekBufferPositive(&pad, 1);
        if ((pad.Buttons & PSP_CTRL_CROSS) && !(previous & PSP_CTRL_CROSS) &&
            !failed && next_step < NUM_STEPS) {
            /* Draw the operation BEFORE calling it: a crash identifies it. */
            pspDebugScreenSetXY(0, 24);
            pspDebugScreenPrintf("EXECUTING: %-31s\n", names[next_step]);
            result = perform_step(next_step);
            last_result = result;
            if (result == 0) ++next_step;
            else failed = 1;
            pspDebugScreenClear();
            draw();
        }
        if (queried && !failed && next_step > 0) {
            raw_state = sceUsbGetState();
            bus_state = sceUsbGetDrvState(PSP_USBBUS_DRIVERNAME);
            storage_state = sceUsbGetDrvState(PSP_USBSTOR_DRIVERNAME);
            draw();
        }
        if ((pad.Buttons & (PSP_CTRL_START | PSP_CTRL_SELECT)) ==
            (PSP_CTRL_START | PSP_CTRL_SELECT)) {
            /* Never deactivate storage while the host can still own FAT. */
            if (!active ||
                (raw_state >= 0 && !(raw_state & PSP_USB_CABLE_CONNECTED)
                 && !(raw_state & PSP_USB_CONNECTION_ESTABLISHED))) {
                result = shutdown_usb();
                if (result == 0) {
                    sceKernelExitGame();
                    return 0;
                }
                failed = 1;
                last_result = result;
                pspDebugScreenClear();
                draw();
            }
        }
        previous = pad.Buttons;
        sceKernelDelayThread(100000);
    }
}
