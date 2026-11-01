#include <assert.h>
#include <stdio.h>
#include "lab_gate.h"

int main(void) {
    const int noent = (int)HID_LAB_STORAGE_NOENT;
    int value;
    assert(hid_lab_safe_to_start(0x111, noent, 1));
    assert(hid_lab_safe_to_start(0x111, 2, 2));
    assert(hid_lab_safe_to_start(0x111, noent, 2));
    for (value = 0; value < 3; ++value) {
        const int flags[] = {0x200, 0x020, 0x002};
        assert(!hid_lab_safe_to_start(0x111 | flags[value], noent, 1));
    }
    assert(!hid_lab_safe_to_start(-1, noent, 1));
    assert(!hid_lab_safe_to_start(0x111, 1, 1));
    assert(!hid_lab_safe_to_start(0x111, 0, 1));
    assert(!hid_lab_safe_to_start(0x111, 3, 1));
    assert(!hid_lab_safe_to_start(0x111, (int)0x80240001u, 1));
    assert(!hid_lab_safe_to_start(0x111, noent, 0));
    assert(!hid_lab_safe_to_start(0x111, noent, 3));
    puts("HID-only gate: off/unregistered, cable, storage and bus guards OK");
    return 0;
}
