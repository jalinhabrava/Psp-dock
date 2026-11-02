/* Presentation regression for the actual 0301 launcher helper; no PSP APIs. */
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "../native_ui.h"

static void fits(const PdnUiView *v) {
    const char *lines[]={v->heading,v->context,v->detail,v->action,
                         v->footer,v->footer_note,v->exit_hint};
    unsigned i;
    for(i=0;i<sizeof(lines)/sizeof(lines[0]);i++)assert(strlen(lines[i])<PDN_UI_LINE_SIZE);
    /* ui_line paints 64 8-pixel glyph cells at x=14, advance=7. */
    assert(14+63*7+8<=480);
    assert(216+8<230 && 230+8<244 && 244+8<258 && 258+8<=272);
}
static void no_active_instructions(const PdnUiView *v) {
    assert(!strstr(v->heading,"SINCRONIZANDO"));
    assert(!strstr(v->heading,"MODO MANDO"));
    assert(!strstr(v->action,"cruceta"));
    assert(!strstr(v->context,"Navega y juega"));
    fits(v);
}
static PdnSharedStatus initial(void) {
    PdnSharedStatus s;
    memset(&s,0,sizeof(s));
    s.phase=PDN_PHASE_STORAGE;s.hid_interface=UINT32_MAX;
    return s;
}
int main(void) {
    PdnSharedStatus s=initial();PdnUiView v;
    /* Exact failing 0300 photo: a stale STORAGE phase cannot claim a session.
     * Index 5 is preserved but no longer mislabelled as a 0301 dependency. */
    s.stage=255;s.error=(int32_t)0x8002013cu;s.error_stage=3;s.dependency=5;
    s.modules_loaded=0x1f;s.usb_state=0x111;s.safe_to_unload=1;
    v=pdn_ui_present(&s,0);
    assert(!strcmp(v.heading,"Arranque detenido"));
    assert(strstr(v.context,"Etapa 3: iniciar modulo Sony"));
    assert(strstr(v.detail,"Dependencia 5: no reconocida"));
    assert(!strcmp(v.interface_label,"sin_asignar"));
    assert(!v.exit_hint[0]);no_active_instructions(&v);
    v=pdn_ui_present(&s,1);
    assert(strstr(v.exit_hint,"O de nuevo"));no_active_instructions(&v);
    s.dependency=4;v=pdn_ui_present(&s,0);
    assert(strstr(v.detail,"usbstorms.prx"));
    assert(!strstr(v.detail,"usbstorboot"));

    s=initial();s.stage=PDN_STAGE_MODULE_START;s.dependency=4;
    v=pdn_ui_present(&s,0);
    assert(!strcmp(v.heading,"Preparando PSP Dock"));
    assert(strstr(v.detail,"usbstorms.prx"));no_active_instructions(&v);

    s=initial();s.stage=PDN_STAGE_ERROR;s.error=-3030;
    s.error_stage=PDN_STAGE_CONNECTED;s.usb_state=0x222;
    s.sony_storage_started=1;s.phase=PDN_PHASE_CONTROLLER;s.hid_interface=1;s.dependency=4;
    v=pdn_ui_present(&s,0);
    assert(strstr(v.context,"conexion con Windows"));
    assert(!strstr(v.detail,"usbstorms")); /* Last loaded module is not the fault's cause. */
    assert(strstr(v.action,"No desconectes"));
    assert(strstr(v.footer_note,"Windows"));
    assert(!v.exit_hint[0]);no_active_instructions(&v);
    /* A stale USB poll must not imply the still-running Sony resource is safe. */
    s.usb_state=0x111;s.error_stage=PDN_STAGE_HID_START;
    v=pdn_ui_present(&s,0);assert(strstr(v.action,"No desconectes"));
    s.usb_state=0x120;s.sony_storage_started=0;s.error_stage=PDN_STAGE_GUARD;
    v=pdn_ui_present(&s,0);assert(strstr(v.action,"No desconectes"));
    assert(!v.exit_hint[0]);

    s=initial();s.stage=PDN_STAGE_STOPPED;s.cleanup_error=-1;s.safe_to_unload=1;
    v=pdn_ui_present(&s,0);assert(v.warning);no_active_instructions(&v);
    assert(!v.exit_hint[0]);
    v=pdn_ui_present(&s,1);assert(strstr(v.exit_hint,"O de nuevo"));
    s.cleanup_error=0;v=pdn_ui_present(&s,0);
    assert(!strcmp(v.heading,"PSP Dock detenido"));no_active_instructions(&v);

    s=initial();s.stage=PDN_STAGE_READY;s.sony_storage_started=1;
    v=pdn_ui_present(&s,0);assert(strstr(v.heading,"LISTO"));no_active_instructions(&v);
    s.stage=PDN_STAGE_CONNECTED;
    v=pdn_ui_present(&s,0);assert(!strcmp(v.heading,"Esperando a Windows"));
    no_active_instructions(&v);
    s.hid_interface=1;
    v=pdn_ui_present(&s,0);
    assert(!strcmp(v.heading,"SINCRONIZANDO PSP"));assert(strstr(v.action,"cruceta y X"));fits(&v);
    s.phase=PDN_PHASE_CONTROLLER;
    v=pdn_ui_present(&s,0);assert(!strcmp(v.heading,"MODO MANDO"));fits(&v);
    s.stage=PDN_STAGE_RECONNECT;
    v=pdn_ui_present(&s,0);no_active_instructions(&v);
    s.stage=PDN_STAGE_CONNECTED;s.phase=PDN_PHASE_COPYING;s.exit_requested=1;
    v=pdn_ui_present(&s,0);assert(!strcmp(v.heading,"COPIANDO AL DOCK"));
    assert(strstr(v.footer,"Salida solicitada"));no_active_instructions(&v);
    s.phase=PDN_PHASE_EXIT_SYNC;v=pdn_ui_present(&s,0);
    assert(strstr(v.heading,"GUARDADOS COMPATIBLES"));no_active_instructions(&v);
    s.phase=PDN_PHASE_SAFE;v=pdn_ui_present(&s,0);
    assert(strstr(v.action,"solo cuando Windows"));assert(!v.exit_hint[0]);no_active_instructions(&v);
    s.phase=PDN_PHASE_ERROR;s.error=0;v=pdn_ui_present(&s,0);
    assert(v.warning);assert(!v.exit_hint[0]);no_active_instructions(&v);

    puts("Native 0301 UI: failure precedence, dependency names, IF, close gate and line layout PASS");
    return 0;
}
