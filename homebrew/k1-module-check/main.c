/*
 * PSP Dock K1 MODULE CHECK — lab-only USER homebrew.
 * Queries public ModuleMgrForUser APIs only; NO USB, kernel patches,
 * filesystem I/O, module loading, flash or SAVEDATA operations.
 *
 * Used after K1's first guarded boot to see whether ModuleMgrForUser
 * exposes the K1 kernel module name. Absence is inconclusive because
 * the firmware may hide kernel modules from user-mode queries.
 */
#include <pspkernel.h>
#include <pspmodulemgr.h>
#include <pspdebug.h>
#include <pspctrl.h>
#include <pspthreadman.h>
#include <string.h>

PSP_MODULE_INFO("PSPDockK1Check", PSP_MODULE_USER, 1, 0);
PSP_MAIN_THREAD_ATTR(PSP_THREAD_ATTR_USER);
PSP_MAIN_THREAD_STACK_SIZE_KB(64);

#define MAX_MODULES 128
static SceUID module_ids[MAX_MODULES];

static void scan_modules(void) {
    SceKernelModuleInfo info;
    int count = 0, result, i, queried = 0, failed = 0;
    int k1_seen = 0, user_seen = 0;

    pspDebugScreenClear();
    pspDebugScreenSetXY(0, 0);
    pspDebugScreenPrintf("PSP Dock / K1 MODULE CHECK (USER)\n\n");
    pspDebugScreenPrintf("READ ONLY - no USB or file operations\n\n");
    result = sceKernelGetModuleIdList(module_ids, sizeof(module_ids), &count);
    pspDebugScreenPrintf("Module list rc: %08X\n", (unsigned int)result);
    pspDebugScreenPrintf("Reported modules: %d\n", count);
    if (result >= 0) {
        int limit = count;
        if (limit > MAX_MODULES) limit = MAX_MODULES;
        if (limit < 0) limit = 0;
        for (i = 0; i < limit; ++i) {
            memset(&info, 0, sizeof(info));
            info.size = sizeof(info);
            if (sceKernelQueryModuleInfo(module_ids[i], &info) < 0) {
                ++failed;
                continue;
            }
            ++queried;
            if (strcmp(info.name, "PSPDockK1Observer") == 0) {
                k1_seen = 1;
                pspDebugScreenPrintf("K1 module visible, UID %08X\n",
                                     (unsigned int)module_ids[i]);
            }
            if (strcmp(info.name, "PSPDockVSHProbe") == 0)
                user_seen = 1;
        }
    }
    pspDebugScreenPrintf("Queryable: %d  restricted: %d\n\n",
                         queried, failed);
    pspDebugScreenPrintf("K1 visible via USER API: %s\n",
                         k1_seen ? "YES" : "NO / INCONCLUSIVE");
    pspDebugScreenPrintf("Original VSH visible: %s\n\n",
                         user_seen ? "YES" : "NO / INCONCLUSIVE");
    pspDebugScreenPrintf("X: rescan modules\n");
    pspDebugScreenPrintf("START + SELECT: return to XMB\n\n");
    pspDebugScreenPrintf("NO does NOT prove K1 is absent.\n");
}

int main(void) {
    SceCtrlData pad;
    unsigned int previous = 0;
    pspDebugScreenInit();
    sceCtrlSetSamplingCycle(0);
    sceCtrlSetSamplingMode(PSP_CTRL_MODE_DIGITAL);
    scan_modules();
    for (;;) {
        sceCtrlPeekBufferPositive(&pad, 1);
        if ((pad.Buttons & (PSP_CTRL_START | PSP_CTRL_SELECT)) ==
                (PSP_CTRL_START | PSP_CTRL_SELECT))
            break;
        if ((pad.Buttons & PSP_CTRL_CROSS) && !(previous & PSP_CTRL_CROSS))
            scan_modules();
        previous = pad.Buttons;
        sceKernelDelayThread(50000);
    }
    sceKernelExitGame();
    return 0;
}
