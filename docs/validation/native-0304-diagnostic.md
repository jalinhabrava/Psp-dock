# PSP Dock 0304: espera de Windows corregida; recepción USB pendiente

## Resultado real del 2 de noviembre de 2026

Durante las mediciones siguientes la PSP ejecutaba 0303. Dos reintentos físicos de HOST_BEGIN terminaron
sin aceptación: secuencia 0, resultado 0, pendiente false, STORAGE y Windows
sin confirmar. No llegaron al inventario ni a copiar juegos o partidas.
La anterior corrección de caché de 0303 no resolvió el fallo observado por David;
su reproducción simulada no demostraba la causa concreta en esta PSP.

Se encontró y corrigió una segunda demora del host: después del timeout,
publicar la pantalla de atención reconstruía la biblioteca del PC durante
34,485 segundos. El mismo worker no podía volver a comprobar USB durante ese
trabajo. Sin una unidad montada, la ruta legacy también reconstruía catálogos
en cada vuelta. `cached_catalogs=True` no evitaba el recorrido si las cachés
materializadas aún estaban vacías.

## Mediciones físicas

La PSP y su volumen ya estaban conectados antes de estas mediciones. No son
mediciones del tiempo de enumeración de Windows desde insertar el cable.

| Etapa | Antes del cambio de publicación | Después |
|---|---:|---:|
| Identificar la tarjeta antes de la orden | 1.312 ms | 1.800,238 ms |
| Llamada síncrona SET_FEATURE | 5.016 ms | 5.012,987 ms |
| Espera del ACK tras el envío | 2.016 ms | 2.005,487 ms |
| Callback de inventario, detenido en HOST_BEGIN | 8.469 ms | 8.976,519 ms |
| Publicar el estado de espera/atención | 34.485 ms | máximo 0,917 ms en 48 publicaciones |
| Resultado del protocolo PSP | 0 / 0 / pendiente false / host false | igual |

Primer reintento: 17:58:55 UTC. Segundo reintento: 18:13:32 UTC.
La publicación pesada desapareció en la segunda prueba; el fallo USB permanece.
El primer estado observado tras pulsar el segundo reintento ya era `inventory`
con operación activa. Las consultas HTTP siguieron respondiendo mientras la
llamada USB estaba bloqueada. Se dejó una sola ventana LIVE abierta.

SET_FEATURE devolvió éxito según la API Win32; eso no equivale a aceptación de
HOST_BEGIN por el protocolo. La firma ctypes BOOLEAN está declarada como un
byte y el código sí lanza error si el retorno es cero. El plazo del ACK empieza
después del SET. No se ha demostrado que los cinco segundos correspondan a un
timeout específico de HidUSB, ni qué metadatos recibió el callback de PSP.

## Cambios en Windows

- `waiting_state()` reutiliza exclusivamente datos de presentación en memoria
  mientras no hay PSP/mando verificado o aún falta HOST_BEGIN. Conserva sesión,
  recuperación, cola, proceso y baselines; deshabilita permisos de lanzamiento.
- La desconexión legacy invalida la cadencia del inventario para revisar la
  misma tarjeta al reaparecer. Un mando legacy verificado conserva su recorrido.
- La UI consulta el estado cada 500 ms sin solapar peticiones. La fase inicial
  dice «Preparando tu PSP»; la sincronización se anuncia en su propia fase.
- `connection_diagnostics.py` conserva 80 eventos y agregados, mide con
  `perf_counter` y guarda errores/etapas importantes o lentas en el PC.
  `GET /api/connection-diagnostics` consulta memoria fuera de Dock.lock.
  El fsync del registro tampoco retiene el bloqueo de lectura del diagnóstico.
- Se distinguen detección, asociación de tarjeta, GET inicial, SET, polling,
  espera de ACK y publicación de la UI. Los GET rápidos no escriben un evento
  en disco por cada sondeo.
- Host, inspector, navegación y UI admiten únicamente los pares de firmware
  0303/03030001 y 0304/03040001, con PDK2/v3 y capacidades 0007. Cada orden
  reproduce la identidad validada del STATUS de su mismo handle y rechaza
  cualquier cambio de identidad durante el ACK. Las revisiones anteriores,
  desconocidas y mezclas de build siguen rechazadas.

## Diagnóstico PSP 0304 instalado; ejecución pendiente

La nueva pantalla NOTE captura en RAM el último SET, setup, submit, completion,
retcode, recvsize, generaciones, motivo de descarte, resultado del parser,
buffer bruto de 64 bytes y actividad del worker. Los GET no pisan esa evidencia.
Una generación de publicación evita presentar una copia parcial de los datos.
El buffer se presenta como bruto, incluso si recvsize es cero; su contenido no
prueba por sí mismo que USB entregara 64 bytes.

No se han modificado las guardas de aceptación, `unkc`, el protocolo de cable,
el tamaño FEATURE ni el manejo de caché. No hay escritura de logs desde PSP
al sistema de archivos mientras Sony USBSTOR posee la Memory Stick.

Launcher y kernel son 3.4, magic PDN4; firmware 0304, build 03040001. El estado
compartido mide 304 bytes en el ELF USER real. La actualización desde 0303 usa
los cuatro hashes instalados y el SHA-256 del recibo original de Windows:
`17bbdd13c46763a3edac4a498385015f8075a58ff17103bbba6047b6e5552437`.
Se conservan las anclas y rutas de recuperación históricas.

Paquete local: `data/native-dock-0304-package`, cuatro archivos, 375.760 bytes.

