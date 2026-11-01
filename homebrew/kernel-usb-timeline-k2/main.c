/* PSP Dock K2, compile-only. Passive kernel USB state log in RAM.
 *
 * There is no USB control, module loading, flash access, FAT access,
 * SAVEDATA access, or writes to the PSP Memory Stick. DO NOT install on
 * the real console before the separate K1 kernel boot is proven stable.
 */
#include <pspkernel.h>
#include <pspthreadman.h>
#include <pspintrman.h>
#include <pspusb.h>
#include <pspusbstor.h>
#include "timeline.h"

PSP_MODULE_INFO("PSPDockK2Timeline", PSP_MODULE_KERNEL, 1, 0);

static PspDockK2Timeline timeline;
static volatile int stopping;
static SceUID worker_thread = -1;

/* KERNEL-ONLY contract; the caller must supply a kernel buffer.
 * No user-memory pointer may be passed to this export. No USB changes.
 */
int pspDockK2GetSnapshot(PspDockK2Timeline *destination) {
    int irq;
    if (!destination) return -1;
    irq = sceKernelCpuSuspendIntr();
    *destination = timeline;
    sceKernelCpuResumeIntr(irq);
    return 0;
}

static int observe_usb(SceSize args, void *argp) {
    int irq;
    int32_t usb, bus, storage;
    uint32_t tick_us;
    (void)args;
    (void)argp;
    sceKernelDelayThread(3000000);
    while (!stopping) {
        usb = sceUsbGetState();
        bus = sceUsbGetDrvState(PSP_USBBUS_DRIVERNAME);
        storage = sceUsbGetDrvState(PSP_USBSTOR_DRIVERNAME);
        if (stopping) break;
        tick_us = sceKernelGetSystemTimeLow();
        irq = sceKernelCpuSuspendIntr();
        pspDockK2Capture(&timeline, tick_us, usb, bus, storage);
        sceKernelCpuResumeIntr(irq);
        sceKernelDelayThread(250000);
    }
    return 0;
}

int module_start(SceSize args, void *argp) {
    (void)args;
    (void)argp;
    stopping = 0;
    pspDockK2Init(&timeline);
    worker_thread = sceKernelCreateThread(
        "PSPDockK2ReadUSB", observe_usb, 0x20, 0x2000, 0, NULL);
    if (worker_thread < 0) return 1;
    if (sceKernelStartThread(worker_thread, 0, NULL) < 0) {
        sceKernelDeleteThread(worker_thread);
        worker_thread = -1;
        return 1;
    }
    return 0;
}

int module_stop(SceSize args, void *argp) {
    (void)args;
    (void)argp;
    stopping = 1;
    if (worker_thread >= 0) {
        sceKernelTerminateDeleteThread(worker_thread);
        worker_thread = -1;
    }
    return 0;
}
