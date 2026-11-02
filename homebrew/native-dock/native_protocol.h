/* SPDX-License-Identifier: GPL-3.0-only
 * PDK2 is a logical UI/input protocol. It grants no filesystem ownership,
 * safe-eject proof, raw-disk access or permission to cut power to storage.
 * Wire: <4sBBBB16sIi>, exactly 32 bytes, little endian, no report ID here.
 */
#ifndef PSPDOCK_NATIVE_PROTOCOL_H
#define PSPDOCK_NATIVE_PROTOCOL_H
#include <stdint.h>
#include <string.h>

#define PDN_FEATURE_SIZE 32u
#define PDN_SESSION_SIZE 16u
#define PDN_VERSION 2u
#define PDN_OP_STATUS 1u
#define PDN_OP_SET_PHASE 2u
#define PDN_FLAG_CONNECTED 1u
#define PDN_FLAG_PENDING 2u
#define PDN_FLAG_SONY_STORAGE 4u
#define PDN_FLAG_EXIT_REQUESTED 8u

enum PdnPhase {
    PDN_PHASE_BOOT=0, PDN_PHASE_STORAGE=1, PDN_PHASE_CONTROLLER=2,
    PDN_PHASE_COPYING=3, PDN_PHASE_EXIT_SYNC=4, PDN_PHASE_SAFE=5,
    PDN_PHASE_ERROR=255
};
enum PdnResult {
    PDN_IDLE=0, PDN_QUEUED=1, PDN_APPLIED=2,
    PDN_ERR_FRAME=-1, PDN_ERR_SESSION=-2, PDN_ERR_SEQUENCE=-3,
    PDN_ERR_BUSY=-4, PDN_ERR_TRANSITION=-5, PDN_ERR_LINK=-6
};
typedef struct {
    uint8_t session[PDN_SESSION_SIZE];
    uint8_t phase, target, pending;
    uint32_t sequence;
    int32_t result;
    uint32_t rejected;
    int32_t last_rejection;
} PdnProtocol;

static inline uint32_t pdn_read32(const uint8_t *p) {
    return (uint32_t)p[0] | ((uint32_t)p[1]<<8) |
           ((uint32_t)p[2]<<16) | ((uint32_t)p[3]<<24);
}
static inline void pdn_write32(uint8_t *p,uint32_t n) {
    p[0]=(uint8_t)n;p[1]=(uint8_t)(n>>8);
    p[2]=(uint8_t)(n>>16);p[3]=(uint8_t)(n>>24);
}
static inline int pdn_protocol_init(PdnProtocol *p,const uint8_t session[16]) {
    unsigned i;uint8_t any=0;
    for(i=0;i<16;i++)any|=session[i];
    if(!any)return PDN_ERR_SESSION;
    memset(p,0,sizeof(*p));memcpy(p->session,session,16);
    p->phase=PDN_PHASE_STORAGE;p->target=PDN_PHASE_STORAGE;
    return 0;
}
static inline int pdn_phase_accepts_input(uint8_t phase) {
    return phase==PDN_PHASE_STORAGE || phase==PDN_PHASE_CONTROLLER;
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
        default:return 0; /* SAFE is terminal for this session. */
    }
}
static inline int pdn_reject(PdnProtocol *p,int32_t code) {
    p->rejected++;p->last_rejection=code;return code;
}
/* Invalid/replayed commands do not overwrite the accepted command's ACK.
 * Otherwise an unrelated stale command could make the host misread success.
 */
static inline int pdn_protocol_receive(PdnProtocol *p,const uint8_t *in,unsigned size) {
    uint32_t seq;uint8_t target;
    if(size!=PDN_FEATURE_SIZE || memcmp(in,"PDK2",4) || in[4]!=PDN_VERSION ||
       in[5]!=PDN_OP_SET_PHASE || in[7] || pdn_read32(in+28))
        return pdn_reject(p,PDN_ERR_FRAME);
    if(memcmp(in+8,p->session,16))return pdn_reject(p,PDN_ERR_SESSION);
    seq=pdn_read32(in+24);target=in[6];
    if(seq && seq==p->sequence) {
        if(target==p->target)return p->result;
        return pdn_reject(p,PDN_ERR_SEQUENCE);
    }
    if(p->pending)return pdn_reject(p,PDN_ERR_BUSY);
    if(p->sequence==UINT32_MAX || !seq || seq!=p->sequence+1u)
        return pdn_reject(p,PDN_ERR_SEQUENCE);
    if(!pdn_transition_valid(p->phase,target))return pdn_reject(p,PDN_ERR_TRANSITION);
    p->sequence=seq;p->target=target;p->pending=1;p->result=PDN_QUEUED;
    return PDN_QUEUED;
}
/* Called by the worker only after EP0 completion and input cancellation.
 * It does not operate on Sony USBSTOR, a filesystem, or any block device.
 */
static inline int pdn_protocol_apply(PdnProtocol *p) {
    if(!p->pending)return 0;
    p->phase=p->target;p->pending=0;p->result=PDN_APPLIED;return 1;
}
static inline void pdn_protocol_fail_pending(PdnProtocol *p,int32_t error) {
    if(p->pending){p->pending=0;p->result=error;}
}
static inline void pdn_protocol_status(const PdnProtocol *p,uint8_t out[32],
                                      int connected,int storage,int exit_requested) {
    memset(out,0,32);memcpy(out,"PDK2",4);out[4]=PDN_VERSION;out[5]=PDN_OP_STATUS;
    out[6]=p->phase;
    out[7]=(uint8_t)((connected?PDN_FLAG_CONNECTED:0u)|
        (p->pending?PDN_FLAG_PENDING:0u)|(storage?PDN_FLAG_SONY_STORAGE:0u)|
        (exit_requested?PDN_FLAG_EXIT_REQUESTED:0u));
    memcpy(out+8,p->session,16);pdn_write32(out+24,p->sequence);
    pdn_write32(out+28,(uint32_t)p->result);
}
#endif
