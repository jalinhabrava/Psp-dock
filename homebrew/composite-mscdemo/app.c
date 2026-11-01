/* SPDX-License-Identifier: GPL-3.0-only
 * USER launcher for the synthetic USB mass-storage + HID diagnostic.
 * No direct USB or storage API imports. Kernel state controls unloading.
 */
#include <pspkernel.h>
#include <pspdebug.h>
#include <pspctrl.h>
#include <pspmodulemgr.h>
#include <pspdisplay.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#include "msc_common.h"

PSP_MODULE_INFO("PSPDockStorageTest",PSP_MODULE_USER,2,7);
PSP_MAIN_THREAD_ATTR(PSP_THREAD_ATTR_USER);
PSP_MAIN_THREAD_STACK_SIZE_KB(64);
static PdcSharedStatus state;

/* PSP 8888 pixels are ABGR. The SDK's MSX glyphs occupy 8x8 pixels.
 * Text uses explicit pixel positions with leading, never its terminal
 * cursor or automatic wrapping. Branding stays above the text body. */
#define UI_BACKGROUND 0xff181310u /* #101318 */
#define UI_WHITE      0xfff2f6f6u
#define UI_TEAL       0xffcce160u
#define UI_MUTED      0xffada298u
#define UI_ERROR      0xff8aa9ffu

static void ui_line(int y,uint32_t color,const char *format,...) {
    char text[64];va_list args;size_t length,i;
    if(y<0 || y>264)return;
    text[0]='\0';
    va_start(args,format);vsnprintf(text,sizeof(text),format,args);va_end(args);
    length=strlen(text);
    /* Same 7-pixel horizontal advance as PSPSDK. Every 8x8 cell also
     * paints its background, so shorter values erase their old suffix. */
    for(i=0;i<64;i++)
        pspDebugScreenPutChar(14+(int)i*7,y,color,(uint8_t)(i<length?text[i]:' '));
}

static void ui_clear_body(void) {
    int y;
    for(y=64;y<=264;y+=8)ui_line(y,UI_WHITE,"");
}

static void ui_clear_footer(void) {
    int y;
    for(y=224;y<=264;y+=8)ui_line(y,UI_WHITE,"");
}

static const uint8_t *brand_glyph(char ch) {
    static const uint8_t p[7]={30,17,17,30,16,16,16};
    static const uint8_t s[7]={15,16,16,14,1,1,30};
    static const uint8_t d[7]={30,17,17,17,17,17,30};
    static const uint8_t o[7]={14,17,17,17,17,17,14};
    static const uint8_t c[7]={14,17,16,16,16,17,14};
    static const uint8_t k[7]={17,18,20,24,20,18,17};
    switch(ch){case 'P':return p;case 'S':return s;case 'D':return d;
        case 'O':return o;case 'C':return c;case 'K':return k;default:return NULL;}
}

static void draw_brand(void) {
    static const char title[]="PSP DOCK";
    void *address=NULL;int stride=0,format=-1,x=14,i,row,col,dx,dy;
    uintptr_t physical;volatile uint32_t *pixels;
    if(sceDisplayGetFrameBuf(&address,&stride,&format,PSP_DISPLAY_SETBUF_IMMEDIATE)<0 ||
       !address || format!=PSP_DISPLAY_PIXEL_FORMAT_8888 || stride<480 || stride>512){
        ui_line(16,UI_TEAL,"PSP DOCK");return;
    }
    /* PSP debug uses VRAM. Validate the full visible buffer, then select
     * its uncached alias; do not manufacture a framebuffer for other modes. */
    physical=(uintptr_t)address&0x1fffffffu;
    if(physical<0x04000000u || physical>0x04200000u-(uint32_t)stride*272u*4u){
        ui_line(16,UI_TEAL,"PSP DOCK");return;
    }
    pixels=(volatile uint32_t *)(physical|0x40000000u);
    for(i=0;title[i];i++){
        const uint8_t *glyph=brand_glyph(title[i]);
        uint32_t color=i<3?UI_WHITE:UI_TEAL;
        if(!glyph){x+=12;continue;}
        /* Original 5x7 lettering, enlarged to 15x21 pixels per glyph. */
        for(row=0;row<7;row++)for(col=0;col<5;col++)if(glyph[row]&(1u<<(4-col)))
            for(dy=0;dy<3;dy++)for(dx=0;dx<3;dx++)
                pixels[(12+row*3+dy)*stride+x+col*3+dx]=color;
        x+=18;
    }
    for(x=14;x<466;x++)pixels[56*stride+x]=0xff3d3228u;
}

