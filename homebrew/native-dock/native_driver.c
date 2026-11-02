/* SPDX-License-Identifier: GPL-3.0-only
 * PSP Dock 0300: native Sony USBSTOR owns the Memory Stick; this module
 * contributes HID and a session-bound phase channel to the USB bus.
 * No filesystem, raw-sector, Sony-driver patch or flash-write API is used.
 * HID mapping/descriptor derived from FuSa GAMEPAD, Andy_maN, upstream
 * 97a1fccb8cb561b14964de4d4dbab4fcd8618297. See README and GPL license.
 */
#include <pspkernel.h>
#include <pspctrl.h>
#include <pspusb.h>
#include <pspusbstor.h>
#include <pspusbbus.h>
#include <pspintrman.h>
#include <pspthreadman.h>
#include <psploadcore.h>
#include <pspmodulemgr.h>
#include <string.h>
#include "native_common.h"
#include "native_descriptors.h"
#include "lab_gate.h"

PSP_MODULE_INFO("PSPDockNative",PSP_MODULE_KERNEL,3,0);

typedef struct {
    const char *path,*name;
    SceUID id;
    int owned,started;
} NativeDependency;
static NativeDependency dependencies[PDN_DEPENDENCIES]={
    {"flash0:/kd/semawm.prx","sceSemawm",-1,0,0},
    {"flash0:/kd/usbstor.prx","sceUSB_Stor_Driver",-1,0,0},
    {"flash0:/kd/usbstormgr.prx","sceUSB_Stor_Mgr_Driver",-1,0,0},
    {"flash0:/kd/usbstorms.prx","sceUSB_Stor_Ms_Driver",-1,0,0},
    {"flash0:/kd/usbstorboot.prx","sceUSB_Stor_Boot_Driver",-1,0,0}
};
static PdnSharedStatus *ui;
static PdnProtocol protocol;
static SceUID worker_thread=-1,pad_thread=-1,lifecycle_lock=-1;
static int pad_started,registered,hid_started,storage_started,bus_owned,activated;
static volatile int running,active,connected,link_attached,link_reset_pending;
static volatile int ctl_busy,ctl_submitting,ctl_quiescing,hid_busy;
static volatile int need_initial_pad=1,input_rearm=1,exit_requested;
static volatile uint32_t link_generation,ctl_generation;
static int ctl_feature_receive,ever_cable,cable_absence_pending,usb_poll_started;
static uint32_t cable_absent_since,last_usb_poll,last_pad_time,hid_reports;
static uint8_t idle_rate,latest_pad[8];
static uint8_t pad_buffer[64] __attribute__((aligned(64)));
static uint8_t ctl_buffer[256] __attribute__((aligned(64)));
static struct UsbdDeviceReq pad_req,ctl_req;

