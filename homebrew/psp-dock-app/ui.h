#ifndef PSP_DOCK_PREMIUM_UI_H
#define PSP_DOCK_PREMIUM_UI_H

#include <stdint.h>

/* PSP LCD: 480x272, ABGR8888; 512-pixel VRAM pitch. No PSP SDK dependencies
 * in the renderer: the exact same pixels can be checked by native tests. */
#define PSP_DOCK_UI_WIDTH 480
#define PSP_DOCK_UI_HEIGHT 272

typedef enum {
    DOCK_UI_WELCOME = 0,
    DOCK_UI_CHECKING,
    DOCK_UI_CONNECT,
    DOCK_UI_CONTROLLER,
    DOCK_UI_ERROR,
    DOCK_UI_EXIT
} DockUiStage;

typedef struct {
    DockUiStage stage;
    unsigned int ticks; /* Monotonic 100ms UI ticks; not USB/sync progress. */
    unsigned int buttons; /* Gamepad buttons for local visual feedback only. */
    unsigned int exit_hold; /* 100ms ticks holding START+SELECT. */
    int first_error;
    int second_error;
} DockUiState;

void dock_ui_render(uint32_t *pixels, int stride, const DockUiState *state);

#endif
