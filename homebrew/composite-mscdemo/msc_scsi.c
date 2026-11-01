/* SPDX-License-Identifier: GPL-3.0-only
 * Original read-only synthetic FAT12 disk and USB Mass Storage BOT engine.
 * Transport contract: USB-IF Bulk-Only Transport 1.0, sections 5 and 6.
 * https://www.usb.org/sites/default/files/usbmassbulk_10.pdf
 * This is a disk-class diagnostic, NOT a Memory Stick backend.
 */
#include "msc_scsi.h"
#include <string.h>

static uint16_t be16(const uint8_t *p) { return (uint16_t)(((uint16_t)p[0]<<8)|p[1]); }
static uint32_t be32(const uint8_t *p) {
    return ((uint32_t)p[0]<<24)|((uint32_t)p[1]<<16)|((uint32_t)p[2]<<8)|p[3];
}
static uint32_t le32(const uint8_t *p) {
    return (uint32_t)p[0]|((uint32_t)p[1]<<8)|((uint32_t)p[2]<<16)|((uint32_t)p[3]<<24);
}
static void put_le16(uint8_t *p, uint16_t n) { p[0]=(uint8_t)n; p[1]=(uint8_t)(n>>8); }
static void put_le32(uint8_t *p, uint32_t n) {
    p[0]=(uint8_t)n; p[1]=(uint8_t)(n>>8); p[2]=(uint8_t)(n>>16); p[3]=(uint8_t)(n>>24);
}
static void put_be16(uint8_t *p, uint16_t n) { p[0]=(uint8_t)(n>>8); p[1]=(uint8_t)n; }
static void put_be32(uint8_t *p, uint32_t n) {
    p[0]=(uint8_t)(n>>24); p[1]=(uint8_t)(n>>16); p[2]=(uint8_t)(n>>8); p[3]=(uint8_t)n;
}
static uint32_t smaller(uint32_t a, uint32_t b) { return a<b?a:b; }

/* FAT entries are computed independently, so even a FAT12 entry crossing a
 * sector boundary is correct without storing an entire FAT or disk image. */
static uint16_t fat_entry(uint32_t cluster) {
    if(cluster==0) return 0xff8;
    if(cluster==1 || cluster==MSC_PROBE_LAST_CLUSTER || cluster==MSC_SESSION_CLUSTER) return 0xfff;
    if(cluster>=MSC_PROBE_FIRST_CLUSTER && cluster<MSC_PROBE_LAST_CLUSTER)
        return (uint16_t)(cluster+1);
    return 0;
}
static uint8_t fat_byte(uint32_t offset) {
    uint32_t pair=offset/3, part=offset%3;
    uint16_t even=fat_entry(pair*2), odd=fat_entry(pair*2+1);
    if(part==0) return (uint8_t)even;
    if(part==1) return (uint8_t)((even>>8)|(odd<<4));
    return (uint8_t)(odd>>4);
}
static void root_file(uint8_t *p, const char name[11], uint16_t cluster, uint32_t bytes) {
    memcpy(p,name,11); p[11]=0x21; /* Read-only, archive. */
    /* 2026-11-01 00:00:00, a deterministic valid DOS date. */
    put_le16(p+16,(uint16_t)(((2026-1980)<<9)|(11<<5)|1));
    put_le16(p+18,(uint16_t)(((2026-1980)<<9)|(11<<5)|1));
    put_le16(p+24,(uint16_t)(((2026-1980)<<9)|(11<<5)|1));
    put_le16(p+26,cluster); put_le32(p+28,bytes);
}
int msc_read_sector(const MscBot *bot, uint32_t lba, uint8_t out[MSC_BLOCK_SIZE]) {
    uint32_t i,offset;
    if(!bot || !out || lba>=MSC_BLOCK_COUNT) return -1;
    memset(out,0,MSC_BLOCK_SIZE);
    if(lba==0) {
        out[0]=0xeb; out[1]=0x3c; out[2]=0x90;
        memcpy(out+3,"PSPDOCK ",8);
        put_le16(out+11,MSC_BLOCK_SIZE); out[13]=1; put_le16(out+14,1);
        out[16]=2; put_le16(out+17,32); put_le16(out+19,MSC_BLOCK_COUNT);
        out[21]=0xf8; put_le16(out+22,3); put_le16(out+24,32); put_le16(out+26,2);
        out[36]=0x80; out[38]=0x29; put_le32(out+39,MSC_VOLUME_SERIAL);
        memcpy(out+43,"PSPDOCKTEST",11); memcpy(out+54,"FAT12   ",8);
        /* Non-bootable volume. Infinite boot-sector loop only if a BIOS is
         * explicitly instructed to execute it; disk access does not execute. */
        out[62]=0xeb; out[63]=0xfe; out[510]=0x55; out[511]=0xaa;
    } else if(lba>=1 && lba<=6) {
        offset=((lba-1)%3)*MSC_BLOCK_SIZE;
        for(i=0;i<MSC_BLOCK_SIZE;i++) out[i]=fat_byte(offset+i);
    } else if(lba==7) {
        memcpy(out,"PSPDOCKTEST",11); out[11]=0x08; /* Root volume-label entry. */
        root_file(out+32,"PROBE   DAT",MSC_PROBE_FIRST_CLUSTER,MSC_PROBE_BYTES);
        root_file(out+64,"SESSION BIN",MSC_SESSION_CLUSTER,16);
    } else if(lba>=MSC_DATA_LBA && lba<MSC_DATA_LBA+293u) {
        offset=(lba-MSC_DATA_LBA)*MSC_BLOCK_SIZE;
        for(i=0;i<MSC_BLOCK_SIZE && offset+i<MSC_PROBE_BYTES;i++)
            out[i]=(uint8_t)((offset+i)*17u+3u);
    } else if(lba==MSC_DATA_LBA+MSC_SESSION_CLUSTER-2u) {
        memcpy(out,bot->session,16);
    }
    return 0;
}

