#include "display_guard.h"

int pdk_display_arm(PdkDisplayGuard *guard, unsigned int session_id,
                    int saved_brightness, int hid_ready,
                    int storage_stopped, int host_live_ack) {
    if (!guard || guard->state != PDK_DISPLAY_IDLE || session_id == 0 ||
        saved_brightness < 1 || saved_brightness > 100 ||
        hid_ready != 1 || storage_stopped != 1 || host_live_ack != 1)
        return -1;
    guard->session_id = session_id;
    guard->previous_brightness = saved_brightness;
    guard->restore_attempts = 0;
    guard->state = PDK_DISPLAY_ARMED;
    return 0;
}

int pdk_display_dim(PdkDisplayGuard *guard,
                    int (*set_brightness)(int, void *), void *opaque) {
    if (!guard || guard->state != PDK_DISPLAY_ARMED || !set_brightness)
        return -1;
    if (set_brightness(0, opaque) != 0) {
        guard->state = PDK_DISPLAY_FAULT;
        return -1;
    }
    guard->state = PDK_DISPLAY_DIMMED;
    return 0;
}

int pdk_display_restore(PdkDisplayGuard *guard,
                        int (*set_brightness)(int, void *), void *opaque) {
    int result;
    if (!guard || !set_brightness)
        return -1;
    if (guard->state == PDK_DISPLAY_IDLE) return 0;
    if (guard->state != PDK_DISPLAY_DIMMED &&
        guard->state != PDK_DISPLAY_FAULT &&
        guard->state != PDK_DISPLAY_RESTORING &&
        guard->state != PDK_DISPLAY_ARMED)
        return -1;
    if (guard->previous_brightness < 1 ||
        guard->previous_brightness > 100)
        return -1;
    guard->state = PDK_DISPLAY_RESTORING;
    guard->restore_attempts += 1;
    result = set_brightness(guard->previous_brightness, opaque);
    if (result != 0) {
        guard->state = PDK_DISPLAY_FAULT;
        return -1;
    }
    guard->state = PDK_DISPLAY_IDLE;
    guard->session_id = 0;
    guard->previous_brightness = 0;
    return 0;
}

int pdk_display_is_safe_to_unload(const PdkDisplayGuard *guard) {
    return guard && guard->state == PDK_DISPLAY_IDLE && guard->session_id == 0;
}
