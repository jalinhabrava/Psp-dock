#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include "gate_guard.h"

int main(void) {
    const int32_t noent = (int32_t)GATE_V08_USB_NOENT;
    assert(gate_v08_precheck(0x111, 2, noent) == GATE_V08_OK);
    assert(gate_v08_precheck(0x111, 2, 2) == GATE_V08_OK);
    assert(gate_v08_precheck(0x311, 2, noent) == GATE_V08_BLOCK_CONNECTION);
    assert(gate_v08_precheck(0x131, 2, noent) == GATE_V08_BLOCK_CONNECTION);
    assert(gate_v08_precheck(0x113, 2, noent) == GATE_V08_BLOCK_CONNECTION);
    assert(gate_v08_precheck(-1, 2, noent) == GATE_V08_BLOCK_USB_ERROR);
    assert(gate_v08_precheck(0x111, 1, noent) == GATE_V08_BLOCK_BUS_OWNERSHIP);
    assert(gate_v08_precheck(0x111, 0, noent) == GATE_V08_BLOCK_BUS_OWNERSHIP);
    assert(gate_v08_precheck(0x111, 2, 1) == GATE_V08_BLOCK_STORAGE);
    assert(gate_v08_precheck(0x111, 2, -1) == GATE_V08_BLOCK_STORAGE);
    puts("USB Bus v0.8 guard: 10 native cases passed.");
    return 0;
}
