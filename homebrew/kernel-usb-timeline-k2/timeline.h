/* PSP Dock K2 — passive USB state transitions in RAM.
 *
 * This standalone C99/C11 component has no PSP SDK dependency. Its history
 * records only observed driver/state changes; it has NO eject permission,
 * FAT ownership claim, role-changing capabilities or hardware ACK.
 */
#ifndef PSP_DOCK_K2_TIMELINE_H
#define PSP_DOCK_K2_TIMELINE_H

#include <stdint.h>
#include <string.h>

#define PSPDOCK_K2_MAGIC 0x50444B32u
#define PSPDOCK_K2_VERSION 2u
#define PSPDOCK_K2_CAPABILITIES 0u
#define PSPDOCK_K2_HISTORY_MAX 16u
#define PSPDOCK_K2_CHANGED_USB 1u
#define PSPDOCK_K2_CHANGED_BUS 2u
#define PSPDOCK_K2_CHANGED_STORAGE 4u

typedef struct {
    uint32_t tick_us; /* sceKernelGetSystemTimeLow wraps; not an RTC. */
    uint32_t changed;
    int32_t usb;
    int32_t bus;
    int32_t storage;
} PspDockK2Event;

typedef struct {
    uint32_t magic;
    uint32_t version;
    uint32_t capabilities;
    uint32_t count;
    uint32_t cursor;
    uint32_t dropped;
    PspDockK2Event history[PSPDOCK_K2_HISTORY_MAX];
} PspDockK2Timeline;

_Static_assert(sizeof(PspDockK2Event) == 20, "K2 event ABI changed");
_Static_assert(sizeof(PspDockK2Timeline) == 344, "K2 timeline ABI changed");

static inline void pspDockK2Init(PspDockK2Timeline *log) {
    memset(log, 0, sizeof(*log));
    log->magic = PSPDOCK_K2_MAGIC;
    log->version = PSPDOCK_K2_VERSION;
    log->capabilities = PSPDOCK_K2_CAPABILITIES;
}

/* Write only if one of the observed values changed, or for the first
 * sample. Comparisons are exact signed bit patterns, including error codes.
 * Returns 1 for a new event, 0 if unchanged. No IO or other side effects.
 */
static inline int pspDockK2Capture(
    PspDockK2Timeline *log,
    uint32_t tick_us,
    int32_t usb, int32_t bus, int32_t storage
) {
    uint32_t changed = 7u;
    PspDockK2Event *last;
    PspDockK2Event *entry;
    if (log->count) {
        last = &log->history[
            (log->cursor + PSPDOCK_K2_HISTORY_MAX - 1u) % PSPDOCK_K2_HISTORY_MAX
        ];
        changed = (last->usb != usb ? PSPDOCK_K2_CHANGED_USB : 0u) |
            (last->bus != bus ? PSPDOCK_K2_CHANGED_BUS : 0u) |
            (last->storage != storage ? PSPDOCK_K2_CHANGED_STORAGE : 0u);
        if (!changed) return 0;
    }
    entry = &log->history[log->cursor];
    entry->tick_us = tick_us;
    entry->changed = changed;
    entry->usb = usb;
    entry->bus = bus;
    entry->storage = storage;
    log->cursor = (log->cursor + 1u) % PSPDOCK_K2_HISTORY_MAX;
    if (log->count < PSPDOCK_K2_HISTORY_MAX)
        ++log->count;
    else
        ++log->dropped;
    return 1;
}

/* Order oldest -> newest. The caller owns the output buffer. */
static inline int pspDockK2GetChronological(
    const PspDockK2Timeline *log, uint32_t index, PspDockK2Event *out
) {
    uint32_t start;
    if (index >= log->count || !out) return -1;
    start = (log->cursor + PSPDOCK_K2_HISTORY_MAX - log->count) %
        PSPDOCK_K2_HISTORY_MAX;
    *out = log->history[(start + index) % PSPDOCK_K2_HISTORY_MAX];
    return 0;
}
#endif
