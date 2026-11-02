/* Actual 0304 transport under asynchronous USB/module doubles. These tests
 * establish ownership and order, not Sony USBBus composition on hardware. */
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "psp_stub/psp_test_api.h"
#define PSPDOCK_NATIVE_DESCRIPTORS_H
static struct UsbEndpoint pdn_endpoints[2]={{0,0,0},{1,0,0}};
static int pdn_interface,pdn_device_hi,pdn_config_hi,pdn_device_full,pdn_config_full,pdn_strings[1];
#include "../native_report_descriptor.h"
static uint8_t pdn_hid_descriptor[9];
#include "../native_driver.c"

static PdnSharedStatus state;
static struct UsbdDeviceReq *pending[2];
static unsigned submissions[2],cancels[2];
static int fake_usb,fake_bus,fake_storage,fake_hid,fake_registered;
static unsigned fake_time,borrow_mask,load_mask,start_mask,stop_mask,unload_mask;
static unsigned operation_count;
static char operations[64][32];
static int fail_load=-1,fail_start=-1,fail_usb_start,hold_cancel,queue_failure;
static int start_failure_code=-601,suppress_sony_registration;
static unsigned boot_loads,boot_starts,forbidden_loads;
static void (*cancel_hook)(unsigned);
/* Two-view cache fixture. Sony may copy EP0 OUT bytes with the CPU before
 * invoking our callback. Such bytes live in the cached view; a subsequent
 * invalidate discards them and reloads the old backing-store contents.
 * This models cache semantics, not proof of the Sony path on this PSP. */
static uint8_t ctl_memory[sizeof(ctl_buffer)];
static int model_ep0_cache,receive_cache_prepared;
static unsigned discarded_cpu_copies;
void sceKernelDcacheInvalidateRange(void *p,size_t n){
    if(!model_ep0_cache || p!=ctl_buffer)return;
    assert(n<=sizeof(ctl_memory));
    if(memcmp(p,ctl_memory,n))discarded_cpu_copies++;
    memcpy(p,ctl_memory,n);
}
void sceKernelDcacheWritebackInvalidateRange(void *p,size_t n){
    if(!model_ep0_cache || p!=ctl_buffer)return;
    assert(n<=sizeof(ctl_memory));memcpy(ctl_memory,p,n);receive_cache_prepared=1;
}
void sceKernelDcacheWritebackRange(void *p,size_t n){
    if(!model_ep0_cache || p!=ctl_buffer)return;
    assert(n<=sizeof(ctl_memory));memcpy(ctl_memory,p,n);
}
enum { REQUIRED_MASK=15u, FAKE_BOOT_MODULE=104 };
/* Independent Sony fixture: Boot really fails to link in this GAME model.
 * Keeping it in the double makes a regression to the 0300 bootstrap fail,
 * instead of silently declaring every newly listed dependency successful.
 */
