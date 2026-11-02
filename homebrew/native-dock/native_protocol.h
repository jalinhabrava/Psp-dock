/* SPDX-License-Identifier: GPL-3.0-only
 * PDK2/v3 is a session-bound UI/input protocol. It grants no filesystem
 * ownership, safe-eject proof or raw-disk access. HOST_SHUTDOWN is sent
 * only after Windows verifies and records media release for this session.
 * Payload: <4sBBBB16sIi4sHHIBBHQQ>, 64 bytes, LE, no report ID here.
 */
#ifndef PSPDOCK_NATIVE_PROTOCOL_H
#define PSPDOCK_NATIVE_PROTOCOL_H
#include <stdint.h>
#include <string.h>

#define PDN_FEATURE_SIZE 64u
#define PDN_TRANSFER_SIZE PDN_FEATURE_SIZE
#define PDN_SESSION_SIZE 16u
#define PDN_VERSION 3u
#define PDN_FIRMWARE_REVISION 0x0307u
#define PDN_BUILD_ID 0x03070001u
#define PDN_CAP_NATIVE_STORAGE 1u
#define PDN_CAP_HOST_BEGIN 2u
#define PDN_CAP_HOST_PROGRESS 4u
#define PDN_CAP_HOST_SHUTDOWN 8u
#define PDN_CAPABILITIES 15u
#define PDN_OP_STATUS 1u
#define PDN_OP_SET_PHASE 2u
#define PDN_OP_HOST_BEGIN 3u
#define PDN_OP_HOST_PROGRESS 4u
#define PDN_OP_HOST_SHUTDOWN 5u
#define PDN_FLAG_CONNECTED 1u
#define PDN_FLAG_PENDING 2u
#define PDN_FLAG_SONY_STORAGE 4u
#define PDN_FLAG_EXIT_REQUESTED 8u
#define PDN_FLAG_HOST_CONFIRMED 16u
#define PDN_FLAG_SHUTDOWN_REQUESTED 32u

/* Keep the actual wire prefix in an auditable ELF symbol. A compiler may
 * encode a short memcpy/memcmp literal only as instruction immediates;
 * searching arbitrary ELF bytes then proves neither presence nor absence.
 * Both the status encoder and command validator use this exact prefix. */
static const uint8_t pdn_wire_prefix[5] __attribute__((used)) = {
    'P','D','K','2',PDN_VERSION
};

/* Compiled identity used by both the encoder and validator. Sony may supply
 * the composite device's bcdDevice=0100; that USB value identifies no build.
 * This is an ABI/build discriminator, not cryptographic attestation.
 */
static const uint8_t pdn_identity[12] __attribute__((used)) = {
    'N','A','T','3',
    PDN_FIRMWARE_REVISION&255u,PDN_FIRMWARE_REVISION>>8,
    PDN_CAPABILITIES,0,
    PDN_BUILD_ID&255u,(PDN_BUILD_ID>>8)&255u,
    (PDN_BUILD_ID>>16)&255u,PDN_BUILD_ID>>24
};
/* Audit checks these compiled bytes against the descriptor and identity.
 * Header, feature size, input/output size, opcodes, supported flags, and
 * claim/progress policy agree with the wire encoder and C regressions.
 */
static const uint8_t pdn_protocol_abi[17] __attribute__((used)) = {
    'P','D','N','A',PDN_VERSION,PDN_TRANSFER_SIZE,8,4,
    PDN_OP_STATUS,PDN_OP_SET_PHASE,PDN_OP_HOST_BEGIN,PDN_OP_HOST_PROGRESS,
    63,1,1,0, /* flags, mandatory work claim, progress, USB-as-build */
    PDN_OP_HOST_SHUTDOWN /* terminal host release; not an input/work claim */
};
/* Exact command transport for 0307: 32 ordered reports, 2 core bytes/report.
 * Status GET keeps the already observed 64-byte Feature layout. */
static const uint8_t pdn_transport_abi[10] __attribute__((used)) = {
    'P','D','F','4',32,2,0xc0,64,0xe0,16 /* final CRC16-CCITT commit */
};

