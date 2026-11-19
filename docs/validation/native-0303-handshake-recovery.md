# PSP Dock 0303: confirmación USB, respuesta y salida del error

## Fallo observado

El 2 de noviembre de 2026, la aplicación LIVE permanecía en `attention`, con propósito inicial, sin entrada completada, sin juego en ejecución y sin devolución pendiente. El error se produjo esperando `HOST_BEGIN`, antes del inventario y de cualquier copia de este intento. La evidencia original está conservada en `data/native-dock-0303-evidence/before-fix-live-state.json`.

La consola estaba después conectada en USB normal del XMB, Sony 054C:02D2. Ese modo permite actualizar los archivos, pero no ejecuta el protocolo de PSP Dock. Por ello, `can_retry=false` mientras sólo está presente USB XMB no demuestra otro fallo de reintento. El reconocimiento de un nonce nuevo para recuperar la misma tarjeta ya existía en HEAD y no se presenta como una corrección nueva.

La prueba 0207 utilizaba almacenamiento sintético de 512 KiB y no interpretaba HOST_BEGIN. Su resultado validó aquella composición de disco de prueba y mando, pero no esta recepción de órdenes con la Memory Stick real.

## Correcciones

### Recepción de órdenes USB

`ctl_done()` invalidaba la caché del búfer después de completar la recepción, inmediatamente antes de interpretar el comando. Si USBBus entrega EP0 OUT mediante copia CPU, esa invalidación puede descartar los bytes recién recibidos y recuperar de memoria el STATUS del GET_REPORT anterior.

Se elimina únicamente la invalidación posterior. Se conserva la preparación de caché antes de `sceUsbbdReqRecv()`, así como los controles de longitud, error, conexión, generación, sesión y secuencia. No se cambian los tamaños de informe ni el campo desconocido `unkc`.

El patrón coincide con la recepción de [usbacc en uofw](https://github.com/uofw/uofw/blob/28306bb5bd4fcdcc0c1d469bf1f3bdb885dde3e9/src/kd/usbacc/usbacc.c): preparación antes de Recv y lectura en la finalización sin invalidación posterior.

La nueva prueba modela memoria y caché separadas: **fuente anterior + prueba nueva falla; fuente corregida + misma prueba pasa**. Esto demuestra el defecto bajo entrega mediante copia CPU. Aún falta confirmar que fuera la ruta concreta usada por la PSP del usuario.

### Cancelación y cierre

- Cancelar una entrada sin HOST_BEGIN confirmado ya no vuelve a ejecutar HOST_BEGIN.
- Un inventario fallido permite pasar a finalización. Una escritura interrumpida conserva su revisión pendiente.
- La cancelación vuelve a comprobar dispositivo, tarjeta y padre USB y solicita expulsión normal de Windows; respeta órdenes pendientes y vetos.
- Atención ofrece **Cerrar app** sin afirmar que la PSP esté expulsada. Cuando la sesión inicial está vinculada y es cancelable, ofrece **Cancelar y desconectar**.
- El cierre explícito no restaura otra vez la ventana; mantiene el journal de recuperación.
- El timeout identifica el comando y conserva la secuencia solicitada y la última respuesta observada, sin repetir automáticamente la orden.

### Respuesta del host

El worker pasa de esperar 3 s entre etapas a 0,25 s durante conexión/preparación y 1 s en mando/atención/finalizado. En USB XMB, la presencia de PSPDockNative evita iniciar el inventario legacy mientras se espera la aplicación nativa; ese marcador no concede identidad ni permiso de escritura.

La publicación de un callback reutiliza catálogos válidos de juegos/ROMs y evita otro recorrido completo. Una caché invalidada se reconstruye, para no mantener obsoleta la disponibilidad de juegos o núcleos recién instalados. Las verificaciones de guardados y de las operaciones continúan realizando sus lecturas. No es una publicación completamente libre de I/O.

## Verificación de software

224 casos Python distintos aprobados en Windows, en ejecuciones focales:

| Módulo | Casos |
|---|---:|
| native_dock | 32 |
| inspect_native_dock | 11 |
| psp_native_package | 8 |
| psp_native_upgrade | 60 |
| psp_native_install | 10 |
| dock_product_flow | 44 |
| dock_product_session | 38 |
| dock_window_close | 12 |
| live_scan_cadence | 6 |
| dock_cached_startup | 3 |

La primera selección contenía un nombre de módulo de arranque inexistente; se ejecutó después el módulo correcto. La regresión nueva de caché tenía una expectativa excesiva sobre el número de llamadas de un mock; se corrigió para comprobar reconstrucción y resultado actualizado, y ambos casos de publicación se repitieron con éxito.

También aprobados:

- 258 aserciones JavaScript de producto y rutas de entrada; 152 de conexión.
- Harness del controlador C con warnings como errores y regresión de caché.
- ASan/UBSan en WSL, incluida detección de fugas; UI C.
- Compilación MIPS kernel/USER con PSP GCC y auditoría ELF, módulos 3.3, PBP y recursos XMB.
- Protocolo PDK2/v3, firmware 0303, build 03030001; USB Sony 0100 sigue siendo independiente.
- `git diff --check`.
- Reinicio normal de Windows: una ventana visible LIVE. API de estado respondió en 16,64 ms en una lectura. Esto no mide detección ni sincronización física.
- Captura de la ventana real confirma **Cerrar app** visible en atención.

## Instalación física realizada

0302 → 0303 terminó y fue releída el **2026-11-02T16:36:28.496829+00:00**. Son cuatro archivos, **371472 bytes**, en `PSP/GAME/PSPDockNative`.

| Archivo | Bytes | SHA-256 |
|---|---:|---|
| EBOOT.PBP | 310817 | 53cf4f25083573c1e306474317ed6de6c0995524f4174d69e5bef614f769d906 |
| docknative.prx | 22322 | d4913e98f06f5a6c4d6502ab1802cb83271f20c857e3413000b37a69ef5eda96 |
| LICENSE-FuSa-GPL-3.0.txt | 35823 | 230184f60bae2feaf244f10a8bac053c8ff33a183bcc365b4d8b876d2b7f4809 |
| LICENSE-DejaVu.txt | 2510 | 3011f92125603da8baa900d59a6ee3cb981d6bbf0fa0c3d9767690aa92c8439c |

Recibo y journal: `data/native-dock-0303-evidence/upgrade.json`. Copia PC previa: `native-app-0302-before-0303`. La aplicación anterior también queda aparcada fuera de GAME. Los respaldos, paquetes y recibos históricos se conservan. Esta operación sólo sustituye la aplicación; no copia juegos ni partidas.

## Estado pendiente y límite de la conclusión

La app Windows actualizada queda abierta. Conserva la sesión fallida en atención, con cierre disponible, sin operación ni juego activo. Cuando PSP Dock vuelva a ejecutarse en la consola y se vincule la misma tarjeta, el reintento explícito podrá recuperar la sesión.

La expulsión normal a las **16:37:24 UTC** fue vetada por **SearchIndexer.exe** (configret 23, veto 3). No se forzó ni se detuvieron servicios. Evidencia: `data/native-dock-0303-evidence/normal-eject.json`.

**Instalada y verificada no significa ejecutada físicamente.** Falta resolver la expulsión normal, iniciar PSP Dock en la consola y observar el ACK de HOST_BEGIN, inventario/elección, sincronización y modo mando. El recorrido previsto sigue siendo almacenamiento → sincronización → mando, sin cambios manuales de modo entre etapas. La PR #11 permanece draft hasta la aceptación física.
