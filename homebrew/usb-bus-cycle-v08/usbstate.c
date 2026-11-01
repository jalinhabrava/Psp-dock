/*
 * PSP Dock USB Bus Cycle v0.8 — controlled USER-mode PRX test.
 *
 * ONLY exercises USBBusDriver, NEVER USBStor_Driver and NEVER sceUsbActivate.
 * First verifies USB is inactive, no cable is present, bus is STOPPED (2),
 * and the storage driver is absent (NOENT) or STOPPED. Any other condition
 * refuses all mutation. sceUsbStop is called only after OUR successful
 * sceUsbStart. No FAT/filesystem access, no flash writes, no kernel imports.
 */
#include <pspkernel.h>
#include <pspusb.h>
#include <pspusbstor.h>
#include "gate_protocol.h"

PSP_MODULE_INFO("PSPDockUsbBus08", PSP_MODULE_USER, 1, 0);

static int valid_result(const volatile GateResult *r) {
    return r->magic == GATE_V08_RESULT_MAGIC &&
        (u32)r->usb_before == GATE_V08_SENTINEL &&
        (u32)r->bus_before == GATE_V08_SENTINEL &&
        (u32)r->storage_before == GATE_V08_SENTINEL &&
        (u32)r->guard == GATE_V08_SENTINEL &&
        (u32)r->start_result == GATE_V08_SENTINEL &&
        (u32)r->bus_after_start == GATE_V08_SENTINEL &&
        (u32)r->stop_result == GATE_V08_SENTINEL &&
        (u32)r->bus_after_stop == GATE_V08_SENTINEL &&
        (u32)r->storage_after == GATE_V08_SENTINEL &&
        (u32)r->usb_after == GATE_V08_SENTINEL;
}

int module_start(SceSize argsize, void *argp) {
    GateArguments *args;
    volatile GateResult *out;
    u32 address;

    if (argsize != sizeof(GateArguments) || !argp)
        return 2;
    args = (GateArguments *)argp;
    if (args->magic != GATE_V08_MAGIC)
        return 2;
    address = args->result_pointer;
    if ((address & 3u) != 0 || address < 0x08800000u ||
        address > 0x0bffffc0u)
        return 2;
    out = (volatile GateResult *)address;
    if (!valid_result(out))
        return 2;

    out->usb_before = sceUsbGetState();
    out->bus_before = sceUsbGetDrvState(PSP_USBBUS_DRIVERNAME);
    out->storage_before = sceUsbGetDrvState(PSP_USBSTOR_DRIVERNAME);
    out->guard = gate_v08_precheck(out->usb_before, out->bus_before,
                                  out->storage_before);
    if (out->guard != GATE_V08_OK)
        return 0;

    out->start_result = sceUsbStart(PSP_USBBUS_DRIVERNAME, 0, 0);
    if (out->start_result == 0) {
        out->bus_after_start = sceUsbGetDrvState(PSP_USBBUS_DRIVERNAME);
        /* We own this driver only when start returned success. Even if the
         * observed state is unexpected, try to release our own start. */
        out->stop_result = sceUsbStop(PSP_USBBUS_DRIVERNAME, 0, 0);
        out->bus_after_stop = sceUsbGetDrvState(PSP_USBBUS_DRIVERNAME);
    }
    out->storage_after = sceUsbGetDrvState(PSP_USBSTOR_DRIVERNAME);
    out->usb_after = sceUsbGetState();
    return 0;
}

int module_stop(SceSize argsize, void *argp) {
    (void)argsize;
    (void)argp;
    return 0;
}
