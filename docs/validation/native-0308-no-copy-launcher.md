# PSP Dock 0308: acceso al launcher después de «Sin copiar ahora»

## Prueba del usuario

El 3 de noviembre de 2026, después de instalar y verificar el paquete 0308,
el usuario probó el recorrido real. Confirmó que el inventario tardó mucho
menos, que pudo elegir con el mando de la PSP, que se escuchó el clic correcto
del XMB y que START+SELECT completó la salida coordinada. Después de elegir
«Sin copiar ahora», sin embargo, Windows volvió a la pantalla de bienvenida.

Se revisaron fotogramas de la grabación `20261103-2048-11.8049332.mp4`, de
51,7 segundos. Muestran la preparación, el selector con 1.467 elementos en PSP,
uno en PC, 1.466 pendientes y 14 guardados pendientes, y después la bienvenida
«Esperando el modo Dock». La duración del vídeo no equivale a la duración total
del inventario. La confirmación auditiva y de salida procede del usuario;
no se presenta como una prueba nueva de transferencia de partidas modificadas.

El vídeo permanece privado. SHA-256:
`15006b7c2c556fa793f62812d4b750ae558e0075478d9435274bfbf607d26356`.

## Causa y cambio

`native_dock.py` admitía la identidad 0308/build03080001/capacidades000F,
pero la lista de `dockConnectionView` en `web/app.js` terminaba en 0307.
El panel de elección podía funcionar durante STORAGE. Cuando la sesión
pasaba a READY, el panel dejaba de mostrarse, pero la identidad desconocida
para la vista impedía mostrar y habilitar el menú XMB. La vista volvía a
`screen=waiting` y `canPlay=false`.

La corrección funcional añade exclusivamente la identidad exacta
`[0x0308, 0x03080001, 15]`. Mantiene las comprobaciones de producto, protocolo,
build, capacidades, fase de mando, confirmación del host y enlace verificado.
La ausencia opcional de sonidos no impide acceder al launcher.

Este cambio pertenece al host Windows. La aplicación 0308 de la consola
permanece instalada; no necesita otra compilación ni reinstalación para este
arreglo. La caché de sonidos que apareció durante la prueba se conserva.

## Regresiones

La prueba de flujo ejecuta los manejadores reales de selección: Círculo enfoca
«Sin copiar ahora», Cruz envía una sola elección `none` con la oferta exacta,
la preparación todavía bloquea el juego y READY muestra el menú. El estado
READY ya no necesita una unidad seleccionada. También comprueba que se
mantiene la política explícita de usar los guardados del PC.

`tests/test_xmb_native_identity_contract.py` obtiene las identidades de las
tablas del backend y evalúa el modelo JavaScript real en Node. Así, añadir
una versión admitida por Python obliga a comprobarla también en la interfaz,
sin mantener otra lista de versiones dentro del test. La prueba actual
incluye seis identidades válidas y 72 variantes incorrectas o retiradas.

Resultados de las pruebas focales:

| Comprobación | Resultado |
| --- | --- |
| Flujo de producto, incluida la transición 0308 tras `none` | 336 aserciones aprobadas |
| Modelo de conexión | 156 aserciones aprobadas |
| Navegación con mando | Aprobada |
| Contrato Python/JavaScript | Dos pruebas aprobadas; seis identidades y 72 negativos |
| Sintaxis de `web/app.js` | Aprobada |

Con `web/app.js` del commit anterior `8e16eb3`, cargado únicamente en memoria,
la regresión falla para 0308: obtiene `waiting` donde exige `menu`. Con el
cambio, pasa. Estas comprobaciones no usan USB ni ejecutan juegos.

La comprobación adicional de DOM utiliza el HTML, CSS y JavaScript reales
con un servidor simulado y un navegador Edge aislado. Pulsa el botón real
mediante un evento de ratón de Chromium y comprueba la respuesta y la vista.

| Estado después de elegir `none` y recibir READY | Código anterior | Código corregido |
| --- | --- | --- |
| Una sola petición, con elección y oferta correctas | Sí | Sí |
| Pantalla | Espera | Menú |
| Mando reconocido y permiso de juego | No | Sí |
| Menú y catálogo visibles, menú habilitado | No | Sí |
| Bienvenida oculta | No | Sí |

El navegador de cada pasada se cerró al terminar. El mando, HTTP, catálogo y
medios del ensayo son simulados; no se accede al USB, a la biblioteca real ni
a un emulador. Los resultados se guardan en
`data/native-dock-0308-evidence/browser-entry-before-8e16eb3.json` y
`browser-entry-after.json`, ambos privados. No equivalen a repetir la conexión
física. El ensayo reproducible está en `tools/validate_xmb_entry_browser.py`.

## Aceptación pendiente

Repetir en la consola el recorrido de entrada y elegir «Sin copiar ahora»
para confirmar que aparece el launcher, permite navegar y abrir el juego ya
disponible en el PC. La prueba anterior confirma los avances del inventario,
selector, clic y cierre; todavía no valida físicamente este arreglo de vista.
