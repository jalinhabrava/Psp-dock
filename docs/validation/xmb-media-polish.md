# XMB: música al lanzar juegos, fondos ampliados y sonidos originales

## Alcance — 3 de noviembre de 2026

El usuario está en el trabajo y no puede probar físicamente la PSP. Los tres
cambios se desarrollaron y probaron en el PC con Chat on Steroids. No se accedió
a la consola, no se instaló otro homebrew y no se modificaron juegos o partidas.
La PSP conserva la 0307. Base de esta tanda: `ecdd0c4` en
`feat/dock-product-session`, PR #11 existente, sin merge.

## Música de selección que seguía sonando sobre el juego

La reproducción anterior dependía de que el siguiente refresco de la ventana
observara `session.running`. Al pasar PPSSPP a primer plano la ventana podía
quedar oculta y dejar de consultar el estado. El audio ya iniciado no se paraba
sólo por ocultarse la página.

Ahora `/api/launch` y `/api/retro-launch` silencian y descargan el audio ANTES
de enviar la orden. Una guarda de lanzamiento impide que una respuesta antigua
del servidor reactive la música. Una respuesta perdida mantiene silencio hasta
obtener un estado nuevo; no se asume que el emulador no arrancó.

También se detiene al ocultar/cerrar la página, al perder el servicio, durante
la salida y mientras existe un juego en ejecución. Las promesas `play()` y
cargas de imagen tardías comprueban generación y estado. La música de selección
vuelve después de confirmar que el emulador terminó y volver al menú visible.

Regresión real sobre funciones de la aplicación:

- Código anterior: falla `Preview stops BEFORE launch HTTP completes`; conserva
  el URL del audio cuando la petición de lanzamiento ya está en curso.
- Código nuevo: pasa con lanzamiento PSP/retro, ventana oculta, estado atrasado,
  `play()` tardío, fallo conocido, entrega incierta y regreso al menú.
- Evidencia local: `data/xmb-audio-before-regression.txt` y
  `tests/xmb_media_lifecycle.test.js`.

Prueba en Edge real con HTML/JS/CSS de la aplicación, HTTP/proceso de juego
simulados, PNG y WAV sintéticos, sonido silenciado y una pulsación real del
navegador para habilitar audio. No se desactiva su política de autoplay.
Resultados: `playing`, `loopBefore`, `hdReady`, `stoppedImmediately`,
`silentDuringGame`, `resumed`, `passed`: todos `true`.
Evidencia: `data/xmb-polish-evidence/browser-media.json`.

## Fondos de mayor resolución

`xmb_backgrounds.BackgroundCache` recibe únicamente bytes del PNG original
ya guardado en la caché del PC. La ruta `/api/media-hd/{id}` no extrae recursos
del juego ni inspecciona USB. Un worker acotado genera derivados mientras la
interfaz mantiene visible el original; nunca bloquea el lanzamiento esperando
una imagen ampliada. La respuesta 202 se consulta un máximo de ocho veces.

Algoritmo `pillow-lanczos-soft-v3`: Lanczos proporcional y enfoque suave,
conservando la transparencia. El enfoque cambia como máximo seis niveles por
canal frente al resultado Lanczos. Objetivo: 3840 píxeles de ancho, con límite
4096 por eje y diez millones de píxeles. No se fuerza 16:9 ni se modifica el
original. Es interpolación, NO reconstrucción generativa ni detalle 4K nativo.

Validación sobre el fondo auténtico de Dariusburst previamente cacheado en PC:

| Medición | Resultado |
|---|---:|
| Original | 480 × 272, 205.242 bytes |
| Derivado | 3840 × 2176, 4.579.279 bytes |
| Encolar trabajo | 0,446 ms |
| Generación de esa imagen | 1,197 s |
| Lectura posterior en memoria | 0,395 ms |

Son mediciones de una imagen en este PC, no una promesa de latencia total del
Dock ni una prueba de arranque USB. Original y hash conservados; resultado
inspeccionado visualmente, con proporción y composición correctas. Conserva
naturalmente las limitaciones de detalle del material de baja resolución.

Caché por SHA-256 del original y versión del algoritmo; publicación atómica;
un worker, cuatro trabajos esperando, deduplicación y memoria acotada. PNG real
validado mediante CRC, estructura y decodificación; se rechazan tamaños
excesivos, APNG, datos truncados, enlaces y junctions. Sin Pillow o con error de
imagen permanece disponible el original. No se descarga ningún modelo ni se
requiere API o GPU.

## Clic original del XMB

Los sonidos NO se generaron ni se aproximaron. Se descargó el actualizador
oficial Sony PSP 6.61 non-Go y se extrajo en el PC, sin ejecutarlo ni escribir
en la flash de ninguna consola.

- Fuente publicada por PSP-Arkfive/Despertar-Del-Cementerio:
  `http://du01.psp.update.playstation.org/update/psp/image/us/2014_1212_6be8878f475ac5b1a499b95ab2f7d301/EBOOT.PBP`.
