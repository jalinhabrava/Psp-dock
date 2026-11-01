# Arte XMB — PSP Dock 0206

Los PNG finales están integrados en `Makefile.app` y se incrustan en
`EBOOT.PBP`. El título de PARAM.SFO es **PSP Dock - USB Storage + HID 0206**.

| Recurso | Tamaño final | Función |
| --- | --- | --- |
| ICON0.PNG | 144 × 80, RGB | PSP DOCK grande, blanco sobre grafito |
| PIC0.PNG | 310 × 180, RGBA | Placa con PSP DOCK y USB STORAGE + HID; margen exterior transparente |
| PIC1.PNG | 480 × 272, RGB | Fondo grafito sobrio sin textos |

XMB superpone PIC0 al fondo PIC1. Por eso la versión final del fondo no
lleva un segundo rótulo: la identificación queda en PIC0 e ICON0. PIC0
conserva un interior grafito opaco y sólo su margen exterior tiene alpha.
Los intentos iniciales de letras blancas sueltas con alpha produjeron
residuos de recorte; fueron descartados y no forman parte del paquete.

## Fuentes y reproducción

Modo utilizado: **herramienta integrada image_gen**, no CLI/API.
Los tres originales generados seleccionados están en `sources/`.
`PIC1-initial.png` conserva además la referencia inicial usada para
retirar el texto del fondo.

```sh
python homebrew/composite-mscdemo/assets/generate_artwork.py
```

Este script únicamente convierte el modo PNG y reduce con Lanczos a las
dimensiones exigidas por PSP. No dibuja letras, fondos, diagramas ni
elementos nuevos. Los originales generados mantienen su resolución y
alpha. No se incrustan archivos de tipografías. Los prompts siguientes
son los utilizados, íntegros; la edición de PIC1 parte de la primera
generación descrita al final.

## Prompt final: ICON0

Use case: logo-brand. Asset type: final PSP XMB menu application icon, downscaled to 144 by 80 pixels, landscape aspect ratio 9:5. Create NEW clean premium minimalist typographic icon: near-black graphite flat background, very large bold condensed sans-serif white uppercase wordmark centered on two tight lines: 'PSP' on top and 'DOCK' below. The lettering should occupy most of the icon width and height with a small safe margin and be exceptionally legible at the tiny final size. White type, restrained tiny teal accent only, such as a short teal rule above the wordmark if room remains. Only the text PSP DOCK, nothing else. No subtitle, no version, no diagram, no device illustration, no logo symbols, no border, no tiny labels, no gradients inside text, no shadows, no glows, no mockup or multiple variants. Smooth crisp edges, final production image only.

## Prompt final: PIC0

Use case: logo-brand. Create one final polished PSP XMB title graphic as a cutout with transparent outer background. Landscape 31:18 aspect ratio, downscaled to 310x180 pixels. A simple premium near-black graphite rectangular title plate, with subtly rounded corners and a small transparent margin around the OUTSIDE of the plate. The INTERIOR of this plate is completely opaque uniform graphite black; keep this dark plate behind all the lettering and inside all letter counters and gaps. On this dark plate, very large bright white bold condensed sans-serif type on two lines reads 'PSP' and 'DOCK'. Beneath it a smaller but clearly readable white subtitle exactly 'USB STORAGE + HID'. One small teal horizontal accent above. This is clean professional graphic typography. Very crisp smooth solid letter shapes, completely flat colors, no distressed texture, no scratches, no noise, no diagrams, no device illustrations, no extra words, no mockup. The plate is the subject to preserve entirely with its opaque black interior and white type; remove ONLY the area outside the rounded rectangular plate, making just that exterior genuinely transparent. Do NOT remove any part of the dark plate or knock out spaces between letters. The final image has true alpha only outside the dark plaque, smooth edges and exceptionally legible solid white wording.

## Prompt final: PIC1

Referencia: `sources/PIC1-initial.png`.

Use case: precise-object-edit. This image is the final 480x272 PSP XMB application background. The actual XMB will overlay a separate large PSP DOCK title plate, so remove ALL typography and the teal line from this background. Change ONLY that: cleanly remove the entire PSP DOCK wordmark, USB STORAGE + HID subtitle, and teal horizontal accent on the right. Seamlessly fill that area with the existing smooth dark graphite background gradient. Preserve the understated near-black/graphite palette and subtle gentle right-side illumination, with very quiet black left two-thirds. Final result is an elegant empty dark graphite background, no text at all, no symbols, no lines, no diagram, no illustration, no extra objects, no grain added. Landscape aspect ratio 30:17. This is a final production background, not a mockup or a collage.

## Generación inicial de PIC1

Esta primera versión incluía el rótulo en el tercio derecho. Se conserva
como fuente de la edición; no se incrusta en el EBOOT.

Use case: logo-brand. Asset type: a final background image for a Sony PSP homebrew application's XMB menu, downscaled to exactly 480 by 272 pixels, landscape aspect ratio 30:17. Create NEW polished minimalist graphic art, not a device mockup or a collage. Palette: near-black graphite background, warm white typography, a single very restrained teal accent. The background has a subtle beautifully smooth graphite gradient, no illustration, no grid, no circuitry, no diagram, no patterns, no particles. Composition: leave the entire left two-thirds extremely quiet and nearly black so the real XMB menu can appear there. Place the identity entirely within the right third, with safe margins. Huge crisp heavy slightly condensed sans-serif uppercase wordmark on two lines, 'PSP' above 'DOCK'. Beneath it the subtitle 'USB STORAGE + HID', wrapping as 'USB STORAGE' and '+ HID' if necessary for large legible type. Text must be perfectly spelled, high contrast, readable when the entire image is only 480px wide. One short thin teal horizontal accent above the identity. Keep all content within the right third, aligned left in that region. Text verbatim: 'PSP', 'DOCK', 'USB STORAGE + HID'. No other words, no version number, no small technical labels, no Sony or PlayStation logo, no icons, no border, no screenshot, no shadows behind letters. Final production artwork only.

## Verificación

Se inspeccionaron los tres recursos finales a su resolución real.
PIC0 tiene canal alpha con valores de 0 a 255. Los tamaños coinciden con
las restricciones del empaquetador. El empaquetador comprueba que cada
PNG incrustado en EBOOT coincide byte por byte con el recurso del proyecto,
además del título y las cabeceras PBP/SFO.
