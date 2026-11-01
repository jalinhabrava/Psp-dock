/* Pure software graphics for PSP Dock, deliberately independent of USB.
 * All status text is either a user action or verified by main.c. The user
 * client has NO channel for a PC sync ACK or kernel HID connection status. */
#include "ui.h"

#include <stdio.h>
#include <string.h>

typedef struct {
    uint32_t codepoint;
    uint32_t offset;
    uint8_t width, height;
    int8_t left, top;
    uint8_t advance;
} UiGlyph;

typedef struct {
    const UiGlyph *glyphs;
    const uint8_t *pixels;
    unsigned int count;
} UiFont;

#include "ui_font_data.h"

typedef struct {
    uint32_t *pixels;
    int stride;
} Canvas;

static uint32_t rgb(unsigned int r, unsigned int g, unsigned int b) {
    return 0xff000000u | (b << 16) | (g << 8) | r;
}

static void mix(Canvas *c, int x, int y, uint32_t color, unsigned int alpha) {
    uint32_t previous;
    unsigned int inverse, r, g, b;
    if (alpha == 0 || x < 0 || x >= PSP_DOCK_UI_WIDTH ||
            y < 0 || y >= PSP_DOCK_UI_HEIGHT)
        return;
    if (alpha >= 255) {
        c->pixels[y * c->stride + x] = color;
        return;
    }
    previous = c->pixels[y * c->stride + x];
    inverse = 255 - alpha;
    r = ((previous & 255u) * inverse + (color & 255u) * alpha) / 255;
    g = (((previous >> 8) & 255u) * inverse +
         ((color >> 8) & 255u) * alpha) / 255;
    b = (((previous >> 16) & 255u) * inverse +
         ((color >> 16) & 255u) * alpha) / 255;
    c->pixels[y * c->stride + x] = rgb(r, g, b);
}

static void rect(Canvas *c, int x, int y, int w, int h, uint32_t color,
                 unsigned int alpha) {
    int i, j;
    if (w <= 0 || h <= 0) return;
    for (j = y < 0 ? 0 : y; j < y + h && j < PSP_DOCK_UI_HEIGHT; ++j)
        for (i = x < 0 ? 0 : x; i < x + w && i < PSP_DOCK_UI_WIDTH; ++i)
            mix(c, i, j, color, alpha);
}

static void rounded(Canvas *c, int x, int y, int w, int h, int radius,
                    uint32_t color, unsigned int alpha) {
    int i, j, dx, dy;
    if (w <= 0 || h <= 0 || radius <= 0) return;
    for (j = y < 0 ? 0 : y; j < y + h && j < PSP_DOCK_UI_HEIGHT; ++j) {
        for (i = x < 0 ? 0 : x; i < x + w && i < PSP_DOCK_UI_WIDTH; ++i) {
            dx = i < x + radius ? x + radius - i - 1 :
                 i >= x + w - radius ? i - (x + w - radius) : 0;
            dy = j < y + radius ? y + radius - j - 1 :
                 j >= y + h - radius ? j - (y + h - radius) : 0;
            if (dx * dx + dy * dy <= radius * radius)
                mix(c, i, j, color, alpha);
        }
    }
}

static void stroked_round(Canvas *c, int x, int y, int w, int h, int radius,
                          uint32_t border, uint32_t body) {
    rounded(c, x, y, w, h, radius, border, 140);
    rounded(c, x + 1, y + 1, w - 2, h - 2, radius - 1, body, 255);
}

static void line(Canvas *c, int x0, int y0, int x1, int y1,
                 uint32_t color, unsigned int alpha) {
    int dx = x1 > x0 ? x1 - x0 : x0 - x1;
    int dy = y1 > y0 ? y1 - y0 : y0 - y1;
    int sx = x0 < x1 ? 1 : -1, sy = y0 < y1 ? 1 : -1;
    int err = dx - dy, e2;
    for (;;) {
        mix(c, x0, y0, color, alpha);
        if (x0 == x1 && y0 == y1) break;
        e2 = err * 2;
        if (e2 > -dy) { err -= dy; x0 += sx; }
        if (e2 < dx) { err += dx; y0 += sy; }
    }
}

