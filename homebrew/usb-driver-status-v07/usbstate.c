/*
 * USB Driver Status Gate 0.7, read-only USER PRX. Checks the three available
 * USB status calls. No starting/activating/stopping drivers or storage.
 */
#include <pspkernel.h>
#include <pspusb.h>
#include <pspusbstor.h>
#include "gate_protocol.h"

PSP_MODULE_INFO("DockUsbStatus07", PSP_MODULE_USER, 1, 0);

int module_start(SceSize argsize, void *argp) {
    GateArguments *args;
    volatile GateResult *target;
    u32 address;

    if (argsize != sizeof(GateArguments) || !argp)
        return 2; /* Reject protocol mismatch; no arbitrary memory write. */
    args = (GateArguments *)argp;
    if (args->magic != GATE_V07_MAGIC)
        return 2;
    address = args->result_pointer;
    /* PSP user memory range, including extra PSP-2000 RAM. */
    if ((address & 3u) != 0 || address < 0x08800000u || address > 0x0bfffff0u)
        return 2;
    target = (volatile GateResult *)address;
    if (target->magic != GATE_V07_RESULT_MAGIC ||
        (u32)target->usb_state != GATE_V07_SENTINEL ||
        (u32)target->bus_driver_state != GATE_V07_SENTINEL ||
        (u32)target->storage_driver_state != GATE_V07_SENTINEL)
        return 2;
    target->usb_state = sceUsbGetState();
    target->bus_driver_state = sceUsbGetDrvState(PSP_USBBUS_DRIVERNAME);
    target->storage_driver_state = sceUsbGetDrvState(PSP_USBSTOR_DRIVERNAME);
    return 0;
}

int module_stop(SceSize argsize, void *argp) {
    (void)argsize;
    (void)argp;
    return 0;
}
