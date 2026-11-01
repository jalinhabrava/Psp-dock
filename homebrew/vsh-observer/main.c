/*
 * PSP Dock VSH observer 0.1.
 *
 * Passive user-mode XMB plugin, NOT the final kernel USB role bridge.
 * The PRX itself has zero direct USB imports: it sleeps briefly, then uses
 * the separately verified PSP Dock USB Status v0.7 USER helper to sample USB.
 * It never starts/stops drivers, never activates storage or HID, never
 * writes firmware, and never accesses SAVEDATA.
 *
 * Optional evidence: one small marker file in its own SEPLUGINS folder,
 * ONLY when an exact ARM.TXT exists AND USB, cable, link are all inactive.
 * The default installer does not create ARM.TXT or enable the plugin.
 *
 * Module is scoped to VSH via ARK's PLUGINS.TXT line. It does not hook VSH
 * functions, controller input, memory, or firmware syscalls.
 */
#include <pspkernel.h>
#include <pspmodulemgr.h>
#include <pspthreadman.h>
#include <pspiofilemgr.h>
#include <string.h>
#include "vsh_probe_protocol.h"

PSP_MODULE_INFO("PSPDockVSHProbe", PSP_MODULE_USER, 1, 0);
PSP_MAIN_THREAD_ATTR(PSP_THREAD_ATTR_USER);

#define PROBE_DIRECTORY "ms0:/SEPLUGINS/PSPDockProbe/"
#define ARM_PATH PROBE_DIRECTORY "ARM.TXT"
#define LOG_PATH PROBE_DIRECTORY "BOOT.TXT"
#define USB_HELPER_PATH PROBE_DIRECTORY "usbstate.prx"
#define PROBE_DELAY_US 3000000

static volatile int stopping;
static SceUID observer_thread = -1;
static GateResult measured = {
    GATE_V07_RESULT_MAGIC,
    (s32)GATE_V07_SENTINEL,
    (s32)GATE_V07_SENTINEL,
    (s32)GATE_V07_SENTINEL
};

/* Exact token prevents accidental writes on plugin enablement. This is an
 * opt-in marker, not an authentication mechanism or secret. */
static int read_arm_token(void) {
    static const char token[] = "PSPDOCK-OBSERVER-ARM-01\n";
    char buffer[sizeof(token)];
    SceUID fd = sceIoOpen(ARM_PATH, PSP_O_RDONLY, 0);
    int count, extra;
    if (fd < 0)
        return 0;
    memset(buffer, 0, sizeof(buffer));
    count = sceIoRead(fd, buffer, sizeof(token) - 1);
    extra = sceIoRead(fd, buffer + sizeof(token) - 1, 1);
    sceIoClose(fd);
    return count == (int)(sizeof(token) - 1) &&
           extra == 0 &&
           memcmp(buffer, token, sizeof(token) - 1) == 0;
}

static int query_with_verified_helper(void) {
    SceUID mod;
    int started, start_status = -1, stop_status = -1, stopped, unloaded;
    GateArguments params;
    params.magic = GATE_V07_MAGIC;
    params.result_pointer = (u32)&measured;
    mod = sceKernelLoadModule(USB_HELPER_PATH, 0, NULL);
    if (mod < 0)
        return -1;
    started = sceKernelStartModule(mod, sizeof(params), &params, &start_status, NULL);
    /* Do not attempt to stop/unload a module with uncertain residency. */
    if (started != mod || start_status != 0)
        return -2;
    stopped = sceKernelStopModule(mod, 0, NULL, &stop_status, NULL);
    if (stopped != 0 || stop_status != 0)
        return -3;
    unloaded = sceKernelUnloadModule(mod);
    if (unloaded != mod)
        return -4;
    if (measured.magic != GATE_V07_RESULT_MAGIC ||
        (u32)measured.usb_state == GATE_V07_SENTINEL ||
        (u32)measured.bus_driver_state == GATE_V07_SENTINEL ||
        (u32)measured.storage_driver_state == GATE_V07_SENTINEL)
        return -5;
    return 0;
}

static int put_literal(char *dest, int offset, int cap, const char *source) {
    while (*source) {
        if (offset >= cap - 1)
            return -1;
        dest[offset++] = *source++;
    }
    return offset;
}

static int put_hex32(char *dest, int offset, int cap, unsigned int value) {
    static const char alphabet[] = "0123456789ABCDEF";
    int n;
    if (offset < 0 || cap - offset <= 8)
        return -1;
    for (n = 7; n >= 0; --n) {
        dest[offset + n] = alphabet[value & 15u];
        value >>= 4;
    }
    return offset + 8;
}

static void write_optional_marker(void) {
    char content[160];
    int length = 0;
    SceUID file;
    const s32 usb = measured.usb_state;

    if (!read_arm_token() || usb < 0 ||
        ((u32)usb & (0x200u | 0x020u | 0x002u)))
        return;

    length = put_literal(content, length, sizeof(content),
                         "PSPDOCK_VSH_OBSERVER=01\nUSB=");
    length = put_hex32(content, length, sizeof(content), (unsigned int)usb);
    length = put_literal(content, length, sizeof(content), "\nBUS=");
    length = put_hex32(content, length, sizeof(content),
                       (unsigned int)measured.bus_driver_state);
    length = put_literal(content, length, sizeof(content), "\nSTORAGE=");
    length = put_hex32(content, length, sizeof(content),
                       (unsigned int)measured.storage_driver_state);
    length = put_literal(content, length, sizeof(content),
                         "\nFIRMWARE_UNCHANGED=1\n");
    if (length <= 0 || length >= (int)sizeof(content))
        return;
    if (stopping)
        return;
    /* The only write is a single diagnostic in our own dedicated directory.
     * Never write if USB is active, the cable is detected, or state is unknown. */
    file = sceIoOpen(LOG_PATH, PSP_O_WRONLY | PSP_O_CREAT | PSP_O_TRUNC, 0666);
    if (file >= 0) {
        sceIoWrite(file, content, length);
        sceIoClose(file);
    }
}

static int observer_main(SceSize args, void *argp) {
    (void)args;
    (void)argp;
    sceKernelDelayThread(PROBE_DELAY_US);
    if (stopping || !read_arm_token())
        return 0;
    if (query_with_verified_helper() == 0 && !stopping)
        write_optional_marker();
    return 0;
}

int module_start(SceSize args, void *argp) {
    (void)args;
    (void)argp;
    stopping = 0;
    observer_thread = sceKernelCreateThread(
        "PSPDockVSHObserver", observer_main, 0x20, 0x3000,
        PSP_THREAD_ATTR_USER, NULL);
    if (observer_thread < 0)
        return 1;
    if (sceKernelStartThread(observer_thread, 0, NULL) < 0) {
        sceKernelDeleteThread(observer_thread);
        observer_thread = -1;
        return 1;
    }
    return 0;
}

int module_stop(SceSize args, void *argp) {
    (void)args;
    (void)argp;
    stopping = 1;
    if (observer_thread >= 0) {
        /* The thread is short-lived; a missing/terminated thread is harmless.
         * Do not force filesystem operations during unloading. */
        sceKernelTerminateDeleteThread(observer_thread);
        observer_thread = -1;
    }
    return 0;
}