static void ring(Canvas *c, int cx, int cy, int radius, int thickness,
                 uint32_t color, unsigned int alpha) {
    int x, y, d, r0 = (radius - thickness) * (radius - thickness);
    int r1 = radius * radius;
    for (y = cy - radius; y <= cy + radius; ++y)
        for (x = cx - radius; x <= cx + radius; ++x) {
            d = (x - cx) * (x - cx) + (y - cy) * (y - cy);
            if (d >= r0 && d <= r1) mix(c, x, y, color, alpha);
        }
}

static unsigned int next_cp(const unsigned char **s) {
    unsigned int cp;
    const unsigned char *p = *s;
    if (!*p) return 0;
    cp = *p++;
    if (cp >= 0xc2 && cp <= 0xdf && (*p & 0xc0) == 0x80) {
        cp = ((cp & 31u) << 6) | (*p++ & 63u);
    } else if (cp >= 128) cp = '?';
    *s = p;
    return cp;
}

static const UiGlyph *glyph(const UiFont *font, unsigned int cp) {
    unsigned int i;
    for (i = 0; i < font->count; ++i)
        if (font->glyphs[i].codepoint == cp) return font->glyphs + i;
    for (i = 0; i < font->count; ++i)
        if (font->glyphs[i].codepoint == '?') return font->glyphs + i;
    return NULL;
}

static void text(Canvas *canvas, int x, int baseline, const char *string,
                 const UiFont *font, uint32_t color) {
    const unsigned char *p = (const unsigned char *)string;
    unsigned int cp;
    while ((cp = next_cp(&p)) != 0) {
        int i, j;
        const UiGlyph *g = glyph(font, cp);
        if (!g) continue;
        for (j = 0; j < g->height; ++j)
            for (i = 0; i < g->width; ++i) {
                unsigned int index = (unsigned int)j * g->width + i;
                unsigned int packed = font->pixels[g->offset + (index >> 1)];
                unsigned int shade = (index & 1u) ? packed >> 4 : packed & 15u;
                if (shade) mix(canvas, x + g->left + i, baseline + g->top + j,
                               color, shade * 17u);
            }
        x += g->advance;
    }
}

static void background(Canvas *canvas, unsigned int ticks) {
    int x, y;
    const uint32_t cyan = rgb(54, 202, 223);
    for (y = 0; y < PSP_DOCK_UI_HEIGHT; ++y)
        for (x = 0; x < PSP_DOCK_UI_WIDTH; ++x) {
            unsigned int v = ((unsigned int)x * 20 / 480) +
                             ((unsigned int)y * 12 / 272);
            canvas->pixels[y * canvas->stride + x] =
                rgb(5 + v / 5, 17 + v / 2, 32 + v);
        }
    /* Calm, abstract XMB waves with a subtle moving highlight. */
    for (x = 0; x < PSP_DOCK_UI_WIDTH; ++x) {
        int first = 239 - x / 9 + ((x / 47 + (int)(ticks / 15)) % 4);
        int second = 251 - x / 4 + ((x / 29 + (int)(ticks / 20)) % 6);
        mix(canvas, x, first, cyan, 32);
        mix(canvas, x, second, rgb(93, 133, 226), 32);
        mix(canvas, x, first + 1, cyan, 13);
    }
    for (y = 65; y < 235; ++y) {
        for (x = 278; x < 477; ++x) {
            int dx = x - 376, dy = y - 154, d = dx * dx + dy * dy;
            if (d < 21000) mix(canvas, x, y, rgb(31, 143, 184),
                              (unsigned int)(21000 - d) * 19u / 21000u);
        }
    }
    rect(canvas, 26, 45, 428, 1, rgb(105, 192, 219), 55);
}