MscPlan msc_plan_transfer(uint32_t h, enum MscDirection hd, uint32_t d,
                          enum MscDirection dd, enum MscStatus status) {
    MscPlan p;
    memset(&p,0,sizeof(p)); p.host_length=h; p.device_length=d;
    p.valid_cbw=1; p.status=(uint8_t)status;
    if(!h) hd=MSC_DIR_NONE;
    if(!d) dd=MSC_DIR_NONE;
    if(hd==MSC_DIR_NONE) {
        p.case_id=(uint8_t)(dd==MSC_DIR_NONE?1:(dd==MSC_DIR_IN?2:3));
        if(dd!=MSC_DIR_NONE) p.status=MSC_STATUS_PHASE_ERROR;
    } else if(hd==MSC_DIR_IN) {
        if(dd==MSC_DIR_NONE) { p.case_id=4; p.stall_in=1; }
        else if(dd==MSC_DIR_OUT) { p.case_id=8; p.stall_in=1; p.status=MSC_STATUS_PHASE_ERROR; }
        else {
            p.direction=MSC_DIR_IN; p.transfer_length=smaller(h,d);
            p.case_id=(uint8_t)(h>d?5:(h==d?6:7));
            if(h>d) p.stall_in=1;
            if(h<d) p.status=MSC_STATUS_PHASE_ERROR;
        }
    } else {
        if(dd==MSC_DIR_NONE) { p.case_id=9; p.stall_out=1; }
        else if(dd==MSC_DIR_IN) { p.case_id=10; p.stall_out=1; p.status=MSC_STATUS_PHASE_ERROR; }
        else {
            p.direction=MSC_DIR_OUT; p.transfer_length=smaller(h,d);
            p.case_id=(uint8_t)(h>d?11:(h==d?12:13));
            if(h>d) p.stall_out=1;
            if(h<d) p.status=MSC_STATUS_PHASE_ERROR;
        }
    }
    p.reset_required=(uint8_t)(p.status==MSC_STATUS_PHASE_ERROR);
    return p;
}
void msc_init(MscBot *bot, const uint8_t session[16]) {
    if(!bot) return;
    memset(bot,0,sizeof(*bot)); bot->medium_present=1;
    if(session) memcpy(bot->session,session,16);
}
void msc_reset(MscBot *bot) {
    if(!bot) return;
    memset(&bot->plan,0,sizeof(bot->plan)); bot->command_active=0; bot->reset_locked=0;
    bot->completed=0; bot->source=0; bot->response_length=0;
}
static void fail(MscBot *b, uint8_t key, uint8_t asc, uint8_t ascq) {
    b->sense_key=key; b->sense_asc=asc; b->sense_ascq=ascq;
    b->source=0; b->response_length=0;
}
static uint8_t command_bytes(uint8_t op) {
    switch(op) {
        case 0x00: case 0x03: case 0x08: case 0x0a: case 0x12:
        case 0x15: case 0x1a: case 0x1b: case 0x1e: return 6;
        case 0x23: case 0x25: case 0x28: case 0x2a: case 0x2f:
        case 0x35: case 0x55: case 0x5a: return 10;
        case 0xa8: case 0xaa: return 12;
        case 0x8a: case 0x88: return 16;
        default: return 1;
    }
}
static int ready(MscBot *b) {
    if(b->medium_present) return 1;
    fail(b,2,0x3a,0); return 0; /* NOT READY / medium not present. */
}
static uint32_t response(MscBot *b, uint32_t size, uint32_t allocation) {
    b->source=1; b->response_length=smaller(size,allocation); return b->response_length;
}
static int inquiry(MscBot *b, const uint8_t *c, uint32_t *length) {
    uint8_t *r=b->response;
    if((c[1]&0xfeu) || c[3]) return 0;
    if(!(c[1]&1)) {
        if(c[2]) return 0;
        r[0]=0; r[1]=0x80; r[2]=4; r[3]=2; r[4]=31;
        memcpy(r+8,"PSPDOCK ",8); memcpy(r+16,"MSC HID TEST    ",16); memcpy(r+32,"0001",4);
        *length=response(b,36,c[4]); return 1;
    }
    r[1]=c[2];
    if(c[2]==0) { r[3]=3; r[4]=0; r[5]=0x80; r[6]=0x83; *length=response(b,7,c[4]); }
    else if(c[2]==0x80) {
        r[3]=8; memcpy(r+4,"50444334",8); *length=response(b,12,c[4]);
    } else if(c[2]==0x83) {
        /* ASCII vendor-specific identifier, no fabricated NAA or IEEE OUI. */
        r[3]=20; r[4]=2; r[5]=0; r[7]=16;
        memcpy(r+8,"PSPDOCK 50444334 ",16); *length=response(b,24,c[4]);
    } else return 0;
    return 1;
}
static int mode_sense(MscBot *b, const uint8_t *c, int ten, uint32_t *length) {
    uint8_t page=c[2]&0x3f, pc=c[2]>>6, *r=b->response;
    uint32_t n=ten?8u:4u, allocation=ten?be16(c+7):c[4];
    if((c[1]&~8u) || c[3] || pc==3 || (page!=0 && page!=8 && page!=0x3f)) return 0;
    if(ten) r[3]=0x80; else r[2]=0x80; /* Write-protected medium. */
    if(!(c[1]&8u)) {
        if(ten) put_be16(r+6,8); else r[3]=8;
        r[n+1]=(uint8_t)(MSC_BLOCK_COUNT>>16); r[n+2]=(uint8_t)(MSC_BLOCK_COUNT>>8);
        r[n+3]=(uint8_t)MSC_BLOCK_COUNT; r[n+6]=2; /* 512-byte blocks. */
        n+=8;
    }
    if(page==8 || page==0x3f) { r[n]=8; r[n+1]=18; n+=20; } /* WCE=0, no write cache. */
    if(ten) put_be16(r,(uint16_t)(n-2)); else r[0]=(uint8_t)(n-1);
    *length=response(b,n,allocation); return 1;
}
static int read_blocks(MscBot *b, uint32_t lba, uint32_t count, uint32_t *length) {
    if(!ready(b)) return -1;
    /* A zero-block READ is a no-op, including at the end of the medium. */
    if(!count) { *length=0; return 1; }
    if(lba>=MSC_BLOCK_COUNT || count>MSC_BLOCK_COUNT-lba) {
        fail(b,5,0x21,0); return -1;
    }
    b->first_lba=lba; b->source=2; *length=count*MSC_BLOCK_SIZE; return 1;
}
static enum MscStatus scsi_command(MscBot *b, const uint8_t *c, uint8_t n,
                                    uint32_t *length, enum MscDirection *direction) {
    int ok=1;
    *length=0; *direction=MSC_DIR_NONE;
    memset(b->response,0,sizeof(b->response)); b->source=0; b->response_length=0;
    if(n<command_bytes(c[0])) { fail(b,5,0x24,0); return MSC_STATUS_FAILED; }
    switch(c[0]) {
        case 0x00: /* TEST UNIT READY */
            if(!ready(b)) return MSC_STATUS_FAILED;
            break;
        case 0x03: /* REQUEST SENSE: fixed format current errors. */
            if(c[1]&1u) { ok=0; break; }
            b->response[0]=0x70; b->response[2]=b->sense_key;
            b->response[7]=10; b->response[12]=b->sense_asc; b->response[13]=b->sense_ascq;
            *length=response(b,18,c[4]);
            b->sense_key=0; b->sense_asc=0; b->sense_ascq=0;
            break;
        case 0x12: ok=inquiry(b,c,length); break;
        case 0x1a: if(!ready(b)) return MSC_STATUS_FAILED; ok=mode_sense(b,c,0,length); break;
        case 0x5a: if(!ready(b)) return MSC_STATUS_FAILED; ok=mode_sense(b,c,1,length); break;
        case 0x25: /* READ CAPACITY (10), supports PMI=0 and zero LBA. */
            if(!ready(b)) return MSC_STATUS_FAILED;
            if(c[8] || be32(c+2)) { ok=0; break; }
            put_be32(b->response,MSC_BLOCK_COUNT-1); put_be32(b->response+4,MSC_BLOCK_SIZE);
            *length=response(b,8,8); break;
        case 0x23: /* READ FORMAT CAPACITIES */
            if(!ready(b)) return MSC_STATUS_FAILED;
            b->response[3]=8; put_be32(b->response+4,MSC_BLOCK_COUNT);
            b->response[8]=2; b->response[10]=2; /* Formatted, block length 0x000200. */
            *length=response(b,12,be16(c+7)); break;
        case 0x08: /* READ (6): a zero count means 256 blocks. */
            ok=read_blocks(b,((uint32_t)(c[1]&31u)<<16)|((uint32_t)c[2]<<8)|c[3],
                           c[4]?c[4]:256,length);
            if(ok<0) return MSC_STATUS_FAILED;
            break;
        case 0x28: case 0xa8: /* READ (10)/(12). DPO/FUA do not change a synthetic disk. */
            if(c[1]&~0x18u) { ok=0; break; }
            ok=read_blocks(b,be32(c+2),c[0]==0x28?be16(c+7):be32(c+6),length);
            if(ok<0) return MSC_STATUS_FAILED;
            break;
        case 0x1b: /* START STOP UNIT */
            if(c[4]&0xfcu) { ok=0; break; }
            if((c[4]&3u)==2u) {
                if(b->removal_prevented) { fail(b,5,0x53,2); return MSC_STATUS_FAILED; }
                b->medium_present=0;
            } else if(c[4]&1u) b->medium_present=1;
            break;
        case 0x1e: /* PREVENT/ALLOW MEDIUM REMOVAL */
            if(c[4]&~1u) { ok=0; break; }
            b->removal_prevented=c[4]&1u; break;
        case 0x35: /* SYNCHRONIZE CACHE: no data/cache to flush. */
            if(!ready(b)) return MSC_STATUS_FAILED;
            if(c[1]&~2u) { ok=0; break; }
            break;
        case 0x2f: /* VERIFY (10), BYTCHK=0 only, generated medium is stable. */
            if(!ready(b)) return MSC_STATUS_FAILED;
            if(c[1]&~0x10u) { ok=0; break; }
            if(be16(c+7) && (be32(c+2)>=MSC_BLOCK_COUNT || be16(c+7)>MSC_BLOCK_COUNT-be32(c+2))) {
                fail(b,5,0x21,0); return MSC_STATUS_FAILED;
            }
            break;
        case 0x0a: case 0x2a: case 0xaa: case 0x8a: /* WRITE (6)/(10)/(12)/(16). */
        case 0x15: case 0x55: /* MODE SELECT (6)/(10) cannot change immutable medium. */
            fail(b,7,0x27,0); return MSC_STATUS_FAILED; /* DATA PROTECT / write protected. */
        default: fail(b,5,0x20,0); return MSC_STATUS_FAILED; /* Invalid command operation code. */
    }
    if(!ok) { fail(b,5,0x24,0); return MSC_STATUS_FAILED; }
    if(*length) *direction=MSC_DIR_IN;
    return MSC_STATUS_PASSED;
}
int msc_begin(MscBot *b, const uint8_t *cbw, size_t size) {
    enum MscStatus status; enum MscDirection hd,dd; uint32_t length,host;
    if(!b) return -1;
    if(b->reset_locked || b->command_active) return -2;
    memset(&b->plan,0,sizeof(b->plan)); b->completed=0; b->source=0;
    if(!cbw || size!=MSC_CBW_SIZE || le32(cbw)!=UINT32_C(0x43425355) ||
       (cbw[12]&0x7fu) || cbw[13]!=0 || cbw[14]<1 || cbw[14]>16) {
        b->plan.stall_in=1; b->plan.stall_out=1; b->plan.reset_required=1;
        b->reset_locked=1; return -1;
    }
    b->tag=le32(cbw+4); host=le32(cbw+8); hd=(cbw[12]&0x80u)?MSC_DIR_IN:MSC_DIR_OUT;
    status=scsi_command(b,cbw+15,cbw[14],&length,&dd);
    b->plan=msc_plan_transfer(host,hd,length,dd,status); b->command_active=1;
    return 0;
}
size_t msc_read_data(const MscBot *b, uint32_t offset, uint8_t *out, size_t cap) {
    uint8_t sector[MSC_BLOCK_SIZE]; uint32_t remaining,position,inside,take; size_t n=0;
    if(!b || !out || !b->command_active || b->plan.direction!=MSC_DIR_IN || offset>=b->plan.transfer_length) return 0;
    remaining=b->plan.transfer_length-offset;
    if(cap<remaining) remaining=(uint32_t)cap;
    if(b->source==1) { memcpy(out,b->response+offset,remaining); return remaining; }
    if(b->source!=2) return 0;
    while(remaining) {
        position=offset+(uint32_t)n; inside=position%MSC_BLOCK_SIZE;
        take=smaller(remaining,MSC_BLOCK_SIZE-inside);
        if(msc_read_sector(b,b->first_lba+position/MSC_BLOCK_SIZE,sector)) return n;
        memcpy(out+n,sector+inside,take); n+=take; remaining-=take;
    }
    return n;
}
int msc_data_complete(MscBot *b, uint32_t bytes) {
    if(!b || !b->command_active || b->completed>b->plan.transfer_length ||
       bytes>b->plan.transfer_length-b->completed) return -1;
    b->completed+=bytes; return 0;
}
int msc_csw(MscBot *b, uint8_t out[MSC_CSW_SIZE]) {
    if(!b || !out || !b->plan.valid_cbw || !b->command_active || b->completed!=b->plan.transfer_length) return -1;
    put_le32(out,UINT32_C(0x53425355)); put_le32(out+4,b->tag);
    put_le32(out+8,b->plan.host_length-b->completed); out[12]=b->plan.status;
    b->command_active=0;
    if(b->plan.reset_required) b->reset_locked=1;
    return MSC_CSW_SIZE;
}
