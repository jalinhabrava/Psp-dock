/* Actual 0300 transport under asynchronous USB/module doubles. These tests
 * establish ownership and order, not Sony USBBus composition on hardware. */
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "psp_stub/psp_test_api.h"
#define PSPDOCK_NATIVE_DESCRIPTORS_H
static struct UsbEndpoint pdn_endpoints[2]={{0,0,0},{1,0,0}};
static int pdn_interface,pdn_device_hi,pdn_config_hi,pdn_device_full,pdn_config_full,pdn_strings[1];
static uint8_t report_descriptor[148],pdn_hid_descriptor[9];
#include "../native_driver.c"

static PdnSharedStatus state;
static struct UsbdDeviceReq *pending[2];
static unsigned submissions[2],cancels[2];
static int fake_usb,fake_bus,fake_storage,fake_hid,fake_registered;
static unsigned fake_time,borrow_mask,load_mask,start_mask,stop_mask,unload_mask;
static unsigned operation_count;
static char operations[64][32];
static int fail_load=-1,fail_start=-1,fail_usb_start,hold_cancel,queue_failure;
static void (*cancel_hook)(unsigned);

static void log_operation(const char *name){
    assert(operation_count<64);snprintf(operations[operation_count++],32,"%s",name);
}
static void complete(unsigned ep,int code,int size){
    struct UsbdDeviceReq *r=pending[ep];assert(r);pending[ep]=NULL;
    r->retcode=code;r->recvsize=size;r->func(r,0,0);
}
int sceUsbbdReqSend(struct UsbdDeviceReq *r){
    unsigned ep=(unsigned)r->endp->endpnum;assert(ep<2);
    if(queue_failure)return queue_failure;
    assert(!pending[ep]);pending[ep]=r;submissions[ep]++;return 0;
}
int sceUsbbdReqRecv(struct UsbdDeviceReq *r){return sceUsbbdReqSend(r);}
int sceUsbbdReqCancelAll(struct UsbEndpoint *ep){
    unsigned n=(unsigned)ep->endpnum;cancels[n]++;
    if(pending[n] && !hold_cancel)complete(n,-3,0);
    if(cancel_hook)cancel_hook(n);
    return 0;
}
int sceUsbbdStall(struct UsbEndpoint *ep){(void)ep;assert(0);return -1;}
unsigned sceKernelGetSystemTimeLow(void){return fake_time;}
int sceKernelDelayThread(int t){fake_time+=(unsigned)t;return 0;}
int sceUsbGetState(void){return fake_usb;}
int sceUsbGetDrvState(const char *n){
    if(!strcmp(n,PSP_USBBUS_DRIVERNAME))return fake_bus;
    if(!strcmp(n,PSP_USBSTOR_DRIVERNAME))return fake_storage;
    if(!strcmp(n,PDN_DRIVER_NAME))return fake_hid;
    return (int)HID_LAB_STORAGE_NOENT;
}
int sceUsbStart(const char *n,int size,void *arg){
    (void)size;(void)arg;log_operation(n);
    if(!strcmp(n,PSP_USBBUS_DRIVERNAME)){fake_bus=1;return 0;}
    if(!strcmp(n,PSP_USBSTOR_DRIVERNAME)){
        assert((start_mask|borrow_mask)==31u && fake_bus==1 && !activated);
        fake_storage=1;return 0;
    }
    assert(!strcmp(n,PDN_DRIVER_NAME) && fake_registered && fake_storage==1 && !activated);
    if(fail_usb_start)return -700;
    fake_hid=1;return 0;
}
int sceUsbStop(const char *n,int size,void *arg){
    (void)size;(void)arg;log_operation(n);
    assert(!activated);
    if(!strcmp(n,PSP_USBBUS_DRIVERNAME)){fake_bus=2;return 0;}
    if(!strcmp(n,PSP_USBSTOR_DRIVERNAME)){fake_storage=2;return 0;}
    assert(!strcmp(n,PDN_DRIVER_NAME) && !pending[0] && !pending[1]);fake_hid=2;return 0;
}
int sceUsbActivate(int pid){
    assert(pid==PDN_PRODUCT && fake_bus==1 && fake_storage==1 && fake_hid==1);
    log_operation("activate");fake_usb|=0x200;return 0;
}
int sceUsbDeactivate(int pid){
    assert(pid==PDN_PRODUCT && !(fake_usb&PSP_USB_CABLE_CONNECTED));
    log_operation("deactivate");fake_usb&=~0x200;return 0;
}
int sceUsbbdRegister(struct UsbDriver *d){assert(d==&driver);fake_registered=1;fake_hid=2;return 0;}
int sceUsbbdUnregister(struct UsbDriver *d){
    assert(d==&driver && fake_hid!=1 && !pending[0] && !pending[1]);fake_registered=0;return 0;
}
SceModule *sceKernelFindModuleByName(const char *name){
    static SceModule modules[5];unsigned i;
    for(i=0;i<5;i++)if(!strcmp(name,dependencies[i].name) && (borrow_mask&(1u<<i))){
        modules[i].modid=(int)i+100;return &modules[i];
    }
    return NULL;
}
SceUID sceKernelLoadModule(const char *path,int flags,void *option){
    unsigned i;(void)flags;(void)option;
    assert(!activated && fake_storage!=1);
    for(i=0;i<5;i++)if(!strcmp(path,dependencies[i].path)){
        if((int)i==fail_load)return -600;
        assert(!(borrow_mask&(1u<<i)));load_mask|=1u<<i;return (int)i+100;
    }
    assert(0);return -1;
}
int sceKernelStartModule(SceUID id,SceSize size,void *args,int *status,void *option){
    unsigned i=(unsigned)(id-100);(void)size;(void)args;(void)option;
    assert(i<5 && (load_mask&(1u<<i)) && !(borrow_mask&(1u<<i)));
    *status=0;if((int)i==fail_start)return -601;start_mask|=1u<<i;
    if(i==1)fake_storage=2;
    return id;
}
int sceKernelStopModule(SceUID id,SceSize size,void *args,int *status,void *option){
    unsigned i=(unsigned)(id-100);(void)size;(void)args;(void)option;
    assert(i<5 && !(borrow_mask&(1u<<i)) && (start_mask&(1u<<i)));
    assert(fake_storage!=1 && !activated);stop_mask|=1u<<i;*status=0;return id;
}
int sceKernelUnloadModule(SceUID id){
    unsigned i=(unsigned)(id-100);assert(i<5 && (load_mask&(1u<<i)) && !(borrow_mask&(1u<<i)));
    if(start_mask&(1u<<i))assert(stop_mask&(1u<<i));
    unload_mask|=1u<<i;return id;
}
static void fixture(void){
    unsigned i;uint8_t session[16]={9};
    memset(&state,0,sizeof(state));ui=&state;
    memset(pending,0,sizeof(pending));memset(submissions,0,sizeof(submissions));memset(cancels,0,sizeof(cancels));
    memset(operations,0,sizeof(operations));operation_count=0;
    for(i=0;i<5;i++){dependencies[i].owned=0;dependencies[i].started=0;dependencies[i].id=-1;}
    active=connected=link_attached=link_reset_pending=0;
    registered=hid_started=storage_started=bus_owned=activated=0;
    ctl_busy=ctl_submitting=ctl_quiescing=hid_busy=0;
    need_initial_pad=input_rearm=1;exit_requested=0;
    link_generation=ctl_generation=0;ctl_feature_receive=0;
    ever_cable=cable_absence_pending=usb_poll_started=0;
    cable_absent_since=last_usb_poll=last_pad_time=hid_reports=0;
    idle_rate=0;neutral_pad(latest_pad);pdn_protocol_init(&protocol,session);
    fake_usb=0;fake_bus=2;fake_storage=(int)HID_LAB_STORAGE_NOENT;fake_hid=2;fake_registered=0;
    fake_time=1;borrow_mask=load_mask=start_mask=stop_mask=unload_mask=0;
    fail_load=fail_start=-1;fail_usb_start=hold_cancel=queue_failure=0;cancel_hook=NULL;
}
static void connected_fixture(void){
    fixture();assert(start_usb()==0 && activated);fake_usb=0x222;
    assert(attach(2,NULL,NULL)==0 && connected);assert(observe_cable(1,fake_usb)==0);
}
static void command(uint8_t out[32],uint8_t phase,uint32_t sequence){
    memset(out,0,32);memcpy(out,"PDK2",4);out[4]=2;out[5]=2;out[6]=phase;
    memcpy(out+8,protocol.session,16);pdn_write32(out+24,sequence);
}
static void receive_phase(uint8_t phase,uint32_t sequence){
    struct DeviceRequest setup={0x21,9,0x0300,1,32};
    assert(receive_control(0,1,&setup)==0 && pending[0]);
    command(ctl_buffer,phase,sequence);complete(0,0,32);
}
static void test_start_order_and_owned_cleanup(void){
    fixture();assert(start_usb()==0);
    assert(load_mask==31u && start_mask==31u && state.modules_loaded==31u);
    assert(operation_count==4 && !strcmp(operations[0],PSP_USBBUS_DRIVERNAME));
    assert(!strcmp(operations[1],PSP_USBSTOR_DRIVERNAME));
    assert(!strcmp(operations[2],PDN_DRIVER_NAME) && !strcmp(operations[3],"activate"));
    assert(stop_usb()==0 && unload_dependencies()==0);
    assert(stop_mask==31u && unload_mask==31u && !registered && !fake_registered && fake_bus==2);
}
static void test_borrowed_modules_and_bus_survive(void){
    fixture();borrow_mask=31u;fake_storage=2;fake_bus=1;
    assert(start_usb()==0 && state.modules_borrowed==31u && !bus_owned);
    assert(stop_usb()==0 && unload_dependencies()==0);
    assert(!load_mask && !start_mask && !stop_mask && !unload_mask && fake_bus==1);
}
static void test_guard_and_bootstrap_failures(void){
    unsigned i;
    fixture();fake_usb=0x20;assert(start_usb()==-3002 && !load_mask && !activated);
    fixture();fake_storage=1;assert(start_usb()==-3002 && !load_mask);
    for(i=0;i<5;i++){
        fixture();fail_load=(int)i;assert(start_usb()==-600 && !activated && !storage_started);
        assert(stop_usb()==0 && unload_dependencies()==0 && unload_mask==load_mask);
        fixture();fail_start=(int)i;assert(start_usb()==-601 && !activated && !storage_started);
        assert(stop_usb()==0 && unload_dependencies()==0 && unload_mask==load_mask);
    }
    fixture();fail_usb_start=1;assert(start_usb()==-700 && storage_started && !activated);
    assert(stop_usb()==0 && !storage_started && !registered && !bus_owned);
    assert(unload_dependencies()==0 && unload_mask==31u);
}
static void test_phase_ack_cancels_old_input_first(void){
    uint8_t status[32];connected_fixture();
    pad_req.endp=&pdn_endpoints[1];pad_req.func=pad_done;pad_req.size=8;hid_busy=1;
    assert(sceUsbbdReqSend(&pad_req)==0);receive_phase(PDN_PHASE_COPYING,1);
    assert(protocol.pending && protocol.phase==PDN_PHASE_STORAGE && protocol.result==PDN_QUEUED);
    pdn_protocol_status(&protocol,status,connected,storage_started,exit_requested);
    assert(status[6]==PDN_PHASE_STORAGE && status[7]==7 && pdn_read32(status+24)==1);
    assert(apply_phase()==0 && !pending[1] && !hid_busy && cancels[1]==1);
    assert(protocol.phase==PDN_PHASE_COPYING && protocol.result==PDN_APPLIED && !protocol.pending);
    assert(activated && storage_started && fake_storage==1 && operation_count==4);
    receive_phase(PDN_PHASE_COPYING,1);assert(protocol.result==PDN_APPLIED && !protocol.pending);
    receive_phase(PDN_PHASE_CONTROLLER,2);assert(apply_phase()==0 && protocol.phase==PDN_PHASE_CONTROLLER);
}
static void test_reconfiguration_and_old_ep0_cannot_change_phase(void){
    struct DeviceRequest setup={0x21,9,0x0300,1,32};
    connected_fixture();assert(receive_control(0,1,&setup)==0);command(ctl_buffer,PDN_PHASE_COPYING,1);
    detach(0,0,0);complete(0,0,32);assert(!protocol.pending && !protocol.sequence);
    assert(!exit_requested && observe_cable(1000000,0x220)==0);
    attach(2,NULL,NULL);assert(!connected && quiesce_link()==0 && connected && !link_reset_pending);
    receive_phase(PDN_PHASE_COPYING,1);detach(0,0,0);
    assert(!protocol.pending && protocol.result==PDN_ERR_LINK);
    attach(2,NULL,NULL);assert(quiesce_link()==0 && protocol.phase==PDN_PHASE_STORAGE);
}
static void test_stop_waits_for_real_unplug(void){
    unsigned before;connected_fixture();before=operation_count;
    request_exit(PDN_STOP_USER);
    assert(stop_usb()==-3030 && operation_count==before && storage_started);
    assert(observe_cable(1000,0x220)==0 && !cable_absence_pending);
    assert(observe_cable(2000,0x200)==0 && observe_cable(501999,0x200)==0);
    assert(observe_cable(502000,0x200)==1);
    fake_usb=0x200;assert(stop_usb()==0 && !storage_started && !activated);
    fixture();activated=1;fake_usb=-55;assert(stop_usb()==-55 && activated);
}
static void test_input_is_neutral_in_transfer_and_after_phase_switch(void){
    uint8_t input[8],neutral[8];SceCtrlData pad={PSP_CTRL_CROSS,127,127};
    connected_fixture();neutral_pad(neutral);
    gated_pad(&pad,input);assert(!memcmp(input,neutral,8) && input_rearm);
    pad.Buttons=0;gated_pad(&pad,input);assert(!input_rearm && !memcmp(input,neutral,8));
    pad.Buttons=PSP_CTRL_CROSS;gated_pad(&pad,input);assert(input[4]&0x10);
    protocol.phase=PDN_PHASE_COPYING;gated_pad(&pad,input);assert(!memcmp(input,neutral,8));
    protocol.phase=PDN_PHASE_EXIT_SYNC;gated_pad(&pad,input);assert(!memcmp(input,neutral,8));
    protocol.phase=PDN_PHASE_SAFE;gated_pad(&pad,input);assert(!memcmp(input,neutral,8));
    protocol.phase=PDN_PHASE_CONTROLLER;protocol.pending=1;gated_pad(&pad,input);assert(!memcmp(input,neutral,8));
    protocol.pending=0;pad.Buttons=PSP_CTRL_START|PSP_CTRL_SELECT;
    gated_pad(&pad,input);assert(!memcmp(input,neutral,8));
}
static void test_exit_request_preserves_controls_until_windows_acknowledges(void){
    uint8_t input[8],neutral[8];SceCtrlData pad={0,127,127};
    connected_fixture();protocol.phase=PDN_PHASE_CONTROLLER;neutral_pad(neutral);
    gated_pad(&pad,input);assert(!input_rearm);
    pad.Buttons=PSP_CTRL_START|PSP_CTRL_SELECT;
    request_exit(PDN_STOP_USER);gated_pad(&pad,input);
    assert(exit_requested && state.exit_requested && !memcmp(input,neutral,8));
    /* Windows may wait until the emulator closes. The user must retain
     * control of that emulator after releasing the request-exit chord. */
    pad.Buttons=PSP_CTRL_CROSS;gated_pad(&pad,input);
    assert(input[4]&0x10);
    receive_phase(PDN_PHASE_EXIT_SYNC,1);gated_pad(&pad,input);
    assert(!memcmp(input,neutral,8));
    assert(apply_phase()==0 && protocol.phase==PDN_PHASE_EXIT_SYNC);
    gated_pad(&pad,input);assert(!memcmp(input,neutral,8));
}
static void test_interface_one_and_transfer_buffer_ownership(void){
    struct DeviceRequest status={0xa1,1,0x0300,1,32};
    struct DeviceRequest alien={0xa1,0xfe,0,0,1};uint8_t first[32];
    connected_fixture();assert(receive_control(0,1,&status)==0 && state.hid_interface==1);
    memcpy(first,ctl_buffer,32);assert(receive_control(0,1,&status)<0 && !memcmp(first,ctl_buffer,32));
    complete(0,0,32);assert(receive_control(0,-1,&status)<0);
    assert(receive_control(0,0,&alien)<0);status.wIndex=0x100;
    assert(receive_control(0,1,&status)<0);
}
static void test_protocol_replay_malformed_and_terminal_safe(void){
    uint8_t msg[32],status[32];unsigned i;fixture();
    command(msg,PDN_PHASE_COPYING,1);msg[8]^=1;
    assert(pdn_protocol_receive(&protocol,msg,32)==PDN_ERR_SESSION && !protocol.sequence);
    msg[8]^=1;msg[7]=1;assert(pdn_protocol_receive(&protocol,msg,32)==PDN_ERR_FRAME);
    msg[7]=0;assert(pdn_protocol_receive(&protocol,msg,31)==PDN_ERR_FRAME);
    assert(pdn_protocol_receive(&protocol,msg,32)==PDN_QUEUED);
    command(msg,PDN_PHASE_CONTROLLER,2);assert(pdn_protocol_receive(&protocol,msg,32)==PDN_ERR_BUSY);
    pdn_protocol_apply(&protocol);command(msg,PDN_PHASE_CONTROLLER,1);
    assert(pdn_protocol_receive(&protocol,msg,32)==PDN_ERR_SEQUENCE && protocol.result==PDN_APPLIED);
    command(msg,PDN_PHASE_SAFE,2);assert(pdn_protocol_receive(&protocol,msg,32)==PDN_ERR_TRANSITION);
    command(msg,PDN_PHASE_EXIT_SYNC,2);assert(pdn_protocol_receive(&protocol,msg,32)==PDN_QUEUED);
    pdn_protocol_apply(&protocol);command(msg,PDN_PHASE_SAFE,3);
    assert(pdn_protocol_receive(&protocol,msg,32)==PDN_QUEUED);pdn_protocol_apply(&protocol);
    for(i=0;i<256;i++)if(i!=PDN_PHASE_SAFE){
        command(msg,(uint8_t)i,4);assert(pdn_protocol_receive(&protocol,msg,32)==PDN_ERR_TRANSITION);
    }
    pdn_protocol_status(&protocol,status,1,1,1);
    assert(!memcmp(status,"PDK2",4) && status[6]==PDN_PHASE_SAFE && status[7]==13);
    assert(pdn_read32(status+24)==3 && pdn_read32(status+28)==PDN_APPLIED);
    protocol.sequence=UINT32_MAX;command(msg,PDN_PHASE_SAFE,0);
    assert(pdn_protocol_receive(&protocol,msg,32)==PDN_ERR_SEQUENCE);
}
int main(void){
    test_start_order_and_owned_cleanup();test_borrowed_modules_and_bus_survive();
    test_guard_and_bootstrap_failures();test_phase_ack_cancels_old_input_first();
    test_reconfiguration_and_old_ep0_cannot_change_phase();test_stop_waits_for_real_unplug();
    test_input_is_neutral_in_transfer_and_after_phase_switch();
    test_exit_request_preserves_controls_until_windows_acknowledges();
    test_interface_one_and_transfer_buffer_ownership();test_protocol_replay_malformed_and_terminal_safe();
    puts("native-dock: protocol, ownership, bootstrap and async lifecycle tests passed");return 0;
}
