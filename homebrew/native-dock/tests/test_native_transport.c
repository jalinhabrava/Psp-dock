/* Actual 0308 transport under asynchronous USB/module doubles. These tests
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
static int submitted_control[2];
static SceCtrlData fake_pad;
static int fake_pad_available,stop_pad_loop_after_delay;
static int fake_usb,fake_bus,fake_storage,fake_hid,fake_registered;
static unsigned fake_time,borrow_mask,load_mask,start_mask,stop_mask,unload_mask;
static unsigned operation_count;
static char operations[64][32];
static int fail_load=-1,fail_start=-1,fail_usb_start,hold_cancel,queue_failure;
static int start_failure_code=-601,suppress_sony_registration;
static int fail_deactivate,fail_stop_storage,detach_after_terminal_apply;
static int override_state_after_load,usb_after_load,storage_after_load,bus_after_load;
static unsigned boot_loads,boot_starts,forbidden_loads;
static void (*cancel_hook)(unsigned);
static void (*delay_hook)(int);
static void (*export_hook)(void);
static int auto_export;
static unsigned export_waits;
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
    assert(!pending[ep]);pending[ep]=r;submitted_control[ep]=r->unkc;
    submissions[ep]++;return 0;
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
int sceKernelDelayThread(int t){
    fake_time+=(unsigned)t;
    if(ui && ui->export_request==PDN_EXPORT_REQUESTED){
        assert(!activated && !storage_started && !hid_started && !registered && !bus_owned);
        assert(!operation_count && (start_mask|borrow_mask)==REQUIRED_MASK);
        export_waits++;
        if(export_hook)export_hook();
        if(auto_export){ui->export_result=PDN_EXPORT_SKIPPED;ui->export_done=1;}
        return 0;
    }
    if(stop_pad_loop_after_delay){running=0;stop_pad_loop_after_delay=0;}
    if(delay_hook)delay_hook(t);
    return 0;
}
int sceKernelSignalSema(int a,int b){
    (void)a;(void)b;
    if(detach_after_terminal_apply && protocol.shutdown_requested){
        detach_after_terminal_apply=0;detach(0,0,0);
    }
    return 0;
}
int sceCtrlPeekBufferPositive(SceCtrlData *p,int n){
    assert(n==1);
    if(!fake_pad_available)return 0;
    *p=fake_pad;fake_pad_available=0;return 1;
}
int sceUsbGetState(void){return fake_usb;}
int sceUsbGetDrvState(const char *n){
    if(!strcmp(n,PSP_USBBUS_DRIVERNAME))return fake_bus;
    if(!strcmp(n,PSP_USBSTOR_DRIVERNAME))return fake_storage;
    if(!strcmp(n,PDN_DRIVER_NAME))return fake_hid;
    return (int)HID_LAB_STORAGE_NOENT;
}
int sceUsbStart(const char *n,int size,void *arg){
    (void)size;(void)arg;log_operation(n);
    assert(export_waits && state.export_done && state.export_request==PDN_EXPORT_REVOKED);
    assert(state.export_result==PDN_EXPORT_READY || state.export_result==PDN_EXPORT_SKIPPED);
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
    if(!strcmp(n,PSP_USBSTOR_DRIVERNAME)){
        if(fail_stop_storage)return -711;
        fake_storage=2;return 0;
    }
    assert(!strcmp(n,PDN_DRIVER_NAME) && !pending[0] && !pending[1]);fake_hid=2;return 0;
}
int sceUsbActivate(int pid){
    assert(pid==PDN_PRODUCT && fake_bus==1 && fake_storage==1 && fake_hid==1);
    log_operation("activate");fake_usb|=0x200;return 0;
}
int sceUsbDeactivate(int pid){
    assert(pid==PDN_PRODUCT &&
           (!(fake_usb&PSP_USB_CABLE_CONNECTED) || protocol.shutdown_requested));
    if(fail_deactivate)return -710;
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
    if(i==PDN_DEPENDENCIES-1 && override_state_after_load){
        fake_usb=usb_after_load;fake_storage=storage_after_load;fake_bus=bus_after_load;
    }
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
    fragment_active=0;fragment_next=0;fragment_token=0;fragment_generation=0;
    memset(fragment_buffer,0,sizeof(fragment_buffer));
    ever_cable=cable_absence_pending=usb_poll_started=0;
    cable_absent_since=last_usb_poll=last_pad_time=hid_reports=0;
    idle_rate=0;neutral_pad(latest_pad);pdn_protocol_init(&protocol,session);
    fake_usb=0;fake_bus=2;fake_storage=(int)HID_LAB_STORAGE_NOENT;fake_hid=2;fake_registered=0;
    session_counter=0;fake_time=1;borrow_mask=load_mask=start_mask=stop_mask=unload_mask=0;
    fail_load=fail_start=-1;fail_usb_start=hold_cancel=queue_failure=0;cancel_hook=NULL;
    start_failure_code=-601;suppress_sony_registration=0;
    fail_deactivate=fail_stop_storage=detach_after_terminal_apply=0;delay_hook=NULL;
    auto_export=1;export_hook=NULL;export_waits=0;
    override_state_after_load=usb_after_load=storage_after_load=bus_after_load=0;
    boot_loads=boot_starts=forbidden_loads=0;
    memset(submitted_control,0,sizeof(submitted_control));
    memset(&fake_pad,0,sizeof(fake_pad));fake_pad_available=stop_pad_loop_after_delay=0;
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
static void shutdown_message(uint8_t out[PDN_FEATURE_SIZE],uint32_t sequence){
    command(out,PDN_PHASE_SAFE,sequence);out[5]=PDN_OP_HOST_SHUTDOWN;
}
static void queue_input(void){
    memset(&pad_req,0,sizeof(pad_req));pad_req.endp=&pdn_endpoints[1];
    pad_req.data=pad_buffer;pad_req.func=pad_done;pad_req.size=8;hid_busy=1;
    assert(sceUsbbdReqSend(&pad_req)==0);
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
static void test_native_start_accepts_passive_cable_with_known_idle_drivers(void){
    const int storage_states[]={HID_LAB_DRIVER_STOPPED,(int)HID_LAB_STORAGE_NOENT};
    const int bus_states[]={HID_LAB_DRIVER_STOPPED,HID_LAB_DRIVER_STARTED};
    unsigned i,j,before;
    for(i=0;i<sizeof(storage_states)/sizeof(storage_states[0]);i++){
        for(j=0;j<sizeof(bus_states)/sizeof(bus_states[0]);j++){
            fixture();fake_usb=PSP_USB_CABLE_CONNECTED;
            fake_storage=storage_states[i];fake_bus=bus_states[j];
            /* The historical HID-only lab still denies a connected cable. */
            assert(!hid_lab_safe_to_start(fake_usb,fake_storage,fake_bus));
            assert(start_usb()==0 && activated && storage_started && hid_started);
            assert(load_mask==REQUIRED_MASK && start_mask==REQUIRED_MASK);
            assert(state.usb_state==PSP_USB_CABLE_CONNECTED);
            assert(fake_usb&PSP_USB_CABLE_CONNECTED);
            assert(!protocol.host_confirmed && !protocol.pending && !protocol.sequence);
            /* Launch permission does not alter the shutdown contract. */
            before=operation_count;
            assert(stop_usb()==-3030 && operation_count==before);
            fake_usb&=~PSP_USB_CABLE_CONNECTED;
            assert(stop_usb()==0 && unload_dependencies()==0);
        }
    }
}
static void test_native_start_rechecks_busy_unknown_and_error_states(void){
    const struct {int usb,storage,bus;} rejected[]={
        {0x200,2,2},{0x002,2,2},{0x202,2,2},
        {0x220,2,2},{0x022,2,2},{0x222,2,2},
        {-1,2,2},{(int)0x80000020u,2,2},
        {0x020,1,2},{0x020,0,2},{0x020,3,2},{0x020,-1,2},
        {0x020,2,0},{0x020,2,3},{0x020,2,-1}
    };
    unsigned after_load,i;
    for(after_load=0;after_load<2;after_load++){
        for(i=0;i<sizeof(rejected)/sizeof(rejected[0]);i++){
            fixture();
            if(after_load){
                fake_usb=PSP_USB_CABLE_CONNECTED;override_state_after_load=1;
                usb_after_load=rejected[i].usb;
                storage_after_load=rejected[i].storage;bus_after_load=rejected[i].bus;
            }else{
                fake_usb=rejected[i].usb;
                fake_storage=rejected[i].storage;fake_bus=rejected[i].bus;
            }
            assert(start_usb()==(after_load?-3003:-3002));
            assert(!activated && !registered && !storage_started && !hid_started);
            assert(!operation_count && load_mask==(after_load?REQUIRED_MASK:0));
            /* A foreign/unknown driver is observed, never stopped here. */
            assert(fake_usb==rejected[i].usb && fake_storage==rejected[i].storage);
            assert(fake_bus==rejected[i].bus && !stop_mask && !unload_mask);
        }
    }
}
static void test_0308_omits_pc_activation_and_keeps_mandatory_link_failures_fatal(void){
    int result,status=-1;SceUID boot;
    /* Reproduce the photographed 0300 failure in the Sony module double. */
    fixture();boot=sceKernelLoadModule("flash0:/kd/usbstorboot.prx",0,NULL);
    assert(boot==FAKE_BOOT_MODULE);
    assert(sceKernelStartModule(boot,0,NULL,&status,NULL)==(int)0x8002013cu);
    assert(boot_loads==1 && boot_starts==1 && !start_mask);
    assert(sceKernelUnloadModule(boot)==boot);

    /* 0308 must reach the normal transport without ever loading that PRX,
     * Pcact, NPDRM, EFlash or usbdevice redirection. */
    fixture();assert(PDN_DEPENDENCIES==4u && PDN_REVISION==0x0308u);
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
    assert(PDN_LAUNCH_MAGIC==0x384e4450u && PDN_LAUNCH_MAGIC!=old_launch.magic);
    assert(module_start(sizeof(old_launch),&old_launch)==-1);
    old_launch.magic=0x314e4450u;
    assert(module_start(sizeof(old_launch),&old_launch)==-1);
    old_launch.magic=0x324e4450u;
    assert(module_start(sizeof(old_launch),&old_launch)==-1);
    old_launch.magic=0x334e4450u;
    assert(module_start(sizeof(old_launch),&old_launch)==-1);
    old_launch.magic=0x344e4450u;
    assert(module_start(sizeof(old_launch),&old_launch)==-1);
    old_launch.magic=0x374e4450u;
    assert(module_start(sizeof(old_launch),&old_launch)==-1);
}
static void test_identification_and_get_are_read_only(void){
    struct DeviceRequest status={0xa1,1,0x0300,1,PDN_FEATURE_SIZE};
    struct DeviceRequest descriptor={0x81,6,0x2200,1,sizeof(report_descriptor)};
    struct DeviceRequest legacy_set={0x21,9,0x0300,1,32};
    const uint8_t identity[12]={'N','A','T','3',8,3,15,0,1,0,8,3};
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
static void test_cancelled_complete_host_begin_cannot_claim_session(void){
    /* HOST_BEGIN captured from the 0304 diagnostic: recvsize=64 but
     * retcode=-3 (cancelled). Retain that evidence, then update only its
     * firmware identity for this compiled revision so a parser rejection
     * of an old build cannot make the cancellation guard pass spuriously. */
    const uint8_t capture_0304[PDN_FEATURE_SIZE]={
        0x50,0x44,0x4b,0x32,0x03,0x03,0x01,0x00,
        0x0b,0xb2,0xe5,0x97,0x2b,0x00,0xa3,0xb5,
        0x47,0x69,0xe2,0xe0,0x01,0x00,0x00,0x00,
        0x01,0x00,0x00,0x00,0x00,0x00,0x00,0x00,
        0x4e,0x41,0x54,0x33,0x04,0x03,0x07,0x00,
        0x01,0x00,0x04,0x03,0x00,0x00,0x00,0x00,
        0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,
        0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00
    };
    struct DeviceRequest setup={0x21,9,0x0300,1,PDN_FEATURE_SIZE};
    struct DeviceRequest status={0xa1,1,0x0300,1,PDN_FEATURE_SIZE};
    uint8_t captured[PDN_FEATURE_SIZE];
    PdnEp0Diagnostic retained;
    unsigned reads;
    memcpy(captured,capture_0304,sizeof(captured));
    memcpy(captured+32,pdn_identity,sizeof(pdn_identity));
    connected_fixture();pdn_protocol_init(&protocol,captured+8);
    assert(receive_control(0,1,&setup)==0 && pending[0]);
    memcpy(ctl_buffer,captured,sizeof(captured));complete(0,-3,sizeof(captured));
    assert(!pending[0] && !ctl_busy && !ctl_submitting);
    assert(state.ep0.submit_result==0 && state.ep0.completions==1);
    assert(state.ep0.recvsize==64 && state.ep0.retcode==-3);
    assert(state.ep0.drop_reason==PDN_EP0_DROP_RETCODE && !state.ep0.parser_calls);
    assert(state.ep0.parser_result==INT32_MIN);
    assert(!memcmp((const void *)state.ep0.raw,captured,sizeof(captured)));
    memcpy(&retained,(const void *)&state.ep0,sizeof(retained));
    for(reads=0;reads<3;reads++){
        assert(apply_phase()==0 && !state.apply_count);
        assert(!protocol.pending && !protocol.sequence && !protocol.result);
        assert(!protocol.host_confirmed && protocol.phase==PDN_PHASE_STORAGE);
        assert(receive_control(0,1,&status)==0);
        assert(!(ctl_buffer[7]&PDN_FLAG_HOST_CONFIRMED));
        assert(!pdn_read32(ctl_buffer+24) && !pdn_read32(ctl_buffer+28));
        complete(0,0,PDN_FEATURE_SIZE);
    }
    assert(!memcmp(&retained,(const void *)&state.ep0,sizeof(retained)));
    assert(active && connected && activated && storage_started && !cancels[0]);
    /* A separately completed transfer, with the same still-unconsumed
     * sequence and session, is valid. This proves the guard above rejected
     * cancellation, rather than a malformed payload or stale session. */
    receive_message(captured);
    assert(state.ep0.parser_calls==1 && protocol.pending && !protocol.host_confirmed);
    assert(apply_phase()==0 && protocol.host_confirmed && protocol.sequence==1);
    assert(protocol.result==PDN_APPLIED && state.apply_count==1);
}
static void test_control_request_flag_at_bus_submission(void){
    /* Submission contract, not an invented USBBus completion model:
     * PSP SDK calls this field unkc; xerpi's usb.h names the same slot
     * isControlRequest. Sony usbacc initializes it to 1 and reuses the
     * request for EP0 Recv and Send. Interrupt IN remains non-control.
     * All completions below are supplied independently of this flag. */
    struct DeviceRequest status={0xa1,1,0x0300,1,PDN_FEATURE_SIZE};
    struct DeviceRequest output={0x21,9,0x0200,1,4};
    struct DeviceRequest feature={0x21,9,0x0300,1,PDN_FEATURE_SIZE};
    uint8_t frame[PDN_FEATURE_SIZE],neutral[8];
    connected_fixture();
    assert(receive_control(0,1,&status)==0 && pending[0]);
    assert(pending[0]->size==64 && submitted_control[0]==1);
    assert(ctl_buffer[5]==PDN_OP_STATUS);complete(0,0,64);

    assert(receive_control(0,1,&output)==0 && pending[0]);
    assert(pending[0]->size==4 && submitted_control[0]==1);
    memcpy(ctl_buffer,(uint8_t[]){0x50,0x44,0x4b,0x32},4);complete(0,0,4);
    assert(!protocol.sequence && !protocol.host_confirmed && !protocol.pending);

    command(frame,PDN_PHASE_STORAGE,1);frame[5]=PDN_OP_HOST_BEGIN;
    assert(receive_control(0,1,&feature)==0 && pending[0]);
    assert(pending[0]->size==64 && submitted_control[0]==1);
    memcpy(ctl_buffer,frame,sizeof(frame));complete(0,0,sizeof(frame));
    assert(protocol.pending && !protocol.host_confirmed);
    assert(apply_phase()==0 && protocol.host_confirmed && protocol.sequence==1);

    /* Execute the actual sampler once. Inventory still gates buttons,
     * but its neutral report uses the real interrupt request setup. */
    fake_pad=(SceCtrlData){PSP_CTRL_CROSS,127,127};fake_pad_available=1;
    running=1;stop_pad_loop_after_delay=1;
    assert(sample_pad(0,NULL)==0 && !running && pending[1]);
    assert(pending[1]->size==8 && submitted_control[1]==0);
    neutral_pad(neutral);assert(!memcmp(pending[1]->data,neutral,sizeof(neutral)));
    complete(1,0,8);assert(!hid_busy && hid_reports==1);
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
static void test_persistent_kernel_switches_do_not_lock_gamepad_rearm(void){
    const uint32_t switches=0x030c0000u; /* MS, DISC, REMOTE, WLAN_UP */
    uint8_t input[8],neutral[8],frame[64];SceCtrlData pad={switches,127,127};
    claimed_fixture();neutral_pad(neutral);
    progress_message(frame,PDN_ACTIVITY_CHOOSING,PDN_UNIT_UNKNOWN,0,0,2);
    receive_message(frame);assert(apply_phase()==0 && input_rearm);
    gated_pad(&pad,input);assert(!input_rearm && !memcmp(input,neutral,8));
    pad.Buttons=switches|PSP_CTRL_CROSS;gated_pad(&pad,input);assert(input[4]&0x10);
    receive_phase(PDN_PHASE_CONTROLLER,3);assert(apply_phase()==0 && input_rearm);
    gated_pad(&pad,input);assert(input_rearm && !memcmp(input,neutral,8));
    pad.Buttons=switches|0x00020000u;gated_pad(&pad,input);
    assert(input_rearm && !memcmp(input,neutral,8)); /* HOLD remains a lock. */
    pad.Buttons=switches;pad.Lx=30;gated_pad(&pad,input);assert(input_rearm);
    pad.Lx=127;gated_pad(&pad,input);assert(!input_rearm);
    pad.Buttons=switches|PSP_CTRL_RIGHT|PSP_CTRL_CIRCLE;gated_pad(&pad,input);
    assert((input[4]&15)==3 && (input[4]&0x20));
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
static void test_shutdown_command_keeps_strict_frame_and_state_guards(void){
    uint8_t frame[64],bad[64];unsigned i,claimed,phase;
    connected_fixture();shutdown_message(frame,1);
    assert(pdn_protocol_receive(&protocol,frame,63)==PDN_ERR_FRAME);
    assert(pdn_protocol_receive(&protocol,frame,65)==PDN_ERR_FRAME);
    for(i=0;i<64;i++){
        if((i>=5 && i<=6) || (i>=8 && i<28))continue;
        memcpy(bad,frame,64);bad[i]^=1;
        assert(pdn_protocol_receive(&protocol,bad,64)==PDN_ERR_FRAME);
    }
    memcpy(bad,frame,64);bad[8]^=1;
    assert(pdn_protocol_receive(&protocol,bad,64)==PDN_ERR_SESSION);
    memcpy(bad,frame,64);pdn_write32(bad+24,0);
    assert(pdn_protocol_receive(&protocol,bad,64)==PDN_ERR_SEQUENCE);
    pdn_write32(bad+24,2);
    assert(pdn_protocol_receive(&protocol,bad,64)==PDN_ERR_SEQUENCE);
    memcpy(bad,frame,64);bad[5]=6;
    assert(pdn_protocol_receive(&protocol,bad,64)==PDN_ERR_FRAME);
    memcpy(bad,frame,64);bad[6]=PDN_PHASE_STORAGE;
    assert(pdn_protocol_receive(&protocol,bad,64)==PDN_ERR_TRANSITION);
    assert(!protocol.sequence && !protocol.pending && !protocol.shutdown_requested);
    for(claimed=0;claimed<2;claimed++)for(phase=0;phase<256;phase++){
        int permitted=(phase==PDN_PHASE_STORAGE && !claimed) ||
                      (phase==PDN_PHASE_SAFE && claimed) || phase==PDN_PHASE_ERROR;
        connected_fixture();protocol.host_confirmed=(uint8_t)claimed;protocol.phase=(uint8_t)phase;
        shutdown_message(frame,1);
        assert(pdn_protocol_receive(&protocol,frame,64)==(permitted?PDN_QUEUED:PDN_ERR_TRANSITION));
        assert(!protocol.shutdown_requested);
        if(!permitted)assert(!protocol.sequence && !protocol.pending);
    }
    claimed_fixture();receive_phase(PDN_PHASE_COPYING,2);
    shutdown_message(frame,3);
    assert(pdn_protocol_receive(&protocol,frame,64)==PDN_ERR_BUSY && !protocol.shutdown_requested);
    connected_fixture();protocol.sequence=UINT32_MAX;shutdown_message(frame,0);
    assert(pdn_protocol_receive(&protocol,frame,64)==PDN_ERR_SEQUENCE);
}
static void test_shutdown_is_terminal_only_after_apply_and_does_not_claim_host(void){
    const struct {uint8_t phase,claimed;} allowed[]={
        {PDN_PHASE_STORAGE,0},{PDN_PHASE_SAFE,1},{PDN_PHASE_ERROR,0},{PDN_PHASE_ERROR,1}
    };
    uint8_t frame[64],other[64],status[64];unsigned i,op,before;
    const uint8_t abi[17]={'P','D','N','A',3,64,8,4,1,2,3,4,127,1,1,0,5};
    assert(sizeof(pdn_protocol_abi)==sizeof(abi) && !memcmp(pdn_protocol_abi,abi,sizeof(abi)));
    assert(PDN_CAPABILITIES==15 && sizeof(PdnSharedStatus)==320 && sizeof(PdnProtocol)==120);
    for(i=0;i<sizeof(allowed)/sizeof(allowed[0]);i++){
        connected_fixture();protocol.phase=allowed[i].phase;protocol.host_confirmed=allowed[i].claimed;
        shutdown_message(frame,1);receive_message(frame);before=operation_count;
        assert(protocol.pending && !protocol.shutdown_requested);
        assert(pdn_protocol_receive(&protocol,frame,64)==PDN_QUEUED);
        assert(stop_usb()==-3030 && operation_count==before);
        assert(apply_phase()==0 && protocol.shutdown_requested && !protocol.pending);
        assert(protocol.host_confirmed==allowed[i].claimed && state.host_session_seen==allowed[i].claimed);
        assert(protocol.phase==PDN_PHASE_SAFE && protocol.result==PDN_APPLIED);
        assert(protocol.activity==(allowed[i].claimed?PDN_ACTIVITY_SAFE:PDN_ACTIVITY_WAITING));
        assert(state.stop_reason==PDN_STOP_HOST && !state.safe_to_unload);
        pdn_protocol_status(&protocol,status,1,1,0);
        assert(status[7]==(uint8_t)(37|(allowed[i].claimed?16:0)));
        assert(pdn_read32(status+24)==1 && pdn_read32(status+28)==PDN_APPLIED);
        assert(pdn_protocol_receive(&protocol,frame,64)==PDN_APPLIED);
        assert(apply_phase()==0 && state.apply_count==1);
        memcpy(other,frame,64);other[6]=PDN_PHASE_STORAGE;
        assert(pdn_protocol_receive(&protocol,other,64)==PDN_ERR_SEQUENCE);
        for(op=PDN_OP_SET_PHASE;op<=PDN_OP_HOST_SHUTDOWN;op++){
            memcpy(other,frame,64);other[5]=(uint8_t)op;pdn_write32(other+24,2);
            assert(pdn_protocol_receive(&protocol,other,64)==PDN_ERR_TRANSITION);
        }
        assert(protocol.shutdown_requested && protocol.sequence==1 && protocol.result==PDN_APPLIED);
        /* An external StopModule still cannot bypass the worker with live USB. */
        assert(module_stop(0,NULL)==-3030 && activated);
        assert(stop_usb()==0 && unload_dependencies()==0 && !activated && !storage_started);
    }
}
static void detach_on_input_cancel(unsigned ep){if(ep==1)detach(0,0,0);}
static void test_shutdown_requires_current_complete_transfer_and_input_drain(void){
    struct DeviceRequest setup={0x21,9,0x0300,1,64};
    uint8_t frame[64],nonce[16];
    connected_fixture();shutdown_message(frame,1);
    assert(receive_control(0,1,&setup)==0);memcpy(ctl_buffer,frame,64);
    assert(apply_phase()==0 && !protocol.pending && !protocol.shutdown_requested);
    complete(0,-3,64);
    assert(!protocol.shutdown_requested && !protocol.pending && !state.ep0.parser_calls);
    assert(stop_usb()==-3030 && !state.safe_to_unload);
    receive_message(frame);ctl_submitting=1;
    assert(apply_phase()==0 && protocol.pending && !protocol.shutdown_requested);
    ctl_submitting=0;queue_input();hold_cancel=1;
    assert(apply_phase()==-3023 && hid_busy && protocol.pending && !protocol.shutdown_requested);
    assert(stop_usb()==-3030 && !state.safe_to_unload);
    complete(1,-3,0);hold_cancel=0;
    assert(apply_phase()==0 && protocol.shutdown_requested && !hid_busy);

    connected_fixture();shutdown_message(frame,1);
    assert(receive_control(0,1,&setup)==0);memcpy(ctl_buffer,frame,64);
    detach(0,0,0);complete(0,0,64);
    assert(!protocol.shutdown_requested && !protocol.pending && !protocol.sequence);
    assert(stop_usb()==-3030);

    connected_fixture();shutdown_message(frame,1);receive_message(frame);queue_input();
    cancel_hook=detach_on_input_cancel;
    assert(apply_phase()==0 && !protocol.shutdown_requested && !protocol.pending);
    assert(protocol.result==PDN_ERR_LINK && stop_usb()==-3030);

    connected_fixture();shutdown_message(frame,1);receive_message(frame);
    assert(apply_phase()==0 && protocol.shutdown_requested);
    memcpy(nonce,protocol.session,16);detach(0,0,0);attach(2,NULL,NULL);
    assert(quiesce_link()==0 && protocol.shutdown_requested && protocol.sequence==1);
    assert(!memcmp(nonce,protocol.session,16) && link_reset_pending);
    assert(stop_usb()==0 && unload_dependencies()==0);
}
static unsigned worker_test_tick,worker_test_mode;
static void worker_shutdown_delivery(int delay){
    uint8_t frame[64];
    if(delay!=4000)return;
    assert(++worker_test_tick<=4); /* Bound the real worker if a regression hangs it. */
    if(worker_test_tick==1){
        fake_usb=0x222;assert(attach(2,NULL,NULL)==0);
        if(worker_test_mode==1){
            begin_session();receive_phase(PDN_PHASE_EXIT_SYNC,2);assert(apply_phase()==0);
            receive_phase(PDN_PHASE_SAFE,3);assert(apply_phase()==0);
        }else if(worker_test_mode==2 || worker_test_mode==4){
            fake_usb=-55;fake_time+=50000;return;
        }else if(worker_test_mode==3){
            begin_session();receive_phase(PDN_PHASE_COPYING,2);
            fake_usb=-55;fake_time+=50000;return;
        }else if(worker_test_mode==5){
            queue_input();hold_cancel=1;
        }
    }else if(worker_test_mode==4 && worker_test_tick==3){
        assert(protocol.pending && protocol.result==PDN_QUEUED && !protocol.shutdown_requested);
        ctl_submitting=0;return;
    }else{
        assert(worker_test_tick==2 && worker_test_mode>=2);
        assert(protocol.phase==PDN_PHASE_ERROR && !protocol.shutdown_requested);
        assert(state.error==(worker_test_mode==5?-3023:-55));
        if(worker_test_mode==3)
            assert(!protocol.pending && protocol.result==PDN_ERR_LINK && state.apply_count==1);
        if(worker_test_mode==5){
            assert(!protocol.pending && protocol.result==PDN_ERR_LINK && !state.apply_count);
            assert(activated && storage_started && !state.safe_to_unload);
            hold_cancel=0;complete(1,-3,0);
            /* The test host now sends a new explicit terminal request;
             * the worker did not apply or replay the failed one. */
        }
        fake_usb=0x222;
    }
    shutdown_message(frame,protocol.sequence+1);receive_message(frame);
    if(worker_test_mode==4)ctl_submitting=1;
}
static void test_worker_closes_released_media_with_cable_and_preserves_failures(void){
    unsigned mode,fail;
    for(mode=0;mode<6;mode++){
        fixture();fake_usb=PSP_USB_CABLE_CONNECTED;running=1;
        worker_test_tick=0;worker_test_mode=mode;delay_hook=worker_shutdown_delivery;
        /* Interrupt immediately after apply, before the worker's next test. */
        detach_after_terminal_apply=1;
        assert(io_worker(0,NULL)==0);
        assert(protocol.shutdown_requested && !running && !activated && !storage_started);
        assert(!registered && !bus_owned && !hid_busy && !ctl_busy && !ctl_submitting);
        assert(state.safe_to_unload && unload_mask==REQUIRED_MASK && state.stop_reason==PDN_STOP_HOST);
        assert(fake_usb&PSP_USB_CABLE_CONNECTED);
        assert(state.detach_count==1 && session_counter==1); /* No replacement nonce after release. */
        if(mode==0 || mode==2 || mode==4 || mode==5){
            assert(!protocol.host_confirmed && !state.host_session_seen);
            assert(protocol.activity==PDN_ACTIVITY_WAITING && protocol.sequence==(mode==5?2u:1u));
        }
        if(mode>=2)assert(state.error==(mode==5?-3023:-55) && state.stage==PDN_STAGE_ERROR);
        else assert(!state.error && state.stage==PDN_STAGE_STOPPED);
    }
    for(fail=0;fail<2;fail++){
        fixture();fake_usb=PSP_USB_CABLE_CONNECTED;running=1;
        worker_test_tick=worker_test_mode=0;delay_hook=worker_shutdown_delivery;
        fail_deactivate=!fail;fail_stop_storage=!!fail;
        assert(io_worker(0,NULL)==0 && protocol.shutdown_requested);
        assert(!state.safe_to_unload && state.stage==PDN_STAGE_ERROR && !unload_mask);
        assert(state.error==(fail?-711:-710) && storage_started);
        assert(!running && (fake_usb&PSP_USB_CABLE_CONNECTED));
    }
}
static void receive_fragment(const uint8_t frame[64],unsigned index,uint8_t token,int code,int size){
    struct DeviceRequest output={0x21,9,0x0200,1,4};
    assert(index<32 && receive_control(0,1,&output)==0 && pending[0]);
    assert(pending[0]->size==4);
    ctl_buffer[0]=(uint8_t)(0xc0u|index);ctl_buffer[1]=token;
    ctl_buffer[2]=frame[index*2];ctl_buffer[3]=frame[index*2+1];
    complete(0,code,size);
}
static void fragment_commit(const uint8_t frame[64],uint8_t token,int corrupt){
    struct DeviceRequest output={0x21,9,0x0200,1,4};
    uint16_t crc=fragment_crc16(frame,64);
    assert(receive_control(0,1,&output)==0);
    ctl_buffer[0]=0xe0;ctl_buffer[1]=token;
    ctl_buffer[2]=(uint8_t)crc;ctl_buffer[3]=(uint8_t)((crc>>8)^(corrupt?1:0));
    complete(0,0,4);
}
static void fragmented_message(const uint8_t frame[64]){
    unsigned index;
    for(index=0;index<32;index++)receive_fragment(frame,index,91,0,4);
    fragment_commit(frame,91,0);
}
static void test_short_reports_preserve_the_full_protocol_and_commit_last(void){
    uint8_t frame[64];unsigned index;
    struct DeviceRequest get={0xa1,1,0x0300,1,64};
    connected_fixture();model_ep0_cache=1;
    command(frame,PDN_PHASE_STORAGE,1);frame[5]=PDN_OP_HOST_BEGIN;
    assert(fragment_crc16((const uint8_t *)"123456789",9)==0x29b1u);
    for(index=0;index<32;index++){
        receive_fragment(frame,index,91,0,4);
        assert(!protocol.sequence && !protocol.pending && !protocol.host_confirmed);
        /* Read-only polls between fragments cannot reset their assembly. */
        assert(receive_control(0,1,&get)==0);complete(0,0,64);
    }
    assert(!state.ep0.parser_calls && !state.apply_count);
    fragment_commit(frame,91,0);
    assert(protocol.pending && protocol.sequence==1 && !protocol.host_confirmed);
    assert(!memcmp(protocol.command,frame,64) && state.ep0.recvsize==4 && !state.ep0.retcode);
    assert(state.ep0.parser_calls==1 && !discarded_cpu_copies);
    assert(apply_phase()==0 && protocol.host_confirmed && protocol.result==PDN_APPLIED);
    fragmented_message(frame);assert(!protocol.pending && protocol.sequence==1 && state.apply_count==1);
    command(frame,PDN_PHASE_EXIT_SYNC,2);fragmented_message(frame);assert(apply_phase()==0);
    command(frame,PDN_PHASE_SAFE,3);fragmented_message(frame);assert(apply_phase()==0);
    shutdown_message(frame,4);fragmented_message(frame);
    assert(!protocol.shutdown_requested && protocol.pending);
    assert(apply_phase()==0 && protocol.shutdown_requested);
    assert(stop_usb()==0 && unload_dependencies()==0 && (fake_usb&PSP_USB_CABLE_CONNECTED));
}
static void test_fragmented_abort_mix_corruption_and_nonce_never_act(void){
    uint8_t frame[64];unsigned index,scenario;
    for(scenario=0;scenario<7;scenario++){
        connected_fixture();command(frame,PDN_PHASE_STORAGE,1);frame[5]=PDN_OP_HOST_BEGIN;
        for(index=0;index<32;index++){
            if(scenario==0 && index==12)continue; /* Missing fragment. */
            receive_fragment(frame,index,(uint8_t)(scenario==1 && index==12?92:91),
                scenario==2 && index==12?-3:0,scenario==3 && index==12?3:4);
            if(scenario==4 && index==12){detach(0,0,0);attach(2,NULL,NULL);assert(quiesce_link()==0);}
            if(scenario==5 && index==12)receive_fragment(frame,index,91,0,4); /* duplicate */
            assert(!protocol.pending && !protocol.sequence && !protocol.host_confirmed);
        }
        fragment_commit(frame,91,scenario==6);
        assert(!protocol.pending && !protocol.sequence && !protocol.host_confirmed);
        assert(apply_phase()==0 && !state.apply_count && stop_usb()==-3030);
    }
    connected_fixture();command(frame,PDN_PHASE_STORAGE,1);frame[5]=PDN_OP_HOST_BEGIN;frame[8]^=1;
    fragmented_message(frame);assert(protocol.last_rejection==PDN_ERR_SESSION && !protocol.sequence);
    frame[8]^=1;fragmented_message(frame);assert(protocol.pending && apply_phase()==0 && protocol.host_confirmed);
    /* Error before the very first handshake can still close once host has
     * released media: the same complete terminal frame, not a fake claim. */
    connected_fixture();shutdown_message(frame,1);fragmented_message(frame);
    assert(!protocol.host_confirmed && apply_phase()==0 && protocol.shutdown_requested);
    assert(!protocol.host_confirmed && stop_usb()==0 && unload_dependencies()==0);
}
static void test_output_diagnostic_never_executes_or_discards_a_partial_frame(void){
    struct DeviceRequest get={0xa1,1,0x0200,1,4};
    PdnEp0Diagnostic before;uint8_t frame[64];
    connected_fixture();command(frame,PDN_PHASE_STORAGE,1);frame[5]=PDN_OP_HOST_BEGIN;
    receive_fragment(frame,0,91,0,4);before=state.ep0;
    assert(receive_control(0,1,&get)==0 && pending[0]->size==4);
    assert(ctl_buffer[0]==PDN_EP0_ASSEMBLING && ctl_buffer[1]==4 && ctl_buffer[2]==0 && ctl_buffer[3]==128);
    complete(0,0,4);assert(!memcmp(&before,(const void *)&state.ep0,sizeof(before)));
    assert(fragment_active && fragment_next==1 && !protocol.sequence && !protocol.pending);
}
static void export_unsafe(void){state.export_result=PDN_EXPORT_UNSAFE;state.export_done=1;}
static void export_unknown(void){state.export_result=99;state.export_done=1;}
static void export_cancel(void){state.request_stop=1;}
static void export_foreign_usb(void){
    state.export_result=PDN_EXPORT_READY;state.export_done=1;fake_usb=0x222;
}
static void export_complete_after_delay(void){
    assert(!activated && !storage_started && !registered && !operation_count);
    if(export_waits==4){state.export_result=PDN_EXPORT_READY;state.export_done=1;}
}
static void test_resource_export_gate_waits_fails_closed_and_rechecks(void){
    fixture();auto_export=0;export_hook=export_complete_after_delay;
    assert(start_usb()==0 && activated && export_waits==4);
    assert(state.export_result==PDN_EXPORT_READY && state.export_request==PDN_EXPORT_REVOKED);
    assert(stop_usb()==0 && unload_dependencies()==0);
    fixture();auto_export=0;
    assert(start_usb()==-3051 && export_waits==PDN_EXPORT_WAIT_US/10000u);
    assert(state.export_request==PDN_EXPORT_REVOKED && !state.export_done && !operation_count);
    assert(!activated && !registered && !storage_started && !hid_started && !bus_owned);
    /* A late USER completion after timeout cannot restart this invocation. */
    state.export_result=PDN_EXPORT_READY;state.export_done=1;
    assert(!operation_count && stop_usb()==0 && unload_dependencies()==0);
    fixture();auto_export=0;export_hook=export_unsafe;
    assert(start_usb()==-3053 && !operation_count && !activated && !storage_started);
    assert(stop_usb()==0 && unload_dependencies()==0);
    fixture();auto_export=0;export_hook=export_unknown;
    assert(start_usb()==-3053 && !operation_count);
    fixture();auto_export=0;export_hook=export_cancel;
    assert(start_usb()==0 && !activated && !operation_count && exit_requested);
    assert(state.export_request==PDN_EXPORT_REVOKED && stop_usb()==0 && unload_dependencies()==0);
    fixture();auto_export=0;export_hook=export_foreign_usb;
    assert(start_usb()==-3054 && !activated && !storage_started && !operation_count);
    fixture();fake_storage=1;
    assert(start_usb()==-3002 && !export_waits && state.export_request==PDN_EXPORT_NOT_REQUESTED);
}
static void test_resource_ready_status_requires_completed_export_this_launch(void){
    struct DeviceRequest status={0xa1,1,0x0300,1,PDN_FEATURE_SIZE};
    const int not_ready[]={PDN_EXPORT_PENDING,PDN_EXPORT_SKIPPED,PDN_EXPORT_UNSAFE,99};
    uint8_t previous_session[16],old_command[PDN_FEATURE_SIZE];unsigned i;
    connected_fixture();
    /* SKIPPED can leave a prior console's file intact on the card. GET_REPORT
     * must not infer its origin from existence, nor from an unfinished result. */
    assert(state.export_done && state.export_result==PDN_EXPORT_SKIPPED);
    for(i=0;i<sizeof(not_ready)/sizeof(not_ready[0]);i++){
        state.export_result=not_ready[i];
        assert(receive_control(0,1,&status)==0 && pending[0]->size==PDN_FEATURE_SIZE);
        assert(!(ctl_buffer[7]&PDN_FLAG_UI_SOUNDS_READY));complete(0,0,PDN_FEATURE_SIZE);
    }
    state.export_result=PDN_EXPORT_READY;state.export_done=0;
    assert(receive_control(0,1,&status)==0 && !(ctl_buffer[7]&PDN_FLAG_UI_SOUNDS_READY));
    complete(0,0,PDN_FEATURE_SIZE);
    state.export_done=1;
    assert(receive_control(0,1,&status)==0 && (ctl_buffer[7]&PDN_FLAG_UI_SOUNDS_READY));
    assert(!memcmp(ctl_buffer+8,protocol.session,16) && !protocol.sequence && !protocol.host_confirmed);
    memcpy(previous_session,ctl_buffer+8,16);complete(0,0,PDN_FEATURE_SIZE);
    command(old_command,PDN_PHASE_STORAGE,1);old_command[5]=PDN_OP_HOST_BEGIN;
    make_session();assert(memcmp(previous_session,protocol.session,16));
    assert(pdn_protocol_receive(&protocol,old_command,PDN_FEATURE_SIZE)==PDN_ERR_SESSION);
    assert(receive_control(0,1,&status)==0 && (ctl_buffer[7]&PDN_FLAG_UI_SOUNDS_READY));
    assert(!memcmp(ctl_buffer+8,protocol.session,16));complete(0,0,PDN_FEATURE_SIZE);
    /* A new startup clears even stale RAM acknowledgement before the gate;
     * the default USER double now skips and cannot reuse a previous READY. */
    fixture();state.export_done=1;state.export_result=PDN_EXPORT_READY;
    assert(start_usb()==0 && state.export_done && state.export_result==PDN_EXPORT_SKIPPED);
    fake_usb=0x222;assert(attach(2,NULL,NULL)==0 && connected);
    assert(receive_control(0,1,&status)==0 && !(ctl_buffer[7]&PDN_FLAG_UI_SOUNDS_READY));
    complete(0,0,PDN_FEATURE_SIZE);
}
int main(void){
    test_start_order_and_owned_cleanup();test_borrowed_modules_and_bus_survive();
    test_guard_and_bootstrap_failures();test_begin_ack_and_phase_ack_cancel_old_input_first();
    test_native_start_accepts_passive_cable_with_known_idle_drivers();
    test_native_start_rechecks_busy_unknown_and_error_states();
    test_0308_omits_pc_activation_and_keeps_mandatory_link_failures_fatal();
    test_sony_registration_and_launcher_revision_remain_required();
    test_identification_and_get_are_read_only();test_reset_nonce_changes_at_constant_timer();
    test_ep0_cpu_copy_keeps_command_until_protocol_reads_it();
    test_cancelled_complete_host_begin_cannot_claim_session();
    test_control_request_flag_at_bus_submission();
    test_reconfiguration_and_old_ep0_cannot_change_session();test_stop_waits_for_real_unplug();
    test_held_control_drain_never_acknowledges_old_generation();
    test_input_only_after_claim_and_choice_or_controller();
    test_persistent_kernel_switches_do_not_lock_gamepad_rearm();
    test_exit_request_before_and_after_handshake_never_cuts_storage();
    test_interface_one_and_transfer_buffer_ownership();test_progress_requires_valid_current_phase_and_preserves_storage();
    test_ep0_diagnostics_distinguish_dispatch_completion_and_parser();
    test_protocol_replay_malformed_and_terminal_safe();
    test_shutdown_command_keeps_strict_frame_and_state_guards();
    test_shutdown_is_terminal_only_after_apply_and_does_not_claim_host();
    test_shutdown_requires_current_complete_transfer_and_input_drain();
    test_worker_closes_released_media_with_cable_and_preserves_failures();
    test_short_reports_preserve_the_full_protocol_and_commit_last();
    test_fragmented_abort_mix_corruption_and_nonce_never_act();
    test_output_diagnostic_never_executes_or_discards_a_partial_frame();
    test_resource_export_gate_waits_fails_closed_and_rechecks();
    test_resource_ready_status_requires_completed_export_this_launch();
    puts("native-dock 0308: optional resource gate, idle recheck, EP0, identity, handshake, progress, replay and lifecycle PASS");return 0;
}
