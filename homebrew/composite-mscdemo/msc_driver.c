/* SPDX-License-Identifier: GPL-3.0-only
 * HID derived from the tested FuSa/PSPDockHID adapter, upstream
 * 97a1fccb8cb561b14964de4d4dbab4fcd8618297. See LICENSE and README.
 *
 * Experimental composite controller: joystick + synthetic read-only USB disk.
 * USB callbacks NEVER perform file operations. Every DMA buffer has a
 * single owner and is retained until its completion/cancellation callback.
 * This driver NEVER starts Sony USBSTOR and has NO filesystem API.
 */
#include <pspkernel.h>
#include <pspctrl.h>
#include <pspusb.h>
#include <pspusbstor.h>
#include <pspusbbus.h>
#include <pspintrman.h>
#include <pspthreadman.h>
#include <string.h>
#include "msc_common.h"
#include "msc_descriptors.h"
#include "msc_scsi.h"
#include "lab_gate.h"

PSP_MODULE_INFO("PSPDockStorageTest",PSP_MODULE_KERNEL,2,6);

static volatile int running,active,connected,stop_requested,ever_connected;
static volatile int hid_busy,ctl_busy,rx_busy,tx_busy,rx_ready,bulk_fault;
static volatile int ctl_submitting,bulk_submitting,tx_ready;
static volatile int bot_reset_pending,reset_clear_mask;
static volatile int link_attached,link_reset_pending,ctl_quiescing,need_initial_pad=1;
static volatile uint32_t link_generation,ctl_generation,bot_reset_generation;
static volatile uint32_t bot_reset_sequence;
static volatile uint32_t rx_generation,tx_generation;
static volatile int tx_size;
static volatile int rx_code,rx_size,tx_code;
static volatile unsigned hid_sent,commands;
static int registered,started,activated,bus_owned;
static SceUID io_thread=-1,pad_thread=-1,lifecycle_lock=-1;
static int pad_started;
static PdcSharedStatus *ui;
static uint8_t session[16],idle_rate,latest_pad[8];
static uint32_t last_pad_time;
static uint8_t pad_buffer[64] __attribute__((aligned(64)));
static uint8_t ctl_buffer[256] __attribute__((aligned(64)));
static uint8_t command_buffer[512] __attribute__((aligned(64)));
static uint8_t response_buffer[65536] __attribute__((aligned(64)));
static struct UsbdDeviceReq pad_req,ctl_req,rx_req,tx_req;
static MscBot bot;
enum BotPhase { BOT_CBW, BOT_DATA, BOT_FINISH_DATA, BOT_CSW, BOT_CSW_WAIT, BOT_RESET_WAIT };
static enum BotPhase bot_phase;
static unsigned data_pending;
static int data_stalls_applied;
static uint32_t cable_absent_since,last_usb_poll;
static int cable_absence_pending,usb_poll_started;

