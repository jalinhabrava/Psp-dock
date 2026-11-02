/* SPDX-License-Identifier: GPL-3.0-only
 * PSP Dock 0306: native Sony USBSTOR owns the Memory Stick; this module
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

PSP_MODULE_INFO("PSPDockNative",PSP_MODULE_KERNEL,3,6);

typedef struct {
    const char *path,*name;
    SceUID id;
    int owned,started;
} NativeDependency;
static NativeDependency dependencies[PDN_DEPENDENCIES]={
    {"flash0:/kd/semawm.prx","sceSemawm",-1,0,0},
    {"flash0:/kd/usbstor.prx","sceUSB_Stor_Driver",-1,0,0},
    {"flash0:/kd/usbstormgr.prx","sceUSB_Stor_Mgr_Driver",-1,0,0},
    {"flash0:/kd/usbstorms.prx","sceUSB_Stor_Ms_Driver",-1,0,0}
};
/* 6.61 usbstorboot adds PC activation imports (Pcact/NPDRM), which may
 * be unavailable in GAME. It consumes sceUsbstor_internal; none of these
 * four modules imports it. USBStor's own module registers its driver.
 * Do not load Boot, Pcact, EFlash or a storage-redirection module here.
 * Every dependency listed above remains mandatory and fails closed.
 */
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
static uint32_t session_counter;
static uint8_t idle_rate,latest_pad[8];
static uint8_t pad_buffer[64] __attribute__((aligned(64)));
static uint8_t ctl_buffer[256] __attribute__((aligned(64)));
static struct UsbdDeviceReq pad_req,ctl_req;
/* Four-byte OUT reports completed on this console. Assemble only 32 ordered
 * successful fragments in one link generation; the unchanged full parser
 * still checks nonce, sequence and firmware before any action.
 * Bytes: C0|index(0..31), assembly token, two payload bytes. The token
 * separates assemblies; it is not authentication or permission to act. */
static uint8_t fragment_buffer[PDN_FEATURE_SIZE];
static unsigned fragment_next;
static uint8_t fragment_token;
static uint32_t fragment_generation;
static int fragment_active;

static void set_stage(unsigned stage) { if(ui)ui->stage=stage; }
static void set_worker_site(unsigned site) { if(ui)ui->worker_site=site; }
/* Diagnostic writers run with interrupts suspended, for RAM publication
 * only. Never cover a USB call or a wait with this diagnostic seqlock. */
