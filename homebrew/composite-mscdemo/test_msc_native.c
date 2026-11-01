/* SPDX-License-Identifier: GPL-3.0-only
 * Native conformance/regression checks for the synthetic MSC engine.
 * File output is restricted to this TEST executable, never the PSP engine.
 */
#include "msc_scsi.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>

static uint32_t le32(const uint8_t *p) {
    return (uint32_t)p[0]|((uint32_t)p[1]<<8)|((uint32_t)p[2]<<16)|((uint32_t)p[3]<<24);
}
static uint16_t le16(const uint8_t *p) { return (uint16_t)((uint16_t)p[0]|((uint16_t)p[1]<<8)); }
static uint32_t be32(const uint8_t *p) {
    return ((uint32_t)p[0]<<24)|((uint32_t)p[1]<<16)|((uint32_t)p[2]<<8)|p[3];
}
static void put_le32(uint8_t *p,uint32_t n) {
    p[0]=(uint8_t)n;p[1]=(uint8_t)(n>>8);p[2]=(uint8_t)(n>>16);p[3]=(uint8_t)(n>>24);
}
static void put_be32(uint8_t *p,uint32_t n) {
    p[0]=(uint8_t)(n>>24);p[1]=(uint8_t)(n>>16);p[2]=(uint8_t)(n>>8);p[3]=(uint8_t)n;
}
static void cbw(uint8_t out[31],uint8_t op,uint8_t cdb_bytes,uint32_t length,int in) {
    memset(out,0,31);memcpy(out,"USBC",4);put_le32(out+4,0xa1234567);
    put_le32(out+8,length);out[12]=in?0x80:0;out[14]=cdb_bytes;out[15]=op;
}
static void finish(MscBot *b,uint8_t status,uint32_t residue) {
    uint8_t csw[13];
    assert(msc_csw(b,csw)==13);assert(!memcmp(csw,"USBS",4));
    assert(le32(csw+4)==0xa1234567);assert(le32(csw+8)==residue);assert(csw[12]==status);
    assert(msc_csw(b,csw)==-1);
}
static void matrix(void) {
    struct MatrixCase {uint32_t h,d;enum MscDirection hd,dd;uint8_t id,status,si,so;uint32_t transfer,wire;} cases[]={
        {0,0,MSC_DIR_NONE,MSC_DIR_NONE,1,0,0,0,0,0},
        {0,64,MSC_DIR_NONE,MSC_DIR_IN,2,2,0,0,0,0},
        {0,64,MSC_DIR_NONE,MSC_DIR_OUT,3,2,0,0,0,0},
        {64,0,MSC_DIR_IN,MSC_DIR_NONE,4,0,1,0,0,0},
        {128,64,MSC_DIR_IN,MSC_DIR_IN,5,0,0,0,64,128},
        {64,64,MSC_DIR_IN,MSC_DIR_IN,6,0,0,0,64,64},
        {32,64,MSC_DIR_IN,MSC_DIR_IN,7,2,0,0,32,32},
        {64,64,MSC_DIR_IN,MSC_DIR_OUT,8,2,1,0,0,0},
        {64,0,MSC_DIR_OUT,MSC_DIR_NONE,9,0,0,1,0,0},
        {64,64,MSC_DIR_OUT,MSC_DIR_IN,10,2,0,1,0,0},
        {128,64,MSC_DIR_OUT,MSC_DIR_OUT,11,0,0,1,64,64},
        {64,64,MSC_DIR_OUT,MSC_DIR_OUT,12,0,0,0,64,64},
        {32,64,MSC_DIR_OUT,MSC_DIR_OUT,13,2,0,0,32,32}
    };
    size_t i;
    for(i=0;i<sizeof(cases)/sizeof(cases[0]);i++) {
        struct MatrixCase *c=&cases[i];
        MscPlan p=msc_plan_transfer(c->h,c->hd,c->d,c->dd,MSC_STATUS_PASSED);
        assert(p.valid_cbw && p.host_length==c->h && p.device_length==c->d);
        assert(p.case_id==c->id && p.status==c->status);
        assert(p.stall_in==c->si && p.stall_out==c->so);
        assert(p.transfer_length==c->transfer && p.reset_required==(c->status==2));
        assert(p.wire_length==c->wire);
        p=msc_plan_transfer(c->h,c->hd,c->d,c->dd,MSC_STATUS_FAILED);
        assert(p.status==(c->status==2?2:1));
    }
    /* Direction bit is explicitly ignored for a zero host transfer. */
    assert(msc_plan_transfer(0,MSC_DIR_IN,0,MSC_DIR_NONE,MSC_STATUS_PASSED).case_id==1);
    assert(msc_plan_transfer(0,MSC_DIR_OUT,8,MSC_DIR_IN,MSC_STATUS_PASSED).case_id==2);
}
static uint16_t fat12(const uint8_t *fat,uint32_t cluster) {
    uint32_t n=cluster+cluster/2;uint16_t pair=(uint16_t)(fat[n]|((uint16_t)fat[n+1]<<8));
    return cluster&1u?(uint16_t)(pair>>4):(uint16_t)(pair&0xfffu);
}
static void volume(void) {
    MscBot b;uint8_t session[16],sector[512],fat[1536],other[512];uint32_t i,c,offset=0;
    for(i=0;i<16;i++)session[i]=(uint8_t)(i+1);
    msc_init(&b,session);
    assert(msc_read_sector(&b,0,sector)==0);
    assert(le16(sector+11)==512 && sector[13]==1 && le16(sector+14)==1);
    assert(sector[16]==2 && le16(sector+17)==32 && le16(sector+19)==1024);
    assert(le16(sector+22)==3 && le32(sector+39)==MSC_VOLUME_SERIAL);
    assert(!memcmp(sector+43,"PSPDOCKTEST",11) && !memcmp(sector+54,"FAT12   ",8));
    assert(sector[510]==0x55 && sector[511]==0xaa);
    for(i=0;i<3;i++) {
        assert(!msc_read_sector(&b,i+1,fat+i*512));
        assert(!msc_read_sector(&b,i+4,other));assert(!memcmp(fat+i*512,other,512));
    }
    assert(fat12(fat,0)==0xff8 && fat12(fat,1)==0xfff);
    for(c=2;c<294;c++)assert(fat12(fat,c)==c+1);
    assert(fat12(fat,294)==0xfff && fat12(fat,295)==0xfff);
    for(c=296;c<1017;c++)assert(fat12(fat,c)==0);
    assert(!msc_read_sector(&b,7,sector));assert(sector[11]==8);
    assert(!memcmp(sector+32,"PROBE   DAT",11) && le16(sector+58)==2 && le32(sector+60)==MSC_PROBE_BYTES);
    assert(!memcmp(sector+64,"SESSION BIN",11) && le16(sector+90)==295 && le32(sector+92)==16);
    for(c=2;c<=294;c++) {
        assert(!msc_read_sector(&b,MSC_DATA_LBA+c-2,sector));
        for(i=0;i<512;i++,offset++)assert(sector[i]==(offset<MSC_PROBE_BYTES?(uint8_t)(offset*17u+3u):0));
    }
    assert(!msc_read_sector(&b,302,sector));assert(!memcmp(sector,session,16));
    for(i=16;i<512;i++)assert(sector[i]==0);
    assert(!msc_read_sector(&b,1023,sector));for(i=0;i<512;i++)assert(sector[i]==0);
    assert(msc_read_sector(&b,1024,sector)==-1 && msc_read_sector(&b,UINT32_MAX,sector)==-1);
    msc_reset(&b);assert(!msc_read_sector(&b,302,sector) && !memcmp(sector,session,16));
}
static void scsi_basics(void) {
    MscBot b;uint8_t c[31],r[64],csw[13];size_t n;
    msc_init(&b,NULL);
    cbw(c,0x12,6,36,1);c[19]=36;assert(!msc_begin(&b,c,31));
    assert(b.plan.case_id==6 && b.plan.transfer_length==36);
    assert(msc_csw(&b,csw)==-1);n=msc_read_data(&b,0,r,sizeof(r));assert(n==36);
    assert(!memcmp(r+8,"PSPDOCK ",8) && !memcmp(r+16,"MSC HID TEST    ",16));
    assert(r[1]==0x80 && r[4]==31);
    assert(!msc_data_complete(&b,36));assert(msc_data_complete(&b,1)==-1);finish(&b,0,0);
    cbw(c,0x12,6,64,1);c[19]=36;assert(!msc_begin(&b,c,31));
    assert(b.plan.case_id==5 && !b.plan.stall_in && b.plan.transfer_length==36 && b.plan.wire_length==64);
    assert(msc_read_data(&b,0,r,sizeof(r))==64);
    assert(!msc_data_complete(&b,36) && msc_csw(&b,csw)==-1);
    assert(!msc_data_complete(&b,28) && b.completed==36 && b.wire_completed==64);finish(&b,0,28);
    cbw(c,0x12,6,7,1);c[16]=1;c[17]=0;c[19]=7;assert(!msc_begin(&b,c,31));
    assert(msc_read_data(&b,0,r,sizeof(r))==7 && r[3]==3 && r[6]==0x83);
    assert(!msc_data_complete(&b,7));finish(&b,0,0);
    cbw(c,0x12,6,12,1);c[16]=1;c[17]=0x80;c[19]=12;assert(!msc_begin(&b,c,31));
    assert(msc_read_data(&b,0,r,sizeof(r))==12 && !memcmp(r+4,"50444334",8));
    assert(!msc_data_complete(&b,12));finish(&b,0,0);
    cbw(c,0x25,10,8,1);assert(!msc_begin(&b,c,31));assert(msc_read_data(&b,0,r,64)==8);
    assert(be32(r)==1023 && be32(r+4)==512);assert(!msc_data_complete(&b,8));finish(&b,0,0);
    cbw(c,0x23,10,12,1);c[23]=12;assert(!msc_begin(&b,c,31));assert(msc_read_data(&b,0,r,64)==12);
    assert(r[3]==8 && be32(r+4)==1024 && r[8]==2 && r[10]==2);
    assert(!msc_data_complete(&b,12));finish(&b,0,0);
    cbw(c,0x1a,6,32,1);c[17]=0x3f;c[19]=32;assert(!msc_begin(&b,c,31));
    assert(msc_read_data(&b,0,r,64)==32 && r[0]==31 && r[2]==0x80 && r[3]==8 && r[12]==8);
    assert(!msc_data_complete(&b,32));finish(&b,0,0);
    cbw(c,0x5a,10,28,1);c[16]=8;c[17]=8;c[23]=28;assert(!msc_begin(&b,c,31));
    assert(msc_read_data(&b,0,r,64)==28 && r[1]==26 && r[3]==0x80 && r[8]==8);
    assert(!msc_data_complete(&b,28));finish(&b,0,0);
    cbw(c,0,6,0,0);assert(!msc_begin(&b,c,31));finish(&b,0,0);
    cbw(c,0x35,10,0,0);assert(!msc_begin(&b,c,31));finish(&b,0,0);
}
static void short_in_padding(void) {
    static const uint8_t mode6[32]={31,0,0x80,8,0,0,4,0,0,0,2,0,8,18};
    MscBot b;uint8_t c[31],r[1024],csw[13];uint32_t i;
    msc_init(&b,NULL);
    /* The exact last command observed in the 0206 hardware photograph:
     * MODE SENSE (6), 32 relevant bytes, host allocation 192. */
    cbw(c,0x1a,6,192,1);c[17]=0x3f;c[19]=192;
    assert(!msc_begin(&b,c,31));
    assert(b.plan.case_id==5 && b.plan.status==0 && !b.plan.reset_required);
    assert(b.plan.transfer_length==32 && b.plan.wire_length==192);
    assert(!b.plan.stall_in && !b.plan.stall_out);
    memset(r,0xa5,sizeof(r));assert(msc_read_data(&b,0,r,sizeof(r))==192);
    assert(!memcmp(r,mode6,32));
    for(i=32;i<192;i++)assert(r[i]==0);
    assert(r[192]==0xa5); /* The output capacity is not the transfer size. */
    assert(!msc_data_complete(&b,17) && b.completed==17 && b.wire_completed==17);
    assert(!msc_data_complete(&b,64) && b.completed==32 && b.wire_completed==81);
    assert(msc_csw(&b,csw)==-1); /* Relevant bytes alone cannot finish the phase. */
    memset(r,0xa5,sizeof(r));assert(msc_read_data(&b,81,r,sizeof(r))==111);
    for(i=0;i<111;i++)assert(r[i]==0);
    assert(r[111]==0xa5 && !msc_data_complete(&b,111));
    assert(b.completed==32 && b.wire_completed==192);
    assert(msc_data_complete(&b,1)==-1);finish(&b,0,160);

    /* Exact Windows EVPD CDB 12 01 00 00 FF 00: valid page zero,
     * 7 relevant bytes and 248 fill bytes. Keep parser validation intact. */
    cbw(c,0x12,6,255,1);c[16]=1;c[19]=255;
    assert(!msc_begin(&b,c,31) && b.plan.status==0 && !b.plan.stall_in);
    assert(b.plan.transfer_length==7 && b.plan.wire_length==255);
    assert(msc_read_data(&b,0,r,sizeof(r))==255);
    assert(!memcmp(r,"\0\0\0\3\0\200\203",7));
    for(i=7;i<255;i++)assert(r[i]==0);
    assert(!msc_data_complete(&b,255) && b.completed==7);finish(&b,0,248);

    /* Fill following block data must not expose the next generated sector. */
    cbw(c,0x28,10,1024,1);put_be32(c+17,9);c[23]=1;
    assert(!msc_begin(&b,c,31) && b.plan.transfer_length==512 && b.plan.wire_length==1024);
    assert(msc_read_data(&b,0,r,sizeof(r))==1024);
    for(i=0;i<512;i++)assert(r[i]==(uint8_t)(i*17u+3u));
    for(i=512;i<1024;i++)assert(r[i]==0);
    assert(!msc_data_complete(&b,512) && msc_csw(&b,csw)==-1);
    assert(!msc_data_complete(&b,512));finish(&b,0,512);

    /* Large host lengths are streamed, with bounded arithmetic and no
     * allocation or access beyond the 32-byte SCSI response. */
    cbw(c,0x1a,6,UINT32_MAX,1);c[17]=0x3f;c[19]=192;
    assert(!msc_begin(&b,c,31));
    assert(msc_read_data(&b,UINT32_MAX-7u,r,sizeof(r))==7);
    for(i=0;i<7;i++)assert(r[i]==0);
    assert(msc_read_data(&b,UINT32_MAX,r,sizeof(r))==0);
    assert(!msc_data_complete(&b,64) && b.completed==32 && b.wire_completed==64);
    msc_reset(&b);assert(!b.completed && !b.wire_completed && !b.command_active);
}
static void read_capacity16(void) {
    static const uint8_t capacity[32]={0,0,0,0,0,0,3,0xff,0,0,2,0};
    MscBot b;uint8_t c[31],r[64];uint32_t i;
    msc_init(&b,NULL);
    /* Exact READ CAPACITY (16), service action 10h, observed from Windows. */
    cbw(c,0x9e,16,32,1);c[16]=0x10;put_be32(c+25,32);
    assert(!msc_begin(&b,c,31) && b.plan.case_id==6 && b.plan.status==0);
    assert(msc_read_data(&b,0,r,sizeof(r))==32 && !memcmp(r,capacity,32));
    assert(!msc_data_complete(&b,32));finish(&b,0,0);
    /* A shorter allocation truncates the same parameter data. */
    cbw(c,0x9e,16,9,1);c[16]=0x10;put_be32(c+25,9);
    assert(!msc_begin(&b,c,31) && b.plan.transfer_length==9);
    assert(msc_read_data(&b,0,r,sizeof(r))==9 && !memcmp(r,capacity,9));
    assert(!msc_data_complete(&b,9));finish(&b,0,0);
    /* Allocation greater than the 32-byte response uses BOT fill. */
    cbw(c,0x9e,16,64,1);c[16]=0x10;put_be32(c+25,UINT32_MAX);
    assert(!msc_begin(&b,c,31) && b.plan.transfer_length==32 && b.plan.wire_length==64);
    assert(msc_read_data(&b,0,r,sizeof(r))==64 && !memcmp(r,capacity,32));
    for(i=32;i<64;i++)assert(r[i]==0);
    assert(!msc_data_complete(&b,64));finish(&b,0,32);
    cbw(c,0x9e,16,0,1);c[16]=0x10;
    assert(!msc_begin(&b,c,31) && b.plan.status==0);finish(&b,0,0);
    /* Invalid service action/reserved bits, either LBA half, PMI and
     * undersized CDB all stay rejected with INVALID FIELD IN CDB. */
    for(i=0;i<6;i++) {
        cbw(c,0x9e,16,32,1);c[16]=0x10;put_be32(c+25,32);
        if(i==0)c[16]=0x11;
        else if(i==1)c[16]=0x30;
        else if(i==2)c[17]=1;
        else if(i==3)c[24]=1;
        else if(i==4)c[29]=1;
        else c[14]=15;
        assert(!msc_begin(&b,c,31) && b.plan.status==1 && b.sense_key==5 && b.sense_asc==0x24);
        finish(&b,1,32);
    }
    cbw(c,0x1b,6,0,0);c[19]=2;
    assert(!msc_begin(&b,c,31));finish(&b,0,0);assert(!b.medium_present);
    cbw(c,0x9e,16,32,1);c[16]=0x10;put_be32(c+25,32);
    assert(!msc_begin(&b,c,31) && b.plan.status==1 && b.sense_key==2 && b.sense_asc==0x3a);
    finish(&b,1,32);
}
static void reads_and_failures(void) {
    MscBot b;uint8_t c[31],data[65536],sense[18];uint32_t offset=0,i;size_t n;
    msc_init(&b,NULL);
    cbw(c,0x28,10,293u*512u,1);put_be32(c+17,9);c[22]=1;c[23]=37;
    assert(!msc_begin(&b,c,31) && b.plan.transfer_length==150016);
    while((n=msc_read_data(&b,offset,data,sizeof(data)))!=0) {
        for(i=0;i<n;i++)assert(data[i]==(offset+i<MSC_PROBE_BYTES?(uint8_t)((offset+i)*17u+3u):0));
        assert(!msc_data_complete(&b,(uint32_t)n));offset+=(uint32_t)n;
    }
    assert(offset==150016);finish(&b,0,0);
    cbw(c,0xa8,12,512,1);put_be32(c+17,0);put_be32(c+21,1);
    assert(!msc_begin(&b,c,31));assert(msc_read_data(&b,507,data,64)==5);
    assert(data[3]==0x55 && data[4]==0xaa);assert(!msc_data_complete(&b,512));finish(&b,0,0);
    cbw(c,0x28,10,511,1);c[23]=1;assert(!msc_begin(&b,c,31));
    assert(b.plan.case_id==7 && b.plan.status==2 && b.plan.reset_required);
    assert(msc_read_data(&b,0,data,sizeof(data))==511);assert(!msc_data_complete(&b,511));finish(&b,2,0);
    assert(msc_begin(&b,c,31)==-2);msc_reset(&b);
    cbw(c,0x28,10,512,1);put_be32(c+17,1024);c[23]=1;assert(!msc_begin(&b,c,31));
    assert(b.plan.status==1 && b.plan.stall_in && !b.plan.transfer_length);finish(&b,1,512);
    cbw(c,0x03,6,18,1);c[19]=18;assert(!msc_begin(&b,c,31));
    assert(msc_read_data(&b,0,sense,18)==18 && sense[2]==5 && sense[12]==0x21);
    assert(!msc_data_complete(&b,18));finish(&b,0,0);
    assert(!msc_begin(&b,c,31));assert(msc_read_data(&b,0,sense,18)==18 && sense[2]==0);
    assert(!msc_data_complete(&b,18));finish(&b,0,0);
    cbw(c,0xa8,12,UINT32_MAX,1);put_be32(c+21,UINT32_MAX);assert(!msc_begin(&b,c,31));
    assert(b.plan.status==1 && !b.plan.transfer_length);finish(&b,1,UINT32_MAX);
    cbw(c,0x2a,10,512,0);c[23]=1;assert(!msc_begin(&b,c,31));
    assert(b.plan.case_id==9 && b.plan.status==1 && b.plan.stall_out && !b.plan.transfer_length);
    assert(msc_read_data(&b,0,data,sizeof(data))==0);finish(&b,1,512);
    cbw(c,0x03,6,18,1);c[19]=18;assert(!msc_begin(&b,c,31));
    assert(msc_read_data(&b,0,sense,18)==18 && sense[2]==7 && sense[12]==0x27);
    assert(!msc_data_complete(&b,18));finish(&b,0,0);
    cbw(c,0xff,16,0,0);assert(!msc_begin(&b,c,31) && b.plan.status==1);finish(&b,1,0);
    cbw(c,0x28,6,512,1);assert(!msc_begin(&b,c,31) && b.plan.status==1);finish(&b,1,512);
    cbw(c,0x28,10,0,0);put_be32(c+17,UINT32_MAX);assert(!msc_begin(&b,c,31));finish(&b,0,0);
}
static void medium_state(void) {
    MscBot b;uint8_t c[31];msc_init(&b,NULL);
    cbw(c,0x1e,6,0,0);c[19]=1;assert(!msc_begin(&b,c,31));finish(&b,0,0);
    cbw(c,0x1b,6,0,0);c[19]=2;assert(!msc_begin(&b,c,31));finish(&b,1,0);assert(b.medium_present);
    cbw(c,0x1e,6,0,0);assert(!msc_begin(&b,c,31));finish(&b,0,0);
    cbw(c,0x1b,6,0,0);c[19]=2;assert(!msc_begin(&b,c,31));finish(&b,0,0);assert(!b.medium_present);
    cbw(c,0,6,0,0);assert(!msc_begin(&b,c,31));finish(&b,1,0);
    cbw(c,0x1b,6,0,0);c[19]=3;assert(!msc_begin(&b,c,31));finish(&b,0,0);assert(b.medium_present);
}
static void malformed(void) {
    MscBot b;uint8_t c[64],out[64];uint32_t state=1,i,j;size_t n;
    for(i=0;i<6;i++) {
        msc_init(&b,NULL);cbw(c,0,6,0,0);
        if(i==0)c[0]=0;else if(i==1)c[12]=1;else if(i==2)c[13]=1;
        else if(i==3)c[14]=0;else if(i==4)c[14]=17;
        assert(msc_begin(&b,c,i==5?32u:31u)==-1);
        assert(!b.plan.valid_cbw && b.plan.stall_in && b.plan.stall_out && b.plan.reset_required);
        assert(msc_csw(&b,out)==-1 && msc_begin(&b,c,31)==-2);
        msc_reset(&b);cbw(c,0,6,0,0);assert(!msc_begin(&b,c,31));finish(&b,0,0);
    }
    /* Deterministic malformed wrappers/CDBs; sanitizer run checks boundaries. */
    for(i=0;i<50000;i++) {
        msc_init(&b,NULL);
        for(j=0;j<sizeof(c);j++) {state=state*1664525u+1013904223u;c[j]=(uint8_t)(state>>24);}
        if(i&1u) { memcpy(c,"USBC",4);c[12]&=0x80;c[13]=0;c[14]=(uint8_t)((i%16u)+1); }
        n=(i%4u)?31u:(size_t)(state%sizeof(c));
        if(!msc_begin(&b,c,n)) {
            size_t got=msc_read_data(&b,0,out,sizeof(out));
            assert(got<=sizeof(out));assert(!msc_data_complete(&b,(uint32_t)got));
            (void)msc_read_data(&b,UINT32_MAX,out,sizeof(out));
            if(b.wire_completed==b.plan.wire_length)assert(msc_csw(&b,out)==13);
            else assert(msc_csw(&b,out)==-1);
        } else assert(!b.plan.valid_cbw);
    }
}
int main(int argc,char **argv) {
    matrix();volume();scsi_basics();short_in_padding();read_capacity16();reads_and_failures();medium_state();malformed();
    if(argc==3 && !strcmp(argv[1],"--dump-image")) {
        MscBot b;uint8_t sector[512],session[16];uint32_t lba;FILE *f;
        for(lba=0;lba<16;lba++)session[lba]=(uint8_t)(lba+1);
        msc_init(&b,session);f=fopen(argv[2],"wb");assert(f);
        for(lba=0;lba<1024;lba++) {assert(!msc_read_sector(&b,lba,sector));assert(fwrite(sector,1,512,f)==512);}
        assert(!fclose(f));
    }
    puts("MSC synthetic disk: 13 BOT cases, FAT12, SCSI, IN fill/residue, reset, 50000 malformed inputs PASS");
    return 0;
}
