/*
 * PSP Dock startup diagnostic (stage 0): deliberately NO USB, no kernel-mode
 * module, no flash access, no file I/O, no plugins and no auto-start.
 * Run this BEFORE attempting any replacement USB probe.
 */
#include <pspkernel.h>
#include <pspdebug.h>
#include <pspctrl.h>
#include <pspthreadman.h>

PSP_MODULE_INFO("DockScreenCheck", PSP_MODULE_USER, 1, 0);
PSP_MAIN_THREAD_ATTR(PSP_THREAD_ATTR_USER);
PSP_MAIN_THREAD_STACK_SIZE_KB(64);

int main(void) {
    SceCtrlData pad;
    unsigned int previous = 0, presses = 0;
    int running = 1;
    pspDebugScreenInit();
    pspDebugScreenClear();
    sceCtrlSetSamplingCycle(0);
    sceCtrlSetSamplingMode(PSP_CTRL_MODE_DIGITAL);
    while (running) {
        pspDebugScreenSetXY(0, 0);
        pspDebugScreenPrintf("PSP DOCK / STARTUP CHECK 1.0\n\n");
        pspDebugScreenPrintf("STAGE 0: USER MODE / NO USB\n\n");
        pspDebugScreenPrintf("If this text is visible, the basic\n");
        pspDebugScreenPrintf("homebrew module and screen started.\n\n");
        pspDebugScreenPrintf("Press CROSS repeatedly: %u          \n\n", presses);
        pspDebugScreenPrintf("Exit: START + SELECT (no USB code)\n\n");
        pspDebugScreenPrintf("This diagnostic NEVER opens the\n");
        pspDebugScreenPrintf("Memory Stick or changes USB mode.\n");
        sceCtrlPeekBufferPositive(&pad, 1);
        if ((pad.Buttons & PSP_CTRL_CROSS) && !(previous & PSP_CTRL_CROSS))
            ++presses;
        if ((pad.Buttons & (PSP_CTRL_START | PSP_CTRL_SELECT)) ==
            (PSP_CTRL_START | PSP_CTRL_SELECT))
            running = 0;
        previous = pad.Buttons;
        sceKernelDelayThread(50000);
    }
    sceKernelExitGame();
    return 0;
}
