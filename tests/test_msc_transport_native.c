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
static void (*cancel_hook)(unsigned);

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
    if(cancel_hook)cancel_hook((unsigned)ep->endpnum);
    return 0;
}
static void fixture(void){
    uint8_t nonce[16]={7};
    memset(pending,0,sizeof(pending));memset(submissions,0,sizeof(submissions));
    memset(halted,0,sizeof(halted));memset(&state,0,sizeof(state));
    active=1;connected=1;ever_connected=1;link_attached=1;
    hid_busy=ctl_busy=rx_busy=tx_busy=rx_ready=tx_ready=0;
    ctl_submitting=bulk_submitting=bot_reset_pending=reset_clear_mask=0;
    link_reset_pending=ctl_quiescing=0;need_initial_pad=1;
    link_generation=ctl_generation=bot_reset_generation=bot_reset_sequence=0;
    rx_generation=tx_generation=0;
    cable_absent_since=last_usb_poll=0;cable_absence_pending=usb_poll_started=0;
    cancel_hook=NULL;
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
static void cbw_mode_sense(uint32_t bytes){
    memset(command_buffer,0,sizeof(command_buffer));memcpy(command_buffer,"USBC",4);
    command_buffer[4]=9;
    command_buffer[8]=(uint8_t)bytes;command_buffer[9]=(uint8_t)(bytes>>8);
    command_buffer[10]=(uint8_t)(bytes>>16);command_buffer[11]=(uint8_t)(bytes>>24);
    command_buffer[12]=0x80;command_buffer[14]=6;command_buffer[15]=0x1a;
    command_buffer[17]=0x3f;command_buffer[19]=192;
}
static uint32_t read_le32(const uint8_t *p){
    return (uint32_t)p[0]|((uint32_t)p[1]<<8)|((uint32_t)p[2]<<16)|((uint32_t)p[3]<<24);
}
static void test_mode_sense_192_fills_before_csw_without_halt(void){
    unsigned i;
    fixture();assert(advance_bot()==0);cbw_mode_sense(192);complete(3,0,31);
    assert(advance_bot()==0 && pending[2] && pending[2]->size==192);
    assert(response_buffer[0]==31 && response_buffer[2]==0x80 && response_buffer[12]==8);
    for(i=32;i<192;i++)assert(response_buffer[i]==0);
    assert(!halted[2] && !halted[3] && bot.plan.case_id==5);
    assert(bot.completed==0 && bot.wire_completed==0 && commands==0);
    assert(advance_bot()==0 && submissions[2]==1); /* No CSW before real data completion. */
    complete(2,0,192);assert(advance_bot()==0);
    assert(bot.completed==32 && bot.wire_completed==192 && state.bytes_read==32);
    assert(pending[2] && pending[2]->size==13 && !memcmp(response_buffer,"USBS",4));
    assert(read_le32(response_buffer+4)==9 && read_le32(response_buffer+8)==160 && response_buffer[12]==0);
    assert(!halted[2] && !halted[3] && !pending[3] && commands==0);
    complete(2,0,13);assert(advance_bot()==0 && commands==1 && bot_phase==BOT_CBW);
    assert(advance_bot()==0 && pending[3]);
}
static void test_fill_crosses_dma_buffers_without_repeating_scsi_data(void){
    unsigned i;
    fixture();advance_bot();cbw_mode_sense(66048);complete(3,0,31);
    assert(advance_bot()==0 && pending[2]->size==65536);
    for(i=32;i<65536;i++)assert(response_buffer[i]==0);
    complete(2,0,65536);assert(advance_bot()==0 && pending[2]->size==512);
    assert(bot.completed==32 && bot.wire_completed==65536);
    for(i=0;i<512;i++)assert(response_buffer[i]==0);
    complete(2,0,512);assert(advance_bot()==0 && pending[2]->size==13);
    assert(bot.completed==32 && bot.wire_completed==66048 && state.bytes_read==32);
    assert(read_le32(response_buffer+8)==66016 && response_buffer[12]==0 && !halted[2]);
    complete(2,0,13);assert(advance_bot()==0 && commands==1);
}
static void test_incomplete_fill_never_produces_a_csw(void){
    struct DeviceRequest reset={0x21,0xff,0,1,0};
    fixture();advance_bot();cbw_mode_sense(192);complete(3,0,31);advance_bot();
    complete(2,0,191);assert(advance_bot()==-1311);
    assert(!pending[2] && !commands && !bot.wire_completed && !bot.completed);
    fixture();advance_bot();cbw_mode_sense(192);complete(3,0,31);advance_bot();
    receive_control(0,0,&reset);assert(recover_bot()==0);
    assert(!pending[2] && !commands && !bot.command_active && !bot.wire_completed && !bot.completed);
    assert(pending[0] && pending[0]->size==0);
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
static void test_configuration_unset_is_not_cable_removal(void){
    fixture();advance_bot();cbw_read(512,1);complete(3,0,31);advance_bot();
    assert(tx_busy && pending[2]);
    assert(state.last_scsi_opcode==0x28 && state.bot_phase==BOT_DATA);
    assert(state.scsi_host_length==512 && state.scsi_completed==0);
    assert(detach(3,4,5)==0 && !connected && link_reset_pending && !stop_requested);
    assert(state.detach_count==1 && state.detach_arg1==3 && state.detach_arg2==4 && state.detach_arg3==5);
    assert(observe_cable(1000000,0x122)==0 && !stop_requested);
    assert(attach(2,NULL,NULL)==0 && !connected && state.attach_count==1);
    assert(quiesce_link()==0 && connected && !link_reset_pending && !stop_requested);
    assert(!pending[2] && !rx_ready && !tx_ready && !data_pending && !bot.command_active);
    assert(need_initial_pad && state.stop_reason==PDC_STOP_NONE);
    assert(state.last_scsi_opcode==0x28 && state.bot_phase==BOT_DATA);
    assert(state.scsi_host_length==512 && state.scsi_completed==0 && state.last_tx_code==0);
    assert(advance_bot()==0 && pending[3]);
}
static void reconfigure_during_cancel(unsigned endpoint){
    if(endpoint==2){cancel_hook=NULL;detach(8,9,10);attach(1,NULL,NULL);}
}
static void test_new_unconfigure_during_drain_is_not_lost(void){
    fixture();advance_bot();detach(1,2,3);attach(2,NULL,NULL);
    cancel_hook=reconfigure_during_cancel;
    assert(quiesce_link()==0 && link_generation==2 && link_reset_pending && !connected);
    assert(quiesce_link()==0 && connected && !link_reset_pending && !stop_requested);
}
static void reset_during_cancel(unsigned endpoint){
    struct DeviceRequest reset={0x21,0xff,0,1,0};
    if(endpoint==2){cancel_hook=NULL;assert(receive_control(0,0,&reset)==0);}
}
static void test_current_generation_reset_survives_old_drain(void){
    fixture();advance_bot();detach(0,0,0);attach(2,NULL,NULL);
    cancel_hook=reset_during_cancel;
    assert(quiesce_link()==0 && bot_reset_pending && bot_reset_generation==link_generation);
    assert(recover_bot()==0 && !bot_reset_pending && pending[0]);
}
static void test_old_ep0_cancel_but_current_ep0_preserved(void){
    uint8_t answer=0;
    fixture();assert(control_submit(&answer,1,0)==0 && pending[0]);
    detach(0,0,0);attach(2,NULL,NULL);
    assert(quiesce_link()==0 && !pending[0] && !ctl_busy && !ctl_quiescing);
    fixture();detach(0,0,0);attach(2,NULL,NULL);
    assert(control_submit(&answer,1,0)==0 && ctl_generation==link_generation);
    assert(quiesce_link()==0 && pending[0] && ctl_busy && !ctl_quiescing);
    complete(0,0,1);
}
static void test_cable_absence_requires_continuous_confirmation(void){
    fixture();assert(observe_cable(1000000,0x102)==0 && !stop_requested);
    assert(observe_cable(1499999,0x102)==0 && !stop_requested);
    assert(observe_cable(1500000,0x122)==0 && !stop_requested);
    assert(observe_cable(1800000,0x102)==0 && !stop_requested);
    assert(observe_cable(2300000,0x102)==0 && stop_requested && state.stop_reason==PDC_STOP_CABLE);
    fixture();assert(observe_cable(1000,-33)==-33 && !cable_absence_pending && !stop_requested);
}
static void test_submissions_paused_and_stale_reset_discarded(void){
    struct DeviceRequest reset={0x21,0xff,0,1,0};
    fixture();assert(receive_control(0,0,&reset)==0);detach(0,0,0);
    assert(bulk_submit(1,512)==1 && submissions[3]==0);
    bot.plan.stall_in=bot.plan.stall_out=1;
    assert(apply_data_stalls()==1 && !halted[2] && !halted[3]);
    assert(recover_bot()==1 && !pending[0]);
    attach(2,NULL,NULL);assert(quiesce_link()==0 && !bot_reset_pending && connected);
}
int main(void){
    test_mode_sense_192_fills_before_csw_without_halt();
    test_fill_crosses_dma_buffers_without_repeating_scsi_data();
    test_incomplete_fill_never_produces_a_csw();
    test_read_crosses_dma_buffers();test_completions_cannot_be_overwritten();
    test_bad_cbw_reset_preserves_halts();test_reset_cancels_old_data_before_ack();
    test_queue_error_and_short_completion();
    test_configuration_unset_is_not_cable_removal();
    test_new_unconfigure_during_drain_is_not_lost();
    test_current_generation_reset_survives_old_drain();
    test_old_ep0_cancel_but_current_ep0_preserved();
    test_cable_absence_requires_continuous_confirmation();
    test_submissions_paused_and_stale_reset_discarded();
    fixture();assert(attach(3,NULL,NULL)==-1 && stop_requested && bulk_fault==-10);
    puts("MSC actual transport: 14 asynchronous ownership/reset/reconfigure/fill scenarios passed");return 0;
}
