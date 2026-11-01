/* PSP Dock Import Gate 0.6: no direct USB imports or kernel libraries.
 *
 * Unlike 0.5, the module_start return value is RESERVED for module lifecycle.
 * The raw sceUsbGetState value is written through an explicit, validated,
 * user-memory result pointer passed as an argument to sceKernelStartModule.
 *
 * Stage 1 (launch, no buttons) is UI-only. CROSS tries exactly one read-only
 * user-mode PRX. No storage drivers, USB activation, flash or savegame writes.
 */
#include <pspkernel.h>
#include <pspdebug.h>
#include <pspctrl.h>
#include <pspmodulemgr.h>
#include <pspthreadman.h>
#include "gate_protocol.h"

PSP_MODULE_INFO("PSPDockUsbGate06", PSP_MODULE_USER, 1, 0);
PSP_MAIN_THREAD_ATTR(PSP_THREAD_ATTR_USER);
PSP_MAIN_THREAD_STACK_SIZE_KB(64);

static int attempted;
static int load_api = -1, start_api = -1, start_status = -1;
static int stop_api = -1, stop_status = -1, unload_api = -1;
static GateResult measured = { GATE_V06_RESULT_MAGIC, (s32)GATE_V06_SENTINEL };

static void paint(void) {
    int usb = measured.usb_state;
    pspDebugScreenSetXY(0, 0);
    pspDebugScreenPrintf("PSP DOCK - IMPORT GATE 0.6\n\n");
    pspDebugScreenPrintf("USER EBOOT / USB READ VIA PRX\n");
    pspDebugScreenPrintf("USB is never started or activated.\n\n");
    if (!attempted) {
        pspDebugScreenPrintf("EBOOT loaded successfully.\n\n");
        pspDebugScreenPrintf("X = load ONE read-only user PRX,\n");
        pspDebugScreenPrintf("    sample USB state, stop & unload.\n\n");
        pspDebugScreenPrintf("No USB operations until X is pressed.\n");
    } else {
        pspDebugScreenPrintf("Load API:       0x%08X\n", load_api);
        pspDebugScreenPrintf("Start API:      0x%08X\n", start_api);
        pspDebugScreenPrintf("Start status:   0x%08X\n", start_status);
        pspDebugScreenPrintf("USB state:      0x%08X\n", usb);
        pspDebugScreenPrintf("Stop API:       0x%08X\n", stop_api);
        pspDebugScreenPrintf("Stop status:    0x%08X\n", stop_status);
        pspDebugScreenPrintf("Unload API:     0x%08X\n\n", unload_api);
        if ((u32)usb == GATE_V06_SENTINEL)
            pspDebugScreenPrintf("No USB readout returned.\n");
        else if (usb < 0)
            pspDebugScreenPrintf("USB readout returned a negative error.\n");
        else
            pspDebugScreenPrintf("Active %d | Cable %d | Linked %d\n",
                !!(usb & 0x200), !!(usb & 0x20), !!(usb & 0x2));
        pspDebugScreenPrintf("No more attempts during this session.\n");
    }
    pspDebugScreenPrintf("\nSTART+SELECT = return to XMB.\n");
    pspDebugScreenPrintf("If PSP switches off: do not retry.\n");
}

int main(void) {
    SceCtrlData pad;
    u32 previous = 0;
    pspDebugScreenInit();
    pspDebugScreenClear();
    sceCtrlSetSamplingCycle(0);
    sceCtrlSetSamplingMode(PSP_CTRL_MODE_DIGITAL);
    paint();
    for (;;) {
        sceCtrlPeekBufferPositive(&pad, 1);
        if ((pad.Buttons & PSP_CTRL_CROSS) && !(previous & PSP_CTRL_CROSS) && !attempted) {
            SceUID module;
            GateArguments args;
            attempted = 1;
            args.magic = GATE_V06_MAGIC;
            args.result_pointer = (u32)&measured;
            pspDebugScreenSetXY(0, 22);
            pspDebugScreenPrintf("LOADING READ-ONLY PRX (NO USB MODE CHANGE)\n");
            module = sceKernelLoadModule(
                "ms0:/PSP/GAME/PSPDockUsbGate06/usbstate.prx", 0, 0);
            load_api = (int)module;
            if (module >= 0) {
                start_api = sceKernelStartModule(
                    module, sizeof(args), &args, &start_status, 0);
                /* Lifecycle success is checked independently of measured USB
                 * state, including when sceUsbGetState returns an error. */
                if (start_api == module) {
                    stop_api = sceKernelStopModule(
                        module, 0, 0, &stop_status, 0);
                    if (stop_api == 0 && stop_status == 0)
                        unload_api = sceKernelUnloadModule(module);
                } else if (start_api < 0) {
                    /* A start failure is not proof that it is safe to unload.
                     * Avoid touching a module in an uncertain state. */
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
