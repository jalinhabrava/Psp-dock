/* Shared user-to-user PRX argument protocol. No PSP USB or kernel APIs. */
#ifndef PSP_DOCK_GATE_V08_PROTOCOL_H
#define PSP_DOCK_GATE_V08_PROTOCOL_H

#include <psptypes.h>
#include "gate_guard.h"

#define GATE_V08_MAGIC 0x50444738u
#define GATE_V08_RESULT_MAGIC 0x52444738u
#define GATE_V08_SENTINEL 0x7fffffffu

typedef struct {
    u32 magic;
    u32 result_pointer; /* address in the same PSP user-mode address space */
} GateArguments;

typedef struct {
    volatile u32 magic;
    volatile s32 usb_before;
    volatile s32 bus_before;
    volatile s32 storage_before;
    volatile s32 guard;
    volatile s32 start_result;
    volatile s32 bus_after_start;
    volatile s32 stop_result;
    volatile s32 bus_after_stop;
    volatile s32 storage_after;
    volatile s32 usb_after;
} GateResult;

_Static_assert(sizeof(GateArguments) == 8, "GateArguments must be 8 bytes");
_Static_assert(sizeof(GateResult) == 44, "GateResult must be 44 bytes");

#endif
