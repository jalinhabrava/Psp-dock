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
    int warning;
} PdnUiView;

static inline const char *pdn_ui_dependency_name(unsigned dependency) {
    /* Exact mandatory dependency order for 0301; Boot is not a dependency. */
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

static inline PdnUiView pdn_ui_present(const PdnSharedStatus *s,int module_unloaded) {
    PdnUiView v;
    int fault=s->error || s->stage==PDN_STAGE_ERROR || s->phase==PDN_PHASE_ERROR;
    int may_have_usb=(s->usb_state&(PDN_UI_USB_ACTIVATED|PDN_UI_USB_CABLE_CONNECTED)) ||
        s->sony_storage_started ||
        (s->error_stage>=PDN_STAGE_ACTIVATE && s->error_stage<=PDN_STAGE_STOPPING);
    memset(&v,0,sizeof(v));
#define PDN_UI_TEXT(field,...) snprintf(v.field,sizeof(v.field),__VA_ARGS__)
    if(s->hid_interface==UINT32_MAX)
        PDN_UI_TEXT(interface_label,"sin_asignar");
    else PDN_UI_TEXT(interface_label,"%u",(unsigned)s->hid_interface);

    /* phase starts as STORAGE before bootstrap. Errors and resource lifetime
     * must win over that value, even after the driver has been unloaded. */
    if(fault){
        v.warning=1;
        PDN_UI_TEXT(heading,"%s",s->error_stage<=PDN_STAGE_ACTIVATE?
            "Arranque detenido":"Error en la conexion");
        PDN_UI_TEXT(context,"Etapa %u: %s",(unsigned)s->error_stage,pdn_ui_stage_name(s->error_stage));
        if(s->error_stage==PDN_STAGE_MODULE_LOAD || s->error_stage==PDN_STAGE_MODULE_START)
            pdn_ui_module_line(v.detail,(unsigned)s->dependency);
        else PDN_UI_TEXT(detail,"Consulta los codigos de diagnostico.");
        if(may_have_usb && !s->safe_to_unload){
            PDN_UI_TEXT(action,"No desconectes hasta el aviso de Windows.");
            PDN_UI_TEXT(footer_note,"Finaliza y expulsa la PSP desde Windows.");
        }else{
            PDN_UI_TEXT(action,"%s",s->error_stage<=PDN_STAGE_ACTIVATE?
                "No se ha iniciado una sesion de Dock.":"Espera a que se confirme el cierre.");
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
        PDN_UI_TEXT(context,"Espera a que termine el cierre de USB.");
        PDN_UI_TEXT(action,"Volver al XMB aparecera cuando sea posible.");
    }else if(s->stage<PDN_STAGE_READY){
        PDN_UI_TEXT(heading,"Preparando PSP Dock");
        PDN_UI_TEXT(context,"Etapa %u: %s",(unsigned)s->stage,pdn_ui_stage_name(s->stage));
        if(s->stage==PDN_STAGE_MODULE_LOAD || s->stage==PDN_STAGE_MODULE_START)
            pdn_ui_module_line(v.detail,(unsigned)s->dependency);
        PDN_UI_TEXT(action,"Espera a que aparezca LISTO para conectar.");
    }else if(!s->sony_storage_started){
        PDN_UI_TEXT(heading,"Esperando almacenamiento Sony");
        PDN_UI_TEXT(context,"La Memory Stick aun no esta disponible.");
        PDN_UI_TEXT(action,"Espera en esta pantalla.");
    }else if(s->stage==PDN_STAGE_RECONNECT){
        PDN_UI_TEXT(heading,"Reconectando con Windows");
        PDN_UI_TEXT(context,"Windows esta reconfigurando el dispositivo.");
        PDN_UI_TEXT(detail,"La navegacion esta en pausa.");
        PDN_UI_TEXT(action,"Manten el cable conectado.");
    }else if(s->phase==PDN_PHASE_SAFE){
        PDN_UI_TEXT(heading,"Finalizando la conexion");
        PDN_UI_TEXT(context,"Terminando la devolucion de guardados compatibles.");
        PDN_UI_TEXT(detail,"Espera la confirmacion de Windows.");
        PDN_UI_TEXT(action,"Retira USB solo cuando Windows lo indique.");
    }else if(s->phase==PDN_PHASE_COPYING || s->phase==PDN_PHASE_EXIT_SYNC){
        PDN_UI_TEXT(heading,"%s",s->phase==PDN_PHASE_COPYING?
            "COPIANDO AL DOCK":"DEVOLVIENDO GUARDADOS COMPATIBLES");
        PDN_UI_TEXT(context,"Consulta el progreso en PSP Dock de Windows.");
        PDN_UI_TEXT(detail,"El mando esta bloqueado durante esta operacion.");
        PDN_UI_TEXT(action,"Manten el cable conectado hasta que termine.");
        if(s->exit_requested)
            PDN_UI_TEXT(footer,"Salida solicitada; espera a que termine.");
    }else if(s->exit_requested){
        PDN_UI_TEXT(heading,"Salida solicitada");
        PDN_UI_TEXT(context,"Finaliza la sesion en PSP Dock de Windows.");
        PDN_UI_TEXT(detail,"Windows devolvera los guardados compatibles.");
        PDN_UI_TEXT(action,"No retires USB hasta el aviso de Windows.");
    }else if(s->stage==PDN_STAGE_READY){
        PDN_UI_TEXT(heading,"LISTO: conecta el cable USB");
        PDN_UI_TEXT(context,"Abre PSP Dock en Windows.");
        PDN_UI_TEXT(detail,"Esperando la conexion del PC.");
    }else if(s->stage!=PDN_STAGE_CONNECTED || s->hid_interface==UINT32_MAX){
        PDN_UI_TEXT(heading,"Esperando a Windows");
        PDN_UI_TEXT(context,"El mando aun no tiene interfaz confirmada.");
        PDN_UI_TEXT(detail,"Espera a que Windows termine de configurar USB.");
        PDN_UI_TEXT(action,"Manten el cable conectado.");
    }else if(s->phase==PDN_PHASE_STORAGE || s->phase==PDN_PHASE_CONTROLLER){
        if(s->phase==PDN_PHASE_STORAGE){
            PDN_UI_TEXT(heading,"SINCRONIZANDO PSP");
            PDN_UI_TEXT(context,"Elige en Windows que quieres sincronizar.");
            PDN_UI_TEXT(detail,"Partidas, juegos, todo o nada.");
            PDN_UI_TEXT(action,"Puedes elegir con la cruceta y X.");
        }else{
            PDN_UI_TEXT(heading,"MODO MANDO");
            PDN_UI_TEXT(context,"Navega y juega con los botones de tu PSP.");
            PDN_UI_TEXT(detail,"Salir en Windows devuelve guardados compatibles.");
        }
        PDN_UI_TEXT(footer,"START + SELECT (1 s): solicitar salida");
        PDN_UI_TEXT(footer_note,"El mando se bloquea mientras se copian archivos.");
    }else{
        PDN_UI_TEXT(heading,"Estado pendiente de confirmar");
        PDN_UI_TEXT(context,"Consulta PSP Dock en Windows y el diagnostico.");
    }
    /* Only the kernel's lifetime gate AND completed Stop/Unload authorize
     * the launcher's existing Circle path. SAFE/USB bits alone never do. */
    if(s->safe_to_unload && module_unloaded){
        PDN_UI_TEXT(footer,"PSP DOCK DETENIDO");
        PDN_UI_TEXT(footer_note,"%s",v.warning?
            "Fotografia los codigos para revisar este intento.":"Ya puedes volver al menu de tu PSP.");
        PDN_UI_TEXT(exit_hint,"Pulsa O de nuevo para volver al XMB.");
    }
#undef PDN_UI_TEXT
    return v;
}
#endif