enum PdnPhase {
    PDN_PHASE_BOOT=0, PDN_PHASE_STORAGE=1, PDN_PHASE_CONTROLLER=2,
    PDN_PHASE_COPYING=3, PDN_PHASE_EXIT_SYNC=4, PDN_PHASE_SAFE=5,
    PDN_PHASE_ERROR=255
};
enum PdnActivity {
    PDN_ACTIVITY_WAITING=0, PDN_ACTIVITY_INVENTORY=1, PDN_ACTIVITY_CHOOSING=2,
    PDN_ACTIVITY_SYNC_IN=3, PDN_ACTIVITY_COPY_GAME=4, PDN_ACTIVITY_CONTROLLER=5,
    PDN_ACTIVITY_SYNC_OUT=6, PDN_ACTIVITY_SAFE=7
};
enum PdnProgressUnit {
    PDN_UNIT_UNKNOWN=0, PDN_UNIT_BYTES=1, PDN_UNIT_FILES=2
};
enum PdnResult {
    PDN_IDLE=0, PDN_QUEUED=1, PDN_APPLIED=2,
    PDN_ERR_FRAME=-1, PDN_ERR_SESSION=-2, PDN_ERR_SEQUENCE=-3,
    PDN_ERR_BUSY=-4, PDN_ERR_TRANSITION=-5, PDN_ERR_LINK=-6,
    PDN_ERR_HANDSHAKE=-7, PDN_ERR_PROGRESS=-8
};
typedef struct {
    uint8_t session[PDN_SESSION_SIZE];
    uint8_t phase,target,pending,host_confirmed;
    uint8_t activity,progress_unit,shutdown_requested;
    uint64_t completed,total;
    uint32_t sequence;
    int32_t result;
    uint32_t rejected;
    int32_t last_rejection;
    uint8_t command[PDN_FEATURE_SIZE];
} PdnProtocol;

static inline uint32_t pdn_read32(const uint8_t *p) {
    return (uint32_t)p[0] | ((uint32_t)p[1]<<8) |
           ((uint32_t)p[2]<<16) | ((uint32_t)p[3]<<24);
}
static inline uint64_t pdn_read64(const uint8_t *p) {
    return (uint64_t)pdn_read32(p) | ((uint64_t)pdn_read32(p+4)<<32);
}
static inline void pdn_write32(uint8_t *p,uint32_t n) {
    p[0]=(uint8_t)n;p[1]=(uint8_t)(n>>8);
    p[2]=(uint8_t)(n>>16);p[3]=(uint8_t)(n>>24);
}
static inline void pdn_write64(uint8_t *p,uint64_t n) {
    pdn_write32(p,(uint32_t)n);pdn_write32(p+4,(uint32_t)(n>>32));
}
static inline int pdn_protocol_init(PdnProtocol *p,const uint8_t session[16]) {
    unsigned i;uint8_t any=0;
    for(i=0;i<16;i++)any|=session[i];
    if(!any)return PDN_ERR_SESSION;
    memset(p,0,sizeof(*p));memcpy(p->session,session,16);
    p->phase=PDN_PHASE_STORAGE;p->target=PDN_PHASE_STORAGE;
    return 0;
}
static inline int pdn_protocol_accepts_input(const PdnProtocol *p) {
    return p->host_confirmed && !p->pending && !p->shutdown_requested &&
        (p->phase==PDN_PHASE_CONTROLLER ||
         (p->phase==PDN_PHASE_STORAGE && p->activity==PDN_ACTIVITY_CHOOSING));
}
static inline int pdn_transition_valid(uint8_t from,uint8_t to) {
    if(from<PDN_PHASE_STORAGE || from>PDN_PHASE_SAFE ||
       to<PDN_PHASE_STORAGE || to>PDN_PHASE_SAFE)return 0;
    if(from==to)return 1;
    switch(from) {
        case PDN_PHASE_STORAGE:
        case PDN_PHASE_CONTROLLER:
        case PDN_PHASE_COPYING:
            return to>=PDN_PHASE_STORAGE && to<=PDN_PHASE_EXIT_SYNC;
        case PDN_PHASE_EXIT_SYNC:
            return to==PDN_PHASE_CONTROLLER || to==PDN_PHASE_SAFE;
        default:return 0;
    }
}
static inline uint8_t pdn_activity_for_phase(uint8_t phase) {
    switch(phase){
        case PDN_PHASE_STORAGE:return PDN_ACTIVITY_INVENTORY;
        case PDN_PHASE_CONTROLLER:return PDN_ACTIVITY_CONTROLLER;
        case PDN_PHASE_COPYING:return PDN_ACTIVITY_COPY_GAME;
        case PDN_PHASE_EXIT_SYNC:return PDN_ACTIVITY_SYNC_OUT;
        case PDN_PHASE_SAFE:return PDN_ACTIVITY_SAFE;
        default:return PDN_ACTIVITY_WAITING;
    }
}
static inline int pdn_activity_valid(uint8_t phase,uint8_t activity) {
    if(phase==PDN_PHASE_STORAGE)
        return activity>=PDN_ACTIVITY_INVENTORY && activity<=PDN_ACTIVITY_SYNC_IN;
    return phase>=PDN_PHASE_CONTROLLER && phase<=PDN_PHASE_SAFE &&
        activity==pdn_activity_for_phase(phase);
}
static inline int pdn_progress_valid(uint8_t unit,uint64_t done,uint64_t total) {
    if(unit==PDN_UNIT_UNKNOWN)return !done && !total;
    return (unit==PDN_UNIT_BYTES || unit==PDN_UNIT_FILES) && total && done<=total;
}
static inline int pdn_reject(PdnProtocol *p,int32_t code) {
    p->rejected++;p->last_rejection=code;return code;
}
/* Invalid/stale commands leave the accepted command's ACK unchanged. An
 * identical replay is read-only/idempotent; sequence reuse with other bytes
 * cannot acknowledge a different action. GET_REPORT never calls this path.
 */
