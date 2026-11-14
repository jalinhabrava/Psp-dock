# PSP Dock · Entrar sin consola

La pantalla inicial ofrece **Jugar con otro mando** una vez que el servicio
local ha confirmado que no está preparando ni sincronizando una PSP.
También se puede pulsar el botón principal de un mando externo para entrar.
El acceso es voluntario y solo afecta a esta ventana: no se escribe un
registro falso de dispositivo ni se alteran las sesiones USB.

## XMB sin PSP

Se conservan el fondo, los efectos de movimiento, los sonidos y la navegación
horizontal/vertical del XMB. Las únicas secciones accesibles son:

- Ajustes: idioma, fondo, animación y estado del mando;
- Dock: información del modo, **Conectar mi PSP** y **Salir de PSP Dock**;
- Juegos Web: todos los ports del catálogo aprobado. Se pueden jugar con
  teclado o mando externo, mediante iframe permitido o el contenedor
  WebView2 nativo ya preparado en la PR de Juegos Web.

No se ofrecen **Juegos (ISO)**, **Retro (RetroArch)**, **Partidas PSP** ni
**Gráficos PSP/PPSSPP**. Esta separación evita jugar accidentalmente a
ROMs asociadas a una PSP y sobrescribir guardados en una conexión futura.
Los guardados de un port web permanecen en su propio origen WebView2/navegador.

## Transiciones y seguridad

La nueva entrada se muestra solo cuando la API informa de espera de PSP
(modo await_native o await_hid), sin unidad Sony, trabajo pendiente,
recuperación, copia, escaneo, emulador abierto ni error de conexión.
La demostración sintética tampoco puede entrar.

Si Windows detecta una PSP o inicia una operación mientras el modo sin PSP
está abierto, se revoca el modo automáticamente. Un juego iframe se descarga
de su vista y, si la partida usa WebView2, el XMB envía al host el mensaje
interno close-web-port para que la vista del juego se cierre. El flujo
normal de conexión de PSP conserva todos sus controles anteriores.
Desconectar de nuevo la PSP **no** reabre el modo invitado automáticamente.

El botón Conectar mi PSP regresa a la pantalla inicial sin escribir archivos
ni cambiar el estado del hardware. Cerrar en modo sin PSP usa el cierre
ordinario de la app: el backend sigue siendo la autoridad que puede vetar
un cierre si tiene trabajo pendiente.

## ¿Y jugar a Retro sin PSP?

No se ha habilitado en esta iteración. Retro ya compara SRAM compatibles
mediante SHA-256 y baselines por identidad de Memory Stick, y obliga a
resolver conflictos. Pero actualmente identifica la ROM y la ruta del
guardado basándose en el inventario de una PSP verificada. Para jugar sin
ella y sincronizar después habría que añadir un catálogo PC-only asociado
a la PSP de origen, registrar el baseline anterior al inicio, detectar
modificaciones de PC y PSP al reconectar, mostrar las dos versiones y
respaldar el destino antes de cada copia explícita. La fecha de modificación
es útil como pista, **no como autorización para sobrescribir**.

## Pruebas

    node tests/xmb_guest_mode.test.js
    node tests/xmb_web_ports.test.js
    python -m unittest tests.test_http tests.test_xmb_window -q

Pendiente de prueba manual: arrancar sin PSP, entrar con un mando Bluetooth,
navegar por Juegos Web, iniciar un juego con WebView2, volver al XMB,
conectar la PSP real y comprobar el flujo normal de sincronización.
La nueva PR no cambia firmware ni la lógica de copias de PSP.
