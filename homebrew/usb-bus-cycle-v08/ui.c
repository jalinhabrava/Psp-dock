/* PSP Dock USB Bus Cycle 0.8 — safe user EBOOT, no direct USB imports.
 * One deliberate L+R+X gesture requests a gated, disconnected-only
 * start/status/stop cycle of USBBusDriver via a separate user-mode PRX.
 * NEVER starts USBStor_Driver or activates the USB device.
 */
#include <pspkernel.h>
#include <pspdebug.h>
#include <pspctrl.h>
#include <pspmodulemgr.h>
#include <pspthreadman.h>
#include "gate_protocol.h"

PSP_MODULE_INFO("PSPDockUsbBus08", PSP_MODULE_USER, 1, 0);
PSP_MAIN_THREAD_ATTR(PSP_THREAD_ATTR_USER);
PSP_MAIN_THREAD_STACK_SIZE_KB(64);

static int attempted, load_api = -1, start_api = -1, start_status = -1;
static int stop_api = -1, stop_status = -1, unload_api = -1;
static GateResult measured = {
    GATE_V08_RESULT_MAGIC,
    (s32)GATE_V08_SENTINEL, (s32)GATE_V08_SENTINEL,
    (s32)GATE_V08_SENTINEL, (s32)GATE_V08_SENTINEL,
    (s32)GATE_V08_SENTINEL, (s32)GATE_V08_SENTINEL,
    (s32)GATE_V08_SENTINEL, (s32)GATE_V08_SENTINEL,
    (s32)GATE_V08_SENTINEL, (s32)GATE_V08_SENTINEL,
};

static void paint(void) {
    pspDebugScreenSetXY(0, 0);
    pspDebugScreenPrintf("PSP DOCK - USB BUS CYCLE 0.8\n");
    pspDebugScreenPrintf("USER MODE / NO USB STORAGE ACTIVATION\n\n");
    if (!attempted) {
        pspDebugScreenPrintf("USB bus start/stop experiment (one time).\n\n");
        pspDebugScreenPrintf("SAFETY PRECONDITIONS:\n");
        pspDebugScreenPrintf(" * No USB cable, no active link\n");
        pspDebugScreenPrintf(" * Bus driver STOPPED\n");
        pspDebugScreenPrintf(" * Storage missing or STOPPED\n\n");
        pspDebugScreenPrintf("L + R + X = test bus start then stop\n");
        pspDebugScreenPrintf("X alone does nothing.\n");
        pspDebugScreenPrintf("The PRX will refuse if any guard fails.\n");
        pspDebugScreenPrintf("Does NOT mount or expose Memory Stick.\n");
    } else {
        pspDebugScreenPrintf("Module Load:   0x%08X\n", load_api);
        pspDebugScreenPrintf("Module Start:  0x%08X status: 0x%08X\n", start_api, start_status);
        pspDebugScreenPrintf("Initial USB:   0x%08X\n", (unsigned int)measured.usb_before);
        pspDebugScreenPrintf("Initial BUS:   0x%08X\n", (unsigned int)measured.bus_before);
        pspDebugScreenPrintf("Initial STOR:  0x%08X\n", (unsigned int)measured.storage_before);
        pspDebugScreenPrintf("Guard code:    0x%08X\n", (unsigned int)measured.guard);
        pspDebugScreenPrintf("BUS Start:     0x%08X\n", (unsigned int)measured.start_result);
        pspDebugScreenPrintf("BUS after on:  0x%08X\n", (unsigned int)measured.bus_after_start);
        pspDebugScreenPrintf("BUS Stop:      0x%08X\n", (unsigned int)measured.stop_result);
        pspDebugScreenPrintf("BUS after off: 0x%08X\n", (unsigned int)measured.bus_after_stop);
        pspDebugScreenPrintf("Final STOR:   0x%08X\n", (unsigned int)measured.storage_after);
        pspDebugScreenPrintf("Final USB:    0x%08X\n", (unsigned int)measured.usb_after);
        pspDebugScreenPrintf("Module Stop:   0x%08X status: 0x%08X\n", stop_api, stop_status);
        pspDebugScreenPrintf("Module Unload: 0x%08X\n\n", unload_api);
        if ((u32)measured.guard != GATE_V08_OK)
            pspDebugScreenPrintf("BLOCKED: safety guard refused bus start.\n");
        else if (measured.start_result == 0 && measured.stop_result == 0 &&
                 measured.bus_after_stop == GATE_V08_STOPPED)
            pspDebugScreenPrintf("USB BUS start/stop completed.\n");
        else
            pspDebugScreenPrintf("BUS NOT VERIFIED STOPPED. DO NOT RETRY.\n");
        pspDebugScreenPrintf("No further attempts this session.\n");
    }
    pspDebugScreenPrintf("\nSTART+SELECT = return to XMB\n");
    pspDebugScreenPrintf("If a crash occurs, do not retry.\n");
}

int main(void) {
    SceCtrlData pad;
    u32 previous = 0;
    const u32 combo = PSP_CTRL_CROSS | PSP_CTRL_LTRIGGER | PSP_CTRL_RTRIGGER;
    pspDebugScreenInit();
    pspDebugScreenClear();
    sceCtrlSetSamplingCycle(0);
    sceCtrlSetSamplingMode(PSP_CTRL_MODE_DIGITAL);
    paint();
    for (;;) {
        sceCtrlPeekBufferPositive(&pad, 1);
        if (!attempted && (pad.Buttons & combo) == combo &&
            (previous & combo) != combo) {
            SceUID module;
            GateArguments args;
            attempted = 1;
            args.magic = GATE_V08_MAGIC;
            args.result_pointer = (u32)&measured;
            pspDebugScreenSetXY(0, 22);
            pspDebugScreenPrintf("RUNNING GATED USB BUS TEST...          \n");
            module = sceKernelLoadModule(
                "ms0:/PSP/GAME/PSPDockBusCycle08/usbstate.prx", 0, 0);
            load_api = (int)module;
            if (module >= 0) {
                start_api = sceKernelStartModule(
                    module, sizeof(args), &args, &start_status, 0);
                if (start_api == module) {
                    stop_api = sceKernelStopModule(module, 0, 0, &stop_status, 0);
                    if (stop_api == 0 && stop_status == 0)
                        unload_api = sceKernelUnloadModule(module);
                }
            }
            pspDebugScreenClear();
            paint();
        }
        if ((pad.Buttons & (PSP_CTRL_START | PSP_CTRL_SELECT)) ==
            (PSP_CTRL_START | PSP_CTRL_SELECT)) {
            sceKernelExitGame();
            return 0;
        }
        previous = pad.Buttons;
        sceKernelDelayThread(50000);
    }
}
