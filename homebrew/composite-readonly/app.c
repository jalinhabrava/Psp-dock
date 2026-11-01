/* SPDX-License-Identifier: GPL-3.0-only
 * Diagnostic application for a separately named, read-only PSP controller.
 * Only standard USER imports. No USB or filesystem operations in this app.
 */
#include <pspkernel.h>
#include <pspdebug.h>
#include <pspctrl.h>
#include <pspmodulemgr.h>
#include <string.h>
#include "pdc_common.h"

PSP_MODULE_INFO("PSPDockCompositeTest",PSP_MODULE_USER,2,3);
PSP_MAIN_THREAD_ATTR(PSP_THREAD_ATTR_USER);
PSP_MAIN_THREAD_STACK_SIZE_KB(64);
static PdcSharedStatus state;

static const char *stage_name(unsigned stage) {
    switch(stage) {
        case PDC_STAGE_INIT:return "Loading";
        case PDC_STAGE_GUARD:return "Checking USB ownership";
        case PDC_STAGE_READY:return "READY - now connect USB";
        case PDC_STAGE_CONNECTED:return "Composite USB connected";
        case PDC_STAGE_FILE:return "Read-only transfer";
        case PDC_STAGE_STOPPING:return "Stopping, waiting for USB callbacks";
        case PDC_STAGE_STOPPED:return "USB stopped safely";
        default:return "Error - record this code";
    }
}
static void wait_cancel(void) {
    SceCtrlData pad;
    for(;;) {
        sceCtrlPeekBufferPositive(&pad,1);
        if(pad.Buttons&PSP_CTRL_CIRCLE)return;
        sceKernelDelayThread(100000);
    }
}
int main(void) {
    SceCtrlData pad;
    PdcLaunchArgs launch;
    SceUID module;
    int result,exit_status=-1,release_seen=0,hold=0,previous=-1;
    pspDebugScreenInit();
    pspDebugScreenPrintf("PSP DOCK COMPOSITE - READ ONLY TEST\n\n");
    pspDebugScreenPrintf("Separate test; keeps your normal PSP Dock intact.\n");
    pspDebugScreenPrintf("No save writes, no flash writes, no USB storage.\n");
    pspDebugScreenPrintf("Start with USB storage OFF and cable UNPLUGGED.\n\n");
    pspDebugScreenPrintf("X: start the test    O: cancel\n");
    for(;;) {
        sceCtrlPeekBufferPositive(&pad,1);
        if(!(pad.Buttons&PSP_CTRL_CROSS))release_seen=1;
        if(pad.Buttons&PSP_CTRL_CIRCLE){sceKernelExitGame();return 0;}
        if(release_seen && (pad.Buttons&PSP_CTRL_CROSS))break;
        sceKernelDelayThread(100000);
    }
    memset(&state,0,sizeof(state));
    state.magic=PDC_LAUNCH_MAGIC;state.size=sizeof(state);
    launch.magic=PDC_LAUNCH_MAGIC;launch.size=sizeof(launch);launch.shared=&state;
    module=sceKernelLoadModule("ms0:/PSP/GAME/PSPDockCompositeTest/dockcomposite.prx",0,NULL);
    if(module<0) {
        pspDebugScreenPrintf("Load error: %08X. O returns to XMB.\n",module);
        wait_cancel();sceKernelExitGame();return 0;
    }
    result=sceKernelStartModule(module,sizeof(launch),&launch,&exit_status,NULL);
    if(result<0 || exit_status<0) {
        pspDebugScreenPrintf("Start error: %08X / %08X. O returns.\n",result,exit_status);
        wait_cancel();sceKernelExitGame();return 0;
    }
    for(;;) {
        sceCtrlPeekBufferPositive(&pad,1);
        if((pad.Buttons&(PSP_CTRL_START|PSP_CTRL_SELECT))==(PSP_CTRL_START|PSP_CTRL_SELECT)) {
            if(++hold>=10)state.request_stop=1;
        }else hold=0;
        if(previous!=(int)state.stage || state.stage==PDC_STAGE_CONNECTED) {
            pspDebugScreenSetXY(0,9);
            pspDebugScreenPrintf("%-48s\n",stage_name(state.stage));
            pspDebugScreenPrintf("HID %u  BULK %u  Bytes %u            \n",
                (unsigned)state.hid_reports,(unsigned)state.bulk_commands,(unsigned)state.bytes_read);
            pspDebugScreenPrintf("Error %08X  Hold START+SELECT to stop.\n",(unsigned)state.error);
            previous=(int)state.stage;
        }
        if(state.safe_to_unload) {
            int stopped=-1;
            result=sceKernelStopModule(module,0,NULL,&stopped,NULL);
            if(result>=0 && stopped>=0) {
                sceKernelUnloadModule(module);
                if(state.error) {pspDebugScreenPrintf("O returns to XMB.\n");wait_cancel();}
                break;
            }
            pspDebugScreenPrintf("USB not safely stopped; module remains loaded.\n");
            state.safe_to_unload=0;
        }
        sceKernelDelayThread(100000);
    }
    sceKernelExitGame();return 0;
}