static void ui_init(void) {
    pspDebugScreenInit();
    pspDebugScreenEnableBackColor(1);
    pspDebugScreenSetBackColor(UI_BACKGROUND);
    pspDebugScreenSetTextColor(UI_WHITE);
    pspDebugScreenClear();
    /* Let a frame scheduled by debug initialization become current. */
    sceDisplayWaitVblankStart();draw_brand();
    ui_line(40,UI_MUTED,"USB STORAGE + HID / 0207");
}

static const char *stage_name(unsigned stage) {
    switch(stage) {
        case PDC_STAGE_INIT:return "Cargando modulo...";
        case PDC_STAGE_GUARD:return "Comprobando USB...";
        case PDC_STAGE_READY:return "LISTO: conecta el cable USB";
        case PDC_STAGE_CONNECTED:return "USB conectado; comprobar en Windows";
        case PDC_STAGE_RECONNECT:return "Windows reconfigura USB; espera...";
        case PDC_STAGE_FILE:return "Lectura del disco de prueba";
        case PDC_STAGE_STOPPING:return "Cerrando USB; espera...";
        case PDC_STAGE_STOPPED:return "USB detenido";
        default:return "Error: anota el codigo de abajo";
    }
}

static const char *stop_reason(unsigned reason) {
    switch(reason) {
        case PDC_STOP_NONE:return "ninguno";
        case PDC_STOP_USER:return "START+SELECT / solicitud del usuario";
        case PDC_STOP_CABLE:return "cable ausente durante 500 ms";
        case PDC_STOP_ERROR:return "error del controlador";
        case PDC_STOP_MODULE:return "descarga del modulo";
        default:return "desconocido";
    }
}

static void draw_diagnostics(void) {
    ui_line(64,state.error?UI_ERROR:UI_TEAL,"%s",stage_name(state.stage));
    ui_line(116,UI_WHITE,"HID %-10u   SCSI %-10u",
        (unsigned)state.hid_reports,(unsigned)state.bulk_commands);
    ui_line(126,UI_WHITE,"Leidos %-10u B   Errores RX %-10u",
        (unsigned)state.bytes_read,(unsigned)state.rx_errors);
    ui_line(136,UI_MUTED,"USB %08X   Control %-10u",
        (unsigned)state.usb_state,(unsigned)state.ctrl_requests);
    ui_line(146,UI_MUTED,"Config %-10u Desconfig %-10u Gen %-10u",
        (unsigned)state.attach_count,(unsigned)state.detach_count,(unsigned)state.link_generation);
    ui_line(156,state.error?UI_ERROR:UI_MUTED,"Error %08X   Parada %u",
        (unsigned)state.error,(unsigned)state.stop_reason);
    ui_line(166,UI_MUTED,"Motivo: %s",stop_reason(state.stop_reason));
    ui_line(176,UI_MUTED,"SETUP %08X   Valor/interfaz %08X",
        (unsigned)state.last_setup,(unsigned)state.last_value_index);
    ui_line(186,UI_MUTED,"Detach %08X %08X %08X",
        (unsigned)state.detach_arg1,(unsigned)state.detach_arg2,(unsigned)state.detach_arg3);
    ui_line(196,UI_WHITE,"BOT %u CDB %08X Datos %u/%u",
        (unsigned)state.bot_phase,(unsigned)state.last_scsi_opcode,
        (unsigned)state.scsi_completed,(unsigned)state.scsi_host_length);
    ui_line(206,UI_MUTED,"RX %08X   TX %08X",
        (unsigned)state.last_rx_code,(unsigned)state.last_tx_code);
}