static void set_stage(unsigned stage) { if(ui)ui->stage=stage; }
static void record_error(int result) {
    if(ui && !ui->error){ui->error=result;ui->error_stage=ui->stage;}
}
static void publish_phase(void) {
    if(!ui)return;
    ui->phase=protocol.phase;ui->sequence=protocol.sequence;ui->phase_result=protocol.result;
    ui->rejected_commands=protocol.rejected;ui->last_rejection=protocol.last_rejection;
}
static void neutral_pad(uint8_t out[8]) { memset(out,0,8);memset(out,0x7f,4); }
static void request_exit(unsigned reason) {
    int irq=sceKernelCpuSuspendIntr();
    exit_requested=1;
    if(ui){ui->exit_requested=1;if(!ui->stop_reason)ui->stop_reason=reason;}
    sceKernelCpuResumeIntr(irq);
}
static int pad_done(struct UsbdDeviceReq *r,int a,int b) {
    (void)a;(void)b;
    if(!r->retcode && r->recvsize==8){hid_reports++;if(ui)ui->hid_reports=hid_reports;}
    else need_initial_pad=1;
    hid_busy=0;return 0;
}
static int ctl_done(struct UsbdDeviceReq *r,int a,int b) {
    int irq;(void)a;(void)b;
    if(ctl_feature_receive && !r->retcode && r->recvsize==(int)PDN_FEATURE_SIZE){
        sceKernelDcacheInvalidateRange(ctl_buffer,sizeof(ctl_buffer));
        irq=sceKernelCpuSuspendIntr();
        if(active && connected && !ctl_quiescing && !link_reset_pending &&
           ctl_generation==link_generation){
            pdn_protocol_receive(&protocol,ctl_buffer,PDN_FEATURE_SIZE);publish_phase();
        }
        sceKernelCpuResumeIntr(irq);
    }
    ctl_busy=0;return 0;
}
static int control_submit(const uint8_t *data,int size,int receive,int feature) {
    int irq=sceKernelCpuSuspendIntr(),result;
    if(!active || ctl_busy || ctl_quiescing || size<0 || size>(int)sizeof(ctl_buffer)){
        sceKernelCpuResumeIntr(irq);return -1;
    }
    ctl_busy=1;ctl_submitting=1;ctl_generation=link_generation;ctl_feature_receive=feature;
    sceKernelCpuResumeIntr(irq);
    memset(&ctl_req,0,sizeof(ctl_req));ctl_req.endp=&pdn_endpoints[0];
    ctl_req.data=ctl_buffer;ctl_req.size=size;ctl_req.func=ctl_done;
    if(receive){
        sceKernelDcacheWritebackInvalidateRange(ctl_buffer,sizeof(ctl_buffer));
        result=sceUsbbdReqRecv(&ctl_req);
    }else{
        if(size)memcpy(ctl_buffer,data,(size_t)size);
        sceKernelDcacheWritebackRange(ctl_buffer,sizeof(ctl_buffer));
        result=sceUsbbdReqSend(&ctl_req);
    }
    irq=sceKernelCpuSuspendIntr();
    if(result<0)ctl_busy=0;
    ctl_submitting=0;sceKernelCpuResumeIntr(irq);return result;
}
static int receive_control(int endpoint,int interface_arg,struct DeviceRequest *r) {
    uint8_t answer[32];int length,irq;
    (void)endpoint;
    if(!r || interface_arg<0 || (r->wIndex&0xff00u))return -1;
    /* Sony USBBus dispatches interface-recipient requests to this driver.
     * Its assigned number may be 1 (Sony occupies 0). Never hard-code 0 or
     * invent a meaning for the SDK's undocumented UsbInterface.unk8 field.
     * Native usbacc uses this same dispatch rule; wire routing still needs
     * physical validation. Only specifically recognized HID requests pass.
     */
    if(ui){
        ui->ctrl_requests++;
        ui->last_setup=(uint32_t)r->bmRequestType|((uint32_t)r->bRequest<<8)|
            ((uint32_t)r->wLength<<16);
        ui->last_value_index=(uint32_t)r->wValue|((uint32_t)r->wIndex<<16);
    }
    if(r->bmRequestType==0x81 && r->bRequest==6){
        const uint8_t *descriptor;
        if(r->wValue==0x2200){descriptor=report_descriptor;length=sizeof(report_descriptor);}
        else if(r->wValue==0x2100){descriptor=pdn_hid_descriptor;length=sizeof(pdn_hid_descriptor);}
        else return -1;
        if(ui)ui->hid_interface=r->wIndex;
        if(length>r->wLength)length=r->wLength;
        return control_submit(descriptor,length,0,0);
    }
    if(r->bmRequestType==0xa1 && r->bRequest==1){
        irq=sceKernelCpuSuspendIntr();
        if(r->wValue==0x0300){
            pdn_protocol_status(&protocol,answer,connected,storage_started,exit_requested);length=32;
        }else if(r->wValue==0x0100){memcpy(answer,latest_pad,8);length=8;}
        else{sceKernelCpuResumeIntr(irq);return -1;}
        sceKernelCpuResumeIntr(irq);
        if(ui)ui->hid_interface=r->wIndex;
        if(length>r->wLength)length=r->wLength;
        return control_submit(answer,length,0,0);
    }
    if(r->bmRequestType==0x21 && r->bRequest==9){
        if(r->wValue==0x0300 && r->wLength==PDN_FEATURE_SIZE)
            return control_submit(NULL,PDN_FEATURE_SIZE,1,1);
        if(r->wValue==0x0200 && r->wLength==4)return control_submit(NULL,4,1,0);
        return -1;
    }
    if(r->bmRequestType==0xa1 && r->bRequest==2 && !r->wValue && r->wLength==1)
        return control_submit(&idle_rate,1,0,0);
    if(r->bmRequestType==0x21 && r->bRequest==10 && !(r->wValue&255) && !r->wLength){
        idle_rate=(uint8_t)(r->wValue>>8);return 0;
    }
    return -1;
}
static int attach(int speed,void *a,void *b) {
    int irq;(void)a;(void)b;
    if(speed!=1 && speed!=2){record_error(-3010);request_exit(PDN_STOP_ERROR);return -1;}
    irq=sceKernelCpuSuspendIntr();
    if(active){
        link_attached=1;need_initial_pad=1;input_rearm=1;
        connected=!link_reset_pending;if(ui)ui->attach_count++;
    }
    sceKernelCpuResumeIntr(irq);
    if(connected)set_stage(PDN_STAGE_CONNECTED);
    return 0;
}
static int detach(int a,int b,int c) {
    int irq=sceKernelCpuSuspendIntr();(void)a;(void)b;(void)c;
    connected=0;link_attached=0;
    if(active){
        link_generation++;link_reset_pending=1;input_rearm=1;
        pdn_protocol_fail_pending(&protocol,PDN_ERR_LINK);publish_phase();
        if(ui){ui->detach_count++;ui->link_generation=link_generation;ui->hid_interface=UINT32_MAX;}
        set_stage(PDN_STAGE_RECONNECT);
    }
    sceKernelCpuResumeIntr(irq);return 0;
}
static int alternate(int a,int b,int c){(void)a;(void)c;return b==0?0:-1;}
static int driver_start(int n,void *p){(void)n;(void)p;return 0;}
static int driver_stop(int n,void *p){(void)n;(void)p;return 0;}
static struct UsbDriver driver={
    .name=PDN_DRIVER_NAME,.endpoints=2,.endp=pdn_endpoints,.intp=&pdn_interface,
    .devp_hi=&pdn_device_hi,.confp_hi=&pdn_config_hi,
    .devp=&pdn_device_full,.confp=&pdn_config_full,.str=pdn_strings,
    .recvctl=receive_control,.func28=alternate,.attach=attach,.detach=detach,
    .start_func=driver_start,.stop_func=driver_stop
};

