/* Shared user-to-user PRX argument protocol. No PSP USB or kernel APIs. */
#ifndef PSP_DOCK_GATE_V07_PROTOCOL_H
#define PSP_DOCK_GATE_V07_PROTOCOL_H

#include <psptypes.h>

#define GATE_V07_MAGIC 0x50444737u
#define GATE_V07_RESULT_MAGIC 0x52444737u
#define GATE_V07_SENTINEL 0x7fffffffu

typedef struct {
    u32 magic;
    u32 result_pointer; /* address in the same PSP user-mode address space */
} GateArguments;

typedef struct {
    volatile u32 magic;
    volatile s32 usb_state;
    volatile s32 bus_driver_state;
    volatile s32 storage_driver_state;
} GateResult;

_Static_assert(sizeof(GateArguments) == 8, "GateArguments must be 8 bytes");
_Static_assert(sizeof(GateResult) == 16, "GateResult must be 16 bytes");

#endif
