/* Shared user-to-user PRX argument protocol. No PSP USB or kernel APIs. */
#ifndef PSP_DOCK_GATE_V06_PROTOCOL_H
#define PSP_DOCK_GATE_V06_PROTOCOL_H

#include <psptypes.h>

#define GATE_V06_MAGIC 0x50444736u
#define GATE_V06_RESULT_MAGIC 0x52444736u
#define GATE_V06_SENTINEL 0x7fffffffu

typedef struct {
    u32 magic;
    u32 result_pointer; /* address in the same PSP user-mode address space */
} GateArguments;

typedef struct {
    volatile u32 magic;
    volatile s32 usb_state;
} GateResult;

_Static_assert(sizeof(GateArguments) == 8, "GateArguments must be 8 bytes");
_Static_assert(sizeof(GateResult) == 8, "GateResult must be 8 bytes");

#endif
