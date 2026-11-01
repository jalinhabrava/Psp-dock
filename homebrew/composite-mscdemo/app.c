/* SPDX-License-Identifier: GPL-3.0-only
 * USER launcher for the synthetic USB mass-storage + HID diagnostic.
 * No direct USB or storage API imports. Kernel state controls unloading.
 */
#include <pspkernel.h>
#include <pspdebug.h>
#include <pspctrl.h>
#include <pspmodulemgr.h>
#include <string.h>
#include "msc_common.h"

PSP_MODULE_INFO("PSPDockStorageTest",PSP_MODULE_USER,2,5);
PSP_MAIN_THREAD_ATTR(PSP_THREAD_ATTR_USER);
PSP_MAIN_THREAD_STACK_SIZE_KB(64);
static PdcSharedStatus state;

static const char *stage_name(unsigned stage) {
    switch(stage) {
        case PDC_STAGE_INIT:return "Cargando modulo...";
        case PDC_STAGE_GUARD:return "Comprobando USB...";
        case PDC_STAGE_READY:return "LISTO: conecta el cable USB";
        case PDC_STAGE_CONNECTED:return "USB conectado; comprobar en Windows";
        case PDC_STAGE_FILE:return "Lectura del disco de prueba";
        case PDC_STAGE_STOPPING:return "Cerrando USB; espera...";
        case PDC_STAGE_STOPPED:return "USB detenido";
        default:return "Error: anota el codigo de abajo";
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
    int result,exit_status=-1,release_seen=0,hold=0;
    pspDebugScreenInit();
    pspDebugScreenSetBackColor(0xff231105);
    pspDebugScreenSetTextColor(0xfff6ede7);
    pspDebugScreenClear();
    pspDebugScreenPrintf("PSP DOCK / USB STORAGE + HID / 0205\n\n");
    pspDebugScreenPrintf("Disco ficticio de 512 KiB, solo lectura.\n");
    pspDebugScreenPrintf("No ofrece los archivos reales de la Memory Stick.\n");
    pspDebugScreenPrintf("Desactiva Conexion USB y quita el cable.\n\n");
    pspDebugScreenPrintf("X: iniciar    O: volver al XMB\n");
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
    module=sceKernelLoadModule("ms0:/PSP/GAME/PSPDockStorageTest/dockmsc.prx",0,NULL);
    if(module<0) {
        pspDebugScreenPrintf("Error al cargar: %08X. O: volver.\n",(unsigned)module);
        wait_cancel();sceKernelExitGame();return 0;
    }
    result=sceKernelStartModule(module,sizeof(launch),&launch,&exit_status,NULL);
    if(result<0 || exit_status<0) {
        pspDebugScreenPrintf("Error al iniciar: %08X / %08X.\n",
            (unsigned)result,(unsigned)exit_status);
        /* A failed module_start must leave no running threads or callbacks. */
        pspDebugScreenPrintf("O: volver al XMB.\n");
        wait_cancel();sceKernelExitGame();return 0;
    }
    pspDebugScreenSetXY(0,7);
    pspDebugScreenPrintf("Mueve la cruceta durante la lectura del PC.\n");
    pspDebugScreenPrintf("START+SELECT (1 s): cerrar la prueba\n");
    for(;;) {
        sceCtrlPeekBufferPositive(&pad,1);
        if((pad.Buttons&(PSP_CTRL_START|PSP_CTRL_SELECT))==(PSP_CTRL_START|PSP_CTRL_SELECT)) {
            if(++hold>=10)state.request_stop=1;
        }else hold=0;
        pspDebugScreenSetXY(0,10);
        pspDebugScreenPrintf("%-54s\n",stage_name(state.stage));
        pspDebugScreenPrintf("HID: %-9u SCSI: %-9u\n",
            (unsigned)state.hid_reports,(unsigned)state.bulk_commands);
        pspDebugScreenPrintf("Bytes: %-10u Errores RX: %-8u\n",
            (unsigned)state.bytes_read,(unsigned)state.rx_errors);
        pspDebugScreenPrintf("Control: %-9u Error: %08X\n",
            (unsigned)state.ctrl_requests,(unsigned)state.error);
        pspDebugScreenPrintf("SETUP: %08X Valor/interfaz: %08X\n",
            (unsigned)state.last_setup,(unsigned)state.last_value_index);
        if(state.safe_to_unload) {
            int stopped=-1;
            result=sceKernelStopModule(module,0,NULL,&stopped,NULL);
            if(result>=0 && stopped>=0) {
                result=sceKernelUnloadModule(module);
                if(result>=0) {
                    if(state.error) {pspDebugScreenPrintf("O: volver al XMB.\n");wait_cancel();}
                    break;
                }
            }
            pspDebugScreenPrintf("El modulo sigue cargado: %08X / %08X\n",
                (unsigned)result,(unsigned)stopped);
            state.safe_to_unload=0;
        }
        sceKernelDelayThread(100000);
    }
    sceKernelExitGame();return 0;
}