static inline int pdn_protocol_receive(PdnProtocol *p,const uint8_t *in,unsigned size) {
    uint32_t seq;uint8_t target,op;unsigned i;
    if(size!=PDN_FEATURE_SIZE || memcmp(in,pdn_wire_prefix,sizeof(pdn_wire_prefix)) ||
       in[5]<PDN_OP_SET_PHASE || in[5]>PDN_OP_HOST_SHUTDOWN ||
       in[7] || pdn_read32(in+28) || memcmp(in+32,pdn_identity,sizeof(pdn_identity)) ||
       in[46] || in[47])return pdn_reject(p,PDN_ERR_FRAME);
    if(memcmp(in+8,p->session,16))return pdn_reject(p,PDN_ERR_SESSION);
    seq=pdn_read32(in+24);target=in[6];op=in[5];
    if(seq && seq==p->sequence){
        if(!memcmp(in,p->command,PDN_FEATURE_SIZE))return p->result;
        return pdn_reject(p,PDN_ERR_SEQUENCE);
    }
    if(p->pending)return pdn_reject(p,PDN_ERR_BUSY);
    if(p->shutdown_requested)return pdn_reject(p,PDN_ERR_TRANSITION);
    if(p->sequence==UINT32_MAX || !seq || seq!=p->sequence+1u)
        return pdn_reject(p,PDN_ERR_SEQUENCE);
    if(op!=PDN_OP_HOST_PROGRESS){
        for(i=44;i<PDN_FEATURE_SIZE;i++)if(in[i])return pdn_reject(p,PDN_ERR_FRAME);
    }
    if(op==PDN_OP_HOST_SHUTDOWN){
        if(target!=PDN_PHASE_SAFE ||
           !((p->phase==PDN_PHASE_STORAGE && !p->host_confirmed) ||
             (p->phase==PDN_PHASE_SAFE && p->host_confirmed) ||
             p->phase==PDN_PHASE_ERROR))return pdn_reject(p,PDN_ERR_TRANSITION);
    }else if(op==PDN_OP_HOST_BEGIN){
        if(p->host_confirmed || p->phase!=PDN_PHASE_STORAGE || target!=PDN_PHASE_STORAGE)
            return pdn_reject(p,PDN_ERR_HANDSHAKE);
    }else{
        if(!p->host_confirmed)return pdn_reject(p,PDN_ERR_HANDSHAKE);
        if(op==PDN_OP_SET_PHASE){
            if(!pdn_transition_valid(p->phase,target))return pdn_reject(p,PDN_ERR_TRANSITION);
        }else if(target!=p->phase || !pdn_activity_valid(p->phase,in[44]) ||
                 !pdn_progress_valid(in[45],pdn_read64(in+48),pdn_read64(in+56)))
            return pdn_reject(p,PDN_ERR_PROGRESS);
    }
    p->sequence=seq;p->target=target;p->pending=1;p->result=PDN_QUEUED;
    memcpy(p->command,in,PDN_FEATURE_SIZE);return PDN_QUEUED;
}
/* Worker calls only after EP0 completion and old input drain. No operation
 * here starts/stops Sony storage, touches FAT, clears exit, or ejects USB.
 * Applying HOST_SHUTDOWN latches terminal host authorization; the worker
 * owns the subsequent shutdown and its independent cleanup checks.
 */
