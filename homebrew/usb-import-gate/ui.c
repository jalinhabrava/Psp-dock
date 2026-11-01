/*
 * PSP Dock USB import gate, stage 0.5.
 *
 * The EBOOT links NO sceUsb/sceUsbstorBoot or kernel libraries. It displays
 * first, and loads a separate, read-only USER PRX only when CROSS is pressed.
 * This isolates load-time import failures from failures inside main().
 * Neither the EBOOT nor the PRX starts, stops, activates or deactivates USB.
 */
#include <pspkernel.h>
#include <pspdebug.h>
#include <pspctrl.h>
#include <pspmodulemgr.h>
#include <pspthreadman.h>

PSP_MODULE_INFO("PSPDockUsbGate", PSP_MODULE_USER, 1, 0);
PSP_MAIN_THREAD_ATTR(PSP_THREAD_ATTR_USER);
PSP_MAIN_THREAD_STACK_SIZE_KB(64);

static int attempted, load_result = -1, start_result = -1;
static int usb_state = -1, stop_result = -1, unload_result = -1;

static void paint(void) {
    pspDebugScreenSetXY(0, 0);
    pspDebugScreenPrintf("PSP DOCK - IMPORT GATE 0.5\n\n");
    pspDebugScreenPrintf("USER EBOOT / NO USB IMPORTS\n");
    pspDebugScreenPrintf("No USB calls before pressing CROSS.\n\n");
    if (!attempted) {
        pspDebugScreenPrintf("EBOOT loaded successfully.\n\n");
        pspDebugScreenPrintf("X = try loading a separate READ-ONLY\n");
        pspDebugScreenPrintf("    USER-mode USB state PRX, ONE time.\n\n");
        pspDebugScreenPrintf("No storage activation, no FAT writes.\n");
    } else {
        pspDebugScreenPrintf("Load USER PRX: 0x%08X\n", load_result);
        pspDebugScreenPrintf("Start USER PRX: 0x%08X\n", start_result);
        pspDebugScreenPrintf("USB raw state: 0x%08X\n", usb_state);
        pspDebugScreenPrintf("Stop USER PRX: 0x%08X\n", stop_result);
        pspDebugScreenPrintf("Unload PRX:    0x%08X\n\n", unload_result);
        pspDebugScreenPrintf("No further attempts in this session.\n");
    }
    pspDebugScreenPrintf("\nSTART + SELECT = return to XMB\n");
    pspDebugScreenPrintf("If console shuts off, do not retry.\n");
}

int main(void) {
    SceCtrlData pad;
    unsigned int previous = 0;
    pspDebugScreenInit();
    pspDebugScreenClear();
    sceCtrlSetSamplingCycle(0);
    sceCtrlSetSamplingMode(PSP_CTRL_MODE_DIGITAL);
    paint();
    for (;;) {
        sceCtrlPeekBufferPositive(&pad, 1);
        if ((pad.Buttons & PSP_CTRL_CROSS) && !(previous & PSP_CTRL_CROSS)
            && !attempted) {
            SceUID module;
            int status = -1;
            attempted = 1;
            pspDebugScreenSetXY(0, 20);
            pspDebugScreenPrintf("LOADING USB STATE PRX (NO ACTIVATION)...\n");
            module = sceKernelLoadModule("ms0:/PSP/GAME/PSPDockUsbGate/usbstate.prx",
                                         0, 0);
            load_result = module;
            if (module >= 0) {
                start_result = sceKernelStartModule(module, 0, 0, &status, 0);
                if (start_result >= 0)
                    usb_state = status;
                /* Even if start failed, only attempt to stop if started. */
                if (start_result >= 0)
                    stop_result = sceKernelStopModule(module, 0, 0, &status, 0);
                if (start_result < 0 || stop_result >= 0)
                    unload_result = sceKernelUnloadModule(module);
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
