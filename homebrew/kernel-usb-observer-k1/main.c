/* PSP Dock K1: kernel observer, compile-only experimental artifact.
 *
 * No FAT or flash operations; no VSH hooks; no USB mode changes; no HID.
 * The only USB operations are sceUsbGetState / sceUsbGetDrvState.
 * DO NOT install on a real PSP until separately reviewed and staged.
 */
#include <pspkernel.h>
#include <pspthreadman.h>
#include <pspusb.h>
#include <pspusbstor.h>
#include "protocol.h"

PSP_MODULE_INFO("PSPDockK1Observer", PSP_MODULE_KERNEL, 1, 0);

static volatile int stopping;
static SceUID observation_thread = -1;
static volatile PspDockK1Snapshot observation = {
    PSPDOCK_K1_MAGIC,
    PSPDOCK_K1_VERSION,
    PSPDOCK_K1_CAPABILITIES,
    PSPDOCK_K1_PENDING,
    -1, -1, -1
};

/* Internal caller only: no USER pointer is accepted into kernel memory.
 * Status is copied field-by-field to a caller-provided kernel buffer.
 * Once READY, the observation is immutable until module_stop.
 */
int pspDockK1Read(PspDockK1Snapshot *out) {
    if (!out)
        return -1;
    out->magic = PSPDOCK_K1_MAGIC;
    out->version = PSPDOCK_K1_VERSION;
    out->capabilities = PSPDOCK_K1_CAPABILITIES;
    out->state = observation.state;
    if (out->state != PSPDOCK_K1_READY)
        return -2;
    out->usb_state = observation.usb_state;
    out->bus_driver_state = observation.bus_driver_state;
    out->storage_driver_state = observation.storage_driver_state;
    return 0;
}

static int sample_once(SceSize args, void *argp) {
    s32 usb, bus, storage;
    (void)args;
    (void)argp;
    sceKernelDelayThread(3000000);
    if (stopping)
        return 0;
    usb = sceUsbGetState();
    bus = sceUsbGetDrvState(PSP_USBBUS_DRIVERNAME);
    storage = sceUsbGetDrvState(PSP_USBSTOR_DRIVERNAME);
    if (stopping)
        return 0;
    observation.usb_state = usb;
    observation.bus_driver_state = bus;
    observation.storage_driver_state = storage;
    observation.state = PSPDOCK_K1_READY; /* Publish last. */
    return 0;
}

int module_start(SceSize args, void *argp) {
    (void)args;
    (void)argp;
    stopping = 0;
    observation.state = PSPDOCK_K1_PENDING;
    observation_thread = sceKernelCreateThread(
        "PSPDockK1UsbRead", sample_once, 0x20, 0x2000, 0, NULL);
    if (observation_thread < 0)
        return 1;
    if (sceKernelStartThread(observation_thread, 0, NULL) < 0) {
        sceKernelDeleteThread(observation_thread);
        observation_thread = -1;
        return 1;
    }
    return 0;
}

int module_stop(SceSize args, void *argp) {
    (void)args;
    (void)argp;
    stopping = 1;
    observation.state = PSPDOCK_K1_CANCELLED;
    if (observation_thread >= 0) {
        sceKernelTerminateDeleteThread(observation_thread);
        observation_thread = -1;
    }
    return 0;
}
