#include <assert.h>
#include <stdio.h>
#include "timeline.h"

int main(void) {
    PspDockK2Timeline log;
    PspDockK2Event event;
    unsigned int i;
    pspDockK2Init(&log);
    assert(log.magic == PSPDOCK_K2_MAGIC);
    assert(log.version == 2 && log.capabilities == 0);
    assert(pspDockK2Capture(&log, 1, 0x111, 1, (int32_t)0x80243005u) == 1);
    assert(log.count == 1 && log.history[0].changed == 7);
    assert(pspDockK2Capture(&log, 2, 0x111, 1, (int32_t)0x80243005u) == 0);
    assert(log.count == 1);
    assert(pspDockK2Capture(&log, 3, 0x333, 1, (int32_t)0x80243005u) == 1);
    assert(log.history[1].changed == PSPDOCK_K2_CHANGED_USB);
    assert(pspDockK2Capture(&log, 4, 0x333, 2, (int32_t)0x80243005u) == 1);
    assert(log.history[2].changed == PSPDOCK_K2_CHANGED_BUS);
    assert(pspDockK2Capture(&log, 5, 0x333, 2, 1) == 1);
    assert(log.history[3].changed == PSPDOCK_K2_CHANGED_STORAGE);
    for (i = 0; i < 50; ++i)
        assert(pspDockK2Capture(&log, 6 + i, 0x444 + i, 2, 1) == 1);
    assert(log.count == PSPDOCK_K2_HISTORY_MAX);
    assert(log.dropped == 54 - PSPDOCK_K2_HISTORY_MAX);
    assert(pspDockK2GetChronological(&log, 0, &event) == 0);
    assert(event.usb == 0x444 + 50 - PSPDOCK_K2_HISTORY_MAX);
    assert(pspDockK2GetChronological(&log, log.count - 1, &event) == 0);
    assert(event.usb == 0x444 + 49);
    assert(pspDockK2GetChronological(&log, log.count, &event) < 0);
    puts("K2 timeline: only transitions, masks, ring overwrite and chronology OK");
    return 0;
}