static void encode_pad(const SceCtrlData *pad,uint8_t out[8]) {
    static const uint32_t masks[12]={PSP_CTRL_CROSS,PSP_CTRL_CIRCLE,PSP_CTRL_SQUARE,
        PSP_CTRL_TRIANGLE,PSP_CTRL_LTRIGGER,PSP_CTRL_RTRIGGER,PSP_CTRL_SELECT,
        PSP_CTRL_START,PSP_CTRL_HOME,PSP_CTRL_VOLDOWN,PSP_CTRL_VOLUP,PSP_CTRL_SCREEN};
    int i;uint32_t b=pad->Buttons;
    neutral_pad(out);
    if(b&PSP_CTRL_NOTE){out[2]=pad->Ly;out[3]=pad->Lx;}
    else{out[0]=pad->Lx;out[1]=pad->Ly;}
    if(b&PSP_CTRL_UP)out[4]=1;
    if(b&PSP_CTRL_RIGHT)out[4]=3;
    if(b&PSP_CTRL_DOWN)out[4]=5;
    if(b&PSP_CTRL_LEFT)out[4]=7;
    if((b&(PSP_CTRL_UP|PSP_CTRL_RIGHT))==(PSP_CTRL_UP|PSP_CTRL_RIGHT))out[4]=2;
    if((b&(PSP_CTRL_RIGHT|PSP_CTRL_DOWN))==(PSP_CTRL_RIGHT|PSP_CTRL_DOWN))out[4]=4;
    if((b&(PSP_CTRL_DOWN|PSP_CTRL_LEFT))==(PSP_CTRL_DOWN|PSP_CTRL_LEFT))out[4]=6;
    if((b&(PSP_CTRL_LEFT|PSP_CTRL_UP))==(PSP_CTRL_LEFT|PSP_CTRL_UP))out[4]=8;
    for(i=0;i<12;i++)if(b&masks[i])out[4+(i+4)/8]|=(uint8_t)(1u<<((i+4)%8));
}
static void gated_pad(const SceCtrlData *pad,uint8_t out[8]) {
    neutral_pad(out);
    if(!pdn_phase_accepts_input(protocol.phase) || protocol.pending)return;
    if(input_rearm){
        /* Release buttons/analogue before the next UI can consume input. */
        if(!pad->Buttons && pad->Lx>=111 && pad->Lx<=143 && pad->Ly>=111 && pad->Ly<=143)
            input_rearm=0;
        return;
    }
    /* The stop chord never leaks a START/SELECT action to an emulator. */
    if((pad->Buttons&(PSP_CTRL_START|PSP_CTRL_SELECT))==(PSP_CTRL_START|PSP_CTRL_SELECT))return;
    encode_pad(pad,out);
}
static int sample_pad(SceSize args,void *argp) {
    SceCtrlData pad;uint8_t sample[8];int hold=0,irq,send,result;
    (void)args;(void)argp;
    sceCtrlSetSamplingCycle(0);sceCtrlSetSamplingMode(PSP_CTRL_MODE_ANALOG);
    while(running){
        if(sceCtrlPeekBufferPositive(&pad,1)>0){
            if((pad.Buttons&(PSP_CTRL_START|PSP_CTRL_SELECT))==(PSP_CTRL_START|PSP_CTRL_SELECT)){
                if(++hold>=250)request_exit(PDN_STOP_USER);
            }else hold=0;
            sceKernelWaitSema(lifecycle_lock,1,NULL);
            if(active && connected && !link_reset_pending){
                irq=sceKernelCpuSuspendIntr();gated_pad(&pad,sample);memcpy(latest_pad,sample,8);
                send=!hid_busy && (need_initial_pad || !hid_reports || memcmp(pad_buffer,sample,8) ||
                    (idle_rate && (uint32_t)(sceKernelGetSystemTimeLow()-last_pad_time)>=(uint32_t)idle_rate*4000));
                if(send){hid_busy=1;need_initial_pad=0;memcpy(pad_buffer,sample,8);}
                sceKernelCpuResumeIntr(irq);
                if(send){
                    memset(&pad_req,0,sizeof(pad_req));pad_req.endp=&pdn_endpoints[1];
                    pad_req.data=pad_buffer;pad_req.size=8;pad_req.func=pad_done;
                    sceKernelDcacheWritebackRange(pad_buffer,sizeof(pad_buffer));
                    result=sceUsbbdReqSend(&pad_req);
                    if(result<0){hid_busy=0;need_initial_pad=1;}
                    else last_pad_time=sceKernelGetSystemTimeLow();
                }
            }
            sceKernelSignalSema(lifecycle_lock,1);
        }
        sceKernelDelayThread(4000);
    }
    return 0;
}
static void make_session(void) {
    uint8_t nonce[16];uint32_t x=sceKernelGetSystemTimeLow()^0x49c3e215u;unsigned i;
    for(i=0;i<16;i++){x^=x<<13;x^=x>>17;x^=x<<5;nonce[i]=(uint8_t)x;}
    nonce[0]|=1;pdn_protocol_init(&protocol,nonce);publish_phase();
}
static int load_dependencies(void) {
    unsigned i;int result,status;SceModule *existing;
    for(i=0;i<PDN_DEPENDENCIES;i++){
        NativeDependency *d=&dependencies[i];
        if(ui)ui->dependency=i+1u;
        set_stage(PDN_STAGE_MODULE_LOAD);
        existing=sceKernelFindModuleByName(d->name);
        if(existing){
            d->id=existing->modid;if(ui)ui->modules_borrowed|=1u<<i;continue;
        }
        d->id=sceKernelLoadModule(d->path,0,NULL);
        if(d->id<0)return d->id; /* No arbitrary error means "already loaded". */
        d->owned=1;if(ui)ui->modules_loaded|=1u<<i;
        set_stage(PDN_STAGE_MODULE_START);status=-1;
        result=sceKernelStartModule(d->id,0,NULL,&status,NULL);
        if(result<0)return result;
        if(status<0)return status;
        d->started=1;
    }
    return 0;
}
static int unload_dependencies(void) {
    unsigned i;int result,status;
    /* Leave an un-stoppable native module and its earlier dependencies in
     * memory. They do not reference our code. Never unload a borrowed PRX. */
    for(i=PDN_DEPENDENCIES;i>0;i--){
        NativeDependency *d=&dependencies[i-1u];
        if(!d->owned)continue;
        if(d->started){
            status=-1;result=sceKernelStopModule(d->id,0,NULL,&status,NULL);
            if(result<0)return result;
            if(status<0)return status;
            d->started=0;
        }
        result=sceKernelUnloadModule(d->id);if(result<0)return result;
        d->owned=0;d->id=-1;
    }
    return 0;
}
static int quiesce_link(void) {
    uint32_t generation;unsigned tries;int irq,result=0,old_control;
    sceKernelWaitSema(lifecycle_lock,1,NULL);
    irq=sceKernelCpuSuspendIntr();generation=link_generation;connected=0;
    old_control=(ctl_busy||ctl_submitting) && ctl_generation!=generation;
    if(old_control)ctl_quiescing=1;
    sceKernelCpuResumeIntr(irq);
    if(old_control){
        for(tries=0;ctl_submitting && tries<2000;tries++)sceKernelDelayThread(1000);
        if(ctl_submitting){result=-3020;goto done;}
        if(ctl_busy)sceUsbbdReqCancelAll(&pdn_endpoints[0]);
        for(tries=0;ctl_busy && tries<2000;tries++)sceKernelDelayThread(1000);
        if(ctl_busy){result=-3021;goto done;}
    }
    sceUsbbdReqCancelAll(&pdn_endpoints[1]);
    for(tries=0;hid_busy && tries<2000;tries++)sceKernelDelayThread(1000);
    if(hid_busy){result=-3022;goto done;}
    irq=sceKernelCpuSuspendIntr();
    need_initial_pad=1;input_rearm=1;neutral_pad(latest_pad);
    if(generation==link_generation){link_reset_pending=0;connected=active && link_attached;}
    sceKernelCpuResumeIntr(irq);
    set_stage(connected?PDN_STAGE_CONNECTED:PDN_STAGE_RECONNECT);
done:
    ctl_quiescing=0;sceKernelSignalSema(lifecycle_lock,1);return result;
}
static int apply_phase(void) {
    unsigned tries;int irq,result=0;uint32_t generation;
    if(!protocol.pending || ctl_busy || ctl_submitting)return 0;
    sceKernelWaitSema(lifecycle_lock,1,NULL);
    irq=sceKernelCpuSuspendIntr();generation=link_generation;
    if(!active || !connected || link_reset_pending || !protocol.pending || ctl_busy || ctl_submitting){
        sceKernelCpuResumeIntr(irq);goto done;
    }
    sceKernelCpuResumeIntr(irq);
    /* Input samples queued for the old UI must finish/cancel before ACK. */
    sceUsbbdReqCancelAll(&pdn_endpoints[1]);
    for(tries=0;hid_busy && tries<2000;tries++)sceKernelDelayThread(1000);
    if(hid_busy){result=-3023;goto done;}
    irq=sceKernelCpuSuspendIntr();
    if(active && connected && !link_reset_pending && generation==link_generation){
        pdn_protocol_apply(&protocol);input_rearm=1;need_initial_pad=1;
        neutral_pad(latest_pad);publish_phase();
    }
    sceKernelCpuResumeIntr(irq);
done:
    sceKernelSignalSema(lifecycle_lock,1);return result;
}
/* Return 1 only after physical absence, never merely configuration-unset.
 * Unknown USB state is not permission to deactivate a writable volume. */
