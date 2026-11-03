# Sonidos del XMB obtenidos de la PSP del usuario

## Decisión de producto

El usuario propone obtener los efectos de la consola que conecta por primera
vez. Ese es el comportamiento implementado: sonidos originales, sin recreación
por IA ni muestras incluidas en el código, el instalador o el paquete homebrew.

La rama `feat/xmb-device-sounds` parte de `ca90c4e` de
`feat/dock-product-session`. Conserva el fix que detiene la música de selección
antes de lanzar un juego, los fondos ampliados y la reproducción nativa Windows
para navegar con la PSP sin depender de activar audio en Chromium.

## Flujo

1. El usuario abre PSP Dock en la consola y confirma el inicio como hasta ahora.
2. El controlador comprueba que USB y almacenamiento están inactivos. Antes de
   iniciarlos, concede al proceso USER una ventana exclusiva de preparación.
3. El homebrew abre únicamente
   `flash0:/vsh/resource/system_plugin.rco` para lectura. Prepara una copia
   acotada en `ms0:/PSP/SYSTEM/PSPDock/xmb/system_plugin.rco`, fuera de partidas
   y de los cuatro archivos que componen la aplicación instalada.
4. Todos los archivos deben quedar cerrados y las escrituras sincronizadas
   antes de confirmar que terminó la preparación. El controlador vuelve a
   comprobar el estado USB y solo entonces inicia el almacenamiento Sony.
5. Tras verificar la consola, la tarjeta y HOST_BEGIN, Windows lee ese único
   archivo durante el inventario inicial, siempre que el estado de esa misma
   sesión confirme que la PSP preparó o cotejó el recurso. Comprueba su tamaño y SHA-256, extrae
   los cuatro efectos y decodifica el ADPCM a WAV en el propio PC.
6. El PC publica el paquete completo en su carpeta privada `xmb-ui-sounds`.
   Las conexiones siguientes reutilizan una caché válida sin volver a leer el
   recurso de la tarjeta. La PSP conserva su copia preparada para otros PC.

No se monta flash0 como una unidad USB ni se modifica su contenido. La PSP
solo escribe en el subdirectorio de caché del Dock en la Memory Stick, antes
de entregarla al PC. Windows nunca escribe en la consola para esta importación.

## Compatibilidad inicial y fidelidad

La versión inicial admite exclusivamente el recurso stock de **PSP 6.61
non-Go** que ya se inspeccionó. La comprobación exacta precede a cualquier uso
de offsets; un tema o firmware distinto no se interpreta por aproximación.

| Recurso | Valor verificado |
| --- | --- |
| Archivo | `system_plugin.rco` |
| Tamaño | 89.012 bytes |
| SHA-256 | `183ec1bf1076ce8fa77ce83e5b94b3bf0f043a73b7db37205bb32cdedb27adb3` |
| Efectos | Cursor, confirmar, cancelar y opciones |
| Salida por efecto | WAV PCM16 mono, 44.100 Hz, 1.848 muestras, 3.740 bytes |
| Conversión | Sin filtros, normalización, remuestreo, recorte ni bucles |

Los offsets proceden de las entradas RCO con nombre verificadas durante la
investigación. El directorio está comprimido, pero los cuatro VAG almacenados
no necesitan esa descompresión. El perfil fijado permite un lector pequeño
sin RLZ, FFmpeg, descarga de firmware ni bibliotecas adicionales.

Se comprobó la coincidencia de los **WAV completos** generados con los cuatro
WAV de referencia privados. Eso verifica el decodificador y el perfil; no
demuestra todavía la lectura del recurso en la PSP física del usuario.

Para ampliar compatibilidad deberá añadirse un perfil comprobado de otro
recurso o un parser acotado de su directorio. No se aceptan offsets de 6.61
sobre archivos desconocidos. PSP Go y recursos modificados no se presentan
como compatibles con esta primera versión.

## Caché, errores y almacenamiento

Una caché completa y válida se conserva, incluida la importada anteriormente
en este PC. La entrada de Ajustes distingue sonidos disponibles de sonidos
pendientes de preparación. La música de los juegos y los efectos conservan
controles de silencio independientes.

El importador verifica la identidad nativa antes y después de leer, y de nuevo
antes de publicar. La raíz capturada debe seguir siendo la raíz verificada:
un cambio de letra de unidad no permite leer la ubicación anterior. Los errores
de identidad o cancelación detienen la operación correspondiente; no se
confunden con la ausencia opcional de sonidos.

La confirmación de recurso disponible pertenece a la sesión nativa 0308.
Un archivo que haya quedado en una tarjeta de otra PSP no basta: si esta
consola omitió la exportación, Windows no lo importa como si fuera suyo. Las
versiones anteriores del homebrew no pueden emitir esa confirmación.

Se rechazan enlaces, junctions, tamaños inesperados y cambios del archivo
mientras se lee. Los cuatro WAV y el manifiesto se escriben primero en un
directorio temporal del PC y se releen. Solo un paquete completo se publica.
Si una caché anterior estaba incompleta, se conserva en un directorio de
respaldo; un fallo al publicar intenta restaurarla. Nunca se sobrescribe una
caché completa válida.

Un archivo ausente, un perfil desconocido o un fallo al crear la caché de PC
deja el audio opcional sin preparar y permite continuar el inventario. En la
PSP, una lectura no disponible también puede omitirse limpiamente. En cambio,
si no se puede demostrar que terminaron los accesos a la tarjeta y su
sincronización, el controlador no inicia USB. Un timeout nunca concede esa
autorización por defecto.

