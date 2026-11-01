/* SPDX-License-Identifier: GPL-3.0-only
 * HID derived from the tested FuSa/PSPDockHID adapter, upstream
 * 97a1fccb8cb561b14964de4d4dbab4fcd8618297. See LICENSE and README.
 *
 * Executable experimental composite controller: joystick + read-only files.
 * USB callbacks NEVER perform file operations. Every DMA buffer has a
 * single owner and is retained until its completion/cancellation callback.
 * This driver NEVER starts Sony USBSTOR and has no file-write API.
 */
#include <pspkernel.h>
#include <pspctrl.h>
#include <pspusb.h>
#include <pspusbstor.h>
#include <pspusbbus.h>
#include <pspintrman.h>
#include <pspthreadman.h>
#include <string.h>
#include "pdc_common.h"
#include "pdc_descriptors.h"
#include "pdc_os_descriptors.h"
#include "pdc_psp_fs.h"
#include "lab_gate.h"

PSP_MODULE_INFO("PSPDockCompositeRO",PSP_MODULE_KERNEL,2,3);

static volatile int running,active,connected,stop_requested,ever_connected;
static volatile int hid_busy,ctl_busy,rx_busy,tx_busy,rx_ready,bulk_fault;
static volatile int ctl_submitting;
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
static uint8_t command_buffer[PDC_COMMAND_CELL] __attribute__((aligned(64)));
static uint8_t response_buffer[PDC_RESPONSE_CELL] __attribute__((aligned(64)));
static uint8_t file_buffer[PDB2_MAX_PAYLOAD] __attribute__((aligned(64)));
static struct UsbdDeviceReq pad_req,ctl_req,rx_req,tx_req;
static PdcService service;

