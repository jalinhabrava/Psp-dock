/* PSP Dock screen blackout design guard (pure C, NO PSP imports).
 *
 * This is a UNIT-TESTED policy, not an installed PRX or USB driver.
 * A future audited kernel adapter must supply read/set brightness
 * callbacks and prove a LIVE, unique HID session and no FAT overlap.
 *
 * Never suspend the PSP: suspend would also stop gamepad input.
 * Never dim before the PC launcher has acquired actual HID input.
 * Failure to restore original brightness is a HARDWARE STOP condition.
 */
#ifndef PSPDOCK_DISPLAY_GUARD_H
#define PSPDOCK_DISPLAY_GUARD_H
#include <stdint.h>

typedef enum {
    PDK_DISPLAY_IDLE = 0,
    PDK_DISPLAY_ARMED = 1,
    PDK_DISPLAY_DIMMED = 2,
    PDK_DISPLAY_RESTORING = 3,
    PDK_DISPLAY_FAULT = 4
} PdkDisplayState;

typedef struct {
    PdkDisplayState state;
    int previous_brightness;
    unsigned int session_id;
    int restore_attempts;
} PdkDisplayGuard;

/* Caller evidence must derive from REAL PSP kernel + PC host ACK, not from a
   BOOT.TXT marker, absent drive letter or a self-provided software flag. */
int pdk_display_arm(PdkDisplayGuard *guard, unsigned int session_id,
                    int saved_brightness, int hid_ready,
                    int storage_stopped, int host_live_ack);
int pdk_display_dim(PdkDisplayGuard *guard,
                    int (*set_brightness)(int, void *), void *opaque);
int pdk_display_restore(PdkDisplayGuard *guard,
                        int (*set_brightness)(int, void *), void *opaque);
int pdk_display_is_safe_to_unload(const PdkDisplayGuard *guard);

#endif
