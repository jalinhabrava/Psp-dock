/* SPDX-License-Identifier: GPL-3.0-only
 * Read-only presentation of kernel status. No USB or filesystem operations.
 */
#ifndef PSPDOCK_NATIVE_UI_H
#define PSPDOCK_NATIVE_UI_H
#include <stdio.h>
#include <string.h>
#include "native_common.h"

#define PDN_UI_LINE_SIZE 64u
/* Observed sceUsbGetState bits, not permission to unload or disconnect. */
#define PDN_UI_USB_ACTIVATED 0x200u
#define PDN_UI_USB_CABLE_CONNECTED 0x20u

typedef struct {
    char heading[PDN_UI_LINE_SIZE],context[PDN_UI_LINE_SIZE];
    char detail[PDN_UI_LINE_SIZE],action[PDN_UI_LINE_SIZE];
    char footer[PDN_UI_LINE_SIZE],footer_note[PDN_UI_LINE_SIZE];
    char exit_hint[PDN_UI_LINE_SIZE],interface_label[16];
    char progress[PDN_UI_LINE_SIZE],progress_detail[PDN_UI_LINE_SIZE];
    int warning,progress_visible,progress_measured;
    unsigned progress_percent;
} PdnUiView;

/* A best-effort RAM snapshot: never spin while the kernel publishes one.
 * The caller keeps its previous complete sample if this read overlaps it. */
static inline int pdn_ui_ep0_snapshot(PdnEp0Diagnostic *out,
                                    const volatile PdnEp0Diagnostic *source) {
    uint32_t before=source->generation,after;
    if(before&1u)return 0;
    __asm__ volatile("" ::: "memory");
    *out=*source;
    __asm__ volatile("" ::: "memory");
    after=source->generation;
    return before==after && !(after&1u);
}

static inline void pdn_ui_ep0_hex(char out[PDN_UI_LINE_SIZE],
                                const PdnEp0Diagnostic *sample,unsigned offset) {
    static const char digits[]="0123456789ABCDEF";
    unsigned i,position;
    if(offset>=PDN_FEATURE_SIZE || offset%16u){out[0]='\0';return;}
    position=(unsigned)snprintf(out,PDN_UI_LINE_SIZE,"%02X: ",offset);
    for(i=0;i<16u;i++){
        uint8_t value=sample->raw[offset+i];
        if(i && !(i%4u))out[position++]=' ';
        out[position++]=digits[value>>4];out[position++]=digits[value&15u];
    }
    out[position]='\0';
}

static inline const char *pdn_ui_dependency_name(unsigned dependency) {
    /* Exact mandatory dependency order; Boot is not a dependency. */
    switch(dependency){
        case 1:return "semawm.prx";
        case 2:return "usbstor.prx";
        case 3:return "usbstormgr.prx";
        case 4:return "usbstorms.prx";
        default:return NULL;
    }
}

static inline const char *pdn_ui_stage_name(unsigned stage) {
    switch(stage){
        case PDN_STAGE_INIT:return "preparar controlador";
        case PDN_STAGE_GUARD:return "comprobar conexion USB";
        case PDN_STAGE_MODULE_LOAD:return "cargar modulo Sony";
        case PDN_STAGE_MODULE_START:return "iniciar modulo Sony";
        case PDN_STAGE_BUS_START:return "iniciar bus USB";
        case PDN_STAGE_HID_REGISTER:return "registrar mando HID";
        case PDN_STAGE_HID_START:return "iniciar mando HID";
        case PDN_STAGE_STORAGE_START:return "iniciar Memory Stick";
        case PDN_STAGE_ACTIVATE:return "activar conexion USB";
        case PDN_STAGE_READY:return "esperar cable USB";
        case PDN_STAGE_CONNECTED:return "conexion con Windows";
        case PDN_STAGE_RECONNECT:return "reconfigurar conexion USB";
        case PDN_STAGE_WAIT_UNPLUG:return "esperar cierre de Windows";
        case PDN_STAGE_STOPPING:return "cerrar conexion USB";
        case PDN_STAGE_STOPPED:return "conexion detenida";
        default:return "etapa no identificada";
    }
}

