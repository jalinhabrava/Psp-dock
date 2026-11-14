# PSP Dock · Entrar sin consola

La pantalla inicial ofrece **Jugar con otro mando** una vez que el servicio
local ha confirmado que no está preparando ni sincronizando una PSP.
También se puede pulsar A o Start en el mando Xbox para entrar.
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

El botón hace una petición autenticada a la API local para suspender
temporalmente la detección de PSP. Mientras se juega en modo invitado, el
worker no enumera dispositivos USB, no lee HID de PSP y no ejecuta los
pasos de sincronización de la consola. Ni un escaneo, ni un cambio de foco
del mando Xbox, ni un error transitorio devuelven al usuario a la portada.
No se guarda esta elección en disco.

Se puede conectar físicamente una PSP durante el juego web, pero habrá
que seleccionar **Conectar mi PSP** para reanudar el sondeo y volver al
proceso normal de verificación y sincronización. La elección se confirma
con el servicio local antes de abandonar el menú invitado.

El botón Conectar mi PSP regresa a la pantalla inicial y reanuda la detección
sin escribir archivos ni cambiar el estado del hardware. Cerrar en modo sin PSP usa el cierre
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
    node tests/web_port_xbox_navigation.test.js
    python -m unittest tests.test_guest_web_mode -q
    python -m unittest tests.test_http tests.test_xmb_window -q

Pendiente de prueba manual: arrancar sin PSP, entrar con un mando Bluetooth,
navegar por Juegos Web, iniciar un juego con WebView2, volver al XMB,
conectar la PSP real y comprobar el flujo normal de sincronización.
La nueva PR no cambia firmware ni la lógica de copias de PSP.