static void ep0_diag_begin(void) {
    if(ui){ui->ep0.generation++;__asm__ volatile("" ::: "memory");}
}
static void ep0_diag_end(void) {
    if(ui){__asm__ volatile("" ::: "memory");ui->ep0.generation++;}
}
static uint32_t ep0_gate_flags(void) {
    return (active?PDN_EP0_ACTIVE:0u)|(connected?PDN_EP0_CONNECTED:0u)|
        (ctl_quiescing?PDN_EP0_QUIESCING:0u)|(link_reset_pending?PDN_EP0_RESET_PENDING:0u)|
        (ctl_generation==link_generation?PDN_EP0_SAME_GENERATION:0u)|
        (ctl_busy?PDN_EP0_CTL_BUSY:0u)|(ctl_submitting?PDN_EP0_CTL_SUBMITTING:0u)|
        (protocol.pending?PDN_EP0_PROTOCOL_PENDING:0u);
}
static void ep0_diag_link(void) {
    if(ui){
        ui->ep0.request_generation=ctl_generation;ui->ep0.live_generation=link_generation;
        ui->ep0.gate_flags=ep0_gate_flags();
    }
}
static void record_error(int result) {
    if(ui && !ui->error){ui->error=result;ui->error_stage=ui->stage;}
}
static void publish_phase(void) {
    if(!ui)return;
    ui->presentation_generation++;
    ui->phase=protocol.phase;ui->sequence=protocol.sequence;ui->phase_result=protocol.result;
    ui->rejected_commands=protocol.rejected;ui->last_rejection=protocol.last_rejection;
    ui->host_confirmed=protocol.host_confirmed;ui->activity=protocol.activity;
    if(protocol.host_confirmed)ui->host_session_seen=1;
    if(protocol.shutdown_requested)ui->stop_reason=PDN_STOP_HOST;
    ui->progress_unit=protocol.progress_unit;ui->completed=protocol.completed;ui->total=protocol.total;
    ui->presentation_generation++;
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
static uint16_t fragment_crc16(const uint8_t *data,unsigned size) {
    uint16_t crc=0xffffu;unsigned i,bit;
    for(i=0;i<size;i++){
        crc^=(uint16_t)((uint16_t)data[i]<<8);
        for(bit=0;bit<8;bit++)crc=(uint16_t)((crc&0x8000u)?
            ((uint32_t)crc<<1)^0x1021u:(uint32_t)crc<<1);
    }
    return crc;
}
static int assemble_output(const uint8_t in[4]) {
    unsigned index=in[0]&31u;
    if(in[0]==0xe0u){
        int valid=fragment_active && fragment_next==PDN_FEATURE_SIZE/2u &&
            fragment_generation==link_generation && in[1]==fragment_token &&
            fragment_crc16(fragment_buffer,PDN_FEATURE_SIZE)==(uint16_t)(in[2]|((uint16_t)in[3]<<8));
        fragment_active=0;return valid?1:-1;
    }
    if((in[0]&0xe0u)!=0xc0u){fragment_active=0;return -1;}
    if(!index){
        fragment_active=1;fragment_next=0;fragment_token=in[1];
        fragment_generation=ctl_generation;
    }
    if(!fragment_active || fragment_generation!=link_generation ||
       in[1]!=fragment_token || index!=fragment_next){fragment_active=0;return -1;}
    fragment_buffer[index*2u]=in[2];fragment_buffer[index*2u+1u]=in[3];
    fragment_next++;
    return 0;
}
static int ctl_done(struct UsbdDeviceReq *r,int a,int b) {
    int irq,received,assembled;unsigned i;
    int expected=ctl_feature_receive==2?4:(int)PDN_TRANSFER_SIZE;
    const uint8_t *payload=ctl_buffer;
    (void)a;(void)b;
    if(ctl_feature_receive && ui){
        irq=sceKernelCpuSuspendIntr();ep0_diag_begin();
        ui->ep0.completions++;ui->ep0.retcode=r->retcode;ui->ep0.recvsize=r->recvsize;
        ui->ep0.drop_reason=r->retcode?PDN_EP0_DROP_RETCODE:
            r->recvsize!=expected?PDN_EP0_DROP_SIZE:PDN_EP0_DROP_NONE;
        ep0_diag_link();
        /* The full allocated buffer is always readable. This snapshot is
         * not a claim that 64 bytes arrived when recvsize reports otherwise. */
        for(i=0;i<PDN_FEATURE_SIZE;i++)ui->ep0.raw[i]=ctl_buffer[i];
        ep0_diag_end();sceKernelCpuResumeIntr(irq);
    }
    if(ctl_feature_receive==2 && (r->retcode || r->recvsize!=expected))fragment_active=0;
    if(ctl_feature_receive && !r->retcode && r->recvsize==expected){
        /* Recv owns this buffer until completion. Prepare its cache before
         * submission (below), as Sony usbacc does for EP0 OUT; do not
         * invalidate here. A USBBus CPU copy can leave the received command
         * dirty in cache, and invalidation would restore the preceding GET
         * report from memory, silently discarding HOST_BEGIN/SET_PHASE. */
        irq=sceKernelCpuSuspendIntr();
        if(active && connected && !ctl_quiescing && !link_reset_pending &&
           ctl_generation==link_generation){
            if(ctl_feature_receive==2){
                assembled=assemble_output(ctl_buffer);
                if(assembled<=0){
                    if(ui){ep0_diag_begin();ui->ep0.drop_reason=assembled<0?
                        PDN_EP0_DROP_FRAGMENT:PDN_EP0_ASSEMBLING;ep0_diag_end();}
                    sceKernelCpuResumeIntr(irq);ctl_busy=0;return 0;
                }
                payload=fragment_buffer;
            }
            received=pdn_protocol_receive(&protocol,payload,PDN_FEATURE_SIZE);
            if(ui){
                ep0_diag_begin();ui->ep0.parser_calls++;ui->ep0.parser_result=received;
                for(i=0;i<PDN_FEATURE_SIZE;i++)ui->ep0.raw[i]=payload[i];
                ui->ep0.drop_reason=received<0?PDN_EP0_DROP_PARSER:PDN_EP0_DROP_NONE;
                ep0_diag_link();ep0_diag_end();
            }
            publish_phase();
        }else if(ui){
            fragment_active=0;
            ep0_diag_begin();ui->ep0.drop_reason=PDN_EP0_DROP_LINK;
            ep0_diag_link();ep0_diag_end();
        }
        sceKernelCpuResumeIntr(irq);
    }
    ctl_busy=0;return 0;
}
static int control_submit(const uint8_t *data,int size,int receive,int feature) {
    int irq=sceKernelCpuSuspendIntr(),result;
    if(!active || ctl_busy || ctl_quiescing || size<0 || size>(int)sizeof(ctl_buffer)){
        if(feature && ui){
            ep0_diag_begin();ui->ep0.drop_reason=PDN_EP0_DROP_SUBMIT_GATE;
            ep0_diag_link();ep0_diag_end();
        }
        sceKernelCpuResumeIntr(irq);return -1;
    }
    ctl_busy=1;ctl_submitting=1;ctl_generation=link_generation;ctl_feature_receive=feature;
    if(feature && ui){
        ep0_diag_begin();ui->ep0.recv_submissions++;ui->ep0.submit_result=INT32_MIN;
        ep0_diag_link();ep0_diag_end();
    }
    sceKernelCpuResumeIntr(irq);
    memset(&ctl_req,0,sizeof(ctl_req));ctl_req.endp=&pdn_endpoints[0];
    ctl_req.data=ctl_buffer;ctl_req.size=size;ctl_req.func=ctl_done;
    /* PSPSDK calls this field unkc; xerpi's matching PSP USB layout names
     * it isControlRequest. Sony usbacc sets it on the EP0 request shared
     * by IN and OUT. Mark control transfers explicitly; EP1 stays zero.
     * A complete payload with a cancelled completion is still rejected. */
    ctl_req.unkc=1;
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
    if(feature && ui){
        ep0_diag_begin();ui->ep0.submit_result=result;
        if(result<0)ui->ep0.drop_reason=PDN_EP0_DROP_SUBMIT_ERROR;
        ep0_diag_end();
    }
    ctl_submitting=0;sceKernelCpuResumeIntr(irq);return result;
}
static int receive_control(int endpoint,int interface_arg,struct DeviceRequest *r) {
    uint8_t answer[PDN_TRANSFER_SIZE];int length,irq;
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
            pdn_protocol_status(&protocol,answer,connected,storage_started,exit_requested);length=PDN_TRANSFER_SIZE;
            if(ui){
                /* 0306 status-only diagnostic: drop4, receive-length7,
                 * signed result5 (-16 unknown). Commands keep these two
                 * reserved bytes zero, so this cannot authorize an action. */
                uint32_t drop=ui->ep0.drop_reason&15u;
                int32_t rx=ui->ep0.recvsize;
                int32_t value=drop==PDN_EP0_DROP_PARSER?ui->ep0.parser_result:ui->ep0.retcode;
                uint32_t encoded=(uint32_t)(value==INT32_MIN?-16:value< -15?-15:value>15?15:value)&31u;
                uint16_t diagnostic=(uint16_t)(drop|((uint32_t)(rx<0 || rx>127?127:rx)<<4)|(encoded<<11));
                answer[46]=(uint8_t)diagnostic;answer[47]=(uint8_t)(diagnostic>>8);
            }
        }else if(r->wValue==0x0100){memcpy(answer,latest_pad,8);length=8;}
        else if(r->wValue==0x0200 && r->wLength==4 && ui){
            int32_t rc=ui->ep0.retcode,parser=ui->ep0.parser_result;
            answer[0]=(uint8_t)ui->ep0.drop_reason;
            answer[1]=(uint8_t)(ui->ep0.recvsize<0 || ui->ep0.recvsize>255?255:ui->ep0.recvsize);
            answer[2]=(uint8_t)(rc==INT32_MIN?-128:rc< -127?-127:rc>127?127:rc);
            answer[3]=(uint8_t)(parser==INT32_MIN?-128:parser< -127?-127:parser>127?127:parser);
            length=4;
        }
        else{sceKernelCpuResumeIntr(irq);return -1;}
        sceKernelCpuResumeIntr(irq);
        if(ui)ui->hid_interface=r->wIndex;
        if(length>r->wLength)length=r->wLength;
        return control_submit(answer,length,0,0);
    }
    if(r->bmRequestType==0x21 && r->bRequest==9){
        if(((r->wValue&0xff00u)==0x0300u || r->wValue==0x0200) && ui){
            unsigned i;
            irq=sceKernelCpuSuspendIntr();ep0_diag_begin();ui->ep0.set_seen++;
            ui->ep0.setup=(uint32_t)r->bmRequestType|((uint32_t)r->bRequest<<8)|
                ((uint32_t)r->wLength<<16);
            ui->ep0.value_index=(uint32_t)r->wValue|((uint32_t)r->wIndex<<16);
            ui->ep0.submit_result=ui->ep0.retcode=ui->ep0.parser_result=INT32_MIN;
            ui->ep0.recvsize=-1;
            ui->ep0.drop_reason=((r->wValue==0x0300 && r->wLength==PDN_TRANSFER_SIZE) ||
                                (r->wValue==0x0200 && r->wLength==4))?
                PDN_EP0_DROP_NONE:PDN_EP0_DROP_SUBMIT_GATE;
            ep0_diag_link();
            for(i=0;i<PDN_FEATURE_SIZE;i++)ui->ep0.raw[i]=0;
            ep0_diag_end();sceKernelCpuResumeIntr(irq);
        }
        if(r->wValue==0x0300 && r->wLength==PDN_TRANSFER_SIZE){
            fragment_active=0;
            return control_submit(NULL,PDN_TRANSFER_SIZE,1,1);
        }
        if(r->wValue==0x0200 && r->wLength==4)return control_submit(NULL,4,1,2);
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
    connected=0;link_attached=0;fragment_active=0;
    if(active){
        link_generation++;link_reset_pending=1;input_rearm=1;
        pdn_protocol_invalidate_link(&protocol);publish_phase();
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
    /* NOTE belongs exclusively to local diagnostics, never to game input.
     * The primary analogue axes remain identical when it is pressed. */
    out[0]=pad->Lx;out[1]=pad->Ly;
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
    if(!pdn_protocol_accepts_input(&protocol))return;
    if(input_rearm){
        /* Release buttons/analogue before the next UI can consume input. */
        if(!(pad->Buttons&~PSP_CTRL_NOTE) && pad->Lx>=111 && pad->Lx<=143 && pad->Ly>=111 && pad->Ly<=143)
            input_rearm=0;
        return;
    }
    /* The stop chord never leaks a START/SELECT action to an emulator. */
    if((pad->Buttons&(PSP_CTRL_START|PSP_CTRL_SELECT))==(PSP_CTRL_START|PSP_CTRL_SELECT))return;
    encode_pad(pad,out);
}
static void diagnostic_button(uint32_t buttons,int *held) {
    /* USER button reads may mask the NOTE system button. Observe it in the
     * existing kernel sampler and expose only a local display preference. */
    if((buttons&PSP_CTRL_NOTE) && !*held && ui)ui->diagnostic_visible^=1u;
    *held=!!(buttons&PSP_CTRL_NOTE);
}
static int sample_pad(SceSize args,void *argp) {
    SceCtrlData pad;uint8_t sample[8];int hold=0,note_held=0,irq,send,result;
    (void)args;(void)argp;
    sceCtrlSetSamplingCycle(0);sceCtrlSetSamplingMode(PSP_CTRL_MODE_ANALOG);
    while(running){
        if(sceCtrlPeekBufferPositive(&pad,1)>0){
            diagnostic_button(pad.Buttons,&note_held);
            if((pad.Buttons&(PSP_CTRL_START|PSP_CTRL_SELECT))==(PSP_CTRL_START|PSP_CTRL_SELECT)){
                if(++hold>=250)request_exit(PDN_STOP_USER);
            }else hold=0;
            sceKernelWaitSema(lifecycle_lock,1,NULL);
            if(active && connected && !link_reset_pending && !protocol.shutdown_requested){
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
    /* Distinguish resets even at an unchanged timer. Not an auth secret. */
    session_counter++;x^=session_counter*0x9e3779b9u;
    if(!x)x=0x49c3e215u;
    for(i=0;i<16;i++){x^=x<<13;x^=x>>17;x^=x<<5;nonce[i]=(uint8_t)x;}
    pdn_write32(nonce+12,session_counter);
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
    /* Applied host release is terminal even if detach races the next tick.
     * Never replace its session/authorization with a fresh waiting nonce. */
    if(protocol.shutdown_requested)return 0;
    set_worker_site(PDN_WORKER_LOCK_WAIT);
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
    if(generation==link_generation){
        /* Drain old callbacks before requiring a new host session/binding. */
        make_session();link_reset_pending=0;connected=active && link_attached;
    }
    sceKernelCpuResumeIntr(irq);
    set_stage(connected?PDN_STAGE_CONNECTED:PDN_STAGE_RECONNECT);
done:
    ctl_quiescing=0;sceKernelSignalSema(lifecycle_lock,1);return result;
}
static int apply_phase(void) {
    unsigned tries;int irq,result=0;uint32_t generation;
    if(!protocol.pending || ctl_busy || ctl_submitting)return 0;
    set_worker_site(PDN_WORKER_LOCK_WAIT);
    sceKernelWaitSema(lifecycle_lock,1,NULL);
    irq=sceKernelCpuSuspendIntr();generation=link_generation;
    if(!active || !connected || link_reset_pending || !protocol.pending || ctl_busy || ctl_submitting){
        sceKernelCpuResumeIntr(irq);goto done;
    }
    sceKernelCpuResumeIntr(irq);
    /* Input samples queued for the old UI must finish/cancel before ACK. */
    set_worker_site(PDN_WORKER_EP1_CANCEL);
    sceUsbbdReqCancelAll(&pdn_endpoints[1]);
    set_worker_site(PDN_WORKER_EP1_DRAIN);
    for(tries=0;hid_busy && tries<2000;tries++)sceKernelDelayThread(1000);
    if(hid_busy){result=-3023;goto done;}
    irq=sceKernelCpuSuspendIntr();
    if(active && connected && !link_reset_pending && generation==link_generation){
        set_worker_site(PDN_WORKER_COMMIT);
        pdn_protocol_apply(&protocol);input_rearm=1;need_initial_pad=1;
        if(ui)ui->apply_count++;
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
    /* The worker may close with the cable present only after applying the
     * bound HOST_SHUTDOWN. SAFE, local exit and detach confer no authority.
     * External module_stop keeps its separate cable guard below. */
    if(activated){
        usb=sceUsbGetState();if(usb<0)return usb;
        if((usb&PSP_USB_CABLE_CONNECTED) && !protocol.shutdown_requested)return -3030;
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
static int pdn_safe_to_start(int usb,int storage,int bus) {
    /* Native Dock starts both storage and HID. A cable attached to inactive,
     * unconfigured USB is safe at launch; it is not a foreign USB session.
     * Preserve the lab's storage/bus checks and every other USB guard. */
    if(usb<0)return 0;
    return hid_lab_safe_to_start(usb&~PSP_USB_CABLE_CONNECTED,storage,bus);
}
static int start_usb(void) {
    int usb,storage,bus,result;
    set_stage(PDN_STAGE_GUARD);usb=sceUsbGetState();
    storage=sceUsbGetDrvState(PSP_USBSTOR_DRIVERNAME);bus=sceUsbGetDrvState(PSP_USBBUS_DRIVERNAME);
    if(ui){ui->usb_state=(uint32_t)usb;ui->bus_before=bus;ui->storage_before=storage;}
    if(!pdn_safe_to_start(usb,storage,bus))return -3002;
    result=load_dependencies();if(result<0)return result;
    /* Recheck for a foreign USB session or storage use during module load. */
    set_stage(PDN_STAGE_GUARD);usb=sceUsbGetState();
    storage=sceUsbGetDrvState(PSP_USBSTOR_DRIVERNAME);bus=sceUsbGetDrvState(PSP_USBBUS_DRIVERNAME);
    if(!pdn_safe_to_start(usb,storage,bus) || storage!=HID_LAB_DRIVER_STOPPED)return -3003;
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
    int result,cleanup,stopped=0,fault=0;(void)args;(void)argp;
    set_worker_site(PDN_WORKER_INIT);
    make_session();result=start_usb();
    if(result<0){record_error(result);request_exit(PDN_STOP_ERROR);fault=1;}
    /* Faults still wait for physical absence or an applied host release.
     * An error by itself never authorizes cutting Sony I/O. */
    while(activated){
        if(protocol.shutdown_requested){request_exit(PDN_STOP_HOST);break;}
        if(ui)ui->worker_loops++;
        if(ui && ui->request_stop)request_exit(PDN_STOP_USER);
        set_worker_site(PDN_WORKER_POLL);
        result=poll_cable();
        if(result==1)break;
        if(result<0){record_error(result);request_exit(PDN_STOP_ERROR);fault=1;}
        if(!fault && link_reset_pending){
            set_worker_site(PDN_WORKER_QUIESCE);
            result=quiesce_link();if(result<0){record_error(result);request_exit(PDN_STOP_ERROR);fault=1;}
        }
        if(!fault || (protocol.pending && protocol.command[5]==PDN_OP_HOST_SHUTDOWN)){
            set_worker_site(PDN_WORKER_APPLY);
            result=apply_phase();
            if(result<0){
                int irq=sceKernelCpuSuspendIntr();
                pdn_protocol_fail_pending(&protocol,PDN_ERR_LINK);sceKernelCpuResumeIntr(irq);
                record_error(result);request_exit(PDN_STOP_ERROR);fault=1;
            }
        }
        if(protocol.shutdown_requested){request_exit(PDN_STOP_HOST);break;}
        if(fault){
            set_worker_site(PDN_WORKER_FAULT);
            int irq=sceKernelCpuSuspendIntr();
            /* A complete terminal command can arrive while this faulty
             * worker is sleeping or its EP0 callback is still unwinding.
             * Preserve it for the guarded apply path; fail ordinary work. */
            if(!protocol.pending || protocol.command[5]!=PDN_OP_HOST_SHUTDOWN)
                pdn_protocol_fail_pending(&protocol,PDN_ERR_LINK);
            protocol.phase=PDN_PHASE_ERROR;
            input_rearm=1;neutral_pad(latest_pad);publish_phase();sceKernelCpuResumeIntr(irq);
        }
        if(exit_requested || protocol.phase==PDN_PHASE_SAFE)set_stage(PDN_STAGE_WAIT_UNPLUG);
        set_worker_site(PDN_WORKER_SLEEP);
        sceKernelDelayThread(4000);
    }
    set_worker_site(PDN_WORKER_STOP);
    cleanup=stop_usb();
    stopped=cleanup>=0;
    if(cleanup<0){record_error(cleanup);fault=1;}
    if(!hid_started && !storage_started && !registered && !activated && !bus_owned){
        cleanup=unload_dependencies();if(ui && cleanup<0)ui->cleanup_error=cleanup;
    }
    running=0;
    set_stage(fault?PDN_STAGE_ERROR:PDN_STAGE_STOPPED);
    if(ui && stopped && !hid_started && !storage_started && !registered && !activated && !bus_owned &&
       !hid_busy && !ctl_busy && !ctl_submitting)ui->safe_to_unload=1;
    return 0;
}
static int valid_shared(PdnSharedStatus *p) {
    uintptr_t address=(uintptr_t)p;
    return !(address&(_Alignof(PdnSharedStatus)-1u)) && address>=0x08800000u &&
        address<=0x0a000000u-sizeof(*p) && p->magic==PDN_LAUNCH_MAGIC && p->size==sizeof(*p);
}
int module_start(SceSize args,void *argp) {
    PdnLaunchArgs launch;int result;
    if(args!=sizeof(launch) || !argp)return -1;
    memcpy(&launch,argp,sizeof(launch));
    if(launch.magic!=PDN_LAUNCH_MAGIC || launch.size!=sizeof(launch) || !valid_shared(launch.shared))return -1;
    ui=launch.shared;ui->safe_to_unload=0;ui->request_stop=0;
    fragment_active=0;fragment_next=0;
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
