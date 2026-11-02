/* SPDX-License-Identifier: GPL-3.0-only
 * USER launcher for native Sony storage + PSP Dock HID phases.
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
#include "native_common.h"

PSP_MODULE_INFO("PSPDockNative",PSP_MODULE_USER,3,0);
PSP_MAIN_THREAD_ATTR(PSP_THREAD_ATTR_USER);
PSP_MAIN_THREAD_STACK_SIZE_KB(64);
static PdnSharedStatus state;

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
    ui_line(40,UI_MUTED,"MEMORY STICK + MANDO / 0300");
}

static const char *stage_name(unsigned stage) {
    switch(stage){
        case PDN_STAGE_INIT:return "Preparando PSP Dock...";
        case PDN_STAGE_GUARD:return "Comprobando conexion USB...";
        case PDN_STAGE_MODULE_LOAD:return "Cargando almacenamiento Sony...";
        case PDN_STAGE_MODULE_START:return "Iniciando almacenamiento Sony...";
        case PDN_STAGE_BUS_START:return "Iniciando bus USB...";
        case PDN_STAGE_HID_REGISTER:return "Registrando mando PSP Dock...";
        case PDN_STAGE_HID_START:return "Iniciando mando...";
        case PDN_STAGE_STORAGE_START:return "Preparando Memory Stick...";
        case PDN_STAGE_ACTIVATE:return "Activando conexion...";
        case PDN_STAGE_READY:return "LISTO: conecta el cable USB";
        case PDN_STAGE_RECONNECT:return "Windows reconfigura USB; espera...";
        case PDN_STAGE_WAIT_UNPLUG:return "Finaliza en Windows antes de desconectar";
        case PDN_STAGE_STOPPING:return "Cerrando conexion...";
        case PDN_STAGE_STOPPED:return "PSP Dock detenido";
        case PDN_STAGE_ERROR:return "No se pudo iniciar o continuar el dock";
        default:return "PSP conectada";
    }
}
static const char *phase_name(unsigned phase) {
    switch(phase){
        case PDN_PHASE_STORAGE:return "SINCRONIZANDO PSP";
        case PDN_PHASE_CONTROLLER:return "MODO MANDO";
        case PDN_PHASE_COPYING:return "COPIANDO; espera a que termine";
        case PDN_PHASE_EXIT_SYNC:return "GUARDANDO PARTIDAS Y ESTADOS";
        case PDN_PHASE_SAFE:return "SINCRONIZACION FINALIZADA";
        case PDN_PHASE_ERROR:return "ERROR; finaliza en Windows";
        default:return "PREPARANDO PSP DOCK";
    }
}
static void draw_status(void) {
    ui_line(68,state.error?UI_ERROR:UI_TEAL,"%s",stage_name(state.stage));
    ui_line(92,UI_WHITE,"%s",phase_name(state.phase));
    if(state.phase==PDN_PHASE_STORAGE){
        ui_line(112,UI_MUTED,"Elige en Windows: partidas, juegos, todo o nada.");
        ui_line(126,UI_MUTED,"Puedes usar la cruceta y X para elegir.");
    }else if(state.phase==PDN_PHASE_CONTROLLER){
        ui_line(112,UI_MUTED,"Navega y juega con los botones de tu PSP.");
        ui_line(126,UI_MUTED,"Salir en Windows guarda partidas y estados.");
    }else if(state.phase==PDN_PHASE_SAFE){
        ui_line(112,UI_MUTED,"Espera el aviso de Windows para retirar USB.");
        ui_line(126,UI_MUTED,"Desconecta el cable para volver al XMB.");
    }else{
        ui_line(112,UI_MUTED,"Consulta el progreso en PSP Dock de Windows.");
        ui_line(126,UI_MUTED,"Manten el cable conectado durante la copia.");
    }
    ui_line(156,UI_MUTED,"USB %08X  Mando %u  IF %u",
        (unsigned)state.usb_state,(unsigned)state.hid_reports,(unsigned)state.hid_interface);
    ui_line(168,UI_MUTED,"Sony %u  Fase %u  ACK %u/%d",
        (unsigned)state.sony_storage_started,(unsigned)state.phase,
        (unsigned)state.sequence,(int)state.phase_result);
    ui_line(180,UI_MUTED,"Arranque %u Modulo %u  Cargados %02X Reusados %02X",
        (unsigned)state.stage,(unsigned)state.dependency,
        (unsigned)state.modules_loaded,(unsigned)state.modules_borrowed);
    ui_line(192,state.error?UI_ERROR:UI_MUTED,"Error %08X en %u  Cierre %08X",
        (unsigned)state.error,(unsigned)state.error_stage,(unsigned)state.cleanup_error);
    ui_line(204,UI_MUTED,"Control %u  SETUP %08X  Interfaz/valor %08X",
        (unsigned)state.ctrl_requests,(unsigned)state.last_setup,(unsigned)state.last_value_index);
    ui_line(230,UI_TEAL,state.exit_requested?"SALIDA SOLICITADA: termina en Windows":
        "START + SELECT (1 s): solicitar salida");
    ui_line(246,UI_MUTED,"El mando se bloquea mientras se copian archivos.");
}
static void wait_cancel(void) {
    SceCtrlData pad;int released=0;
    for(;;){
        sceCtrlPeekBufferPositive(&pad,1);
        if(!(pad.Buttons&PSP_CTRL_CIRCLE))released=1;
        if(released && (pad.Buttons&PSP_CTRL_CIRCLE))return;
        sceKernelDelayThread(100000);
    }
}
int main(void) {
    SceCtrlData pad;PdnLaunchArgs launch;SceUID module;
    int result,exit_status=-1,release_seen=0,hold=0,unload_retry=0;
    ui_init();
    ui_line(76,UI_WHITE,"Tu PSP, tus juegos y tus partidas en el dock.");
    ui_line(108,UI_MUTED,"Abre PSP Dock en Windows para empezar.");
    ui_line(128,UI_MUTED,"Esta version utiliza tu Memory Stick real.");
    ui_line(168,UI_WHITE,"1. Sal de Conexion USB y desconecta el cable.");
    ui_line(188,UI_WHITE,"2. Pulsa X; conecta cuando aparezca LISTO.");
    ui_line(236,UI_TEAL,"X  INICIAR                O  VOLVER AL XMB");
    for(;;){
        sceCtrlPeekBufferPositive(&pad,1);
        if(!(pad.Buttons&PSP_CTRL_CROSS))release_seen=1;
        if(pad.Buttons&PSP_CTRL_CIRCLE){sceKernelExitGame();return 0;}
        if(release_seen && (pad.Buttons&PSP_CTRL_CROSS))break;
        sceKernelDelayThread(100000);
    }
    memset(&state,0,sizeof(state));state.magic=PDN_LAUNCH_MAGIC;state.size=sizeof(state);
    launch.magic=PDN_LAUNCH_MAGIC;launch.size=sizeof(launch);launch.shared=&state;
    ui_clear_body();ui_line(68,UI_TEAL,"Preparando PSP Dock...");
    /* Last Memory Stick access by this launcher: every UI asset is already
     * resident. The kernel loads only native read-only flash PRXs before
     * activating Sony USBSTOR. No file opens occur during the USB session.
     */
    module=sceKernelLoadModule("ms0:/PSP/GAME/PSPDockNative/docknative.prx",0,NULL);
    if(module<0){
        ui_line(100,UI_ERROR,"No se pudo cargar docknative.prx: %08X",(unsigned)module);
        ui_line(236,UI_TEAL,"O  VOLVER AL XMB");wait_cancel();sceKernelExitGame();return 0;
    }
    result=sceKernelStartModule(module,sizeof(launch),&launch,&exit_status,NULL);
    if(result<0 || exit_status<0){
        ui_line(100,UI_ERROR,"No se pudo iniciar: %08X / %08X",
            (unsigned)result,(unsigned)exit_status);
        /* module_start starts no worker on failure; no USB is reachable. */
        sceKernelUnloadModule(module);
        ui_line(236,UI_TEAL,"O  VOLVER AL XMB");wait_cancel();sceKernelExitGame();return 0;
    }
    for(;;){
        sceCtrlPeekBufferPositive(&pad,1);
        if((pad.Buttons&(PSP_CTRL_START|PSP_CTRL_SELECT))==(PSP_CTRL_START|PSP_CTRL_SELECT)){
            if(++hold>=10)state.request_stop=1;
        }else hold=0;
        draw_status();if(unload_retry>0)--unload_retry;
        if(state.safe_to_unload && !unload_retry){
            int stopped=-1;
            result=sceKernelStopModule(module,0,NULL,&stopped,NULL);
            if(result>=0 && stopped>=0){
                result=sceKernelUnloadModule(module);
                if(result>=0){
                    draw_status();ui_clear_footer();
                    ui_line(230,UI_TEAL,"PSP DOCK DETENIDO");
                    ui_line(244,UI_WHITE,state.error?"Fotografia los codigos para revisar el arranque.":
                        "Ya puedes volver al menu de tu PSP.");
                    ui_line(258,UI_TEAL,"Pulsa O de nuevo para volver al XMB.");
                    wait_cancel();break;
                }
            }
            ui_clear_footer();ui_line(230,UI_ERROR,"Esperando cierre: %08X / %08X",
                (unsigned)result,(unsigned)stopped);unload_retry=10;
        }
        sceKernelDelayThread(100000);
    }
    sceKernelExitGame();return 0;
}