static inline void pdn_ui_module_line(char out[PDN_UI_LINE_SIZE],unsigned dependency) {
    const char *name=pdn_ui_dependency_name(dependency);
    if(name)snprintf(out,PDN_UI_LINE_SIZE,"Modulo %u: %s",dependency,name);
    else snprintf(out,PDN_UI_LINE_SIZE,"Dependencia %u: no reconocida en esta version",dependency);
}

/* Exact floor(done*100/total) without overflowing 64-bit byte counts. */
static inline unsigned pdn_ui_percent(uint64_t done,uint64_t total) {
    uint64_t whole,remainder,threshold;unsigned percent,result=0;
    if(!total || done>total)return 0;
    whole=total/100u;remainder=total%100u;
    for(percent=1;percent<=100;percent++){
        threshold=whole*percent+(remainder*percent+99u)/100u;
        if(done<threshold)break;
        result=percent;
    }
    return result;
}
static inline void pdn_ui_progress(PdnUiView *v,const PdnSharedStatus *s) {
    uint32_t before=s->presentation_generation,unit,after;
    uint64_t done,total;
    v->progress_visible=1;
    snprintf(v->progress,sizeof(v->progress),"Consulta la actividad en Windows.");
    if(before&1u)return;
    __asm__ volatile("" ::: "memory");
    unit=s->progress_unit;done=s->completed;total=s->total;
    __asm__ volatile("" ::: "memory");
    after=s->presentation_generation;
    /* PSP is 32 bit: never display a torn 64-bit progress sample. */
    if(before!=after || (after&1u) || unit==PDN_UNIT_UNKNOWN ||
       !pdn_progress_valid((uint8_t)unit,done,total))return;
    v->progress_measured=1;v->progress_percent=pdn_ui_percent(done,total);
    snprintf(v->progress,sizeof(v->progress),"%u%% completado",v->progress_percent);
    snprintf(v->progress_detail,sizeof(v->progress_detail),"%llu / %llu %s",
        (unsigned long long)done,(unsigned long long)total,
        unit==PDN_UNIT_BYTES?"bytes":"archivos");
}

