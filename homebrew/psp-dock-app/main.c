/* PSP Dock / manually launched USER client during USB bridge development.
 *
 * Load only the two reviewed kernel modules; NO direct USB or USBSTOR
 * USER imports, no storage activation, no flash or SAVEDATA operations.
 * NO time limit. The operator decides when to exit by holding
 * START+SELECT for >=1.5 seconds. Kernel stops HID on START+SELECT or
 * USB cable loss, and refuses overlapping storage ownership.
 */
#include <pspkernel.h>
#include <pspmodulemgr.h>
#include <pspdebug.h>
#include <pspctrl.h>
#include <pspthreadman.h>

PSP_MODULE_INFO("PSPDockApp", PSP_MODULE_USER, 1, 0);
PSP_MAIN_THREAD_ATTR(PSP_THREAD_ATTR_USER);
PSP_MAIN_THREAD_STACK_SIZE_KB(64);

#define HID_MODULE "ms0:/PSP/GAME/PSPDock/bridgehid.prx"
#define LAB_MODULE "ms0:/PSP/GAME/PSPDock/dockcore.prx"

static int load_and_start(const char *path) {
    int status = -1;
    SceUID uid = sceKernelLoadModule(path, 0, NULL);
    int started;
    if (uid < 0) return uid;
    started = sceKernelStartModule(uid, 0, NULL, &status, NULL);
    if (started < 0 || status < 0)
        return started < 0 ? started : status;
    return 0;
}

int main(void) {
    SceCtrlData pad;
    unsigned int exit_hold = 0;
    int first, second;
    pspDebugScreenInit();
    sceCtrlSetSamplingCycle(0);
    sceCtrlSetSamplingMode(PSP_CTRL_MODE_DIGITAL);
    pspDebugScreenClear();
    pspDebugScreenPrintf("PSP DOCK / CONTROLADOR USB\n\n");
    pspDebugScreenPrintf("NO USB STORAGE, NO FAT WRITES\n");
    pspDebugScreenPrintf("Keep USB cable OUT until the prompt.\n\n");
    first = load_and_start(HID_MODULE);
    second = first < 0 ? first : load_and_start(LAB_MODULE);
    if (first < 0 || second < 0) {
        pspDebugScreenPrintf("Module load failed: %08X / %08X\n",
                             (unsigned int)first, (unsigned int)second);
        pspDebugScreenPrintf("No USB role changes were requested.\n");
        pspDebugScreenPrintf("Press START + SELECT to return to XMB.\n");
        for (;;) {
            sceCtrlPeekBufferPositive(&pad, 1);
            if ((pad.Buttons & (PSP_CTRL_START | PSP_CTRL_SELECT)) ==
                    (PSP_CTRL_START | PSP_CTRL_SELECT))
                break;
            sceKernelDelayThread(100000);
        }
        sceKernelExitGame();
        return 1;
    }

    pspDebugScreenPrintf("Kernel modules started.\n");
    pspDebugScreenPrintf("HID bus begins ONLY if storage is off\n");
    pspDebugScreenPrintf("and there is NO USB cable at start.\n\n");
    pspDebugScreenPrintf("Checking USB ownership without cable...\n");
    /* The controller thread checks bus/storage 1.5 s after start.
     * Never invite the user to plug the cable before that guard has run. */
    sceKernelDelayThread(4000000);
    pspDebugScreenPrintf("Now connect the USB cable to your PC.\n");
    pspDebugScreenPrintf("Look for PSP Dock HID in Windows.\n\n");
    pspDebugScreenPrintf("El mando no tiene limite de tiempo.\n");
    pspDebugScreenPrintf("START+SELECT 1,5s = cerrar PSP Dock.\n");
    pspDebugScreenPrintf("No actives USB Sony mientras HID funcione.\n");
    pspDebugScreenPrintf("Si retiras el cable, HID se detendra.\n");
    /* Run for as long as the user wants, NOT for a fixed number
     * of seconds. Never spin: the 100ms delay bounds CPU usage. */
    for (;;) {
        sceCtrlPeekBufferPositive(&pad, 1);
        if ((pad.Buttons & (PSP_CTRL_START | PSP_CTRL_SELECT)) ==
                (PSP_CTRL_START | PSP_CTRL_SELECT))
            ++exit_hold;
        else
            exit_hold = 0;
        if (exit_hold >= 15) {
            /* Kernel independently sees START+SELECT (>=1s). Give it
             * ample time to release USB endpoints before USER exits. */
            sceKernelDelayThread(3000000);
            break;
        }
        sceKernelDelayThread(100000);
    }
    pspDebugScreenPrintf("\nReturning to XMB.\n");
    sceKernelExitGame();
    return 0;
}