| Archivo | Bytes | SHA-256 |
|---|---:|---|
| EBOOT.PBP | 312.697 | `be219b2c87152e1cf2d16efebc65d9ea2177313a07f34ca4ca8114e93ec16048` |
| docknative.prx | 24.730 | `e2b9ce41c22f0511d7e487dc05e9f82623b8e1cbc6ad012e3ea3270ab1a66e12` |
| LICENSE-FuSa-GPL-3.0.txt | 35.823 | `230184f60bae2feaf244f10a8bac053c8ff33a183bcc365b4d8b876d2b7f4809` |
| LICENSE-DejaVu.txt | 2.510 | `3011f92125603da8baa900d59a6ee3cb981d6bbf0fa0c3d9767690aa92c8439c` |

Manifest SHA-256:
`9b6539bb4a4766c16b976f917ca31cc9ea097697378dd20383701c8d1a7515c2`.
La actualización física 0303→0304 terminó el 2 de noviembre a las 18:41:27 UTC,
después de verificar la identidad Sony del USB normal del XMB. El instalador
copió los cuatro archivos anteriores a un respaldo nuevo del PC, verificó los
hashes, preparó y publicó el paquete y volvió a comprobar ambos extremos.
La aplicación anterior también permanece aparcada en la tarjeta, fuera de GAME.
Recibo privado: `data/native-dock-0304-evidence/upgrade.json`.
La instalación no acredita todavía la ejecución ni el handshake físico de 0304.

## Validación y evidencia

- 111 pruebas Windows de cadencia, sesión, protocolo nativo, inspector y registro:
  PASS, con USB físico deshabilitado para las fixtures.
- 102 pruebas Windows de paquete, actualización, recuperación e instalación:
  PASS, sin instalar en la PSP real.
- JavaScript: 264 aserciones de producto y 156 de conexión, PASS.
- C transporte/UI, ASan/UBSan, MIPS con -Werror y auditoría ELF/USER/PBP/recursos:
  PASS. La auditoría comprobó versiones minor/major 04 03; `psp-nm` confirmó
  los 304 bytes del estado compartido en el ELF USER real.
- Reintentos reales: publicación corregida; handshake todavía falla. Las pruebas
  anteriores no acreditan sincronización, mando ni salida completos en hardware.

Evidencia íntegra conservada en `data/native-dock-0304-diagnostic/` del PC:
`live-before-instrumentation.json`, `read-only-timings.json`,
`startup-with-timing.json`, `retry-with-timing.json`, `post-retry-timing.json`,
`retry-after-waiting-fix.json` y `diagnostic-artifacts.json`.
Los registros completos, que incluyen rutas locales y estado de la sesión,
no se incorporan a git. No se borraron journals ni se forzó expulsión.
SHA-256 de `retry-after-waiting-fix.json`:
`1e801c4beb41c093199c84be76adf5d46e879b594d2b6cd481affdcac0cc5bb2`.

## Foto 0303 recibida y nueva observación necesaria

La foto recibida muestra ACK0/0, Host0, Rechazos0/0 y Gen0. El estado vivo del
host conserva el mismo nonce. En el protocolo0303, una aceptación establecería
una secuencia positiva y un rechazo incrementaría el contador: la orden no
llegó a esas rutas durante la sesión observada. El último SETUP004001A1 es
GET_REPORT, con longitud64, interfaz1 y Feature report0; no acredita el SET.
0303 no conserva los motivos previos al parser. La captura0304 ahora instalada
registra esos puntos sin modificar las condiciones de aceptación.

Tras esa foto, Cancelar y desconectar terminó con verified y
host_eject_accepted verdaderos a las18:29:08 UTC. La app salió normalmente.
David volvió al XMB y activó USB para la actualización. Reportó además que la
salida de Dock sigue exigiendo retirar cable aunque Windows ya haya expulsado.
El requisito explícito es cerrar Dock en Windows y poder volver al XMB desde
PSP manteniendo el cable, sin expulsión manual ni START+SELECT obligatorio.
El análisis de ese cierre se conserva en `native-exit-connected-cable.md`.

La primera solicitud normal de expulsión del USB XMB tras instalar0304 recibió
veto de SearchIndexer (CR23, tipo3); no se forzó. Ese resultado es distinto de la
expulsión nativa anterior, que sí fue aceptada. Registros privados en
`data/native-dock-0304-evidence/`.
Un reintento tras acabar las escrituras recibió el mismo veto. La solicitud
normal al disco USBSTOR exacto recibió CR23/tipo8 y la unidad seguía montada.
No se interpretó ninguno como cierre. Se abrió Indexing Options y se revisó su
botón Pause, protegido con el escudo de administrador; los intentos de invocarlo
no cambiaron el estado visible «Indexing in progress». No hubo pausa confirmada
ni cambios de servicio. La PSP sigue en USB XMB mientras se resuelve ese bloqueo.

`tools/inspect_native_ejection.py` está preparado para capturar el subárbol
nativo y sus relaciones sin expulsar ni escribir en PSP. Compilación e importación
CLI verificadas en Windows. En la consulta XMB de lectura, algunas relaciones
devolvieron CR0x25; se conservan como null con su código, nunca como listas vacías.

Falta lanzar0304 y capturar NOTE tras una sola orden HOST_BEGIN real, junto con
las relaciones PnP del dispositivo para el cierre con cable conectado.
No retirar la guarda de longitud ni alterar `unkc` por conjetura.
La aceptación completa requiere después observar HOST_BEGIN, inventario,
sincronización, modo mando y salida al XMB con cable conectado reales.
PR #11 continúa draft, sin fusionar.
