# Arte de PSP Dock

El nombre visible es **PSP Dock**, tanto en PARAM.SFO como en la placa del XMB.
No se incluyen versiones ni nombres del transporte en el arte de producto.

| Recurso | Dimensiones | Uso |
| --- | --- | --- |
| ICON0.PNG | 144 × 80, RGB | Miniatura blanca y turquesa sobre grafito |
| PIC0.PNG | 310 × 180, RGBA | Placa con PSP DOCK; sólo el exterior es transparente |
| PIC1.PNG | 480 × 272, RGB | Fondo grafito sin texto para la ficha del XMB |

ICON0, PIC0 y PIC1 son los recursos finales aprobados para PSP Dock 0308.
PIC0 se editó el 2 de noviembre de 2026 para eliminar un subtítulo técnico.
Los originales de producción están en `sources/`; las variantes previas
permanecen accesibles en el historial Git, pero no forman parte del producto.

`generate_artwork.py` sólo convierte el modo PNG y reduce con Lanczos a la
resolución de PSP. No dibuja ni retoca contenido. El empaquetador verifica
que los tres PNG incrustados coinciden byte por byte con estos archivos.

## Prompt de edición de PIC0

Referencia: `homebrew/native-dock/assets/sources/PIC0-generated.png`.
Se utilizó `transparent_background=true`.

Edit this existing PSP Dock XMB title plate for the finished product. Preserve the same high contrast white PSP DOCK wordmark, short turquoise horizontal accent above it, opaque near-black graphite rounded rectangle, subtle texture, and overall style. Remove the entire subtitle USB STORAGE + HID cleanly. Rebalance the vertical spacing so the remaining PSP DOCK title feels centered with comfortable padding, without adding any new words, symbols, version numbers, or technical text. The only words must be PSP DOCK. Keep a landscape 31:18 composition. True transparent alpha only outside the rounded plate; the plate interior and all letter counters must remain opaque graphite. Clean crisp legibility when reduced to 310 by 180 pixels. This is a production bitmap branding asset, not a mockup or photographed screen.

La licencia DejaVu corresponde al atlas de texto utilizado por
la interfaz de PSP y acompaña también al paquete instalable.
