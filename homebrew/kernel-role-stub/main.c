/*
 * PSP Dock future kernel role manager: inert compiled scaffold.
 *
 * Deliberately no imports of sceUsb, USBStor, flash0, any I/O, or VSH hooks.
 * Does NOT register in ARK plugin manager, run automatically, or switch modes.
 * Any role mutation is refused until a separate hardware-tested version exists.
 */
#include <pspkernel.h>
#include "role_protocol.h"

PSP_MODULE_INFO("PSPDockKStub", PSP_MODULE_KERNEL, 0, 1);

static volatile u32 started;
static volatile u32 startup_tick;

int pspDockKernelGetStatus(PspDockKernelStatus *status) {
    if (!status)
        return PSPDOCK_KSTUB_UNAVAILABLE;
    status->magic = PSPDOCK_KSTUB_MAGIC;
    status->version = PSPDOCK_KSTUB_VERSION;
    status->capabilities = PSPDOCK_KSTUB_CAPS;
    status->role = PSPDOCK_KSTUB_IDLE;
    status->started = started;
    return 0;
}

int pspDockKernelRequestRole(int role) {
    (void)role;
    return PSPDOCK_KSTUB_UNAVAILABLE;
}

int module_start(SceSize args, void *argp) {
    (void)args;
    (void)argp;
    started = 1;
    /* Harmless kernel timestamp; also gives the PRX one SDK import stub. */
    startup_tick = sceKernelGetSystemTimeLow();
    return 0;
}
int module_stop(SceSize args, void *argp) {
    (void)args;
    (void)argp;
    started = 0;
    return 0;
}