static void set_stage(unsigned stage,int error) {
    if(ui){ui->stage=stage;if(error)ui->error=error;}
}
static void request_shutdown(unsigned reason){
    int irq=sceKernelCpuSuspendIntr();
    if(ui && !ui->stop_reason)ui->stop_reason=reason;
    stop_requested=1;sceKernelCpuResumeIntr(irq);
}
static void hid_status(uint8_t out[32]) {
    int irq=sceKernelCpuSuspendIntr();
    memset(out,0,32);memcpy(out,"PDK1",4);
    out[4]=1;out[5]=1;out[6]=active?2:0;out[7]=connected?1:0;
    memcpy(out+8,session,16);sceKernelCpuResumeIntr(irq);
}
static int pad_done(struct UsbdDeviceReq *r,int a,int b) {
    (void)a;(void)b;
    if(r->retcode==0 && r->recvsize==8){hid_sent++;if(ui)ui->hid_reports=hid_sent;}
    else need_initial_pad=1;
    hid_busy=0;return 0;
}
static int ctl_done(struct UsbdDeviceReq *r,int a,int b) {
    (void)r;(void)a;(void)b;ctl_busy=0;return 0;
}
static int rx_done(struct UsbdDeviceReq *r,int a,int b) {
    int irq;(void)a;(void)b;
    irq=sceKernelCpuSuspendIntr();
    rx_code=r->retcode;rx_size=r->recvsize;rx_ready=1;rx_busy=0;
    if(ui && active && !link_reset_pending && !bot_reset_pending && rx_generation==link_generation)
        ui->last_rx_code=r->retcode;
    sceKernelCpuResumeIntr(irq);
    return 0;
}
static int tx_done(struct UsbdDeviceReq *r,int a,int b) {
    int irq;(void)a;(void)b;
    irq=sceKernelCpuSuspendIntr();
    tx_code=r->retcode;tx_size=r->recvsize;tx_ready=1;tx_busy=0;
    if(ui && active && !link_reset_pending && !bot_reset_pending && tx_generation==link_generation)
        ui->last_tx_code=r->retcode;
    sceKernelCpuResumeIntr(irq);return 0;
}
static int control_submit_checked(const uint8_t *data,int size,int receive,
                                  int check_generation,uint32_t generation) {
    int irq=sceKernelCpuSuspendIntr();int result;
    if(!active || ctl_quiescing || ctl_busy || size<0 || size>(int)sizeof(ctl_buffer) ||
       (check_generation && generation!=link_generation)) {
        sceKernelCpuResumeIntr(irq);return -1;
    }
    ctl_busy=1;ctl_submitting=1;ctl_generation=link_generation;
    sceKernelCpuResumeIntr(irq);
    memset(&ctl_req,0,sizeof(ctl_req));ctl_req.endp=&pdc_endpoints[0];
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
    ctl_submitting=0;
    sceKernelCpuResumeIntr(irq);
    return result;
}
static int control_submit(const uint8_t *data,int size,int receive){
    return control_submit_checked(data,size,receive,0,0);
}
static int receive_control(int endpoint,int arg2,struct DeviceRequest *r) {
    uint8_t answer[32];int length,irq;
    (void)endpoint;
    if(!r || arg2==-1)return -1;
    if(ui){
        ui->ctrl_requests++;
        ui->last_setup=(uint32_t)r->bmRequestType|((uint32_t)r->bRequest<<8)|
                       ((uint32_t)r->wLength<<16);
        ui->last_value_index=(uint32_t)r->wValue|((uint32_t)r->wIndex<<16);
    }
    if(r->bmRequestType==0xa1 && r->bRequest==0xfe &&
       r->wValue==0 && r->wIndex==1 && r->wLength==1){
        answer[0]=0;return control_submit(answer,1,0);
    }
    if(r->bmRequestType==0x21 && r->bRequest==0xff &&
       r->wValue==0 && r->wIndex==1 && r->wLength==0){
        /* Supervisor drains both bulk pipes BEFORE submitting the EP0
         * status phase. Never clear HALT or reset a data toggle here. */
        irq=sceKernelCpuSuspendIntr();
        bot_reset_pending=1;bot_reset_generation=link_generation;
        bot_reset_sequence++;reset_clear_mask=3;
        sceKernelCpuResumeIntr(irq);
        if(ui)ui->ctrl_status++;
        return 0;
    }
    if(r->bmRequestType==0x02 && r->bRequest==1 &&
       r->wValue==0 && r->wLength==0 &&
       (r->wIndex==PDC_BULK_IN || r->wIndex==PDC_BULK_OUT)){
        /* USBBus owns the actual endpoint clear/toggle operation. This
         * notification is diagnostic; its routing requires hardware proof. */
        irq=sceKernelCpuSuspendIntr();
        if(r->wIndex==PDC_BULK_IN)reset_clear_mask&=~1;
        else reset_clear_mask&=~2;
        sceKernelCpuResumeIntr(irq);
        return 0;
    }
    if(r->wIndex!=0)return -1;
    if(r->bmRequestType==0x81 && r->bRequest==6){
        const uint8_t *data;
        if(r->wValue==0x2200){data=report_descriptor;length=sizeof(report_descriptor);}
        else if(r->wValue==0x2100){data=pdc_hid_descriptor;length=sizeof(pdc_hid_descriptor);}
        else return -1;
        if(length>r->wLength)length=r->wLength;
        return control_submit(data,length,0);
    }
    if(r->bmRequestType==0xa1 && r->bRequest==1){
        if(r->wValue==0x0300){hid_status(answer);length=32;}
        else if(r->wValue==0x0100){
            irq=sceKernelCpuSuspendIntr();memcpy(answer,latest_pad,8);
            sceKernelCpuResumeIntr(irq);length=8;
        }else return -1;
        if(length>r->wLength)length=r->wLength;
        return control_submit(answer,length,0);
    }
    if(r->bmRequestType==0x21 && r->bRequest==9 && r->wValue==0x0200 && r->wLength==4)
        return control_submit(NULL,4,1);
    if(r->bmRequestType==0xa1 && r->bRequest==2 && r->wValue==0 && r->wLength==1)
        return control_submit(&idle_rate,1,0);
    if(r->bmRequestType==0x21 && r->bRequest==10 && !(r->wValue&255) && !r->wLength){
        idle_rate=(uint8_t)(r->wValue>>8);return 0;
    }
    return -1;
}
static int attach(int speed,void *a,void *b){
    int irq;(void)a;(void)b;
    if(speed!=1 && speed!=2){bulk_fault=-10;request_shutdown(PDC_STOP_ERROR);return -1;}
    irq=sceKernelCpuSuspendIntr();
    if(active){
        ever_connected=1;link_attached=1;need_initial_pad=1;
        connected=!link_reset_pending;
        if(ui)ui->attach_count++;
    }
    sceKernelCpuResumeIntr(irq);
    if(connected)set_stage(PDC_STAGE_CONNECTED,0);
    return 0;
}
static int detach(int a,int b,int c){
    int irq=sceKernelCpuSuspendIntr();
    connected=0;link_attached=0;
    /* PSPSDK documents detach as configuration unset. It is not evidence
     * that the physical cable was removed: Windows can reconfigure USB. */
    if(active){
        link_generation++;link_reset_pending=1;
        if(ui){
            ui->detach_count++;ui->link_generation=link_generation;
            ui->detach_arg1=a;ui->detach_arg2=b;ui->detach_arg3=c;
            ui->stage=PDC_STAGE_RECONNECT;
        }
    }
    sceKernelCpuResumeIntr(irq);
    return 0;
}
static int alternate(int a,int b,int c){(void)a;(void)c;return b==0?0:-1;}
static int driver_start(int n,void *p){(void)n;(void)p;return 0;}
static int driver_stop(int n,void *p){(void)n;(void)p;return 0;}
static struct UsbDriver driver={
    .name=PDC_DRIVER_NAME,.endpoints=4,.endp=pdc_endpoints,.intp=&pdc_interface,
    .devp_hi=&pdc_device_hi,.confp_hi=&pdc_config_hi,
    .devp=&pdc_device_full,.confp=&pdc_config_full,.str=pdc_strings,
    .recvctl=receive_control,.func28=alternate,.attach=attach,.detach=detach,
    .start_func=driver_start,.stop_func=driver_stop
};

