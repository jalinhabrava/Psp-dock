/* Presentation regression for the actual launcher helper; no PSP APIs. */
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "../native_ui.h"

static void fits(const PdnUiView *v) {
    const char *lines[]={v->heading,v->context,v->detail,v->action,
                         v->footer,v->footer_note,v->exit_hint,v->progress,v->progress_detail};
    unsigned i;
    for(i=0;i<sizeof(lines)/sizeof(lines[0]);i++)assert(strlen(lines[i])<PDN_UI_LINE_SIZE);
    /* ui_line paints 64 8-pixel glyph cells at x=14, advance=7. */
    assert(14+63*7+8<=480);
    assert(216+8<230 && 230+8<244 && 244+8<258 && 258+8<=272);
}
static void no_active_instructions(const PdnUiView *v) {
    assert(!strstr(v->heading,"Sincronizando PSP"));
    assert(!strstr(v->heading,"Todo listo"));
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
static void test_ep0_snapshot_keeps_complete_raw_evidence(void) {
    volatile PdnEp0Diagnostic shared={0};
    PdnEp0Diagnostic sample={0},previous;unsigned i;
    shared.generation=2;shared.recvsize=0;shared.retcode=-3;
    for(i=0;i<PDN_FEATURE_SIZE;i++)shared.raw[i]=(uint8_t)i;
    assert(pdn_ui_ep0_snapshot(&sample,&shared));
    /* recvsize is evidence, not permission to suppress the buffer view. */
    assert(sample.recvsize==0 && sample.retcode==-3 && sample.raw[63]==63);
    previous=sample;shared.generation=3;shared.raw[0]=255;
    assert(!pdn_ui_ep0_snapshot(&sample,&shared));
    assert(!memcmp(&sample,&previous,sizeof(sample)));
    shared.generation=4;shared.recvsize=64;shared.retcode=0;
    assert(pdn_ui_ep0_snapshot(&sample,&shared));
    assert(sample.raw[0]==255 && sample.recvsize==64 && !sample.retcode);
}
static void test_ep0_raw_rows_are_bounded_and_offset_labelled(void) {
    PdnEp0Diagnostic sample={0};unsigned i;
    struct {char line[PDN_UI_LINE_SIZE];unsigned char guard[8];} output;
    memset(&output,0xa5,sizeof(output));
    for(i=0;i<PDN_FEATURE_SIZE;i++)sample.raw[i]=(uint8_t)i;
    pdn_ui_ep0_hex(output.line,&sample,0);
    assert(!strcmp(output.line,"00: 00010203 04050607 08090A0B 0C0D0E0F"));
    pdn_ui_ep0_hex(output.line,&sample,48);
    assert(!strcmp(output.line,"30: 30313233 34353637 38393A3B 3C3D3E3F"));
    assert(strlen(output.line)<PDN_UI_LINE_SIZE);
    pdn_ui_ep0_hex(output.line,&sample,64);assert(!output.line[0]);
    pdn_ui_ep0_hex(output.line,&sample,1);assert(!output.line[0]);
    pdn_ui_ep0_hex(output.line,&sample,UINT32_MAX);assert(!output.line[0]);
    for(i=0;i<sizeof(output.guard);i++)assert(output.guard[i]==0xa5);
}
int main(void) {
    PdnSharedStatus s=initial();PdnUiView v;
    test_ep0_snapshot_keeps_complete_raw_evidence();
    test_ep0_raw_rows_are_bounded_and_offset_labelled();
    /* Exact failing 0300 photo: a stale STORAGE phase cannot claim a session.
     * Index 5 is preserved but no longer mislabelled as a 0302 dependency. */
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
    assert(!strstr(v.detail,"usbstorms.prx"));no_active_instructions(&v);

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
    v=pdn_ui_present(&s,0);assert(!strcmp(v.heading,"Esperando a Windows"));
    assert(!v.progress_visible);no_active_instructions(&v);
    /* Mere connected USB/descriptor reads must never advertise real work. */
    s.usb_state=0x222;s.exit_requested=1;s.stage=PDN_STAGE_WAIT_UNPLUG;
    v=pdn_ui_present(&s,0);assert(!strcmp(v.heading,"Conexion cancelada"));
    assert(strstr(v.context,"no ha iniciado"));assert(strstr(v.detail,"Expulsa la PSP"));
    assert(strstr(v.action,"retira el cable"));assert(!v.exit_hint[0]);
    assert(!v.progress_visible);no_active_instructions(&v);
    s.host_session_seen=1;v=pdn_ui_present(&s,0);
    assert(!strcmp(v.heading,"Conexion interrumpida") && !strstr(v.context,"no ha iniciado"));
    s.host_session_seen=0;s.exit_requested=0;s.stage=PDN_STAGE_CONNECTED;
    s.host_confirmed=1;s.activity=PDN_ACTIVITY_INVENTORY;
    v=pdn_ui_present(&s,0);
    assert(!strcmp(v.heading,"Sincronizando PSP"));assert(!strstr(v.action,"cruceta"));
    assert(v.progress_visible && !v.progress_measured && !strchr(v.progress,'%'));fits(&v);
    s.activity=PDN_ACTIVITY_CHOOSING;v=pdn_ui_present(&s,0);
    assert(strstr(v.heading,"Elige") && strstr(v.action,"cruceta y X"));
    assert(!v.progress_visible);fits(&v);
    s.activity=PDN_ACTIVITY_SYNC_IN;s.progress_unit=PDN_UNIT_BYTES;s.completed=5;s.total=10;
    v=pdn_ui_present(&s,0);
    assert(!strcmp(v.heading,"Sincronizando PSP") && v.progress_measured && v.progress_percent==50);
    assert(!strcmp(v.progress_detail,"5 / 10 bytes"));fits(&v);
    s.presentation_generation=1;v=pdn_ui_present(&s,0);
    assert(v.progress_visible && !v.progress_measured); /* No torn 64-bit counter. */
    s.presentation_generation=2;s.progress_unit=PDN_UNIT_UNKNOWN;s.completed=s.total=0;
    v=pdn_ui_present(&s,0);assert(!v.progress_measured && !strchr(v.progress,'%'));
    s.progress_unit=PDN_UNIT_FILES;s.completed=100;s.total=10;
    v=pdn_ui_present(&s,0);assert(!v.progress_measured);
    s.completed=3;s.total=11;v=pdn_ui_present(&s,0);
    assert(v.progress_measured && v.progress_percent==27 && !strcmp(v.progress_detail,"3 / 11 archivos"));
    s.completed=UINT64_MAX/2;s.total=UINT64_MAX;s.progress_unit=PDN_UNIT_BYTES;
    v=pdn_ui_present(&s,0);assert(v.progress_measured && v.progress_percent==49);fits(&v);
    s.completed=UINT64_MAX;v=pdn_ui_present(&s,0);assert(v.progress_percent==100);fits(&v);
    assert(pdn_ui_percent(0,1)==0 && pdn_ui_percent(1,1)==100);
    assert(pdn_ui_percent(1,3)==33 && pdn_ui_percent(2,3)==66);
    assert(pdn_ui_percent(UINT64_MAX-1,UINT64_MAX)==99);
    assert(pdn_ui_percent(1,0)==0 && pdn_ui_percent(2,1)==0);

    s.phase=PDN_PHASE_CONTROLLER;s.activity=PDN_ACTIVITY_CONTROLLER;
    v=pdn_ui_present(&s,0);assert(!strcmp(v.heading,"Todo listo"));assert(!v.progress_visible);fits(&v);
    s.stage=PDN_STAGE_RECONNECT;s.host_confirmed=0;
    v=pdn_ui_present(&s,0);no_active_instructions(&v);
    s.stage=PDN_STAGE_CONNECTED;s.host_confirmed=1;s.phase=PDN_PHASE_COPYING;s.exit_requested=1;
    s.activity=PDN_ACTIVITY_COPY_GAME;
    v=pdn_ui_present(&s,0);assert(!strcmp(v.heading,"Copiando el juego"));
    assert(strstr(v.footer,"Salida solicitada"));no_active_instructions(&v);
    s.phase=PDN_PHASE_EXIT_SYNC;s.activity=PDN_ACTIVITY_SYNC_OUT;v=pdn_ui_present(&s,0);
    assert(!strcmp(v.heading,"Sincronizando partidas"));no_active_instructions(&v);
    s.phase=PDN_PHASE_SAFE;s.activity=PDN_ACTIVITY_SAFE;v=pdn_ui_present(&s,0);
    assert(strstr(v.action,"solo cuando Windows"));assert(!v.exit_hint[0]);
    assert(!v.progress_visible);no_active_instructions(&v);
    s.phase=PDN_PHASE_CONTROLLER;s.activity=PDN_ACTIVITY_CONTROLLER;v=pdn_ui_present(&s,0);
    assert(!strcmp(v.heading,"Salida solicitada"));assert(!v.progress_visible);no_active_instructions(&v);
    s.phase=PDN_PHASE_ERROR;s.error=0;v=pdn_ui_present(&s,0);
    assert(v.warning);assert(!v.exit_hint[0]);no_active_instructions(&v);

    puts("Native 0304 UI: RAM snapshot, raw EP0, host confirmation, progress, errors and close gate PASS");
    return 0;
}