static inline int pdn_protocol_apply(PdnProtocol *p) {
    uint8_t op;
    if(!p->pending)return 0;
    op=p->command[5];
    if(op==PDN_OP_HOST_PROGRESS){
        p->activity=p->command[44];p->progress_unit=p->command[45];
        p->completed=pdn_read64(p->command+48);p->total=pdn_read64(p->command+56);
    }else{
        if(op==PDN_OP_HOST_BEGIN)p->host_confirmed=1;
        p->phase=p->target;p->activity=pdn_activity_for_phase(p->phase);
        if(op==PDN_OP_HOST_SHUTDOWN){
            p->shutdown_requested=1;
            if(!p->host_confirmed)p->activity=PDN_ACTIVITY_WAITING;
        }
        p->progress_unit=PDN_UNIT_UNKNOWN;p->completed=p->total=0;
    }
    p->pending=0;p->result=PDN_APPLIED;return 1;
}
static inline void pdn_protocol_fail_pending(PdnProtocol *p,int32_t error) {
    if(p->pending){p->pending=0;p->result=error;}
}
static inline void pdn_protocol_invalidate_link(PdnProtocol *p) {
    /* A completed terminal authorization survives detach until the worker
     * stops. A merely queued command still fails with its old generation. */
    if(p->shutdown_requested)return;
    pdn_protocol_fail_pending(p,PDN_ERR_LINK);
    p->host_confirmed=0;p->activity=PDN_ACTIVITY_WAITING;
    p->progress_unit=PDN_UNIT_UNKNOWN;p->completed=p->total=0;
}
static inline void pdn_protocol_status(const PdnProtocol *p,uint8_t out[PDN_FEATURE_SIZE],
                                      int connected,int storage,int exit_requested) {
    memset(out,0,PDN_FEATURE_SIZE);memcpy(out,pdn_wire_prefix,sizeof(pdn_wire_prefix));out[5]=PDN_OP_STATUS;
    out[6]=p->phase;
    out[7]=(uint8_t)((connected?PDN_FLAG_CONNECTED:0u)|
        (p->pending?PDN_FLAG_PENDING:0u)|(storage?PDN_FLAG_SONY_STORAGE:0u)|
        (exit_requested?PDN_FLAG_EXIT_REQUESTED:0u)|
        (p->host_confirmed?PDN_FLAG_HOST_CONFIRMED:0u)|
        (p->shutdown_requested?PDN_FLAG_SHUTDOWN_REQUESTED:0u));
    memcpy(out+8,p->session,16);pdn_write32(out+24,p->sequence);
    pdn_write32(out+28,(uint32_t)p->result);memcpy(out+32,pdn_identity,sizeof(pdn_identity));
    out[44]=p->activity;out[45]=p->progress_unit;
    pdn_write64(out+48,p->completed);pdn_write64(out+56,p->total);
}
#endif