/* The joystick order is unchanged from the PSP-2003-tested driver. */
static void encode_pad(const SceCtrlData *pad,uint8_t out[8]) {
    static const uint32_t masks[12]={PSP_CTRL_CROSS,PSP_CTRL_CIRCLE,PSP_CTRL_SQUARE,
        PSP_CTRL_TRIANGLE,PSP_CTRL_LTRIGGER,PSP_CTRL_RTRIGGER,PSP_CTRL_SELECT,
        PSP_CTRL_START,PSP_CTRL_HOME,PSP_CTRL_VOLDOWN,PSP_CTRL_VOLUP,PSP_CTRL_SCREEN};
    int i;uint32_t b=pad->Buttons;
    memset(out,0,8);memset(out,0x7f,4);
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
    for(i=0;i<12;i++)if(b&masks[i])out[4+(i+4)/8]|=1<<((i+4)%8);
}
static int sample_pad(SceSize args,void *argp){
    SceCtrlData pad;uint8_t sample[8];int hold=0,send,irq,result;
    (void)args;(void)argp;
    sceCtrlSetSamplingCycle(0);sceCtrlSetSamplingMode(PSP_CTRL_MODE_ANALOG);
    while(running){
        if(sceCtrlPeekBufferPositive(&pad,1)>0){
            if((pad.Buttons&(PSP_CTRL_START|PSP_CTRL_SELECT))==(PSP_CTRL_START|PSP_CTRL_SELECT)){
                if(++hold>=250)request_shutdown(PDC_STOP_USER);
            }else hold=0;
            sceKernelWaitSema(lifecycle_lock,1,NULL);
            if(active && connected && !link_reset_pending){
                encode_pad(&pad,sample);irq=sceKernelCpuSuspendIntr();
                memcpy(latest_pad,sample,8);
                send=active && connected && !link_reset_pending && !hid_busy &&
                     (need_initial_pad || !hid_sent || memcmp(pad_buffer,sample,8) ||
                     (idle_rate && (uint32_t)(sceKernelGetSystemTimeLow()-last_pad_time)>=(uint32_t)idle_rate*4000));
                if(send){hid_busy=1;need_initial_pad=0;memcpy(pad_buffer,sample,8);}
                sceKernelCpuResumeIntr(irq);
                if(send){
                    memset(&pad_req,0,sizeof(pad_req));pad_req.endp=&pdc_endpoints[1];
                    pad_req.data=pad_buffer;pad_req.size=8;pad_req.func=pad_done;
                    sceKernelDcacheWritebackRange(pad_buffer,sizeof(pad_buffer));
                    result=sceUsbbdReqSend(&pad_req);
                    if(result<0){hid_busy=0;need_initial_pad=1;}
                    else last_pad_time=sceKernelGetSystemTimeLow();
                }
            }
            /* Release only the semaphore acquired after a valid pad sample. */
            sceKernelSignalSema(lifecycle_lock,1);
        }
        sceKernelDelayThread(4000);
    }
    return 0;
}
static void make_session(void){
    unsigned x=sceKernelGetSystemTimeLow()^0x5d43ac27u;unsigned i;
    for(i=0;i<16;i++){x^=x<<13;x^=x>>17;x^=x<<5;session[i]=(uint8_t)x;}
    session[0]|=1; /* Correlation nonce, not cryptographic authentication. */
}
static int stop_usb(void){
    unsigned i,tries;int result;
    sceKernelWaitSema(lifecycle_lock,1,NULL);
    active=0;set_stage(PDC_STAGE_STOPPING,0);
    /* A callback may have reserved EP0 but not queued it yet. Drain
     * submission BEFORE cancelling requests, otherwise it could queue
     * after CancelAll and keep a buffer owned past module teardown. */
    for(tries=0;(ctl_submitting||bulk_submitting) && tries<2000;tries++)sceKernelDelayThread(1000);
    if(ctl_submitting||bulk_submitting){result=-1003;goto done;}
    if(activated){result=sceUsbDeactivate(PDC_TEST_PRODUCT);if(result<0)goto done;activated=0;}
    if(started){
        for(i=0;i<4;i++)sceUsbbdReqCancelAll(&pdc_endpoints[i]);
        for(tries=0;(hid_busy||ctl_busy||rx_busy||tx_busy)&&tries<2000;tries++)sceKernelDelayThread(1000);
        if(hid_busy||ctl_busy||rx_busy||tx_busy){result=-1001;goto done;}
        result=sceUsbStop(PDC_DRIVER_NAME,0,NULL);if(result<0)goto done;started=0;
    }
    msc_reset(&bot);
    if(bus_owned){result=sceUsbStop(PSP_USBBUS_DRIVERNAME,0,NULL);if(result<0)goto done;bus_owned=0;}
    connected=0;result=0;
done:
    sceKernelSignalSema(lifecycle_lock,1);return result;
}
/* Only the supervisor owns the bulk DMA buffers and the SCSI engine.
 * USB callbacks merely publish completion or reset notifications. */
