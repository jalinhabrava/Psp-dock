/* Native, asset-free PSP LCD renderer checks, with optional PPM snapshots.
 * cc -std=c99 -Wall -Wextra -Werror -fsanitize=address,undefined \
 *    -I.. ../ui.c test_ui_renderer.c -o test-ui && ./test-ui /tmp/psp-ui
 */
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "ui.h"

#define STRIDE 512
#define W 480
#define H 272

static uint32_t screen[STRIDE * H + 32];
static uint64_t hash_screen(void) {
    uint64_t hash = 1469598103934665603ull;
    int x, y;
    for (y = 0; y < H; ++y)
        for (x = 0; x < W; ++x) {
            hash ^= screen[y * STRIDE + x];
            hash *= 1099511628211ull;
        }
    return hash;
}

static void snapshot(const char *dir, int stage) {
    FILE *f;
    char filename[500];
    int x, y;
    if (!dir) return;
    snprintf(filename, sizeof(filename), "%s/state-%d.ppm", dir, stage);
    f = fopen(filename, "wb");
    assert(f);
    fprintf(f, "P6\n%d %d\n255\n", W, H);
    for (y = 0; y < H; ++y)
        for (x = 0; x < W; ++x) {
            uint32_t color = screen[y * STRIDE + x];
            unsigned char rgb[] = {(unsigned char)(color & 255u),
                                   (unsigned char)((color >> 8) & 255u),
                                   (unsigned char)((color >> 16) & 255u)};
            assert(fwrite(rgb, 1, 3, f) == 3);
        }
    assert(fclose(f) == 0);
}

int main(int argc, char **argv) {
    DockUiState state;
    uint64_t hashes[6];
    int i, x, y;
    memset(&state, 0, sizeof(state));
    dock_ui_render(NULL, STRIDE, &state);
    dock_ui_render(screen, 100, &state); /* Invalid pitch is a no-op. */
    for (i = DOCK_UI_WELCOME; i <= DOCK_UI_EXIT; ++i) {
        state.stage = (DockUiStage)i;
        state.ticks = 18;
        state.buttons = 0;
        state.first_error = -1;
        state.second_error = -5;
        for (y = 0; y < H; ++y)
            for (x = 0; x < STRIDE; ++x)
                screen[y * STRIDE + x] = 0x12345678u;
        for (x = 0; x < 32; ++x) screen[STRIDE * H + x] = 0xabcdefffu;
        dock_ui_render(screen, STRIDE, &state);
        for (y = 0; y < H; ++y)
            for (x = W; x < STRIDE; ++x)
                assert(screen[y * STRIDE + x] == 0x12345678u);
        for (x = 0; x < 32; ++x)
            assert(screen[STRIDE * H + x] == 0xabcdefffu);
        assert(screen[0] != 0x12345678u);
        assert(screen[100 * STRIDE + 100] != 0x12345678u);
        hashes[i] = hash_screen();
        snapshot(argc > 1 ? argv[1] : NULL, i);
    }
    for (i = 0; i < 6; ++i)
        for (x = i + 1; x < 6; ++x)
            assert(hashes[i] != hashes[x]);
    state.stage = DOCK_UI_CONTROLLER;
    state.buttons = 0;
    dock_ui_render(screen, STRIDE, &state);
    hashes[0] = hash_screen();
    state.buttons = 1;
    dock_ui_render(screen, STRIDE, &state);
    assert(hashes[0] != hash_screen());
    state.stage = DOCK_UI_ERROR;
    state.first_error = 0x80000001u;
    dock_ui_render(screen, STRIDE, &state);
    hashes[0] = hash_screen();
    state.first_error = 0x80000002u;
    dock_ui_render(screen, STRIDE, &state);
    assert(hashes[0] != hash_screen());
    puts("PSP Dock UI native: 6 states + stride + input + error rendering OK");
    return 0;
}
