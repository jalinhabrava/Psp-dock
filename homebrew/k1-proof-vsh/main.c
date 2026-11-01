/*
 * PSP Dock K1 export probe. Kernel-mode, VSH-scoped, opt-in one-shot.
 *
 * Does not start/stop/activate USB, alter FAT ownership, switch to HID,
 * touch flash, or access SAVEDATA. This module imports ONLY the exported
 * read function of the already installed K1 kernel observer.
 *
 * With no ARM.TXT it creates a short-lived thread and exits without I/O.
 * When armed, it reads its marker / writes one small proof file ONLY if
 * USB is inactive and disconnected, and no previous proof file exists.
 * No proof is an authorization for host eject or USB role changes.
 */
#include <pspkernel.h>
#include <pspthreadman.h>
#include <pspiofilemgr.h>
#include <pspusb.h>
#include <pspusbstor.h>
#include <string.h>
#include "../kernel-usb-observer-k1/protocol.h"

PSP_MODULE_INFO("PSPDockK1ProofVSH", PSP_MODULE_KERNEL, 1, 0);

#define PROOF_DIR "ms0:/SEPLUGINS/PSPDockK1Proof/"
#define ARM_FILE PROOF_DIR "ARM.TXT"
#define RESULT_FILE PROOF_DIR "K1PROOF.TXT"
#define TOKEN "PSPDOCK-K1-PROOF-ARM-01\n"
#define IDLE_MASK (0x200u | 0x020u | 0x002u)

extern int pspDockK1Read(PspDockK1Snapshot *out);

static volatile int stopping = 0;
static SceUID thread_uid = -1;

static int idle_and_not_mounted(void) {
    int usb = sceUsbGetState();
    int stor = sceUsbGetDrvState(PSP_USBSTOR_DRIVERNAME);
    if (usb < 0 || (usb & IDLE_MASK) != 0)
        return 0;
    /* 1 = started: cannot touch the filesystem even without a USB cable.
     * Reject any ambiguous successful storage state except known stopped 2.
     * Negative states (including SCE_USB_ERROR_NOENT) are permitted only
     * when the cable/link flags above are definitively inactive. */
    return stor < 0 || stor == 2;
}

static int armed(void) {
    static const char expected[] = TOKEN;
    char buffer[sizeof(expected)];
    int n, excess;
    SceUID fd = sceIoOpen(ARM_FILE, PSP_O_RDONLY, 0);
    if (fd < 0) return 0;
    memset(buffer, 0, sizeof(buffer));
    n = sceIoRead(fd, buffer, sizeof(expected) - 1);
    excess = sceIoRead(fd, buffer + sizeof(expected) - 1, 1);
    sceIoClose(fd);
    return n == (int)sizeof(expected) - 1 &&
        excess == 0 && memcmp(buffer, expected, sizeof(expected) - 1) == 0;
}

static int push_literal(char *out, int at, int cap, const char *source) {
    if (at < 0) return -1;
    while (*source) {
        if (at >= cap - 1) return -1;
        out[at++] = *source++;
    }
    return at;
}

static int push_hex(char *out, int at, int cap, u32 value) {
    static const char alphabet[] = "0123456789ABCDEF";
    int i;
    if (at < 0 || cap - at <= 8) return -1;
    for (i = 7; i >= 0; --i) {
        out[at + i] = alphabet[value & 0x0fu];
        value >>= 4;
    }
    return at + 8;
}

static int write_proof(const PspDockK1Snapshot *sample) {
    char message[220];
    SceIoStat st;
    int length = 0;
    int written;
    SceUID file;

    if (!sample || stopping || !idle_and_not_mounted() || !armed())
        return -1;
    /* Never truncate a previous result from a different boot. */
    if (sceIoGetstat(RESULT_FILE, &st) >= 0)
        return -2;
    length = push_literal(message, length, sizeof(message), "PSPDOCK_K1_PROOF=01\nMAGIC=");
    length = push_hex(message, length, sizeof(message), sample->magic);
    length = push_literal(message, length, sizeof(message), "\nVERSION=");
    length = push_hex(message, length, sizeof(message), sample->version);
    length = push_literal(message, length, sizeof(message), "\nCAPABILITIES=");
    length = push_hex(message, length, sizeof(message), sample->capabilities);
    length = push_literal(message, length, sizeof(message), "\nSTATE=");
    length = push_hex(message, length, sizeof(message), sample->state);
    length = push_literal(message, length, sizeof(message), "\nUSB=");
    length = push_hex(message, length, sizeof(message), (u32)sample->usb_state);
    length = push_literal(message, length, sizeof(message), "\nBUS=");
    length = push_hex(message, length, sizeof(message), (u32)sample->bus_driver_state);
    length = push_literal(message, length, sizeof(message), "\nSTORAGE=");
    length = push_hex(message, length, sizeof(message), (u32)sample->storage_driver_state);
    length = push_literal(message, length, sizeof(message), "\nROLE_SWITCHED=0\n");
    if (length <= 0 || length >= (int)sizeof(message) || stopping || !idle_and_not_mounted())
        return -3;
    file = sceIoOpen(RESULT_FILE, PSP_O_WRONLY | PSP_O_CREAT | PSP_O_EXCL, 0666);
    if (file < 0) return -4;
    written = sceIoWrite(file, message, length);
    sceIoClose(file);
    return written == length ? 0 : -5;
}

static int proof_main(SceSize args, void *argp) {
    PspDockK1Snapshot sample;
    int result;
    (void)args;
    (void)argp;
    sceKernelDelayThread(6000000);
    if (stopping || !idle_and_not_mounted() || !armed())
        return 0;
    memset(&sample, 0, sizeof(sample));
    result = pspDockK1Read(&sample);
    if (result != 0 || sample.magic != PSPDOCK_K1_MAGIC ||
        sample.version != PSPDOCK_K1_VERSION ||
        sample.capabilities != 0 ||
        sample.state != PSPDOCK_K1_READY)
        return 0;
    write_proof(&sample);
    return 0;
}

int module_start(SceSize args, void *argp) {
    (void)args;
    (void)argp;
    stopping = 0;
    thread_uid = sceKernelCreateThread(
        "PSPDockK1ProofOnce", proof_main, 0x20, 0x3000, 0, NULL);
    if (thread_uid < 0) return 1;
    if (sceKernelStartThread(thread_uid, 0, NULL) < 0) {
        sceKernelDeleteThread(thread_uid);
        thread_uid = -1;
        return 1;
    }
    return 0;
}

int module_stop(SceSize args, void *argp) {
    (void)args;
    (void)argp;
    stopping = 1;
    if (thread_uid >= 0) {
        sceKernelTerminateDeleteThread(thread_uid);
        thread_uid = -1;
    }
    return 0;
}
