/* PSP Dock / manually launched USER client during USB bridge development.
 *
 * A native PSP LCD UI replaces the old pspDebugScreen console. It does not
 * invent a Windows connection or sync state: there is no PC->PSP ACK channel
 * yet. Kernel HID and storage ownership logic is deliberately unchanged.
 *
 * NO direct USER USB/USBSTOR imports, no storage activation or FAT writes.
 * The operator disconnects USB before starting, waits for the kernel guard,
 * then connects it. START+SELECT held for >=1.5s exits. The kernel
 * independently stops HID after cable loss or holding START+SELECT.
 */
#include <pspkernel.h>
#include <pspmodulemgr.h>
#include <pspctrl.h>
#include <pspthreadman.h>
#include <pspdisplay.h>
#include <pspge.h>
#include <stdint.h>
#include "ui.h"

PSP_MODULE_INFO("PSPDockApp", PSP_MODULE_USER, 1, 0);
PSP_MAIN_THREAD_ATTR(PSP_THREAD_ATTR_USER);
PSP_MAIN_THREAD_STACK_SIZE_KB(64);

#define HID_MODULE "ms0:/PSP/GAME/PSPDock/bridgehid.prx"
#define LAB_MODULE "ms0:/PSP/GAME/PSPDock/dockcore.prx"
#define PSP_LCD_PITCH 512
#define PSP_FRAME_BYTES (PSP_LCD_PITCH * PSP_DOCK_UI_HEIGHT * 4)
#define EXIT_HOLD_TICKS 15 /* 1.5s at 100ms polling. */
#define USB_GUARD_TICKS 40 /* Kernel preflight begins 1.5s after module start. */
#define CONNECT_GUIDE_TICKS 90 /* Show guidance, then enter ambient display. */

static DockUiState ui;
static unsigned int frame_index;

static int load_and_start(const char *path) {
    int status = -1;
    SceUID uid = sceKernelLoadModule(path, 0, NULL);
    int started;
    if (uid < 0) return uid;
    started = sceKernelStartModule(uid, 0, NULL, &status, NULL);
    if (started < 0 || status < 0)
        return started < 0 ? started : status;
    return 0;
}

static void show_screen(void) {
    /* The 2 MiB of PSP eDRAM fits two 512x272x32-bit framebuffers. Render
     * through the uncached alias, and display only on VBlank to avoid tearing.
     * Never paint directly into storage or rely on PSPSDK's debug font. */
    uintptr_t base = (uintptr_t)sceGeEdramGetAddr();
    uintptr_t offset = frame_index ? PSP_FRAME_BYTES : 0u;
    uint32_t *draw = (uint32_t *)((base + offset) | 0x40000000u);
    dock_ui_render(draw, PSP_LCD_PITCH, &ui);
    sceDisplayWaitVblankStart();
    sceDisplaySetFrameBuf((void *)(base + offset), PSP_LCD_PITCH,
                           PSP_DISPLAY_PIXEL_FORMAT_8888,
                           PSP_DISPLAY_SETBUF_IMMEDIATE);
    frame_index ^= 1u;
}

static void transition(DockUiStage stage) {
    ui.stage = stage;
    ui.ticks = 0;
    ui.exit_hold = 0;
    show_screen();
}

int main(void) {
    SceCtrlData pad;
    int first, second;
    int released = 0;
    int previous_cross = 0;
    int previous_circle = 0;
    int requested_exit = 0;

    sceCtrlSetSamplingCycle(0);
    sceCtrlSetSamplingMode(PSP_CTRL_MODE_DIGITAL);
    sceDisplaySetMode(0, PSP_DOCK_UI_WIDTH,
                      PSP_DOCK_UI_HEIGHT);
    ui.stage = DOCK_UI_WELCOME;
    ui.ticks = 0;
    ui.first_error = 0;
    ui.second_error = 0;
    ui.buttons = 0;
    ui.exit_hold = 0;
    frame_index = 0;
    show_screen();

    for (;;) {
        int cross, circle, both;
        sceCtrlPeekBufferPositive(&pad, 1);
        cross = !!(pad.Buttons & PSP_CTRL_CROSS);
        circle = !!(pad.Buttons & PSP_CTRL_CIRCLE);
        both = (pad.Buttons & (PSP_CTRL_START | PSP_CTRL_SELECT)) ==
               (PSP_CTRL_START | PSP_CTRL_SELECT);
        if (!cross) released = 1;
        ui.buttons = pad.Buttons;
        if (both) ++ui.exit_hold;
        else ui.exit_hold = 0;

        if (ui.stage == DOCK_UI_WELCOME) {
            /* Do not immediately activate if X is still held from the XMB. */
            if (released && cross && !previous_cross) {
                transition(DOCK_UI_CHECKING);
                first = load_and_start(HID_MODULE);
                second = first < 0 ? first : load_and_start(LAB_MODULE);
                if (first < 0 || second < 0) {
                    ui.first_error = first;
                    ui.second_error = second;
                    transition(DOCK_UI_ERROR);
                }
            } else if ((circle && !previous_circle) || both)
                requested_exit = 1;
        } else if (ui.stage == DOCK_UI_CHECKING) {
            /* Waiting is conservative, NOT a kernel ACK. The kernel's 1.5s
             * guard must run before the UI invites USB insertion. */
            if (ui.ticks >= USB_GUARD_TICKS)
                transition(DOCK_UI_CONNECT);
            if (ui.exit_hold >= EXIT_HOLD_TICKS)
                requested_exit = 1;
        } else if (ui.stage == DOCK_UI_CONNECT) {
            /* Do not consume an extra X after HID starts: X would also reach
             * the PC and could accidentally select or launch a game there.
             * Automatic *visual* transition never means HID was detected. */
            if (ui.ticks >= CONNECT_GUIDE_TICKS)
                transition(DOCK_UI_CONTROLLER);
            if (ui.exit_hold >= EXIT_HOLD_TICKS)
                requested_exit = 1;
        } else if (ui.stage == DOCK_UI_CONTROLLER) {
            if (ui.exit_hold >= EXIT_HOLD_TICKS)
                requested_exit = 1;
        } else if (ui.stage == DOCK_UI_ERROR) {
            if ((circle && !previous_circle) ||
                    ui.exit_hold >= EXIT_HOLD_TICKS)
                requested_exit = 1;
        }

        if (requested_exit) {
            transition(DOCK_UI_EXIT);
            /* Kernel separately sees START+SELECT for 1s; retain the original
             * three seconds for endpoint shutdown before leaving USER mode. */
            if (both && ui.stage == DOCK_UI_EXIT)
                sceKernelDelayThread(3000000);
            break;
        }
        previous_cross = cross;
        previous_circle = circle;
        ++ui.ticks;
        if (!(ui.ticks % 3u)) show_screen(); /* ~3fps, battery-friendly. */
        sceKernelDelayThread(100000);
    }
    sceKernelExitGame();
    return 0;
}
