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
#include "native_ui.h"

PSP_MODULE_INFO("PSPDockNative",PSP_MODULE_USER,3,7);
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
    ui_line(40,UI_MUTED,"");
}

static void draw_progress_bar(int measured,unsigned percent) {
    void *address=NULL;int stride=0,format=-1,x,y,filled;
    uintptr_t physical;volatile uint32_t *pixels;
    if(sceDisplayGetFrameBuf(&address,&stride,&format,PSP_DISPLAY_SETBUF_IMMEDIATE)<0 ||
       !address || format!=PSP_DISPLAY_PIXEL_FORMAT_8888 || stride<480 || stride>512)return;
    physical=(uintptr_t)address&0x1fffffffu;
    if(physical<0x04000000u || physical>0x04200000u-(uint32_t)stride*272u*4u)return;
    pixels=(volatile uint32_t *)(physical|0x40000000u);
    filled=measured?(int)(452u*(percent>100u?100u:percent)/100u):0;
    for(y=204;y<212;y++)for(x=14;x<466;x++)
        pixels[y*stride+x]=!measured?UI_BACKGROUND:(x-14<filled?UI_TEAL:0xff3d3228u);
}
static void diagnostic_result(char out[12],int32_t value,int hexadecimal) {
    if(value==INT32_MIN)snprintf(out,12,"--");
    else if(hexadecimal)snprintf(out,12,"%08X",(unsigned)value);
    else snprintf(out,12,"%d",(int)value);
}
static void draw_ep0_diagnostic(const PdnUiView *view) {
    static PdnEp0Diagnostic retained;
    PdnEp0Diagnostic sample;char line[PDN_UI_LINE_SIZE],submit[12],retcode[12],parser[12];unsigned row;
    if(pdn_ui_ep0_snapshot(&sample,&state.ep0))retained=sample;
    diagnostic_result(submit,retained.submit_result,1);
    diagnostic_result(retcode,retained.retcode,1);
    diagnostic_result(parser,retained.parser_result,0);
    ui_line(40,UI_MUTED,"DIAGNOSTICO RAM / VERSION %04X",(unsigned)PDN_REVISION);
    ui_line(68,view->warning?UI_ERROR:UI_TEAL,"%s",view->heading);
    ui_line(80,UI_MUTED,"USB %08X F %u ACK %u/%d H %u",
        (unsigned)state.usb_state,(unsigned)state.phase,(unsigned)state.sequence,
        (int)state.phase_result,(unsigned)state.host_confirmed);
    ui_line(92,view->warning?UI_ERROR:UI_MUTED,"Error %08X paso %u cierre %08X",
        (unsigned)state.error,(unsigned)state.error_stage,(unsigned)state.cleanup_error);
    ui_line(104,UI_MUTED,"SET %u ENV %u FIN %u PAR %u",
        (unsigned)retained.set_seen,(unsigned)retained.recv_submissions,
        (unsigned)retained.completions,(unsigned)retained.parser_calls);
    ui_line(116,UI_MUTED,"Setup %08X Valor/IF %08X Envio %s",
        (unsigned)retained.setup,(unsigned)retained.value_index,submit);
    ui_line(128,UI_MUTED,"Len %u RX %d RC %s Gen %u/%u",
        (unsigned)(retained.setup>>16),(int)retained.recvsize,retcode,
        (unsigned)retained.request_generation,(unsigned)retained.live_generation);
    ui_line(140,UI_MUTED,"Drop %u Parser %s Flags %02X RAM %u",
        (unsigned)retained.drop_reason,parser,
        (unsigned)retained.gate_flags,(unsigned)retained.generation);
    ui_line(152,UI_MUTED,"CPU %u Sitio %u Aplica %u / Buffer bruto:",
        (unsigned)state.worker_loops,(unsigned)state.worker_site,(unsigned)state.apply_count);
    for(row=0;row<4u;row++){
        pdn_ui_ep0_hex(line,&retained,row*16u);
        ui_line(164+(int)row*12,UI_MUTED,"%s",line);
    }
    ui_line(212,UI_MUTED,"Ctrl %u HID %u Rechazos %u/%d",
        (unsigned)state.ctrl_requests,(unsigned)state.hid_reports,
        (unsigned)state.rejected_commands,(int)state.last_rejection);
    ui_line(230,UI_TEAL,"%s",view->action[0]?view->action:view->footer);
    ui_line(244,UI_MUTED,"Nota musical: ocultar diagnostico RAM");
    ui_line(258,UI_TEAL,"%s",view->exit_hint);
}
static void draw_status(int module_unloaded) {
    static int last_diagnostic=-1;
    PdnUiView view=pdn_ui_present(&state,module_unloaded);
    int diagnostic=state.diagnostic_visible?2:(view.warning?1:0);
    if(diagnostic!=last_diagnostic){ui_clear_body();last_diagnostic=diagnostic;}
    if(diagnostic==2){draw_ep0_diagnostic(&view);return;}
    if(diagnostic)ui_line(40,UI_MUTED,"DIAGNOSTICO / VERSION %04X",(unsigned)PDN_REVISION);
    else ui_line(40,UI_MUTED,"");
    ui_line(68,view.warning?UI_ERROR:UI_TEAL,"%s",view.heading);
    ui_line(92,UI_WHITE,"%s",view.context);
    ui_line(112,UI_MUTED,"%s",view.detail);
    ui_line(126,UI_MUTED,"%s",view.action);
    if(diagnostic){
        ui_line(156,UI_MUTED,"USB %08X  HID %u  IF %s",
            (unsigned)state.usb_state,(unsigned)state.hid_reports,view.interface_label);
        ui_line(168,UI_MUTED,"Sony %u Fase %u ACK %u/%d Host %u",
            (unsigned)state.sony_storage_started,(unsigned)state.phase,
            (unsigned)state.sequence,(int)state.phase_result,(unsigned)state.host_confirmed);
        ui_line(180,UI_MUTED,"Actividad %u  Gen %u  Rechazos %u/%d",
            (unsigned)state.activity,(unsigned)state.link_generation,
            (unsigned)state.rejected_commands,(int)state.last_rejection);
        ui_line(192,UI_MUTED,"Paso %u Mod %u  Carg %02X Reus %02X",
            (unsigned)state.stage,(unsigned)state.dependency,
            (unsigned)state.modules_loaded,(unsigned)state.modules_borrowed);
        ui_line(204,view.warning?UI_ERROR:UI_MUTED,"Error %08X en %u  Cierre %08X",
            (unsigned)state.error,(unsigned)state.error_stage,(unsigned)state.cleanup_error);
        ui_line(216,UI_MUTED,"Ctrl %u  SETUP %08X  IF/valor %08X",
            (unsigned)state.ctrl_requests,(unsigned)state.last_setup,(unsigned)state.last_value_index);
    }else{
        ui_line(168,UI_TEAL,"%s",view.progress_visible?view.progress:"");
        ui_line(184,UI_MUTED,"%s",view.progress_measured?view.progress_detail:"");
        draw_progress_bar(view.progress_measured,view.progress_percent);
    }
    ui_line(230,UI_TEAL,"%s",view.footer);
    ui_line(244,UI_MUTED,"%s",view.footer_note);
    ui_line(258,UI_TEAL,"%s",view.exit_hint);
}
static void draw_launcher_failure(const char *operation,int result,int status) {
    ui_clear_body();
    ui_line(40,UI_MUTED,"DIAGNOSTICO / VERSION %04X",(unsigned)PDN_REVISION);
    ui_line(68,UI_ERROR,"Arranque detenido");
    ui_line(92,UI_WHITE,"No se pudo %s el controlador.",operation);
    ui_line(112,UI_MUTED,"Archivo: docknative.prx");
    ui_line(126,UI_MUTED,"No se ha iniciado una sesion de Dock.");
    ui_line(156,UI_ERROR,"Resultado %08X  Estado %08X",(unsigned)result,(unsigned)status);
    ui_line(230,UI_WHITE,"Fotografia esta pantalla para revisar el fallo.");
    ui_line(258,UI_TEAL,"O  VOLVER AL XMB");
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
    ui_line(128,UI_MUTED,"Elige que copiar al comenzar la sesion.");
    ui_line(168,UI_WHITE,"1. Sal de Conexion USB del XMB.");
    ui_line(188,UI_WHITE,"2. Manten el cable conectado y pulsa X.");
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
        draw_launcher_failure("cargar",module,0);
        wait_cancel();sceKernelExitGame();return 0;
    }
    result=sceKernelStartModule(module,sizeof(launch),&launch,&exit_status,NULL);
    if(result<0 || exit_status<0){
        draw_launcher_failure("iniciar",result,exit_status);
        /* module_start starts no worker on failure; no USB is reachable. */
        sceKernelUnloadModule(module);
        wait_cancel();sceKernelExitGame();return 0;
    }
    for(;;){
        sceCtrlPeekBufferPositive(&pad,1);
        if((pad.Buttons&(PSP_CTRL_START|PSP_CTRL_SELECT))==(PSP_CTRL_START|PSP_CTRL_SELECT)){
            if(++hold>=10)state.request_stop=1;
        }else hold=0;
        draw_status(0);if(unload_retry>0)--unload_retry;
        if(state.safe_to_unload && !unload_retry){
            int stopped=-1;
            result=sceKernelStopModule(module,0,NULL,&stopped,NULL);
            if(result>=0 && stopped>=0){
                result=sceKernelUnloadModule(module);
                if(result>=0){
                    draw_status(1);
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
