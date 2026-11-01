/* Exercise the actual PSP transport state machine using asynchronous USB
 * doubles. This verifies ownership/sequencing, not PSP USBBus semantics. */
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "msc_psp_stub/psp_test_api.h"
#define PSPDOCK_MSC_DESCRIPTORS_H
static struct UsbEndpoint pdc_endpoints[4]={{0,0,0},{1,0,0},{2,0,0},{3,0,0}};
static int pdc_interface,pdc_device_hi,pdc_config_hi,pdc_device_full,pdc_config_full,pdc_strings[1];
static uint8_t report_descriptor[9],pdc_hid_descriptor[9];
#include "../homebrew/composite-mscdemo/msc_driver.c"

static struct UsbdDeviceReq *pending[4];
static unsigned submissions[4];
static int halted[4],queue_error;
static PdcSharedStatus state;

int sceUsbbdReqSend(struct UsbdDeviceReq *r){
    unsigned ep=(unsigned)r->endp->endpnum;
    if(queue_error)return queue_error;
    assert(ep<4 && !pending[ep]);pending[ep]=r;submissions[ep]++;return 0;
}
int sceUsbbdReqRecv(struct UsbdDeviceReq *r){return sceUsbbdReqSend(r);}
int sceUsbbdStall(struct UsbEndpoint *ep){halted[ep->endpnum]=1;return 0;}
static void complete(unsigned ep,int code,int size){
    struct UsbdDeviceReq *r=pending[ep];assert(r);
    assert(code<0 || !halted[ep]);pending[ep]=NULL;
    r->retcode=code;r->recvsize=size;r->func(r,0,0);
}
int sceUsbbdReqCancelAll(struct UsbEndpoint *ep){
    if(pending[ep->endpnum])complete((unsigned)ep->endpnum,-99,0);
    return 0;
}
static void fixture(void){
    uint8_t nonce[16]={7};
    memset(pending,0,sizeof(pending));memset(submissions,0,sizeof(submissions));
    memset(halted,0,sizeof(halted));memset(&state,0,sizeof(state));
    active=1;connected=1;hid_busy=ctl_busy=rx_busy=tx_busy=rx_ready=tx_ready=0;
    ctl_submitting=bulk_submitting=bot_reset_pending=reset_clear_mask=0;
    bulk_fault=queue_error=stop_requested=0;commands=0;ui=&state;
    data_pending=0;data_stalls_applied=0;bot_phase=BOT_CBW;msc_init(&bot,nonce);
}
static void cbw_read(uint32_t bytes,unsigned blocks){
    memset(command_buffer,0,sizeof(command_buffer));memcpy(command_buffer,"USBC",4);
    command_buffer[4]=9;
    command_buffer[8]=(uint8_t)bytes;command_buffer[9]=(uint8_t)(bytes>>8);
    command_buffer[10]=(uint8_t)(bytes>>16);command_buffer[11]=(uint8_t)(bytes>>24);
    command_buffer[12]=0x80;command_buffer[14]=10;command_buffer[15]=0x28;
    command_buffer[20]=MSC_DATA_LBA;
    command_buffer[22]=(uint8_t)(blocks>>8);command_buffer[23]=(uint8_t)blocks;
}
static void test_read_crosses_dma_buffers(void){
    fixture();assert(advance_bot()==0);assert(pending[3] && pending[3]->size==512);
    cbw_read(66560,130);complete(3,0,31);assert(advance_bot()==0);
    assert(pending[2] && pending[2]->size==65536 && !memcmp(response_buffer,"\003\024\045",3));
    assert(advance_bot()==0 && submissions[2]==1 && bot.completed==0);
    complete(2,0,65536);assert(advance_bot()==0);
    assert(pending[2]->size==1024 && bot.completed==65536 && state.bytes_read==65536);
    complete(2,0,1024);assert(advance_bot()==0);
    assert(pending[2]->size==13 && !memcmp(response_buffer,"USBS",4));
    assert(commands==0 && !pending[3]);
    complete(2,0,13);assert(advance_bot()==0 && commands==1);
    assert(state.bytes_read==66560 && bot_phase==BOT_CBW);
}
static void test_completions_cannot_be_overwritten(void){
    fixture();rx_ready=1;rx_size=31;
    assert(bulk_submit(1,512)==1 && rx_ready==1 && submissions[3]==0);
    rx_ready=0;tx_ready=1;tx_size=13;
    assert(bulk_submit(0,13)==1 && tx_ready==1 && submissions[2]==0);
    tx_ready=0;bot_phase=BOT_DATA;data_pending=512;
    assert(advance_bot()==0 && submissions[2]==0);
}
static void test_bad_cbw_reset_preserves_halts(void){
    struct DeviceRequest reset={0x21,0xff,0,1,0};
    fixture();assert(advance_bot()==0);memset(command_buffer,0,31);complete(3,0,31);
    assert(advance_bot()==0 && bot_phase==BOT_RESET_WAIT && halted[2] && halted[3]);
    assert(!pending[2] && receive_control(0,0,&reset)==0 && bot_reset_pending);
    assert(recover_bot()==0 && halted[2] && halted[3] && !bot_reset_pending);
    assert(pending[0] && pending[0]->size==0);complete(0,0,0);
    assert(advance_bot()==0 && pending[3]);
    /* The test double holds the request; only the host clears HALT. */
    halted[2]=halted[3]=0;cbw_read(512,1);complete(3,0,31);
    assert(advance_bot()==0 && pending[2] && pending[2]->size==512);
}
static void test_reset_cancels_old_data_before_ack(void){
    struct DeviceRequest reset={0x21,0xff,0,1,0};
    fixture();advance_bot();cbw_read(512,1);complete(3,0,31);advance_bot();
    assert(pending[2] && tx_busy);receive_control(0,0,&reset);
    assert(recover_bot()==0 && !pending[2] && !tx_busy && !tx_ready);
    assert(pending[0] && data_pending==0 && !bot.command_active);
}
static void test_queue_error_and_short_completion(void){
    fixture();queue_error=-77;assert(advance_bot()==-77 && !rx_busy && !bulk_submitting);
    fixture();advance_bot();cbw_read(512,1);complete(3,0,31);advance_bot();
    complete(2,0,511);assert(advance_bot()==-1311 && bot.completed==0);
    assert(!pending[2] && !pending[3]);
}
int main(void){
    test_read_crosses_dma_buffers();test_completions_cannot_be_overwritten();
    test_bad_cbw_reset_preserves_halts();test_reset_cancels_old_data_before_ack();
    test_queue_error_and_short_completion();
    fixture();assert(attach(3,NULL,NULL)==-1 && stop_requested && bulk_fault==-10);
    puts("MSC actual transport: 5 asynchronous ownership/reset scenarios passed");return 0;
}
