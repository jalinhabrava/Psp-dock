/*
 * Import Gate 0.6 read-only USER PRX. It does not use the return from
 * module_start() to communicate the USB state. Returning 0 keeps the module
 * resident for a clean sceKernelStopModule + sceKernelUnloadModule sequence.
 */
#include <pspkernel.h>
#include <pspusb.h>
#include "gate_protocol.h"

PSP_MODULE_INFO("DockUsbStateRead06", PSP_MODULE_USER, 1, 0);

int module_start(SceSize argsize, void *argp) {
    GateArguments *args;
    volatile GateResult *target;
    u32 address;

    if (argsize != sizeof(GateArguments) || !argp)
        return 2; /* Reject protocol mismatch; no arbitrary memory write. */
    args = (GateArguments *)argp;
    if (args->magic != GATE_V06_MAGIC)
        return 2;
    address = args->result_pointer;
    /* PSP user memory range, including extra PSP-2000 RAM. */
    if ((address & 3u) != 0 || address < 0x08800000u || address > 0x0bfffff8u)
        return 2;
    target = (volatile GateResult *)address;
    if (target->magic != GATE_V06_RESULT_MAGIC ||
        (u32)target->usb_state != GATE_V06_SENTINEL)
        return 2;
    target->usb_state = sceUsbGetState();
    return 0;
}

int module_stop(SceSize argsize, void *argp) {
    (void)argsize;
    (void)argp;
    return 0;
}