static void device_art(Canvas *c, const DockUiState *s) {
    const uint32_t cyan = rgb(105, 223, 236);
    const uint32_t muted = rgb(91, 168, 203);
    int cx = 375, cy = 137;
    int beat = (int)((s->ticks / 5u) % 5u);
    ring(c, cx, cy, 42 + beat, 1, cyan, 45 - beat * 5);
    ring(c, cx, cy, 36, 1, cyan, 110);
    rounded(c, cx - 37, cy - 16, 74, 37, 13, rgb(16, 42, 68), 255);
    rounded(c, cx - 21, cy - 13, 42, 29, 4, rgb(29, 100, 125), 255);
    rect(c, cx - 28, cy - 2, 11, 4, muted, 220);
    rect(c, cx - 25, cy - 5, 4, 11, muted, 220);
    ring(c, cx + 28, cy - 4, 3, 2, cyan, 230);
    ring(c, cx + 27, cy + 7, 3, 2, muted, 210);
    line(c, cx - 11, cy + 18, cx + 12, cy + 18, cyan, 190);
    if (s->stage >= DOCK_UI_CONNECT && s->stage <= DOCK_UI_CONTROLLER) {
        int offset = (int)((s->ticks / 3) % 24);
        line(c, cx - 25, cy + 28, cx + 26, cy + 28, muted, 95);
        rounded(c, cx - 25 + offset, cy + 26, 14, 4, 2, cyan, 180);
    }
}

static void badge(Canvas *c, const char *label, uint32_t border) {
    rounded(c, 43, 72, 120, 20, 10, border, 76);
    text(c, 53, 86, label, &ui_body_font, rgb(189, 243, 243));
}

static void timeline(Canvas *c, DockUiStage stage) {
    static const char *labels[] = {"01  PREPARAR", "02  ACTIVAR", "03  USB", "04  MANDO"};
    int active = stage == DOCK_UI_WELCOME ? 0 : stage == DOCK_UI_CHECKING ||
                 stage == DOCK_UI_ERROR ? 1 : stage == DOCK_UI_CONNECT ? 2 : 3;
    int i;
    for (i = 0; i < 4; ++i) {
        int x = 28 + i * 113;
        uint32_t color = i == active ? rgb(177, 245, 238) : rgb(102, 153, 177);
        rounded(c, x, 239, 107, 21, 8, rgb(14, 45, 65), i == active ? 255 : 155);
        if (i == active) rect(c, x + 8, 240, 26, 2, rgb(79, 232, 216), 255);
        text(c, x + 10, 254, labels[i], &ui_body_font, color);
    }
}

static void header(Canvas *c, const DockUiState *s) {
    text(c, 28, 32, "PSP DOCK", &ui_heading_font, rgb(234, 248, 253));
    rounded(c, 336, 15, 118, 23, 11, rgb(21, 65, 85), 255);
    ring(c, 351, 26, 3, 2, s->stage == DOCK_UI_ERROR ?
        rgb(244, 160, 103) : rgb(87, 233, 195), 255);
    text(c, 363, 30, "MODO PSP", &ui_body_font, rgb(183, 227, 233));
}