- Tamaño: 32.580.549 bytes. MD5: `6be8878f475ac5b1a499b95ab2f7d301`.
- SHA-256 del actualizador:
  `dc23a6dabdaed40bbfaad811d1f170346b911ba9750478fe5928d9f10c94f552`.
- Recurso: `flash0:/vsh/resource/system_plugin.rco` (89.012 bytes).
- SHA-256 RCO: `183ec1bf1076ce8fa77ce83e5b94b3bf0f043a73b7db37205bb32cdedb27adb3`.

Se intentó HTTPS; falló la coincidencia del certificado. Se utilizó el HTTP
publicado por el proyecto, sin desactivar TLS. El hash se cotejó con la fuente.
El MD5 sirve aquí para contrastar la descarga publicada, no como firma de Sony.

Herramientas revisadas y fijadas: `John-K/pspdecrypt` commit
`c156627db7634d395c380c0a9589130f603307fc`; `kakaroto/RCOMage` commit
`54ca649a9a6aba150a1fbe423f4c3aec611ee913`; libLZR del primero. RCOMage no
descomprime ese directorio RLZ: libLZR descomprimió sólo los 1.946 bytes de
directorio a 8.612 bytes. Los VAG se copiaron byte a byte por sus entradas
nombradas, no mediante generación ni una búsqueda de firmas ambiguas.

Recursos: `snd_cursor`, `snd_decide`, `snd_cancel`, `snd_option`. Se conservan
los VAG originales. FFmpeg interpreta incorrectamente la versión 4 de este
VAG PSP como un contenedor PS2 estéreo: una copia temporal cambia sólo ese
campo a 0x20 para decodificar mono desde el desplazamiento correcto. El
payload ADPCM y los originales no cambian. Conversión a WAV PCM16 mono,
44.100 Hz, 1.848 muestras, 41,905 ms por sonido; sin filtros ni remuestreo.
Cursor/decide/option comparten ya el mismo payload en Sony; no es duplicación
accidental de un efecto inventado.

SHA-256 WAV cursor/decide/option:
`edf6c8f14ca6176c80210832718ee6014afa4103d10eb4ec2efefb618b400f9a`.
Cancel: `dfae796ee12e5f989f6f44f47f7c2332e6aebba296e82cab818a2c08b8b4c294`.

Validación independiente por Python wave/ffprobe y slices originales: PASS.
Los cuatro WAV, el manifiesto y los VAG originales están instalados en la
carpeta privada LIVE `xmb-ui-sounds`; el manifiesto tiene SHA-256
`f57867e1f582d315945c1109e313f08e53c05d36e2cbbd56b3a4e6c532810555`.
Scripts, comandos y comprobaciones se conservan en `data/xmb-sound-research`.
Esos recursos Sony, el actualizador y las imágenes NO se incorporan a Git.
Las licencias de las herramientas no se atribuyen a los recursos de Sony.

`UiSoundPlayer` usa reproducción asíncrona de Windows, de modo que el control
exclusivo con PSP no depende de activar audio con un ratón en Chromium. Sólo
admite los cuatro efectos locales, PCM acotado y hash coincidente. Se descartan
solicitudes atrasadas o durante juego/copia; no se encolan tras una operación
lenta. Sin recurso original queda en silencio, sin pitido genérico de Windows.

El clic se activa en movimientos y acciones reales, no en refrescos del estado.
Hay ajustes independientes para música de selección y sonidos del XMB.

## Fuentes técnicas y reproducibilidad

- https://github.com/PSP-Arkfive/Despertar-Del-Cementerio
- https://github.com/John-K/pspdecrypt
- https://github.com/kakaroto/RCOMage
- https://github.com/FFmpeg/FFmpeg/blob/master/libavformat/vag.c
- https://github.com/vgmstream/vgmstream/blob/master/src/meta/vag.c
- https://docs.python.org/3/library/winsound.html
- https://developer.mozilla.org/en-US/docs/Web/API/HTMLMediaElement/play
- https://developer.mozilla.org/en-US/docs/Web/API/Page_Visibility_API
- https://pillow.readthedocs.io/en/stable/handbook/concepts.html#filters

## Límites de la validación

Regresión final: **259 pruebas Python PASS** en 63,427 s, con
`PSP_DOCK_TEST_NO_REAL_USB=1`. Incluye integración de medios, HTTP, flujo de
producto, cierre de ventana, mando y transporte nativo. Seis suites JavaScript
PASS: ciclo multimedia, flujo de producto (316 aserciones), conexión (156),
navegación XMB, control por mando y menú de Triángulo. `git diff --check`: PASS.
Log: `data/xmb-polish-evidence/regression.txt`.

La prueba Edge usa elementos multimedia reales con un emulador simulado;
no inicia PPSSPP ni representa una prueba física. No se ha escuchado el efecto
en la consola durante esta tanda. Se verificaron su procedencia, bytes y
decodificación, y el recorrido de reproducción por código.

Los cambios cargan en el siguiente inicio de la app de Windows. No hace falta
reinstalar la 0307 ni interrumpir al usuario en el trabajo. Sigue pendiente,
para cuando disponga de su PSP, la comprobación física de los ajustes de
rendimiento/mando anteriores y la percepción audiovisual en su pantalla.