static void wait_cancel(void) {
    SceCtrlData pad;
    int released=0;
    for(;;) {
        sceCtrlPeekBufferPositive(&pad,1);
        if(!(pad.Buttons&PSP_CTRL_CIRCLE))released=1;
        if(released && (pad.Buttons&PSP_CTRL_CIRCLE))return;
        sceKernelDelayThread(100000);
    }
}

int main(void) {
    SceCtrlData pad;
    PdcLaunchArgs launch;
    SceUID module;
    int result,exit_status=-1,release_seen=0,hold=0,unload_retry=0;
    ui_init();
    ui_line(76,UI_WHITE,"Almacenamiento USB y mando en el mismo cable.");
    ui_line(108,UI_MUTED,"Disco ficticio de 512 KiB, solo lectura.");
    ui_line(128,UI_MUTED,"No ofrece los archivos reales de la Memory Stick.");
    ui_line(168,UI_WHITE,"1. Sal de Conexion USB y desconecta el cable.");
    ui_line(188,UI_WHITE,"2. Pulsa X para preparar el controlador.");
    ui_line(236,UI_TEAL,"X  INICIAR                O  VOLVER AL XMB");
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
    ui_clear_body();ui_line(64,UI_TEAL,"Preparando PSP Dock...");
    module=sceKernelLoadModule("ms0:/PSP/GAME/PSPDockStorageTest/dockmsc.prx",0,NULL);
    if(module<0) {
        ui_line(96,UI_ERROR,"Error al cargar el controlador: %08X",(unsigned)module);
        ui_line(236,UI_TEAL,"O  VOLVER AL XMB");
        wait_cancel();sceKernelExitGame();return 0;
    }
    result=sceKernelStartModule(module,sizeof(launch),&launch,&exit_status,NULL);
    if(result<0 || exit_status<0) {
        ui_line(96,UI_ERROR,"Error al iniciar: %08X / %08X",
            (unsigned)result,(unsigned)exit_status);
        /* A failed module_start must leave no running threads or callbacks. */
        ui_line(236,UI_TEAL,"O  VOLVER AL XMB");
        wait_cancel();sceKernelExitGame();return 0;
    }
    ui_line(84,UI_WHITE,"Mueve la cruceta durante la lectura del PC.");
    ui_line(96,UI_MUTED,"START + SELECT (1 s) cierra la prueba.");
    ui_line(236,UI_TEAL,"CONTROLADOR ACTIVO");
    for(;;) {
        sceCtrlPeekBufferPositive(&pad,1);
        if((pad.Buttons&(PSP_CTRL_START|PSP_CTRL_SELECT))==(PSP_CTRL_START|PSP_CTRL_SELECT)) {
            if(++hold>=10)state.request_stop=1;
        }else hold=0;
        draw_diagnostics();
        if(unload_retry>0)--unload_retry;
        if(state.safe_to_unload && !unload_retry) {
            int stopped=-1;
            result=sceKernelStopModule(module,0,NULL,&stopped,NULL);
            if(result>=0 && stopped>=0) {
                result=sceKernelUnloadModule(module);
                if(result>=0) {
                    draw_diagnostics();
                    ui_clear_footer();
                    ui_line(230,UI_TEAL,"PRUEBA DETENIDA");
                    ui_line(242,UI_WHITE,"Anota o fotografia esta pantalla.");
                    ui_line(256,UI_TEAL,"Pulsa O de nuevo para volver al XMB.");
                    wait_cancel();
                    break;
                }
            }
            ui_clear_footer();
            ui_line(230,UI_ERROR,"El modulo sigue cargado: %08X / %08X",
                (unsigned)result,(unsigned)stopped);
            ui_line(248,UI_MUTED,"Esperando el cierre seguro del controlador...");
            unload_retry=10;
        }
        sceKernelDelayThread(100000);
    }
    sceKernelExitGame();return 0;
}