El manifiesto privado guarda el hash del recurso y la identidad verificada de
la tarjeta/dispositivo, sin convertir datos del recurso en rutas de archivos.
Ningún original, WAV, VAG, RCO o firmware de referencia se incluye en Git.
El paquete homebrew también rechaza una sección PBP SND0 no vacía.

## Componentes

| Componente | Responsabilidad |
| --- | --- |
| `homebrew/native-dock` | Ventana previa a USB y exportación USER acotada |
| `psp_xmb_audio.py` | Perfil RCO, validación y decodificación PS-ADPCM |
| `xmb_sound_import.py` | Lectura de dispositivo y publicación de la caché |
| `dock_product_session.py` | Admisión dentro del inventario inicial |
| `xmb_sounds.py` | Reproducción local asíncrona, hash y debounce |
| `native_dock.py` | Aceptación de 0308 conservando versiones anteriores |
| `psp_native_package.py` / `psp_native_upgrade.py` | Paquete auditable y actualización reversible |

El cambio de ABI compartida exige launcher y controlador **0308 / PDN8**.
El protocolo USB sigue siendo PDK2/v3; no añade otro interfaz ni cambia el
formato de los informes HID. Un bit de estado de 0308 indica que el recurso
fue preparado en esta sesión. No se reutiliza la etiqueta 0307 para binarios
diferentes.

## Validación y aceptación pendiente

Las pruebas de decodificación usan VAG sintéticos creados por los tests. La
comparación con los recursos auténticos solo utiliza la referencia privada ya
existente; sus archivos y resultados locales quedan fuera del repositorio.

Las pruebas de integración comprueban importación tras HOST_BEGIN, caché sin
lecturas repetidas, conservación/rollback, límites, corrupción y cambio de
dispositivo. Las pruebas C verifican exportación y exclusión de USB, incluidos
fallos y cancelación. Las regresiones de medios conservan el silencio antes
de iniciar PSP/retro, las respuestas atrasadas y el regreso al launcher.

Validación realizada el 3 de noviembre de 2026:

| Tanda | Resultado |
| --- | --- |
| Decodificador, importador, caché, reproducción e integración multimedia | 48 pruebas Python aprobadas |
| Sesión de producto con transporte simulado | 49 pruebas aprobadas |
| API HTTP y sus guardas | 14 pruebas aprobadas |
| Transporte, paquete, actualización y rollback | 278 pruebas aprobadas |
| Revisión final de protocolo/paquete con confirmación de exportación | 99 pruebas aprobadas; se solapan con la tanda anterior |
| Navegación, mando, Triángulo, medios, producto y conexión | Seis suites JavaScript aprobadas |
| Transporte y exportador C, ASan/UBSan, compilación MIPS y auditoría ELF/PBP | Aprobados; LeakSanitizer desactivado por compatibilidad con WSL |
| Importación completa con recurso privado local y PSP simulada | Cuatro WAV idénticos; origen intacto; segunda llamada sin leer la PSP |

El paquete final 0308 se construyó y verificó en el PC: cuatro archivos,
382.973 bytes. El manifiesto tiene SHA-256
`aad421a18b48a5e377083e4d08ae8c3232e95594af87f35ae6eccd4d042fe0e8`.
Contiene el lector y el controlador, con SND0 vacío. No contiene el recurso
RCO ni los efectos de Sony. Estos resultados no deben sumarse como un único
recuento de pruebas distintas.

**Actualización tras la primera prueba física, 3 de noviembre:** la 0308 se
instaló con el actualizador verificado y respaldo de la 0307. Se conservó la
caché anterior y se dejó vacía la ruta de importación para la prueba. El usuario
confirmó el clic correcto del XMB y el selector con el mando; apareció una
nueva caché de sonidos. La prueba también detectó una omisión de 0308 en la
lista de identidades de la interfaz Windows, que impedía mostrar el launcher
después de elegir «Sin copiar ahora». El arreglo y sus regresiones se describen
en [la validación de entrada 0308](validation/native-0308-no-copy-launcher.md).
Falta repetir físicamente esa entrada con la interfaz corregida. La 0308 de
la consola no necesita reinstalarse para ese arreglo de Windows.

## Procedencia y distribución

Este diseño evita que PSP Dock distribuya los recursos Sony. Los efectos
proceden de la consola del usuario y permanecen en su caché local; no se
suben a un servidor ni se ofrecen como un paquete compartido. La extracción
no concede una licencia nueva ni garantiza por sí sola todos los usos de
recursos protegidos.

Referencias primarias:

- [PSPSDK: operaciones de archivos, lectura y sincronización](https://pspdev.github.io/pspsdk/group__FileIO.html).
- [PSPSDK: almacenamiento USB](https://github.com/pspdev/pspsdk/blob/master/src/samples/usb/storage/main.c).
- [RCOMage: definición del contenedor RCO](https://github.com/kakaroto/RCOMage/blob/54ca649a9a6aba150a1fbe423f4c3aec611ee913/src/rcofile.h).
- [FFmpeg: referencia de decodificación ADPCM](https://ffmpeg.org/doxygen/trunk/adpcm_8c_source.html).
- [LPI: derechos sobre reproducción y distribución de fonogramas, artículos 114–117](https://www.boe.es/buscar/act.php?id=BOE-A-1996-8930).
