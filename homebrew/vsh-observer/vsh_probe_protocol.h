/* Copy of the *proven* 0.7 user helper IPC ABI. */
#ifndef PSP_DOCK_VSH_PROBE_PROTOCOL_H
#define PSP_DOCK_VSH_PROBE_PROTOCOL_H
#include <psptypes.h>
#define GATE_V07_MAGIC 0x50444737u
#define GATE_V07_RESULT_MAGIC 0x52444737u
#define GATE_V07_SENTINEL 0x7fffffffu
typedef struct {
    u32 magic;
    u32 result_pointer;
} GateArguments;
typedef struct {
    volatile u32 magic;
    volatile s32 usb_state;
    volatile s32 bus_driver_state;
    volatile s32 storage_driver_state;
} GateResult;
_Static_assert(sizeof(GateArguments) == 8, "GateArguments ABI differs");
_Static_assert(sizeof(GateResult) == 16, "GateResult ABI differs");
#endif