static int observe_cable(uint32_t now,int usb) {
    if(ui)ui->usb_state=(uint32_t)usb;
    if(usb<0)return usb;
    if(usb&PSP_USB_CABLE_CONNECTED){ever_cable=1;cable_absence_pending=0;return 0;}
    if(!ever_cable)return exit_requested?1:0;
    if(!cable_absence_pending){cable_absence_pending=1;cable_absent_since=now;return 0;}
    if((uint32_t)(now-cable_absent_since)<500000u)return 0;
    if(ui && !ui->stop_reason)ui->stop_reason=PDN_STOP_CABLE;
    return 1;
}
static int poll_cable(void) {
    uint32_t now=sceKernelGetSystemTimeLow();
    if(usb_poll_started && (uint32_t)(now-last_usb_poll)<50000u)return 0;
    usb_poll_started=1;last_usb_poll=now;return observe_cable(now,sceUsbGetState());
}
static int stop_usb(void) {
    unsigned tries;int result,usb;
    /* This check also protects error paths and external module_stop. */
    if(activated){
        usb=sceUsbGetState();if(usb<0)return usb;
        if(usb&PSP_USB_CABLE_CONNECTED)return -3030;
    }
    sceKernelWaitSema(lifecycle_lock,1,NULL);active=0;set_stage(PDN_STAGE_STOPPING);
    for(tries=0;ctl_submitting && tries<2000;tries++)sceKernelDelayThread(1000);
    if(ctl_submitting){result=-3031;goto done;}
    if(activated){result=sceUsbDeactivate(PDN_PRODUCT);if(result<0)goto done;activated=0;}
    if(hid_started){
        sceUsbbdReqCancelAll(&pdn_endpoints[0]);sceUsbbdReqCancelAll(&pdn_endpoints[1]);
        for(tries=0;(hid_busy||ctl_busy)&&tries<2000;tries++)sceKernelDelayThread(1000);
        if(hid_busy||ctl_busy){result=-3032;goto done;}
        result=sceUsbStop(PDN_DRIVER_NAME,0,NULL);if(result<0)goto done;hid_started=0;
    }
    if(storage_started){
        result=sceUsbStop(PSP_USBSTOR_DRIVERNAME,0,NULL);if(result<0)goto done;
        storage_started=0;if(ui)ui->sony_storage_started=0;
    }
    if(registered){result=sceUsbbdUnregister(&driver);if(result<0)goto done;registered=0;}
    if(bus_owned){result=sceUsbStop(PSP_USBBUS_DRIVERNAME,0,NULL);if(result<0)goto done;bus_owned=0;}
    connected=0;result=0;
done:
    sceKernelSignalSema(lifecycle_lock,1);return result;
}
static int start_usb(void) {
    int usb,storage,bus,result;
    set_stage(PDN_STAGE_GUARD);usb=sceUsbGetState();
    storage=sceUsbGetDrvState(PSP_USBSTOR_DRIVERNAME);bus=sceUsbGetDrvState(PSP_USBBUS_DRIVERNAME);
    if(ui){ui->usb_state=(uint32_t)usb;ui->bus_before=bus;ui->storage_before=storage;}
    if(!hid_lab_safe_to_start(usb,storage,bus))return -3002;
    result=load_dependencies();if(result<0)return result;
    /* A cable or foreign USB session may have appeared during module load. */
    set_stage(PDN_STAGE_GUARD);usb=sceUsbGetState();
    storage=sceUsbGetDrvState(PSP_USBSTOR_DRIVERNAME);bus=sceUsbGetDrvState(PSP_USBBUS_DRIVERNAME);
    if(!hid_lab_safe_to_start(usb,storage,bus) || storage!=HID_LAB_DRIVER_STOPPED)return -3003;
    if(ui && ui->request_stop){request_exit(PDN_STOP_USER);return 0;}
    if(bus==HID_LAB_DRIVER_STOPPED){
        set_stage(PDN_STAGE_BUS_START);result=sceUsbStart(PSP_USBBUS_DRIVERNAME,0,NULL);
        if(result<0)return result;
        bus_owned=1;
    }
    set_stage(PDN_STAGE_HID_REGISTER);result=sceUsbbdRegister(&driver);
    if(result<0)return result;
    registered=1;
    /* Start native storage first: it may reserve interface zero. HID asks
     * the bus for an interface, starts separately, then one activation
     * exposes the composed configuration. No storage helper is statically
     * imported: every Sony module has already loaded/started at this point.
     */
    set_stage(PDN_STAGE_STORAGE_START);result=sceUsbStart(PSP_USBSTOR_DRIVERNAME,0,NULL);
    if(result<0)return result;
    storage_started=1;
    if(ui){ui->sony_storage_started=1;ui->storage_after=sceUsbGetDrvState(PSP_USBSTOR_DRIVERNAME);}
    set_stage(PDN_STAGE_HID_START);result=sceUsbStart(PDN_DRIVER_NAME,0,NULL);
    if(result<0)return result;
    hid_started=1;active=1;
    set_stage(PDN_STAGE_ACTIVATE);result=sceUsbActivate(PDN_PRODUCT);
    if(result<0)return result;
    activated=1;set_stage(PDN_STAGE_READY);return 0;
}
static int io_worker(SceSize args,void *argp) {
    int result,cleanup,fault=0;(void)args;(void)argp;
    make_session();result=start_usb();
    if(result<0){record_error(result);request_exit(PDN_STOP_ERROR);fault=1;}
    /* If activation did succeed, even a later HID fault waits for unplug.
     * A controller error is never treated as permission to cut Sony I/O. */
    while(activated){
        if(ui && ui->request_stop)request_exit(PDN_STOP_USER);
        result=poll_cable();
        if(result==1)break;
        if(result<0){record_error(result);request_exit(PDN_STOP_ERROR);fault=1;}
        if(!fault && link_reset_pending){
            result=quiesce_link();if(result<0){record_error(result);request_exit(PDN_STOP_ERROR);fault=1;}
        }
        if(!fault){
            result=apply_phase();if(result<0){record_error(result);request_exit(PDN_STOP_ERROR);fault=1;}
        }
        if(fault){
            int irq=sceKernelCpuSuspendIntr();
            pdn_protocol_fail_pending(&protocol,PDN_ERR_LINK);protocol.phase=PDN_PHASE_ERROR;
            input_rearm=1;neutral_pad(latest_pad);publish_phase();sceKernelCpuResumeIntr(irq);
        }
        if(exit_requested || protocol.phase==PDN_PHASE_SAFE)set_stage(PDN_STAGE_WAIT_UNPLUG);
        sceKernelDelayThread(4000);
    }
    cleanup=stop_usb();
    if(cleanup<0){record_error(cleanup);fault=1;}
    if(!hid_started && !storage_started && !registered && !activated && !bus_owned){
        cleanup=unload_dependencies();if(ui && cleanup<0)ui->cleanup_error=cleanup;
    }
    running=0;
    set_stage(fault?PDN_STAGE_ERROR:PDN_STAGE_STOPPED);
    if(ui && !hid_started && !storage_started && !registered && !activated && !bus_owned &&
       !hid_busy && !ctl_busy && !ctl_submitting)ui->safe_to_unload=1;
    return 0;
}
static int valid_shared(PdnSharedStatus *p) {
    uintptr_t address=(uintptr_t)p;
    return !(address&3u) && address>=0x08800000u &&
        address<=0x0a000000u-sizeof(*p) && p->magic==PDN_LAUNCH_MAGIC && p->size==sizeof(*p);
}
int module_start(SceSize args,void *argp) {
    PdnLaunchArgs launch;int result;
    if(args!=sizeof(launch) || !argp)return -1;
    memcpy(&launch,argp,sizeof(launch));
    if(launch.magic!=PDN_LAUNCH_MAGIC || launch.size!=sizeof(launch) || !valid_shared(launch.shared))return -1;
    ui=launch.shared;ui->safe_to_unload=0;ui->request_stop=0;
    ui->error=0;ui->stage=PDN_STAGE_INIT;ui->hid_interface=UINT32_MAX;
    neutral_pad(latest_pad);
    lifecycle_lock=sceKernelCreateSema("DockNativeLock",0,1,1,NULL);
    if(lifecycle_lock<0)return lifecycle_lock;
    pad_thread=sceKernelCreateThread("DockNativePad",sample_pad,0x20,0x3000,0,NULL);
    worker_thread=sceKernelCreateThread("DockNativeUSB",io_worker,0x24,0x6000,0,NULL);
    if(pad_thread<0 || worker_thread<0){result=-3040;goto failed;}
    running=1;result=sceKernelStartThread(pad_thread,0,NULL);if(result<0)goto failed;pad_started=1;
    result=sceKernelStartThread(worker_thread,0,NULL);if(result>=0)return 0;
failed:
    running=0;
    if(pad_thread>=0){
        if(pad_started)sceKernelWaitThreadEnd(pad_thread,NULL);
        sceKernelDeleteThread(pad_thread);pad_thread=-1;
    }
    if(worker_thread>=0){sceKernelDeleteThread(worker_thread);worker_thread=-1;}
    if(lifecycle_lock>=0){sceKernelDeleteSema(lifecycle_lock);lifecycle_lock=-1;}
    ui->safe_to_unload=1;return result;
}
int module_stop(SceSize args,void *argp) {
    int result,usb;SceUInt timeout=3000000;(void)args;(void)argp;
    request_exit(PDN_STOP_MODULE);
    if(activated){usb=sceUsbGetState();if(usb<0 || (usb&PSP_USB_CABLE_CONNECTED))return -3030;}
    if(worker_thread>=0){result=sceKernelWaitThreadEnd(worker_thread,&timeout);if(result<0)return result;}
    running=0;timeout=1000000;
    if(pad_thread>=0){result=sceKernelWaitThreadEnd(pad_thread,&timeout);if(result<0)return result;}
    if(hid_started||storage_started||registered||activated||bus_owned||hid_busy||ctl_busy||ctl_submitting)return -1;
    if(worker_thread>=0){sceKernelDeleteThread(worker_thread);worker_thread=-1;}
    if(pad_thread>=0){sceKernelDeleteThread(pad_thread);pad_thread=-1;}
    if(lifecycle_lock>=0){sceKernelDeleteSema(lifecycle_lock);lifecycle_lock=-1;}
    ui=NULL;return 0;
}
