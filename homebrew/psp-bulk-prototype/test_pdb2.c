#include "pdb2_wire.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>

int main(void) {
    uint8_t encoded[PDB2_MAX_FRAME];
    uint8_t session[16] = {0x04};
    const uint8_t payload[] = "ISO/Patapon.iso";
    Pdb2Frame in, out;
    size_t len;
    uint32_t crc;
    memset(&in, 0, sizeof(in));
    in.op = PDB2_OP_STAT;
    memcpy(in.session, session, sizeof(session));
    in.seq = 42;
    in.offset = 34359738368ull;
    in.len = sizeof(payload) - 1;
    in.payload = payload;
    len = pdb2_encode(&in, encoded, sizeof(encoded));
    assert(len == 44 + sizeof(payload) - 1);
    assert(pdb2_decode(encoded, len, &out) == 0);
    assert(out.op == PDB2_OP_STAT && out.seq == 42);
    assert(out.offset == 34359738368ull);
    assert(out.len == in.len);
    assert(memcmp(out.session, session, 16) == 0);
    assert(memcmp(out.payload, payload, in.len) == 0);
    crc = pdb2_crc32((const uint8_t *)"123456789", 9);
    assert(crc == 0xcbf43926u);
    encoded[len-1] ^= 1;
    assert(pdb2_decode(encoded, len, &out) < 0);
    encoded[len-1] ^= 1;
    assert(pdb2_decode(encoded, len-1, &out) < 0);
    encoded[4] = 2;
    assert(pdb2_decode(encoded, len, &out) < 0);
    puts("PDB2 PSP pure-C wire framing: OK");
    return 0;
}