static void card(Canvas *c, const DockUiState *s) {
    char code[68];
    int i;
    stroked_round(c, 26, 58, 428, 169, 14, rgb(56, 129, 155), rgb(10, 31, 48));
    rect(c, 43, 173, 393, 1, rgb(91, 153, 176), 64);
    device_art(c, s);
    switch (s->stage) {
    case DOCK_UI_WELCOME:
        badge(c, "PASO 1 / 4", rgb(37, 128, 143));
        text(c, 44, 117, "PREPARA TU DOCK", &ui_heading_font, rgb(235, 250, 252));
        text(c, 44, 141, "Termina las copias en Windows.", &ui_body_font, rgb(179, 213, 225));
        text(c, 44, 158, "Desconecta el cable USB.", &ui_body_font, rgb(179, 213, 225));
        text(c, 44, 196, "X  CONTINUAR", &ui_body_font, rgb(113, 240, 215));
        text(c, 186, 196, "O  VOLVER", &ui_body_font, rgb(160, 189, 203));
        text(c, 44, 214, "Las partidas se sincronizan en Windows.", &ui_body_font, rgb(130, 176, 193));
        break;
    case DOCK_UI_CHECKING:
        badge(c, "PASO 2 / 4", rgb(41, 123, 145));
        text(c, 44, 117, "ACTIVANDO MANDO", &ui_heading_font, rgb(235, 250, 252));
        text(c, 44, 141, "Preparando el controlador HID...", &ui_body_font, rgb(179, 213, 225));
        text(c, 44, 158, "No conectes el cable todavía.", &ui_body_font, rgb(179, 213, 225));
        rounded(c, 44, 185, 257, 5, 2, rgb(37, 67, 81), 255);
        i = (int)((s->ticks % 40u) * 251u / 40u);
        rounded(c, 47, 186, i, 3, 1, rgb(99, 225, 220), 255);
        text(c, 44, 212, "Comprobación segura de USB y almacenamiento.",
             &ui_body_font, rgb(135, 189, 201));
        break;
    case DOCK_UI_CONNECT:
        badge(c, "PASO 3 / 4", rgb(35, 145, 140));
        text(c, 44, 117, "CONECTA LA PSP", &ui_heading_font, rgb(235, 250, 252));
        text(c, 44, 141, "Conecta ahora el USB al ordenador.",
             &ui_body_font, rgb(179, 213, 225));
        text(c, 44, 158, "Verifica la detección en Windows.",
             &ui_body_font, rgb(179, 213, 225));
        text(c, 44, 196, "VERIFICAR EN WINDOWS", &ui_body_font, rgb(113, 240, 215));
        text(c, 44, 214, "La vista cambiará al modo mando.",
             &ui_body_font, rgb(130, 176, 193));
        break;
    case DOCK_UI_CONTROLLER:
        badge(c, "PASO 4 / 4", rgb(24, 135, 136));
        text(c, 44, 117, "MODO MANDO", &ui_heading_font, rgb(235, 250, 252));
        text(c, 44, 141, "Usa la PSP si Windows detecta HID.",
             &ui_body_font, rgb(179, 213, 225));
        text(c, 44, 158, "Estado real del enlace: ver en PC.",
             &ui_body_font, rgb(179, 213, 225));
        text(c, 44, 196, "START + SELECT  1,5 s", &ui_body_font, rgb(113, 240, 215));
        text(c, 44, 214, "Salir al XMB / sin límite de sesión.",
             &ui_body_font, rgb(130, 176, 193));
        if (s->buttons) {
            rounded(c, 344, 199, 68, 15, 7, rgb(39, 107, 121), 255);
            text(c, 353, 210, "INPUT", &ui_body_font, rgb(202, 250, 247));
        }
        break;
    case DOCK_UI_ERROR:
        badge(c, "ATENCIÓN", rgb(151, 84, 60));
        text(c, 44, 117, "NO SE PUDO INICIAR", &ui_heading_font,
             rgb(255, 221, 203));
        text(c, 44, 141, "Apaga USB Sony y desconecta el cable.",
             &ui_body_font, rgb(214, 206, 199));
        text(c, 44, 158, "No se ha iniciado una sincronización.",
             &ui_body_font, rgb(214, 206, 199));
        snprintf(code, sizeof(code), "Módulos: %08X / %08X",
                 (unsigned int)s->first_error, (unsigned int)s->second_error);
        text(c, 44, 196, code, &ui_body_font, rgb(241, 165, 128));
        text(c, 44, 214, "O  VOLVER AL XMB", &ui_body_font, rgb(180, 195, 204));
        break;
    case DOCK_UI_EXIT:
        badge(c, "FINALIZANDO", rgb(49, 131, 142));
        text(c, 44, 117, "CERRANDO DOCK", &ui_heading_font,
             rgb(237, 248, 250));
        text(c, 44, 141, "Finalizando la sesión de mando.",
             &ui_body_font, rgb(179, 213, 225));
        text(c, 44, 158, "Puedes volver al XMB de la PSP.",
             &ui_body_font, rgb(179, 213, 225));
        text(c, 44, 203, "Gestión de partidas: revisa Windows.",
             &ui_body_font, rgb(130, 176, 193));
        break;
    }
    if (s->stage == DOCK_UI_CONTROLLER && s->exit_hold) {
        int progress = (int)(s->exit_hold > 15 ? 15 : s->exit_hold) * 56 / 15;
        rounded(c, 351, 184, 58, 4, 2, rgb(50, 77, 88), 255);
        rect(c, 352, 185, progress, 2, rgb(105, 223, 196), 255);
    }
}

void dock_ui_render(uint32_t *pixels, int stride, const DockUiState *state) {
    Canvas c;
    if (!pixels || !state || stride < PSP_DOCK_UI_WIDTH) return;
    c.pixels = pixels;
    c.stride = stride;
    background(&c, state->ticks);
    header(&c, state);
    card(&c, state);
    timeline(&c, state->stage);
}