static int bulk_submit(int receive,unsigned size){
    struct UsbdDeviceReq *r=receive?&rx_req:&tx_req;
    int irq,result;
    irq=sceKernelCpuSuspendIntr();
    if(!active || !connected || link_reset_pending || bot_reset_pending ||
       (receive?(rx_busy||rx_ready):(tx_busy||tx_ready))){
        sceKernelCpuResumeIntr(irq);return 1;
    }
    bulk_submitting=1;
    if(receive){rx_busy=1;rx_ready=0;rx_generation=link_generation;}
    else{tx_busy=1;tx_ready=0;tx_generation=link_generation;}
    sceKernelCpuResumeIntr(irq);
    memset(r,0,sizeof(*r));
    r->endp=&pdc_endpoints[receive?3:2];r->size=(int)size;
    if(receive){
        r->data=command_buffer;r->func=rx_done;
        sceKernelDcacheWritebackInvalidateRange(command_buffer,sizeof(command_buffer));
        result=sceUsbbdReqRecv(r);
    }else{
        r->data=response_buffer;r->func=tx_done;
        sceKernelDcacheWritebackRange(response_buffer,sizeof(response_buffer));
        result=sceUsbbdReqSend(r);
    }
    irq=sceKernelCpuSuspendIntr();
    if(result<0){if(receive)rx_busy=0;else tx_busy=0;}
    bulk_submitting=0;sceKernelCpuResumeIntr(irq);
    return result<0?result:0;
}
static int take_tx(int *code,int *size){
    int irq=sceKernelCpuSuspendIntr(),ready=tx_ready;
    if(ready){*code=tx_code;*size=tx_size;tx_ready=0;}
    sceKernelCpuResumeIntr(irq);return ready;
}
static int apply_data_stalls(void){
    int result;
    if(!active || !connected || link_reset_pending)return 1;
    if(bot.plan.stall_in){result=sceUsbbdStall(&pdc_endpoints[2]);if(result<0)return result;}
    if(!active || !connected || link_reset_pending)return 1;
    if(bot.plan.stall_out){result=sceUsbbdStall(&pdc_endpoints[3]);if(result<0)return result;}
    return 0;
}
static int recover_bot(void){
    unsigned tries;int result,irq;
    uint32_t generation=bot_reset_generation,sequence=bot_reset_sequence;
    /* This runs in the only bulk submitter, so a reset callback cannot
     * race a later data submission. Cancel/drain; preserve HALT/toggles. */
    sceUsbbdReqCancelAll(&pdc_endpoints[2]);
    sceUsbbdReqCancelAll(&pdc_endpoints[3]);
    for(tries=0;(rx_busy||tx_busy)&&tries<2000;tries++)sceKernelDelayThread(1000);
    if(rx_busy||tx_busy)return -1301;
    for(tries=0;ctl_busy && tries<2000;tries++)sceKernelDelayThread(1000);
    if(ctl_busy)return -1302;
    irq=sceKernelCpuSuspendIntr();
    rx_ready=0;tx_ready=0;
    sceKernelCpuResumeIntr(irq);
    if(link_reset_pending || generation!=link_generation || sequence!=bot_reset_sequence)
        return 1; /* The setup packet belongs to an old configuration/reset. */
    msc_reset(&bot);bot_phase=BOT_CBW;data_pending=0;data_stalls_applied=0;
    /* Zero-byte send supplies the control status phase only after drain.
     * Actual USBBus status-phase/CLEAR_FEATURE routing is a hardware gate.
     * We do not wait exclusively on recvctl seeing standard endpoint
     * requests: USBBus may consume them internally. A queued bulk request
     * must remain hardware-blocked until the host clears that endpoint.
     * Queue rejection is surfaced as an error, never cleared locally. */
    result=control_submit_checked(NULL,0,0,1,generation);if(result<0)return result;
    irq=sceKernelCpuSuspendIntr();
    if(sequence==bot_reset_sequence)bot_reset_pending=0;
    sceKernelCpuResumeIntr(irq);return 0;
}
static int advance_bot(void){
    int irq,ready,code=0,size=0,result;size_t count;
    if(bot_phase==BOT_RESET_WAIT)return 0;
    if(bot_phase==BOT_CBW){
        irq=sceKernelCpuSuspendIntr();ready=rx_ready;
        if(ready){code=rx_code;size=rx_size;rx_ready=0;}
        sceKernelCpuResumeIntr(irq);
        if(!ready)return rx_busy?0:bulk_submit(1,sizeof(command_buffer));
        if(code!=0){if(ui)ui->rx_errors++;return code<0?code:-1310;}
        sceKernelDcacheInvalidateRange(command_buffer,sizeof(command_buffer));
        result=msc_begin(&bot,command_buffer,(size_t)size);
        if(ui){
            ui->last_scsi_opcode=bot.plan.valid_cbw?command_buffer[15]:UINT32_MAX;
            ui->scsi_host_length=bot.plan.host_length;ui->scsi_completed=bot.completed;
        }
        if(result<0){
            if(ui)ui->rx_errors++;
            result=apply_data_stalls();bot_phase=BOT_RESET_WAIT;
            if(ui)ui->bot_phase=(uint32_t)bot_phase;
            return result;
        }
        data_pending=0;data_stalls_applied=0;bot_phase=BOT_DATA;
        if(ui)ui->bot_phase=(uint32_t)bot_phase;
        set_stage(PDC_STAGE_FILE,0);
    }
    if(bot_phase==BOT_DATA){
        if(take_tx(&code,&size)){
            if(code!=0 || size!=(int)data_pending)return code<0?code:-1311;
            if(msc_data_complete(&bot,(uint32_t)size)<0)return -1312;
            if(ui){ui->bytes_read+=(uint32_t)size;ui->scsi_completed=bot.completed;}
            data_pending=0;
        }
        /* A completion may arrive immediately after take_tx tested its
         * flag. Keep the outstanding chunk reserved until its completion
         * is consumed; tx_busy alone cannot prove the buffer is free. */
        if(data_pending || tx_busy)return 0;
        if(bot.completed<bot.plan.transfer_length){
            /* Every command implemented by the synthetic backend is
             * IN or NONE. WRITE/MODE SELECT fail DATA PROTECT with OUT
             * stalled; there is no real-storage or discard-write path. */
            if(bot.plan.direction!=MSC_DIR_IN)return -1313;
            count=msc_read_data(&bot,bot.completed,response_buffer,sizeof(response_buffer));
            if(!count || count>sizeof(response_buffer))return -1314;
            data_pending=(unsigned)count;return bulk_submit(0,(unsigned)count);
        }
        bot_phase=BOT_FINISH_DATA;
        if(ui)ui->bot_phase=(uint32_t)bot_phase;
    }
    if(bot_phase==BOT_FINISH_DATA){
        if(!data_stalls_applied){
            result=apply_data_stalls();if(result!=0)return result;
            data_stalls_applied=1;
        }
        if(msc_csw(&bot,response_buffer)!=(int)MSC_CSW_SIZE)return -1315;
        bot_phase=BOT_CSW;
        if(ui)ui->bot_phase=(uint32_t)bot_phase;
    }
    if(bot_phase==BOT_CSW){
        /* A halted IN pipe is not cleared by firmware. USBBus must hold
         * this CSW until host CLEAR_FEATURE. Verify this on the PSP. */
        result=bulk_submit(0,MSC_CSW_SIZE);
        if(result==0){bot_phase=BOT_CSW_WAIT;if(ui)ui->bot_phase=(uint32_t)bot_phase;}
        return result;
    }
    if(bot_phase==BOT_CSW_WAIT && take_tx(&code,&size)){
        if(code!=0 || size!=(int)MSC_CSW_SIZE)return code<0?code:-1316;
        commands++;if(ui)ui->bulk_commands=commands;
        bot_phase=bot.plan.reset_required?BOT_RESET_WAIT:BOT_CBW;
        if(ui)ui->bot_phase=(uint32_t)bot_phase;
        set_stage(PDC_STAGE_CONNECTED,0);
    }
    return 0;
}
static int observe_cable(uint32_t now,int usb){
    if(ui)ui->usb_state=(uint32_t)usb;
    if(usb<0)return usb;
    if(!ever_connected || (usb&PSP_USB_CABLE_CONNECTED)){
        cable_absence_pending=0;return 0;
    }
    if(!cable_absence_pending){cable_absence_pending=1;cable_absent_since=now;}
    else if((uint32_t)(now-cable_absent_since)>=500000u)
        request_shutdown(PDC_STOP_CABLE);
    return 0;
}
static int poll_cable(void){
    uint32_t now=sceKernelGetSystemTimeLow();
    if(usb_poll_started && !link_reset_pending && (uint32_t)(now-last_usb_poll)<50000u)
        return 0;
    usb_poll_started=1;last_usb_poll=now;
    return observe_cable(now,sceUsbGetState());
}
static int quiesce_link(void){
    uint32_t generation;unsigned tries;int irq,result=0,old_control;
    sceKernelWaitSema(lifecycle_lock,1,NULL);
    irq=sceKernelCpuSuspendIntr();
    generation=link_generation;connected=0;
    old_control=(ctl_busy||ctl_submitting) && ctl_generation!=generation;
    if(old_control)ctl_quiescing=1;
    sceKernelCpuResumeIntr(irq);
    /* Holding lifecycle_lock drains any pad submit already in flight.
     * This supervisor is the sole bulk submitter. EP0 stays available
     * except while cancelling a request from the OLD configuration. */
    if(old_control){
        for(tries=0;ctl_submitting && tries<2000;tries++)sceKernelDelayThread(1000);
        if(ctl_submitting){result=-1320;goto done;}
        if(ctl_busy)sceUsbbdReqCancelAll(&pdc_endpoints[0]);
        for(tries=0;ctl_busy && tries<2000;tries++)sceKernelDelayThread(1000);
        if(ctl_busy){result=-1321;goto done;}
        ctl_quiescing=0;
    }
    sceUsbbdReqCancelAll(&pdc_endpoints[1]);
    sceUsbbdReqCancelAll(&pdc_endpoints[2]);
    sceUsbbdReqCancelAll(&pdc_endpoints[3]);
    for(tries=0;(hid_busy||rx_busy||tx_busy)&&tries<2000;tries++)sceKernelDelayThread(1000);
    if(hid_busy||rx_busy||tx_busy){result=-1322;goto done;}
    msc_reset(&bot);bot_phase=BOT_CBW;data_pending=0;data_stalls_applied=0;
    irq=sceKernelCpuSuspendIntr();
    rx_ready=0;tx_ready=0;need_initial_pad=1;
    /* A fresh BOT reset may arrive while cancelling an old transfer.
     * Preserve that request; only discard requests from the old epoch. */
    if(bot_reset_pending && bot_reset_generation!=link_generation)bot_reset_pending=0;
    if(generation==link_generation){
        link_reset_pending=0;connected=active && link_attached;
    }
    sceKernelCpuResumeIntr(irq);
    set_stage(connected?PDC_STAGE_CONNECTED:PDC_STAGE_RECONNECT,0);
done:
    ctl_quiescing=0;sceKernelSignalSema(lifecycle_lock,1);return result;
}
static int io_worker(SceSize args,void *argp){
    int usb,storage,bus,result;
    (void)args;(void)argp;
    set_stage(PDC_STAGE_GUARD,0);
    usb=sceUsbGetState();if(ui)ui->usb_state=(uint32_t)usb;
    storage=sceUsbGetDrvState(PSP_USBSTOR_DRIVERNAME);
    bus=sceUsbGetDrvState(PSP_USBBUS_DRIVERNAME);
    if(!hid_lab_safe_to_start(usb,storage,bus)){result=-1002;goto ended;}
    if(stop_requested || (ui && ui->request_stop)){result=0;goto ended;}
    if(bus==2){result=sceUsbStart(PSP_USBBUS_DRIVERNAME,0,NULL);if(result<0)goto ended;bus_owned=1;}
    result=sceUsbbdRegister(&driver);if(result<0)goto cleanup;
    registered=1;
    if(stop_requested || (ui && ui->request_stop)){result=0;goto cleanup;}
    make_session();msc_init(&bot,session);bot_phase=BOT_CBW;
    result=sceUsbStart(PDC_DRIVER_NAME,0,NULL);if(result<0)goto cleanup;
    started=1;active=1;result=sceUsbActivate(PDC_TEST_PRODUCT);
    if(result<0)goto cleanup;
    activated=1;set_stage(PDC_STAGE_READY,0);
    while(!stop_requested && !(ui && ui->request_stop)){
        result=poll_cable();if(result<0){bulk_fault=result;break;}
        if(stop_requested || (ui && ui->request_stop))break;
        if(link_reset_pending){
            result=quiesce_link();if(result<0){bulk_fault=result;break;}
        }
        if(connected && active && !link_reset_pending){
            if(bot_reset_pending)result=recover_bot();
            else result=advance_bot();
            if(result<0 && !link_reset_pending){bulk_fault=result;break;}
        }
        sceKernelDelayThread(1000);
    }
    result=bulk_fault;
cleanup:
    {int stopped=stop_usb();if(stopped<0)result=stopped;}
ended:
    running=0;
    if(ui){
        if(result<0)ui->stop_reason=PDC_STOP_ERROR;
        else if(!ui->stop_reason && ui->request_stop)ui->stop_reason=PDC_STOP_USER;
    }
    if(result<0)set_stage(PDC_STAGE_ERROR,result);
    else set_stage(PDC_STAGE_STOPPED,0);
    if(ui && !started && !activated && !bus_owned && !hid_busy && !ctl_busy &&
       !ctl_submitting && !bulk_submitting && !rx_busy && !tx_busy)ui->safe_to_unload=1;
    return 0;
}
static int valid_shared(PdcSharedStatus *p){
    uintptr_t address=(uintptr_t)p;
    return !(address&3u) && address>=0x08800000u &&
           address<=0x0a000000u-sizeof(*p) && p->magic==PDC_LAUNCH_MAGIC && p->size==sizeof(*p);
}
int module_start(SceSize args,void *argp){
    PdcLaunchArgs launch;int result;
    if(args!=sizeof(launch) || !argp)return -1;
    memcpy(&launch,argp,sizeof(launch));
    if(launch.magic!=PDC_LAUNCH_MAGIC || launch.size!=sizeof(launch) || !valid_shared(launch.shared))return -1;
    ui=launch.shared;ui->safe_to_unload=0;ui->request_stop=0;
    ui->error=0;ui->stage=PDC_STAGE_INIT;
    lifecycle_lock=sceKernelCreateSema("MSC5Lifecycle",0,1,1,NULL);
    if(lifecycle_lock<0)return lifecycle_lock;
    pad_thread=sceKernelCreateThread("MSC5Joystick",sample_pad,0x20,0x3000,0,NULL);
    io_thread=sceKernelCreateThread("MSC5Disk",io_worker,0x24,0x6000,0,NULL);
    if(pad_thread<0 || io_thread<0){result=-1200;goto failed;}
    running=1;result=sceKernelStartThread(pad_thread,0,NULL);if(result<0)goto failed;
    pad_started=1;
    result=sceKernelStartThread(io_thread,0,NULL);if(result>=0)return 0;
failed:
    running=0;
    if(pad_thread>=0){
        if(pad_started)sceKernelWaitThreadEnd(pad_thread,NULL);
        sceKernelDeleteThread(pad_thread);pad_thread=-1;
    }
    if(io_thread>=0){sceKernelDeleteThread(io_thread);io_thread=-1;}
    /* No USB registration is reachable before io_thread was started. */
    if(lifecycle_lock>=0){sceKernelDeleteSema(lifecycle_lock);lifecycle_lock=-1;}
    ui->safe_to_unload=1;return result;
}
int module_stop(SceSize args,void *argp){
    int result;SceUInt timeout=3000000;(void)args;(void)argp;
    request_shutdown(PDC_STOP_MODULE);
    if(io_thread>=0){result=sceKernelWaitThreadEnd(io_thread,&timeout);if(result<0)return result;}
    running=0;timeout=1000000;
    if(pad_thread>=0){result=sceKernelWaitThreadEnd(pad_thread,&timeout);if(result<0)return result;}
    if(started||activated||bus_owned||hid_busy||ctl_busy||ctl_submitting||bulk_submitting||rx_busy||tx_busy)return -1;
    if(registered){result=sceUsbbdUnregister(&driver);if(result<0)return result;registered=0;}
    if(io_thread>=0){sceKernelDeleteThread(io_thread);io_thread=-1;}
    if(pad_thread>=0){sceKernelDeleteThread(pad_thread);pad_thread=-1;}
    if(lifecycle_lock>=0){sceKernelDeleteSema(lifecycle_lock);lifecycle_lock=-1;}
    ui=NULL;return 0;
}
