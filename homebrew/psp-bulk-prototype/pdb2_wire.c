#include "pdb2_wire.h"
#include <string.h>

static uint32_t get32(const uint8_t *s) {
    return ((uint32_t)s[0]) | ((uint32_t)s[1] << 8) |
           ((uint32_t)s[2] << 16) | ((uint32_t)s[3] << 24);
}
static uint64_t get64(const uint8_t *s) {
    return (uint64_t)get32(s) | ((uint64_t)get32(s + 4) << 32);
}
static void put32(uint8_t *d, uint32_t n) {
    d[0] = (uint8_t)n;
    d[1] = (uint8_t)(n >> 8);
    d[2] = (uint8_t)(n >> 16);
    d[3] = (uint8_t)(n >> 24);
}
static void put64(uint8_t *d, uint64_t n) {
    put32(d, (uint32_t)n);
    put32(d + 4, (uint32_t)(n >> 32));
}
static int supported(uint8_t op) {
    return (op >= PDB2_OP_HELLO && op <= PDB2_OP_LIST_DATA) ||
           op == PDB2_OP_ERROR;
}
static int nonzero_session(const uint8_t *s) {
    unsigned int i;
    uint8_t v = 0;
    for (i = 0; i < 16; ++i) v |= s[i];
    return v != 0;
}
static uint32_t crc_update(uint32_t sum, const uint8_t *data, size_t len) {
    size_t i;
    for (i = 0; i < len; ++i) {
        unsigned int j;
        sum ^= data[i];
        for (j = 0; j < 8; ++j) {
            uint32_t mask = (uint32_t)-(int)(sum & 1u);
            sum = (sum >> 1) ^ (0xedb88320u & mask);
        }
    }
    return sum;
}
uint32_t pdb2_crc32(const uint8_t *data, size_t len) {
    if (!data && len) return 0;
    return ~crc_update(0xffffffffu, data, len);
}

size_t pdb2_encode(const Pdb2Frame *frame, uint8_t *out, size_t cap) {
    uint32_t checksum;
    uint32_t sum;
    if (!frame || !out || !supported(frame->op) ||
        !frame->seq || !nonzero_session(frame->session) ||
        frame->len > PDB2_MAX_PAYLOAD ||
        (frame->len && !frame->payload) ||
        cap < PDB2_HEADER_SIZE + PDB2_CRC_SIZE + frame->len)
        return 0;
    memcpy(out, "PDB2", 4);
    out[4] = 1; out[5] = frame->op; out[6] = 0; out[7] = 0;
    put32(out + 8, frame->seq);
    memcpy(out + 12, frame->session, 16);
    put64(out + 28, frame->offset);
    put32(out + 36, frame->len);
    sum = crc_update(0xffffffffu, out, PDB2_HEADER_SIZE);
    sum = crc_update(sum, frame->payload, frame->len);
    checksum = ~sum;
    put32(out + PDB2_HEADER_SIZE, checksum);
    if (frame->len) memcpy(out + PDB2_HEADER_SIZE + PDB2_CRC_SIZE,
                           frame->payload, frame->len);
    return PDB2_HEADER_SIZE + PDB2_CRC_SIZE + frame->len;
}

int pdb2_decode(const uint8_t *input, size_t len, Pdb2Frame *frame) {
    uint32_t expected;
    uint32_t sum;
    uint32_t payload_len;
    if (!input || !frame || len < PDB2_HEADER_SIZE + PDB2_CRC_SIZE ||
        len > PDB2_MAX_FRAME || memcmp(input, "PDB2", 4) ||
        input[4] != 1 || !supported(input[5]) ||
        input[6] != 0 || input[7] != 0 ||
        !get32(input + 8) || !nonzero_session(input + 12))
        return -1;
    payload_len = get32(input + 36);
    if (payload_len > PDB2_MAX_PAYLOAD ||
        len != PDB2_HEADER_SIZE + PDB2_CRC_SIZE + payload_len)
        return -1;
    sum = crc_update(0xffffffffu, input, PDB2_HEADER_SIZE);
    sum = crc_update(sum, input + PDB2_HEADER_SIZE + PDB2_CRC_SIZE,
                     payload_len);
    expected = ~sum;
    if (expected != get32(input + PDB2_HEADER_SIZE))
        return -1;
    frame->op = input[5];
    frame->seq = get32(input + 8);
    memcpy(frame->session, input + 12, 16);
    frame->offset = get64(input + 28);
    frame->len = payload_len;
    frame->payload = input + PDB2_HEADER_SIZE + PDB2_CRC_SIZE;
    return 0;
}
