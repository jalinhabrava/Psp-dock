#include <assert.h>
#include <stdio.h>
#include "display_guard.h"

typedef struct {
    int last_level, calls, fail;
} MockDisplay;

static int set_level(int level, void *opaque) {
    MockDisplay *state = (MockDisplay *)opaque;
    state->last_level = level;
    state->calls++;
    return state->fail ? -1 : 0;
}

int main(void) {
    PdkDisplayGuard g = {0};
    MockDisplay mock = {0};

    /* No dim for storage still active, unknown firmware ACK or no session. */
    assert(pdk_display_arm(&g, 1, 45, 1, 0, 1) < 0);
    assert(pdk_display_arm(&g, 1, 45, 1, 1, 0) < 0);
    assert(pdk_display_arm(&g, 0, 45, 1, 1, 1) < 0);
    assert(pdk_display_arm(&g, 1, 0, 1, 1, 1) < 0);
    assert(mock.calls == 0);

    assert(pdk_display_arm(&g, 0x4321, 45, 1, 1, 1) == 0);
    assert(pdk_display_dim(&g, set_level, &mock) == 0);
    assert(mock.last_level == 0 && mock.calls == 1);
    assert(g.state == PDK_DISPLAY_DIMMED);
    assert(!pdk_display_is_safe_to_unload(&g));
    assert(pdk_display_dim(&g, set_level, &mock) < 0);
    assert(pdk_display_restore(&g, set_level, &mock) == 0);
    assert(mock.last_level == 45);
    assert(pdk_display_is_safe_to_unload(&g));
    assert(mock.calls == 2);

    /* If restore fails, never silently unload and leave a black display. */
    assert(pdk_display_arm(&g, 0x5432, 72, 1, 1, 1) == 0);
    assert(pdk_display_dim(&g, set_level, &mock) == 0);
    mock.fail = 1;
    assert(pdk_display_restore(&g, set_level, &mock) < 0);
    assert(g.state == PDK_DISPLAY_FAULT);
    assert(!pdk_display_is_safe_to_unload(&g));
    mock.fail = 0;
    assert(pdk_display_restore(&g, set_level, &mock) == 0);
    assert(mock.last_level == 72);
    assert(pdk_display_is_safe_to_unload(&g));
    puts("PSP Dock display-guard pure-C safety tests: OK");
    return 0;
}
