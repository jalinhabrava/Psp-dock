/* SPDX-License-Identifier: GPL-3.0-only */
#ifndef BRIDGE_PROTOCOL_H
#define BRIDGE_PROTOCOL_H
#include "bridge_hid.h"
#include <string.h>

typedef struct {
    uint8_t session[BRIDGE_SESSION_SIZE];
    uint32_t sequence;
    int32_t result;
    uint8_t pending;
    uint8_t consumed;
} BridgeProtocol;

static uint32_t bridge_read32(const uint8_t *p) {
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) |
        ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}
static void bridge_write32(uint8_t *p, uint32_t n) {
    p[0] = n; p[1] = n >> 8; p[2] = n >> 16; p[3] = n >> 24;
}
static void bridge_protocol_status(const BridgeProtocol *p, uint8_t *out,
                                   int active, int connected) {
    memset(out, 0, BRIDGE_FEATURE_SIZE);
    memcpy(out, "PDK1", 4);
    out[4] = BRIDGE_PROTOCOL_VERSION;
    out[5] = BRIDGE_OP_STATUS;
    out[6] = active ? BRIDGE_MODE_HID : 0;
    out[7] = (connected ? BRIDGE_FLAG_CONNECTED : 0) |
        (p->pending ? BRIDGE_FLAG_PENDING : 0) |
        (p->consumed ? BRIDGE_FLAG_CONSUMED : 0);
    memcpy(out + 8, p->session, BRIDGE_SESSION_SIZE);
    bridge_write32(out + 24, p->sequence);
    bridge_write32(out + 28, (uint32_t)p->result);
}
/* Run only for a complete successful 32-byte EP0 receive, under the lock.
 * Malformed/foreign-session reports must never change the accepted command.
 * Retransmission of the identical command is idempotent, including consumed.
 */
static int bridge_protocol_receive(BridgeProtocol *p, const uint8_t *in) {
    uint32_t seq = bridge_read32(in + 24);
    if (memcmp(in, "PDK1", 4) || in[4] != BRIDGE_PROTOCOL_VERSION ||
        in[5] != BRIDGE_OP_REQUEST_STORAGE || in[6] || in[7] ||
        bridge_read32(in + 28) || !seq)
        return BRIDGE_ERROR_FRAME;
    if (memcmp(in + 8, p->session, BRIDGE_SESSION_SIZE))
        return BRIDGE_ERROR_SESSION;
    if (p->pending || p->consumed)
        return seq == p->sequence ? p->result : BRIDGE_ERROR_BUSY;
    p->sequence = seq;
    p->pending = 1;
    p->result = BRIDGE_RESULT_QUEUED;
    return p->result;
}
static int bridge_protocol_take(BridgeProtocol *p, uint32_t *sequence) {
    if (!p->pending) return 0;
    *sequence = p->sequence;
    p->pending = 0;
    p->consumed = 1;
    p->result = BRIDGE_RESULT_CONSUMED;
    return 1;
}
#endif