static inline PdnUiView pdn_ui_present(const PdnSharedStatus *s,int module_unloaded) {
    PdnUiView v;
    int fault=s->error || s->stage==PDN_STAGE_ERROR || s->phase==PDN_PHASE_ERROR;
    int may_have_usb=(s->usb_state&(PDN_UI_USB_ACTIVATED|PDN_UI_USB_CABLE_CONNECTED)) ||
        s->sony_storage_started ||
        (s->error_stage>=PDN_STAGE_ACTIVATE && s->error_stage<=PDN_STAGE_STOPPING);
    memset(&v,0,sizeof(v));
#define PDN_UI_TEXT(field,...) snprintf(v.field,sizeof(v.field),__VA_ARGS__)
    if(s->hid_interface==UINT32_MAX)PDN_UI_TEXT(interface_label,"sin_asignar");
    else PDN_UI_TEXT(interface_label,"%u",(unsigned)s->hid_interface);

    /* Initial phase STORAGE is a transport condition, never a host claim. */
    if(s->stop_reason==PDN_STOP_HOST && !s->safe_to_unload &&
       s->stage!=PDN_STAGE_ERROR && !s->cleanup_error){
        PDN_UI_TEXT(heading,"Cerrando PSP Dock");
        PDN_UI_TEXT(context,"Windows ha solicitado el cierre de USB.");
        PDN_UI_TEXT(detail,"Espera a que termine la conexion.");
        PDN_UI_TEXT(action,"Volver al XMB aparecera cuando sea posible.");
        PDN_UI_TEXT(footer,"Puedes mantener el cable conectado.");
    }else if(fault){
        v.warning=1;
        PDN_UI_TEXT(heading,"%s",s->error_stage<=PDN_STAGE_ACTIVATE?
            "Arranque detenido":"Error en la conexion");
        PDN_UI_TEXT(context,"Etapa %u: %s",(unsigned)s->error_stage,pdn_ui_stage_name(s->error_stage));
        if(s->error_stage==PDN_STAGE_MODULE_LOAD || s->error_stage==PDN_STAGE_MODULE_START)
            pdn_ui_module_line(v.detail,(unsigned)s->dependency);
        else PDN_UI_TEXT(detail,"Consulta los codigos de diagnostico.");
        if(may_have_usb && !s->safe_to_unload){
            PDN_UI_TEXT(action,"No desconectes hasta el aviso de Windows.");
            PDN_UI_TEXT(footer_note,"Cierra PSP Dock en Windows para finalizar.");
        }else{
            PDN_UI_TEXT(action,"%s",s->error_stage<=PDN_STAGE_ACTIVATE?
                "No se ha iniciado una sesion de PSP Dock.":"Espera a que se confirme el cierre.");
            PDN_UI_TEXT(footer_note,"Preparando la salida al XMB.");
        }
        PDN_UI_TEXT(footer,"Fotografia esta pantalla para revisar el fallo.");
    }else if(s->cleanup_error){
        v.warning=1;
        PDN_UI_TEXT(heading,"Aviso al cerrar PSP Dock");
        PDN_UI_TEXT(context,"Un modulo Sony no se ha descargado.");
        PDN_UI_TEXT(detail,"El codigo de cierre queda en el diagnostico.");
        PDN_UI_TEXT(action,"Espera a que se confirme el cierre.");
        PDN_UI_TEXT(footer,"Fotografia el aviso antes de salir.");
    }else if(s->safe_to_unload || s->stage==PDN_STAGE_STOPPED){
        PDN_UI_TEXT(heading,"PSP Dock detenido");
        PDN_UI_TEXT(context,"Preparando el regreso al XMB.");
    }else if(s->stage==PDN_STAGE_STOPPING){
        PDN_UI_TEXT(heading,"Cerrando PSP Dock");
        PDN_UI_TEXT(context,"Espera a que termine el cierre de la conexion.");
        PDN_UI_TEXT(action,"Volver al XMB aparecera cuando sea posible.");
    }else if(s->stage==PDN_STAGE_RESOURCE_EXPORT){
        PDN_UI_TEXT(heading,"Preparando PSP Dock");
        PDN_UI_TEXT(context,"Preparando los sonidos de tu PSP.");
        PDN_UI_TEXT(action,"Espera un momento.");
    }else if(s->stage<PDN_STAGE_READY){
        PDN_UI_TEXT(heading,"Preparando PSP Dock");
        PDN_UI_TEXT(context,"Espera un momento.");
        PDN_UI_TEXT(action,"Conecta el cable cuando aparezca LISTO.");
    }else if(!s->sony_storage_started){
        PDN_UI_TEXT(heading,"Preparando PSP Dock");
        PDN_UI_TEXT(context,"La tarjeta de memoria aun no esta disponible.");
        PDN_UI_TEXT(action,"Espera en esta pantalla.");
    }else if(s->exit_requested && !s->host_confirmed){
        PDN_UI_TEXT(heading,"%s",s->host_session_seen?"Conexion interrumpida":"Conexion cancelada");
        PDN_UI_TEXT(context,"%s",s->host_session_seen?
            "Revisa el estado de tu sesion en Windows.":"Windows no ha iniciado la sincronizacion.");
        if(s->usb_state&PDN_UI_USB_CABLE_CONNECTED){
            PDN_UI_TEXT(detail,"Cierra PSP Dock en Windows.");
            PDN_UI_TEXT(action,"Windows cerrara la conexion USB.");
            PDN_UI_TEXT(footer,"Manten el cable conectado.");
        }else{
            PDN_UI_TEXT(detail,"Preparando el regreso al XMB.");
            PDN_UI_TEXT(action,"Espera al aviso para pulsar O.");
        }
    }else if(s->stage==PDN_STAGE_RECONNECT){
        PDN_UI_TEXT(heading,"Restableciendo la conexion");
        PDN_UI_TEXT(context,"Abre PSP Dock en Windows y revisa su aviso.");
        PDN_UI_TEXT(detail,"La navegacion esta en pausa.");
        PDN_UI_TEXT(action,"Manten el cable conectado.");
    }else if(s->stage==PDN_STAGE_READY && !s->host_confirmed){
        PDN_UI_TEXT(heading,"LISTO: conecta el cable USB");
        PDN_UI_TEXT(context,"Abre PSP Dock en Windows.");
        PDN_UI_TEXT(detail,"Esperando la conexion del PC.");
        PDN_UI_TEXT(footer,"START + SELECT (1 s): cancelar");
    }else if(!s->host_confirmed){
        PDN_UI_TEXT(heading,"Esperando a Windows");
        PDN_UI_TEXT(context,"Abre PSP Dock en Windows para continuar.");
        PDN_UI_TEXT(detail,"La sincronizacion empezara cuando se conecte.");
        PDN_UI_TEXT(action,"Manten el cable conectado.");
        PDN_UI_TEXT(footer,"START + SELECT (1 s): cancelar");
    }else if(s->phase==PDN_PHASE_SAFE){
        PDN_UI_TEXT(heading,"Finalizando la conexion");
        PDN_UI_TEXT(context,"El trabajo de PSP Dock ha terminado.");
        PDN_UI_TEXT(detail,"Windows esta preparando la expulsion.");
        PDN_UI_TEXT(action,"Espera al aviso para volver al XMB.");
    }else if(s->phase==PDN_PHASE_COPYING || s->phase==PDN_PHASE_EXIT_SYNC){
        PDN_UI_TEXT(heading,"%s",s->phase==PDN_PHASE_COPYING?
            "Copiando el juego":"Sincronizando partidas");
        PDN_UI_TEXT(context,"%s",s->phase==PDN_PHASE_COPYING?
            "El juego estara disponible cuando termine.":"Devolviendo los guardados compatibles a tu PSP.");
        PDN_UI_TEXT(detail,"El mando esta en pausa durante esta operacion.");
        PDN_UI_TEXT(action,"Manten el cable conectado hasta que termine.");
        pdn_ui_progress(&v,s);
        if(s->exit_requested)PDN_UI_TEXT(footer,"Salida solicitada; espera a que termine.");
    }else if(s->exit_requested){
        PDN_UI_TEXT(heading,"Salida solicitada");
        PDN_UI_TEXT(context,"Sigue las instrucciones de PSP Dock en Windows.");
        PDN_UI_TEXT(detail,"Si estas jugando, termina la partida primero.");
        PDN_UI_TEXT(action,"No retires USB hasta el aviso de Windows.");
    }else if(s->phase==PDN_PHASE_STORAGE){
        if(s->activity==PDN_ACTIVITY_CHOOSING){
            PDN_UI_TEXT(heading,"Elige que quieres sincronizar");
            PDN_UI_TEXT(context,"Partidas, juegos, todo o continuar sin copiar.");
            PDN_UI_TEXT(detail,"Haz tu seleccion en PSP Dock de Windows.");
            PDN_UI_TEXT(action,"Puedes elegir con la cruceta y X.");
        }else{
            PDN_UI_TEXT(heading,"Sincronizando PSP");
            PDN_UI_TEXT(context,"%s",s->activity==PDN_ACTIVITY_INVENTORY?
                "Comprobando tus juegos y partidas.":"Copiando lo que has elegido.");
            PDN_UI_TEXT(detail,"El mando estara disponible al terminar.");
            PDN_UI_TEXT(action,"Manten el cable conectado.");
            pdn_ui_progress(&v,s);
        }
        PDN_UI_TEXT(footer,"START + SELECT (1 s): solicitar salida");
    }else if(s->phase==PDN_PHASE_CONTROLLER){
        PDN_UI_TEXT(heading,"Todo listo");
        PDN_UI_TEXT(context,"Navega y juega con los botones de tu PSP.");
        PDN_UI_TEXT(detail,"Tus juegos y partidas estan en PSP Dock.");
        PDN_UI_TEXT(action,"Al salir se devolveran los guardados compatibles.");
        PDN_UI_TEXT(footer,"START + SELECT (1 s): solicitar salida");
    }else{
        PDN_UI_TEXT(heading,"Esperando confirmacion");
        PDN_UI_TEXT(context,"Consulta PSP Dock en Windows.");
    }
    if(!v.footer_note[0] && !v.warning)
        PDN_UI_TEXT(footer_note,"Nota musical: mostrar u ocultar diagnostico");
    /* The kernel lifetime gate AND actual Stop/Unload authorize Circle.
     * Neither SAFE nor any progress counter proves Windows safe eject. */
    if(s->safe_to_unload && module_unloaded){
        PDN_UI_TEXT(footer,"PSP Dock detenido");
        PDN_UI_TEXT(footer_note,"%s",v.warning?
            "Fotografia los codigos para revisar este intento.":"Ya puedes volver al menu de tu PSP.");
        PDN_UI_TEXT(exit_hint,"Pulsa O de nuevo para volver al XMB.");
    }
#undef PDN_UI_TEXT
    return v;
}
#endif