static void set_stage(unsigned stage,int error) {
    if(ui){ui->stage=stage;if(error)ui->error=error;}
}
static void put32(uint8_t *p,uint32_t n) {
    p[0]=(uint8_t)n;p[1]=(uint8_t)(n>>8);p[2]=(uint8_t)(n>>16);p[3]=(uint8_t)(n>>24);
}
static void hid_status(uint8_t out[32]) {
    int irq=sceKernelCpuSuspendIntr();
    memset(out,0,32);memcpy(out,"PDK1",4);
    out[4]=1;out[5]=1;out[6]=active?2:0;out[7]=connected?1:0;
    memcpy(out+8,session,16);sceKernelCpuResumeIntr(irq);
}
static void composite_status(uint8_t out[32]) {
    int irq=sceKernelCpuSuspendIntr();
    memset(out,0,32);memcpy(out,"PDC2",4);
    out[4]=2;out[5]=PDC_CAPABILITIES;out[6]=active?1:0;out[7]=connected?1:0;
    memcpy(out+8,session,16);put32(out+24,commands);put32(out+28,(uint32_t)bulk_fault);
    sceKernelCpuResumeIntr(irq);
}
static int pad_done(struct UsbdDeviceReq *r,int a,int b) {
    (void)a;(void)b;
    if(r->retcode==0 && r->recvsize==8){hid_sent++;if(ui)ui->hid_reports=hid_sent;}
    hid_busy=0;return 0;
}
static int ctl_done(struct UsbdDeviceReq *r,int a,int b) {
    (void)r;(void)a;(void)b;ctl_busy=0;return 0;
}
static int rx_done(struct UsbdDeviceReq *r,int a,int b) {
    int irq;(void)a;(void)b;
    irq=sceKernelCpuSuspendIntr();
    rx_code=r->retcode;rx_size=r->recvsize;rx_ready=1;rx_busy=0;
    sceKernelCpuResumeIntr(irq);
    return 0;
}
static int tx_done(struct UsbdDeviceReq *r,int a,int b) {
    (void)a;(void)b;
    tx_code=(r->retcode==0 && r->recvsize!=(int)PDC_RESPONSE_CELL)?-1103:r->retcode;
    tx_busy=0;return 0;
}
static int control_submit(const uint8_t *data,int size,int receive) {
    int irq=sceKernelCpuSuspendIntr();int result;
    if(!active || ctl_busy || size<0 || size>(int)sizeof(ctl_buffer)) {
        sceKernelCpuResumeIntr(irq);return -1;
    }
    ctl_busy=1;ctl_submitting=1;sceKernelCpuResumeIntr(irq);
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
static int receive_control(int endpoint,int arg2,struct DeviceRequest *r) {
    uint8_t answer[256];size_t os_size;int length,irq;
    (void)endpoint;
    if(!r || arg2==-1)return -1;
    /* Must dispatch OS string/vendor requests BEFORE HID interface checks. */
    os_size=pdc_os_control(r->bmRequestType,r->bRequest,r->wValue,r->wIndex,
                           r->wLength,answer,sizeof(answer));
    if(os_size)return control_submit(answer,(int)os_size,0);
    if(r->bmRequestType==0xc1 && r->bRequest==PDC_STATUS_REQUEST &&
       r->wIndex==PDC_USB_INTERFACE && r->wValue==0){
        composite_status(answer);length=32;if(r->wLength<length)length=r->wLength;
        return control_submit(answer,length,0);
    }
    if(r->wIndex!=0)return -1; /* Other standard requests belong to USBBus. */
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
    /* FuSa's output report has no effects. REQUEST_STORAGE is deliberately
     * NOT implemented: no role switch and no competing owner of FAT. */
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
    (void)a;(void)b;
    if(speed!=1 && speed!=2){bulk_fault=-10;return -1;}
    ever_connected=1;connected=1;set_stage(PDC_STAGE_CONNECTED,0);return 0;
}
static int detach(int a,int b,int c){
    (void)a;(void)b;(void)c;
    connected=0;
    if(ever_connected)stop_requested=1;
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
                if(++hold>=250)stop_requested=1;
            }else hold=0;
            sceKernelWaitSema(lifecycle_lock,1,NULL);
            if(active && connected){
                encode_pad(&pad,sample);irq=sceKernelCpuSuspendIntr();
                memcpy(latest_pad,sample,8);
                send=!hid_busy && (!hid_sent || memcmp(pad_buffer,sample,8) ||
                     (idle_rate && (uint32_t)(sceKernelGetSystemTimeLow()-last_pad_time)>=(uint32_t)idle_rate*4000));
                if(send){hid_busy=1;memcpy(pad_buffer,sample,8);}
                sceKernelCpuResumeIntr(irq);
                if(send){
                    memset(&pad_req,0,sizeof(pad_req));pad_req.endp=&pdc_endpoints[1];
                    pad_req.data=pad_buffer;pad_req.size=8;pad_req.func=pad_done;
                    sceKernelDcacheWritebackRange(pad_buffer,sizeof(pad_buffer));
                    result=sceUsbbdReqSend(&pad_req);
                    if(result<0)hid_busy=0;
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
    for(tries=0;ctl_submitting && tries<2000;tries++)sceKernelDelayThread(1000);
    if(ctl_submitting){result=-1003;goto done;}
    if(activated){result=sceUsbDeactivate(PDC_TEST_PRODUCT);if(result<0)goto done;activated=0;}
    if(started){
        for(i=0;i<4;i++)sceUsbbdReqCancelAll(&pdc_endpoints[i]);
        for(tries=0;(hid_busy||ctl_busy||rx_busy||tx_busy)&&tries<2000;tries++)sceKernelDelayThread(1000);
        if(hid_busy||ctl_busy||rx_busy||tx_busy){result=-1001;goto done;}
        result=sceUsbStop(PDC_DRIVER_NAME,0,NULL);if(result<0)goto done;started=0;
    }
    pdc_service_reset(&service);
    if(bus_owned){result=sceUsbStop(PSP_USBBUS_DRIVERNAME,0,NULL);if(result<0)goto done;bus_owned=0;}
    connected=0;result=0;
done:
    sceKernelSignalSema(lifecycle_lock,1);return result;
}
static int io_worker(SceSize args,void *argp){
    int usb,storage,bus,result,received_length,irq,have_rx,queue_rx;
    int completed_code,completed_size;PdcReadOnlyFS fs;
    (void)args;(void)argp;
    set_stage(PDC_STAGE_GUARD,0);
    usb=sceUsbGetState();storage=sceUsbGetDrvState(PSP_USBSTOR_DRIVERNAME);
    bus=sceUsbGetDrvState(PSP_USBBUS_DRIVERNAME);
    if(!hid_lab_safe_to_start(usb,storage,bus)){result=-1002;goto ended;}
    if(stop_requested || (ui && ui->request_stop)){result=0;goto ended;}
    if(bus==2){result=sceUsbStart(PSP_USBBUS_DRIVERNAME,0,NULL);if(result<0)goto ended;bus_owned=1;}
    /* Register only after the no-storage/no-cable gate, and inside the
     * supervisor thread. A module_start allocation failure must never
     * leave live USB descriptor pointers into an unloaded module. */
    result=sceUsbbdRegister(&driver);if(result<0)goto cleanup;
    registered=1;
    if(stop_requested || (ui && ui->request_stop)){result=0;goto cleanup;}
    make_session();pdc_psp_readonly_fs(&fs);pdc_service_init(&service,session,&fs);
    result=sceUsbStart(PDC_DRIVER_NAME,0,NULL);if(result<0)goto cleanup;
    started=1;active=1;result=sceUsbActivate(PDC_TEST_PRODUCT);
    if(result<0)goto cleanup;
    activated=1;set_stage(PDC_STAGE_READY,0);
    while(!stop_requested && !(ui && ui->request_stop)){
        if(connected && active && !bulk_fault && !tx_busy){
            /* Snapshot and claim the OUT slot atomically. A completion
             * between testing rx_ready and rx_busy must not cause a
             * second receive to overwrite an unprocessed command. */
            irq=sceKernelCpuSuspendIntr();
            have_rx=rx_ready;completed_code=rx_code;completed_size=rx_size;
            queue_rx=!have_rx && !rx_busy;
            if(have_rx)rx_ready=0;
            if(queue_rx)rx_busy=1;
            sceKernelCpuResumeIntr(irq);
            if(have_rx){
                if(completed_code!=0 || completed_size!=(int)PDC_COMMAND_CELL){bulk_fault=-1101;break;}
                /* Guard against another plugin starting Sony USBSTOR:
                 * no filesystem callback is permitted if FAT ownership
                 * becomes ambiguous, even though this service is read-only. */
                storage=sceUsbGetDrvState(PSP_USBSTOR_DRIVERNAME);
                if(!(storage==HID_LAB_DRIVER_STOPPED ||
                    (uint32_t)storage==HID_LAB_STORAGE_NOENT)){
                    bulk_fault=-1104;break;
                }
                sceKernelDcacheInvalidateRange(command_buffer,sizeof(command_buffer));
                set_stage(PDC_STAGE_FILE,0);
                received_length=pdc_service_process(&service,command_buffer,PDC_COMMAND_CELL,
                    response_buffer,sizeof(response_buffer),file_buffer,sizeof(file_buffer));
                if(received_length<=0){bulk_fault=-1102;break;}
                commands++;
                if(ui){
                    ui->bulk_commands=commands;
                    if(response_buffer[5]==PDB2_OP_DATA)
                        ui->bytes_read+=(unsigned)received_length-PDB2_HEADER_SIZE-PDB2_CRC_SIZE;
                }
                memset(&tx_req,0,sizeof(tx_req));tx_req.endp=&pdc_endpoints[2];
                tx_req.data=response_buffer;tx_req.size=sizeof(response_buffer);tx_req.func=tx_done;
                tx_code=0;tx_busy=1;
                sceKernelDcacheWritebackRange(response_buffer,sizeof(response_buffer));
                result=sceUsbbdReqSend(&tx_req);
                if(result<0){tx_busy=0;bulk_fault=result;break;}
                set_stage(PDC_STAGE_CONNECTED,0);
            }else if(queue_rx){
                if(tx_code<0){rx_busy=0;bulk_fault=tx_code;break;}
                memset(&rx_req,0,sizeof(rx_req));rx_req.endp=&pdc_endpoints[3];
                rx_req.data=command_buffer;rx_req.size=sizeof(command_buffer);rx_req.func=rx_done;
                sceKernelDcacheWritebackInvalidateRange(command_buffer,sizeof(command_buffer));
                result=sceUsbbdReqRecv(&rx_req);
                if(result<0){rx_busy=0;bulk_fault=result;break;}
            }
        }
        sceKernelDelayThread(1000);
    }
    result=bulk_fault;
cleanup:
    {int stopped=stop_usb();if(stopped<0)result=stopped;}
ended:
    running=0;
    if(result<0)set_stage(PDC_STAGE_ERROR,result);
    else set_stage(PDC_STAGE_STOPPED,0);
    if(ui && !started && !activated && !bus_owned && !hid_busy && !ctl_busy && !ctl_submitting && !rx_busy && !tx_busy)
        ui->safe_to_unload=1;
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
    ui->error=0;ui->stage=PDC_STAGE_INIT;service.file=-1;
    lifecycle_lock=sceKernelCreateSema("PDC2Lifecycle",0,1,1,NULL);
    if(lifecycle_lock<0)return lifecycle_lock;
    pad_thread=sceKernelCreateThread("PDC2Joystick",sample_pad,0x20,0x3000,0,NULL);
    io_thread=sceKernelCreateThread("PDC2ReadOnly",io_worker,0x24,0x6000,0,NULL);
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
    stop_requested=1;
    if(io_thread>=0){result=sceKernelWaitThreadEnd(io_thread,&timeout);if(result<0)return result;}
    running=0;timeout=1000000;
    if(pad_thread>=0){result=sceKernelWaitThreadEnd(pad_thread,&timeout);if(result<0)return result;}
    if(started||activated||bus_owned||hid_busy||ctl_busy||ctl_submitting||rx_busy||tx_busy)return -1;
    if(registered){result=sceUsbbdUnregister(&driver);if(result<0)return result;registered=0;}
    if(io_thread>=0){sceKernelDeleteThread(io_thread);io_thread=-1;}
    if(pad_thread>=0){sceKernelDeleteThread(pad_thread);pad_thread=-1;}
    if(lifecycle_lock>=0){sceKernelDeleteSema(lifecycle_lock);lifecycle_lock=-1;}
    ui=NULL;return 0;
}
