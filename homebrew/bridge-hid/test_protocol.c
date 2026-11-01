/* Native executable exercises the SAME protocol code built into the PRX. */
#include <assert.h>
#include <stdio.h>
#include "protocol.h"

int main(void) {
    BridgeProtocol p = {0}, before;
    uint8_t command[32] = {0}, status[32], bad[32];
    uint32_t seq = 0;
    int i;
    for (i = 0; i < 16; ++i) p.session[i] = i + 1;
    memcpy(command, "PDK1", 4);
    command[4] = 1; command[5] = 2;
    memcpy(command + 8, p.session, 16);
    bridge_write32(command + 24, 0x12345678);
    bridge_protocol_status(&p, status, 1, 1);
    assert(!memcmp(status, "PDK1\1\1\2\1", 8));
    assert(!memcmp(status + 8, command + 8, 16));
    assert(!bridge_read32(status + 24));
    assert(!bridge_protocol_take(&p, &seq));
    /* Each identifying/reserved byte rejects safely without changing state. */
    for (i = 0; i < 32; ++i) {
        if (i >= 24 && i < 28) continue;
        memcpy(bad, command, 32); bad[i] ^= 0x80;
        before = p;
        assert(bridge_protocol_receive(&p, bad) < 0);
        assert(!memcmp(&p, &before, sizeof(p)));
    }
    memcpy(bad, command, 32); memset(bad + 24, 0, 4);
    assert(bridge_protocol_receive(&p, bad) == BRIDGE_ERROR_FRAME);
    assert(bridge_protocol_receive(&p, command) == BRIDGE_RESULT_QUEUED);
    assert(bridge_protocol_receive(&p, command) == BRIDGE_RESULT_QUEUED);
    before = p;
    memcpy(bad, command, 32); bad[24]++;
    assert(bridge_protocol_receive(&p, bad) == BRIDGE_ERROR_BUSY);
    assert(!memcmp(&p, &before, sizeof(p)));
    bridge_protocol_status(&p, status, 1, 1);
    assert(status[7] == 3 && bridge_read32(status + 24) == 0x12345678);
    assert(bridge_protocol_take(&p, &seq) == 1 && seq == 0x12345678);
    assert(!bridge_protocol_take(&p, &seq));
    assert(bridge_protocol_receive(&p, command) == BRIDGE_RESULT_CONSUMED);
    assert(!bridge_protocol_take(&p, &seq));
    bridge_protocol_status(&p, status, 1, 1);
    assert(status[7] == 5 && bridge_read32(status + 28) == 2);
    puts("protocol: malformed/stale/replay/single-consumer/status passed");
    return 0;
}