static const char *fake_paths[5]={
    "flash0:/kd/semawm.prx", "flash0:/kd/usbstor.prx",
    "flash0:/kd/usbstormgr.prx", "flash0:/kd/usbstorms.prx",
    "flash0:/kd/usbstorboot.prx"
};
static const char *fake_names[5]={
    "sceSemawm", "sceUSB_Stor_Driver", "sceUSB_Stor_Mgr_Driver",
    "sceUSB_Stor_Ms_Driver", "sceUSB_Stor_Boot_Driver"
};

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
int sceUsbbdReqRecv(struct UsbdDeviceReq *r){
    if(model_ep0_cache && r->data==ctl_buffer){
        assert(receive_cache_prepared);receive_cache_prepared=0;
    }
    return sceUsbbdReqSend(r);
}
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
        assert((start_mask|borrow_mask)==REQUIRED_MASK && fake_bus==1 && !activated);
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
    for(i=0;i<5;i++)if(!strcmp(name,fake_names[i]) && (borrow_mask&(1u<<i))){
        modules[i].modid=(int)i+100;return &modules[i];
    }
    return NULL;
}
SceUID sceKernelLoadModule(const char *path,int flags,void *option){
    unsigned i;(void)flags;(void)option;
    assert(!activated && fake_storage!=1);
    if(strstr(path,"/pcact.prx") || strstr(path,"/npdrm.prx") ||
       strstr(path,"/usbstoreflash.prx") || strstr(path,"/usbdevice.prx")){
        forbidden_loads++;return -602;
    }
    for(i=0;i<5;i++)if(!strcmp(path,fake_paths[i])){
        if(i==4)boot_loads++;
        if((int)i==fail_load)return -600;
        assert(!(borrow_mask&(1u<<i)));load_mask|=1u<<i;return (int)i+100;
    }
    assert(0);return -1;
}
int sceKernelStartModule(SceUID id,SceSize size,void *args,int *status,void *option){
    unsigned i=(unsigned)(id-100);(void)size;(void)args;(void)option;
    assert(i<5 && (load_mask&(1u<<i)) && !(borrow_mask&(1u<<i)));
    *status=0;
    if(id==FAKE_BOOT_MODULE){boot_starts++;return (int)0x8002013cu;}
    if((int)i==fail_start)return start_failure_code;
    start_mask|=1u<<i;
    if(i==1 && !suppress_sony_registration)fake_storage=2;
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
    for(i=0;i<PDN_DEPENDENCIES;i++){dependencies[i].owned=0;dependencies[i].started=0;dependencies[i].id=-1;}
    active=connected=link_attached=link_reset_pending=0;
    registered=hid_started=storage_started=bus_owned=activated=0;
    ctl_busy=ctl_submitting=ctl_quiescing=hid_busy=0;
    need_initial_pad=input_rearm=1;exit_requested=0;
    link_generation=ctl_generation=0;ctl_feature_receive=0;
    ever_cable=cable_absence_pending=usb_poll_started=0;
    cable_absent_since=last_usb_poll=last_pad_time=hid_reports=0;
    idle_rate=0;neutral_pad(latest_pad);pdn_protocol_init(&protocol,session);
    fake_usb=0;fake_bus=2;fake_storage=(int)HID_LAB_STORAGE_NOENT;fake_hid=2;fake_registered=0;
    session_counter=0;fake_time=1;borrow_mask=load_mask=start_mask=stop_mask=unload_mask=0;
    fail_load=fail_start=-1;fail_usb_start=hold_cancel=queue_failure=0;cancel_hook=NULL;
    start_failure_code=-601;suppress_sony_registration=0;
    boot_loads=boot_starts=forbidden_loads=0;
    model_ep0_cache=receive_cache_prepared=0;discarded_cpu_copies=0;
    memset(ctl_memory,0,sizeof(ctl_memory));
}
static void connected_fixture(void){
    fixture();assert(start_usb()==0 && activated);fake_usb=0x222;
    assert(attach(2,NULL,NULL)==0 && connected);assert(observe_cable(1,fake_usb)==0);
}
static void command(uint8_t out[PDN_FEATURE_SIZE],uint8_t phase,uint32_t sequence){
    memset(out,0,PDN_FEATURE_SIZE);memcpy(out,"PDK2",4);out[4]=3;out[5]=2;out[6]=phase;
    memcpy(out+8,protocol.session,16);pdn_write32(out+24,sequence);
    memcpy(out+32,pdn_identity,sizeof(pdn_identity));
}
static void receive_message(const uint8_t frame[PDN_FEATURE_SIZE]){
    struct DeviceRequest setup={0x21,9,0x0300,1,PDN_FEATURE_SIZE};
    assert(receive_control(0,1,&setup)==0 && pending[0]);
    memcpy(ctl_buffer,frame,PDN_FEATURE_SIZE);complete(0,0,PDN_FEATURE_SIZE);
}
static void receive_phase(uint8_t phase,uint32_t sequence){
    uint8_t frame[PDN_FEATURE_SIZE];command(frame,phase,sequence);receive_message(frame);
}
static void begin_session(void){
    uint8_t frame[PDN_FEATURE_SIZE];command(frame,PDN_PHASE_STORAGE,protocol.sequence+1);
    frame[5]=PDN_OP_HOST_BEGIN;receive_message(frame);
    assert(protocol.pending && !protocol.host_confirmed);
    assert(apply_phase()==0 && protocol.host_confirmed && protocol.activity==PDN_ACTIVITY_INVENTORY);
}
static void claimed_fixture(void){connected_fixture();begin_session();}
static void progress_message(uint8_t out[PDN_FEATURE_SIZE],uint8_t activity,
                             uint8_t unit,uint64_t done,uint64_t total,uint32_t seq){
    command(out,protocol.phase,seq);out[5]=PDN_OP_HOST_PROGRESS;
    out[44]=activity;out[45]=unit;pdn_write64(out+48,done);pdn_write64(out+56,total);
}
static void test_start_order_and_owned_cleanup(void){
    fixture();assert(start_usb()==0);
    assert(load_mask==REQUIRED_MASK && start_mask==REQUIRED_MASK && state.modules_loaded==REQUIRED_MASK);
    assert(operation_count==4 && !strcmp(operations[0],PSP_USBBUS_DRIVERNAME));
    assert(!strcmp(operations[1],PSP_USBSTOR_DRIVERNAME));
    assert(!strcmp(operations[2],PDN_DRIVER_NAME) && !strcmp(operations[3],"activate"));
    assert(stop_usb()==0 && unload_dependencies()==0);
    assert(stop_mask==REQUIRED_MASK && unload_mask==REQUIRED_MASK && !registered && !fake_registered && fake_bus==2);
}
static void test_borrowed_modules_and_bus_survive(void){
    fixture();borrow_mask=REQUIRED_MASK;fake_storage=2;fake_bus=1;
    assert(start_usb()==0 && state.modules_borrowed==REQUIRED_MASK && !bus_owned);
    assert(stop_usb()==0 && unload_dependencies()==0);
    assert(!load_mask && !start_mask && !stop_mask && !unload_mask && fake_bus==1);
}
static void test_guard_and_bootstrap_failures(void){
    unsigned i;
    fixture();fake_usb=0x20;assert(start_usb()==-3002 && !load_mask && !activated);
    fixture();fake_storage=1;assert(start_usb()==-3002 && !load_mask);
    for(i=0;i<PDN_DEPENDENCIES;i++){
        fixture();fail_load=(int)i;assert(start_usb()==-600 && !activated && !storage_started);
        assert(stop_usb()==0 && unload_dependencies()==0 && unload_mask==load_mask);
        fixture();fail_start=(int)i;assert(start_usb()==-601 && !activated && !storage_started);
        assert(stop_usb()==0 && unload_dependencies()==0 && unload_mask==load_mask);
    }
    fixture();fail_usb_start=1;assert(start_usb()==-700 && storage_started && !activated);
    assert(stop_usb()==0 && !storage_started && !registered && !bus_owned);
    assert(unload_dependencies()==0 && unload_mask==REQUIRED_MASK);
}
static void test_0304_omits_pc_activation_and_keeps_mandatory_link_failures_fatal(void){
    int result,status=-1;SceUID boot;
    /* Reproduce the photographed 0300 failure in the Sony module double. */
    fixture();boot=sceKernelLoadModule("flash0:/kd/usbstorboot.prx",0,NULL);
    assert(boot==FAKE_BOOT_MODULE);
    assert(sceKernelStartModule(boot,0,NULL,&status,NULL)==(int)0x8002013cu);
    assert(boot_loads==1 && boot_starts==1 && !start_mask);
    assert(sceKernelUnloadModule(boot)==boot);

    /* 0304 must reach the normal transport without ever loading that PRX,
     * Pcact, NPDRM, EFlash or usbdevice redirection. */
    fixture();assert(PDN_DEPENDENCIES==4u && PDN_REVISION==0x0304u);
    assert(start_usb()==0 && activated && storage_started);
    assert(load_mask==REQUIRED_MASK && !boot_loads && !boot_starts && !forbidden_loads);
    assert(stop_usb()==0 && unload_dependencies()==0);

    /* The same error on a mandatory Memory Stick module is still fatal. */
    fixture();fail_start=3;start_failure_code=(int)0x8002013cu;
    result=start_usb();assert(result==(int)0x8002013cu);record_error(result);
    assert(state.dependency==4 && state.error_stage==PDN_STAGE_MODULE_START);
    assert(state.error==(int)0x8002013cu && !activated && !storage_started && !registered);
    assert(stop_usb()==0 && unload_dependencies()==0);
    assert(unload_mask==REQUIRED_MASK && stop_mask==7u);
}
static void test_sony_registration_and_launcher_revision_remain_required(void){
    PdnLaunchArgs old_launch={0x304e4450u,sizeof(PdnLaunchArgs),&state};
    fixture();suppress_sony_registration=1;
    assert(start_usb()==-3003 && !activated && !registered && !storage_started);
    assert(!operation_count && stop_usb()==0 && unload_dependencies()==0);
    assert(PDN_LAUNCH_MAGIC==0x344e4450u && PDN_LAUNCH_MAGIC!=old_launch.magic);
    assert(module_start(sizeof(old_launch),&old_launch)==-1);
    old_launch.magic=0x314e4450u;
    assert(module_start(sizeof(old_launch),&old_launch)==-1);
    old_launch.magic=0x324e4450u;
    assert(module_start(sizeof(old_launch),&old_launch)==-1);
    old_launch.magic=0x334e4450u;
    assert(module_start(sizeof(old_launch),&old_launch)==-1);
}
static void test_identification_and_get_are_read_only(void){
    struct DeviceRequest status={0xa1,1,0x0300,1,PDN_FEATURE_SIZE};
    struct DeviceRequest descriptor={0x81,6,0x2200,1,sizeof(report_descriptor)};
    struct DeviceRequest legacy_set={0x21,9,0x0300,1,32};
    const uint8_t identity[12]={'N','A','T','3',4,3,7,0,1,0,4,3};
    uint8_t before[16];unsigned i;
    connected_fixture();memcpy(before,protocol.session,16);
    assert(sizeof(pdn_wire_prefix)==5 && !memcmp(pdn_wire_prefix,"PDK2\x03",5));
    for(i=0;i<3;i++){
        assert(receive_control(0,1,&status)==0 && state.hid_interface==1);
        assert(pending[0]->size==64 && !memcmp(ctl_buffer,"PDK2",4));
        assert(ctl_buffer[4]==3 && ctl_buffer[5]==1 && ctl_buffer[6]==1 && ctl_buffer[7]==5);
        assert(!memcmp(ctl_buffer+32,identity,12));
        assert(!memcmp(ctl_buffer+44,(uint8_t[20]){0},20));
        complete(0,0,64);
    }
    assert(receive_control(0,1,&descriptor)==0 && pending[0]->size==136);
    assert(ctl_buffer[132]==0x40);complete(0,0,136);
    assert(!protocol.host_confirmed && !protocol.sequence && !protocol.pending);
    assert(protocol.activity==PDN_ACTIVITY_WAITING && !memcmp(before,protocol.session,16));
    assert(receive_control(0,1,&legacy_set)<0);
    receive_phase(PDN_PHASE_CONTROLLER,1);
    assert(protocol.last_rejection==PDN_ERR_HANDSHAKE && !protocol.sequence && !protocol.pending);
}
static void test_ep0_cpu_copy_keeps_command_until_protocol_reads_it(void){
    struct DeviceRequest status={0xa1,1,0x0300,1,PDN_FEATURE_SIZE};
    struct DeviceRequest setup={0x21,9,0x0300,1,PDN_FEATURE_SIZE};
    uint8_t frame[PDN_FEATURE_SIZE];
    connected_fixture();model_ep0_cache=1;
    /* The preceding GET_REPORT remains in memory. An EP0 CPU copy replaces
     * the cached view only. Invalidating in ctl_done loses HOST_BEGIN and
     * makes the parser see the old STATUS report instead (regression 0302). */
    assert(receive_control(0,1,&status)==0);complete(0,0,PDN_FEATURE_SIZE);
    assert(ctl_memory[5]==PDN_OP_STATUS);
    begin_session();
    assert(protocol.sequence==1 && protocol.result==PDN_APPLIED && !discarded_cpu_copies);
    receive_phase(PDN_PHASE_CONTROLLER,2);assert(apply_phase()==0);
    assert(protocol.phase==PDN_PHASE_CONTROLLER && protocol.sequence==2 && !discarded_cpu_copies);
    /* A coherent DMA completion also works: memory and the CPU-visible
     * view contain the received frame before the completion callback. */
    command(frame,PDN_PHASE_STORAGE,3);
    assert(receive_control(0,1,&setup)==0);
    memcpy(ctl_memory,frame,PDN_FEATURE_SIZE);memcpy(ctl_buffer,frame,PDN_FEATURE_SIZE);
    complete(0,0,PDN_FEATURE_SIZE);assert(apply_phase()==0);
    assert(protocol.phase==PDN_PHASE_STORAGE && protocol.sequence==3 && !discarded_cpu_copies);
    /* Short/error completions still cannot become commands. */
    command(frame,PDN_PHASE_CONTROLLER,4);
    assert(receive_control(0,1,&setup)==0);memcpy(ctl_buffer,frame,PDN_FEATURE_SIZE);
    complete(0,0,PDN_FEATURE_SIZE-1);assert(!protocol.pending && protocol.sequence==3);
    assert(receive_control(0,1,&setup)==0);memcpy(ctl_buffer,frame,PDN_FEATURE_SIZE);
    complete(0,-3,PDN_FEATURE_SIZE);assert(!protocol.pending && protocol.sequence==3);
}
static void test_begin_ack_and_phase_ack_cancel_old_input_first(void){
    uint8_t frame[PDN_FEATURE_SIZE],status[PDN_FEATURE_SIZE];unsigned before;
    connected_fixture();
    command(frame,PDN_PHASE_STORAGE,1);frame[5]=PDN_OP_HOST_BEGIN;receive_message(frame);
    assert(protocol.pending && !protocol.host_confirmed && !state.host_confirmed);
    assert(apply_phase()==0 && protocol.host_confirmed && state.host_confirmed);
    assert(protocol.activity==PDN_ACTIVITY_INVENTORY && state.activity==PDN_ACTIVITY_INVENTORY);
    before=cancels[1];pad_req.endp=&pdn_endpoints[1];pad_req.func=pad_done;pad_req.size=8;hid_busy=1;
    assert(sceUsbbdReqSend(&pad_req)==0);receive_phase(PDN_PHASE_COPYING,2);
    assert(protocol.pending && protocol.phase==PDN_PHASE_STORAGE && protocol.result==PDN_QUEUED);
    pdn_protocol_status(&protocol,status,connected,storage_started,exit_requested);
    assert(status[6]==PDN_PHASE_STORAGE && status[7]==23 && pdn_read32(status+24)==2);
    assert(apply_phase()==0 && !pending[1] && !hid_busy && cancels[1]==before+1);
    assert(protocol.phase==PDN_PHASE_COPYING && protocol.result==PDN_APPLIED && !protocol.pending);
    assert(protocol.activity==PDN_ACTIVITY_COPY_GAME && !protocol.completed && !protocol.total);
    assert(activated && storage_started && fake_storage==1 && operation_count==4);
    receive_phase(PDN_PHASE_COPYING,2);assert(protocol.result==PDN_APPLIED && !protocol.pending);
    receive_phase(PDN_PHASE_CONTROLLER,3);assert(apply_phase()==0 && protocol.phase==PDN_PHASE_CONTROLLER);
}
static void test_reconfiguration_and_old_ep0_cannot_change_session(void){
    struct DeviceRequest setup={0x21,9,0x0300,1,PDN_FEATURE_SIZE};
    uint8_t old[PDN_FEATURE_SIZE],old_nonce[16],new_nonce[16];
    claimed_fixture();memcpy(old_nonce,protocol.session,16);
    command(old,PDN_PHASE_COPYING,2);
    assert(receive_control(0,1,&setup)==0);memcpy(ctl_buffer,old,64);
    detach(0,0,0);complete(0,0,64);
    assert(!protocol.pending && protocol.sequence==1 && !protocol.host_confirmed);
    assert(!state.host_confirmed && protocol.activity==PDN_ACTIVITY_WAITING);
    assert(!exit_requested && observe_cable(1000000,0x220)==0);
    attach(2,NULL,NULL);assert(!connected && quiesce_link()==0 && connected && !link_reset_pending);
    assert(!protocol.sequence && !protocol.host_confirmed && protocol.phase==PDN_PHASE_STORAGE);
    assert(memcmp(protocol.session,old_nonce,16));memcpy(new_nonce,protocol.session,16);
    receive_message(old);assert(protocol.last_rejection==PDN_ERR_SESSION && !protocol.sequence);
    begin_session();receive_phase(PDN_PHASE_COPYING,2);detach(0,0,0);
    assert(!protocol.pending && protocol.result==PDN_ERR_LINK);
    request_exit(PDN_STOP_USER);attach(2,NULL,NULL);assert(quiesce_link()==0);
    assert(protocol.phase==PDN_PHASE_STORAGE && !protocol.host_confirmed && !protocol.sequence);
    assert(exit_requested && state.exit_requested && memcmp(protocol.session,new_nonce,16));
    begin_session();assert(exit_requested); /* Early cancellation remains recoverable. */
}
static void test_reset_nonce_changes_at_constant_timer(void){
    uint8_t first[16],second[16];fixture();fake_time=7;make_session();
    memcpy(first,protocol.session,16);make_session();memcpy(second,protocol.session,16);
    make_session();assert(memcmp(first,second,16) && memcmp(second,protocol.session,16));
    assert(pdn_read32(first+12)==1 && pdn_read32(second+12)==2 && pdn_read32(protocol.session+12)==3);
}
static void test_held_control_drain_never_acknowledges_old_generation(void){
    struct DeviceRequest setup={0x21,9,0x0300,1,PDN_FEATURE_SIZE};
    uint8_t nonce[16];claimed_fixture();memcpy(nonce,protocol.session,16);
    assert(receive_control(0,1,&setup)==0);command(ctl_buffer,PDN_PHASE_COPYING,2);
    detach(0,0,0);attach(2,NULL,NULL);hold_cancel=1;
    assert(quiesce_link()==-3021 && !connected && link_reset_pending && !protocol.host_confirmed);
    assert(!memcmp(nonce,protocol.session,16));
    complete(0,0,64);assert(!protocol.pending && protocol.sequence==1);
    hold_cancel=0;assert(quiesce_link()==0 && connected && !memcmp(protocol.command,(uint8_t[64]){0},64));
    assert(memcmp(nonce,protocol.session,16) && !protocol.sequence);
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
static void test_input_only_after_claim_and_choice_or_controller(void){
    uint8_t input[8],neutral[8],frame[64],without_note[8];SceCtrlData pad={0,127,127};
    int note_held=0;
    connected_fixture();neutral_pad(neutral);
    gated_pad(&pad,input);assert(!memcmp(input,neutral,8) && input_rearm);
    begin_session();gated_pad(&pad,input);assert(!memcmp(input,neutral,8) && input_rearm);
    progress_message(frame,PDN_ACTIVITY_CHOOSING,PDN_UNIT_UNKNOWN,0,0,2);
    receive_message(frame);assert(apply_phase()==0);
    gated_pad(&pad,input);assert(!input_rearm && !memcmp(input,neutral,8));
    pad.Buttons=PSP_CTRL_CROSS;gated_pad(&pad,input);assert(input[4]&0x10);
    pad.Lx=42;pad.Ly=201;encode_pad(&pad,without_note);pad.Buttons|=PSP_CTRL_NOTE;
    encode_pad(&pad,input);assert(!memcmp(input,without_note,8)); /* No button or axis leak. */
    diagnostic_button(pad.Buttons,&note_held);assert(state.diagnostic_visible==1);
    diagnostic_button(pad.Buttons,&note_held);assert(state.diagnostic_visible==1);
    diagnostic_button(0,&note_held);diagnostic_button(PSP_CTRL_NOTE,&note_held);
    assert(!state.diagnostic_visible && protocol.phase==PDN_PHASE_STORAGE && protocol.sequence==2);
    pad.Buttons=0;pad.Lx=pad.Ly=127;
    progress_message(frame,PDN_ACTIVITY_SYNC_IN,PDN_UNIT_UNKNOWN,0,0,3);
    receive_message(frame);assert(apply_phase()==0);
    pad.Buttons=PSP_CTRL_CROSS;gated_pad(&pad,input);assert(!memcmp(input,neutral,8));
    protocol.phase=PDN_PHASE_COPYING;gated_pad(&pad,input);assert(!memcmp(input,neutral,8));
    protocol.phase=PDN_PHASE_EXIT_SYNC;gated_pad(&pad,input);assert(!memcmp(input,neutral,8));
    protocol.phase=PDN_PHASE_SAFE;gated_pad(&pad,input);assert(!memcmp(input,neutral,8));
    protocol.phase=PDN_PHASE_CONTROLLER;protocol.pending=1;gated_pad(&pad,input);assert(!memcmp(input,neutral,8));
    protocol.pending=0;input_rearm=0;pad.Buttons=PSP_CTRL_START|PSP_CTRL_SELECT;
    gated_pad(&pad,input);assert(!memcmp(input,neutral,8));
}
static void test_exit_request_before_and_after_handshake_never_cuts_storage(void){
    uint8_t input[8],neutral[8];SceCtrlData pad={0,127,127};unsigned before;
    connected_fixture();request_exit(PDN_STOP_USER);before=operation_count;
    begin_session();assert(exit_requested && state.exit_requested && operation_count==before);
    receive_phase(PDN_PHASE_EXIT_SYNC,2);assert(apply_phase()==0);
    receive_phase(PDN_PHASE_SAFE,3);assert(apply_phase()==0);
    assert(protocol.phase==PDN_PHASE_SAFE && operation_count==before && activated && storage_started);
    assert(stop_usb()==-3030); /* SAFE is not Windows eject proof. */
    claimed_fixture();receive_phase(PDN_PHASE_CONTROLLER,2);assert(apply_phase()==0);
    neutral_pad(neutral);gated_pad(&pad,input);assert(!input_rearm);
    pad.Buttons=PSP_CTRL_START|PSP_CTRL_SELECT;
    request_exit(PDN_STOP_USER);gated_pad(&pad,input);
    assert(exit_requested && !memcmp(input,neutral,8));
    /* A still-running emulator retains control after the exit chord. */
    pad.Buttons=PSP_CTRL_CROSS;gated_pad(&pad,input);assert(input[4]&0x10);
    receive_phase(PDN_PHASE_EXIT_SYNC,3);gated_pad(&pad,input);assert(!memcmp(input,neutral,8));
    assert(apply_phase()==0 && protocol.phase==PDN_PHASE_EXIT_SYNC);
    gated_pad(&pad,input);assert(!memcmp(input,neutral,8));
}
static void test_interface_one_and_transfer_buffer_ownership(void){
    struct DeviceRequest status={0xa1,1,0x0300,1,PDN_FEATURE_SIZE};
    struct DeviceRequest alien={0xa1,0xfe,0,0,1};uint8_t first[64];
    connected_fixture();assert(receive_control(0,1,&status)==0 && state.hid_interface==1);
    memcpy(first,ctl_buffer,64);assert(receive_control(0,1,&status)<0 && !memcmp(first,ctl_buffer,64));
    complete(0,0,64);assert(receive_control(0,-1,&status)<0);
    assert(receive_control(0,0,&alien)<0);status.wIndex=0x100;
    assert(receive_control(0,1,&status)<0);
}
static void test_progress_requires_valid_current_phase_and_preserves_storage(void){
    uint8_t msg[64],status[64];uint64_t done=UINT64_C(0x100000123),total=UINT64_C(0x200000456);
    unsigned before;connected_fixture();
    progress_message(msg,PDN_ACTIVITY_INVENTORY,PDN_UNIT_BYTES,done,total,1);
    assert(pdn_protocol_receive(&protocol,msg,64)==PDN_ERR_HANDSHAKE);
    begin_session();before=operation_count;
    progress_message(msg,PDN_ACTIVITY_INVENTORY,PDN_UNIT_BYTES,done,total,2);
    receive_message(msg);assert(protocol.pending && !protocol.total);
    assert(apply_phase()==0 && protocol.completed==done && protocol.total==total);
    assert(protocol.phase==PDN_PHASE_STORAGE && operation_count==before && storage_started && activated);
    pdn_protocol_status(&protocol,status,1,1,0);
    assert(status[44]==PDN_ACTIVITY_INVENTORY && status[45]==PDN_UNIT_BYTES);
    assert(pdn_read64(status+48)==done && pdn_read64(status+56)==total && !status[46] && !status[47]);
    assert(state.completed==done && state.total==total && state.progress_unit==PDN_UNIT_BYTES);
    assert(pdn_protocol_receive(&protocol,msg,64)==PDN_APPLIED && !protocol.pending);
    msg[48]^=1;assert(pdn_protocol_receive(&protocol,msg,64)==PDN_ERR_SEQUENCE);
    progress_message(msg,PDN_ACTIVITY_COPY_GAME,PDN_UNIT_BYTES,0,1,3);
    assert(pdn_protocol_receive(&protocol,msg,64)==PDN_ERR_PROGRESS);
    progress_message(msg,PDN_ACTIVITY_SYNC_IN,PDN_UNIT_BYTES,2,1,3);
    assert(pdn_protocol_receive(&protocol,msg,64)==PDN_ERR_PROGRESS);
    progress_message(msg,PDN_ACTIVITY_SYNC_IN,PDN_UNIT_UNKNOWN,0,1,3);
    assert(pdn_protocol_receive(&protocol,msg,64)==PDN_ERR_PROGRESS);
    progress_message(msg,PDN_ACTIVITY_SYNC_IN,PDN_UNIT_FILES,0,0,3);
    assert(pdn_protocol_receive(&protocol,msg,64)==PDN_ERR_PROGRESS);
    progress_message(msg,PDN_ACTIVITY_SYNC_IN,PDN_UNIT_UNKNOWN,0,0,3);msg[6]=PDN_PHASE_COPYING;
    assert(pdn_protocol_receive(&protocol,msg,64)==PDN_ERR_PROGRESS);
    msg[6]=PDN_PHASE_STORAGE;receive_message(msg);assert(apply_phase()==0);
    assert(!protocol.completed && !protocol.total && protocol.progress_unit==PDN_UNIT_UNKNOWN);
    receive_phase(PDN_PHASE_CONTROLLER,4);assert(apply_phase()==0 && !protocol.total);
    progress_message(msg,PDN_ACTIVITY_CONTROLLER,PDN_UNIT_UNKNOWN,0,0,5);
    receive_message(msg);assert(apply_phase()==0 && protocol.phase==PDN_PHASE_CONTROLLER);
}
static void test_ep0_diagnostics_distinguish_dispatch_completion_and_parser(void){
    struct DeviceRequest setup={0x21,9,0x0300,1,PDN_FEATURE_SIZE};
    struct DeviceRequest status={0xa1,1,0x0300,1,PDN_FEATURE_SIZE};
    PdnEp0Diagnostic retained;
    uint8_t frame[PDN_FEATURE_SIZE];
    connected_fixture();command(frame,PDN_PHASE_STORAGE,1);frame[5]=PDN_OP_HOST_BEGIN;
    setup.wLength=65;
    assert(receive_control(0,1,&setup)<0 && state.ep0.set_seen==1);
    assert(state.ep0.setup==0x00410921u && state.ep0.value_index==0x00010300u);
    assert(!state.ep0.recv_submissions && state.ep0.drop_reason==PDN_EP0_DROP_SUBMIT_GATE);
    setup.wLength=PDN_FEATURE_SIZE;queue_failure=-88;
    assert(receive_control(0,1,&setup)==-88 && state.ep0.recv_submissions==1);
    assert(state.ep0.submit_result==-88 && state.ep0.drop_reason==PDN_EP0_DROP_SUBMIT_ERROR);
    assert(!state.ep0.completions && !protocol.sequence && !ctl_busy);
    queue_failure=0;
    assert(receive_control(0,1,&setup)==0);memcpy(ctl_buffer,frame,PDN_FEATURE_SIZE);
    complete(0,0,0);
    assert(state.ep0.completions==1 && state.ep0.recvsize==0 && !state.ep0.retcode);
    assert(state.ep0.drop_reason==PDN_EP0_DROP_SIZE && !state.ep0.parser_calls);
    assert(state.ep0.parser_result==INT32_MIN && !protocol.sequence && !protocol.pending);
    assert(!memcmp((const void *)state.ep0.raw,frame,PDN_FEATURE_SIZE));
    assert(receive_control(0,1,&setup)==0);memcpy(ctl_buffer,frame,PDN_FEATURE_SIZE);
    complete(0,-9,PDN_FEATURE_SIZE);
    assert(state.ep0.completions==2 && state.ep0.retcode==-9);
    assert(state.ep0.drop_reason==PDN_EP0_DROP_RETCODE && !state.ep0.parser_calls);
    frame[5]=PDN_OP_STATUS;receive_message(frame);
    assert(state.ep0.parser_calls==1 && state.ep0.parser_result==PDN_ERR_FRAME);
    assert(state.ep0.drop_reason==PDN_EP0_DROP_PARSER && protocol.rejected==1);
    assert(!protocol.sequence && !protocol.pending && state.ep0.raw[5]==PDN_OP_STATUS);
    frame[5]=PDN_OP_HOST_BEGIN;
    assert(receive_control(0,1,&setup)==0);memcpy(ctl_buffer,frame,PDN_FEATURE_SIZE);
    link_generation++;complete(0,0,PDN_FEATURE_SIZE);
    assert(state.ep0.drop_reason==PDN_EP0_DROP_LINK && state.ep0.parser_calls==1);
    assert(state.ep0.request_generation==0 && state.ep0.live_generation==1);
    assert(!(state.ep0.gate_flags&PDN_EP0_SAME_GENERATION) && !protocol.sequence);
    receive_message(frame);
    assert(state.ep0.parser_calls==2 && state.ep0.parser_result==PDN_QUEUED);
    assert(state.ep0.drop_reason==PDN_EP0_DROP_NONE && protocol.sequence==1 && protocol.pending);
    assert(state.ep0.gate_flags&PDN_EP0_PROTOCOL_PENDING);
    assert(!(state.ep0.generation&1u) && !state.apply_count);
    memcpy(&retained,(const void *)&state.ep0,sizeof(retained));
    assert(receive_control(0,1,&status)==0);complete(0,0,PDN_FEATURE_SIZE);
    assert(!memcmp(&retained,(const void *)&state.ep0,sizeof(retained)));
    assert(apply_phase()==0 && state.apply_count==1 && protocol.host_confirmed);
    assert(state.worker_site==PDN_WORKER_COMMIT && activated && storage_started);
}
static void test_protocol_replay_malformed_and_terminal_safe(void){
    uint8_t msg[64],status[64];unsigned i;claimed_fixture();
    command(msg,PDN_PHASE_COPYING,2);msg[8]^=1;
    assert(pdn_protocol_receive(&protocol,msg,64)==PDN_ERR_SESSION && protocol.sequence==1);
    msg[8]^=1;msg[7]=1;assert(pdn_protocol_receive(&protocol,msg,64)==PDN_ERR_FRAME);
    msg[7]=0;assert(pdn_protocol_receive(&protocol,msg,63)==PDN_ERR_FRAME);
    for(i=0;i<5;i++){
        msg[i]^=1;assert(pdn_protocol_receive(&protocol,msg,64)==PDN_ERR_FRAME);msg[i]^=1;
    }
    memcpy(msg,"PDK1",4);
    assert(pdn_protocol_receive(&protocol,msg,64)==PDN_ERR_FRAME && protocol.sequence==1);
    memcpy(msg,"PDK2",4);
    for(i=32;i<44;i++){
        msg[i]^=1;assert(pdn_protocol_receive(&protocol,msg,64)==PDN_ERR_FRAME);msg[i]^=1;
    }
    msg[46]=1;assert(pdn_protocol_receive(&protocol,msg,64)==PDN_ERR_FRAME);msg[46]=0;
    msg[44]=PDN_ACTIVITY_COPY_GAME;
    assert(pdn_protocol_receive(&protocol,msg,64)==PDN_ERR_FRAME);msg[44]=0;
    assert(pdn_protocol_receive(&protocol,msg,64)==PDN_QUEUED);
    command(msg,PDN_PHASE_CONTROLLER,3);assert(pdn_protocol_receive(&protocol,msg,64)==PDN_ERR_BUSY);
    pdn_protocol_apply(&protocol);command(msg,PDN_PHASE_CONTROLLER,2);
    assert(pdn_protocol_receive(&protocol,msg,64)==PDN_ERR_SEQUENCE && protocol.result==PDN_APPLIED);
    command(msg,PDN_PHASE_SAFE,3);assert(pdn_protocol_receive(&protocol,msg,64)==PDN_ERR_TRANSITION);
    command(msg,PDN_PHASE_EXIT_SYNC,3);assert(pdn_protocol_receive(&protocol,msg,64)==PDN_QUEUED);
    pdn_protocol_apply(&protocol);command(msg,PDN_PHASE_SAFE,4);
    assert(pdn_protocol_receive(&protocol,msg,64)==PDN_QUEUED);pdn_protocol_apply(&protocol);
    for(i=0;i<256;i++)if(i!=PDN_PHASE_SAFE){
        command(msg,(uint8_t)i,5);assert(pdn_protocol_receive(&protocol,msg,64)==PDN_ERR_TRANSITION);
    }
    command(msg,PDN_PHASE_STORAGE,5);msg[5]=PDN_OP_HOST_BEGIN;
    assert(pdn_protocol_receive(&protocol,msg,64)==PDN_ERR_HANDSHAKE);
    pdn_protocol_status(&protocol,status,1,1,1);
    assert(!memcmp(status,"PDK2",4) && status[6]==PDN_PHASE_SAFE && status[7]==29);
    assert(pdn_read32(status+24)==4 && pdn_read32(status+28)==PDN_APPLIED && status[44]==PDN_ACTIVITY_SAFE);
    protocol.sequence=UINT32_MAX;command(msg,PDN_PHASE_SAFE,0);
    assert(pdn_protocol_receive(&protocol,msg,64)==PDN_ERR_SEQUENCE);
}
int main(void){
    test_start_order_and_owned_cleanup();test_borrowed_modules_and_bus_survive();
    test_guard_and_bootstrap_failures();test_begin_ack_and_phase_ack_cancel_old_input_first();
    test_0304_omits_pc_activation_and_keeps_mandatory_link_failures_fatal();
    test_sony_registration_and_launcher_revision_remain_required();
    test_identification_and_get_are_read_only();test_reset_nonce_changes_at_constant_timer();
    test_ep0_cpu_copy_keeps_command_until_protocol_reads_it();
    test_reconfiguration_and_old_ep0_cannot_change_session();test_stop_waits_for_real_unplug();
    test_held_control_drain_never_acknowledges_old_generation();
    test_input_only_after_claim_and_choice_or_controller();
    test_exit_request_before_and_after_handshake_never_cuts_storage();
    test_interface_one_and_transfer_buffer_ownership();test_progress_requires_valid_current_phase_and_preserves_storage();
    test_ep0_diagnostics_distinguish_dispatch_completion_and_parser();
    test_protocol_replay_malformed_and_terminal_safe();
    puts("native-dock 0304: EP0 cache, identity, handshake, progress, replay, ownership and async lifecycle PASS");return 0;
}
