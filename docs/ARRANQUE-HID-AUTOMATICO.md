# PSP Dock — abrir Windows y esperar a la consola

## Decisión vigente

David eligió este flujo el 1 de noviembre de 2026:

1. Abrir **PSP Dock en Windows**.
2. Ver la pantalla **Conecta tu PSP**.
3. Abrir PSP Dock en la consola y conectar el cable.
4. Pasar automáticamente al menú cuando la aplicación verifica el modo Dock.

La detección se ejecuta dentro de la app abierta. No se instala un servicio
Windows, una tarea de llegada USB ni un monitor al iniciar sesión. El botón
de la PSP no se presenta como capaz de abrir una aplicación cerrada en el PC.

## Identidades y estados

| Conexión | Comportamiento de la app |
|---|---|
| Sin PSP | Pantalla de espera, con el nombre PSP DOCK y los dos pasos de conexión. |
| USB Sony normal 054C:02D2 | Indica almacenamiento USB; no afirma que el modo Dock esté activo. «Preparar biblioteca» permite abrir explícitamente las acciones de preparación anteriores. |
| HID estable 054C:01CB / 0104 | Comprueba descriptor, PDK1 y sesión; permite navegar por el menú. Los juegos conservan sus comprobaciones independientes. |
| Compuesto 054C:01CC / 0207 | Abre el menú en modo diagnóstico, permite navegar y mantiene los juegos desactivados. El disco de 512 KiB es sintético. |
| Identidad ambigua, revisión desconocida o PDK1 inválido | Mantiene la espera y no habilita juegos. |
| Desconexión sin trabajo pendiente | Vuelve a la pantalla de espera. |
| Juego, transferencia o devolución pendiente | Conserva visibles el estado y las acciones de recuperación. |

La clasificación no ejecuta `SET_FEATURE`, no cambia el papel USB y no realiza
copias de juegos o partidas. El volumen ficticio no se añade al descubrimiento
de Memory Sticks. No se modifica el firmware 0207 físicamente validado.

## Una ventana y una biblioteca

`start_dock.py` abre la ventana del Dock o reutiliza la instancia LIVE existente.
La identidad HTTP de `server.json` y el bloqueo de la carpeta siguen vigentes.
Abrir el acceso dos veces no crea dos escritores sobre las partidas.

La aplicación utiliza la biblioteca LIVE existente. Puede indicarse una ruta
con `python start_dock.py --data RUTA_LIVE`. La detección no se instala como
trabajo de inicio de Windows. El cierre de la ventana retira la detección
cuando no hay una operación que deba finalizar; una devolución pendiente
permanece registrada para la siguiente apertura y nunca se da por completada
por cerrar la ventana.

## Retirada del piloto anterior

La tarea histórica **PSP Dock - USB HID** usaba el evento 410 del canal
`Microsoft-Windows-Kernel-PnP/Configuration`. En el PC de David los últimos
410 de Sony eran de las 21:20 UTC; las conexiones físicas posteriores de
0206/0207, incluida la aceptación de las 22:23–22:24, no generaron ese evento.
Por tanto no sirve para prometer apertura en cada reconexión.

La tarea se eliminó con `python -m psp_dock.core.setup_usb_event --remove`. La comprobación
`python -m psp_dock.core.setup_startup --status` devolvió **DESACTIVADO**. No se llegó a instalar
ni crear un servicio Windows. Los modos `--install` de los scripts antiguos
quedan retirados; `--remove` y la consulta de estado se conservan para limpiar
instalaciones antiguas. Los helpers de localización de datos siguen usados
por el lanzador manual.

Evidencia local del diagnóstico del piloto:
`data/windows-dock-arrival/installed-task-before.xml`,
`psp-pnp-history.json` e `installer-preflight.json`.

## Comprobación del flujo

### Resultado en el PC de David

El 1 de noviembre, 23:01:38–23:01:49 UTC, la app real abrió una única ventana.
La segunda apertura devolvió `already_running` con el mismo HWND. Enviando
el cierre normal de la ventana, terminó con código 0 en **4,172 segundos**,
eliminó `server.json`, liberó el bloqueo LIVE y conservó `session.json`
idéntico por SHA-256. No había PSP HID conectada durante esta comprobación.

Las vistas de espera, diagnóstico, mando estable y desconexión se renderizaron
en Chrome de Windows con fixtures aislados: 1600×900 y 540×960, sin errores JS,
desbordamientos horizontales ni operaciones imprevistas. Se inspeccionó además
la captura de la ventana real. Pasaron 87 pruebas Python de integración y
41 de la revisión final de ventana/sesión (con solapamiento), 151 aserciones
JS de conexión/cierre y las dos suites previas de navegación.

El acceso de escritorio **PSP Dock** se creó y releyó con su destino, argumentos
e icono correctos. El script `windows/Create-PSP-Dock-Shortcut.ps1` instala,
consulta o retira únicamente ese acceso. El icono envuelve el PNG del thumbnail
aprobado en un contenedor ICO, sin cambiar sus píxeles.

La comprobación final encontró cero tareas PSP Dock, cero servicios PSPDock
y ninguna entrada de inicio de sesión. El resumen con hashes está en
[windows-manual-entry.json](validation/windows-manual-entry.json).

### Transiciones con PSP real — 2 de noviembre de 2026

David conectó la PSP con Dock 0207 y confirmó el paso de la pantalla de espera
al XMB de Windows. Su captura muestra la ventana PSP Dock y el aviso
**Modo diagnóstico · 0207**, disco de prueba de 512 KiB y juegos desactivados.
Después confirmó que detener Dock en la PSP devuelve Windows a la espera.

El sondeo posterior, a las 09:30:52 de Madrid (08:30:52 UTC), encontró
`dock_connected`, `msc_diagnostic`, PID `01CC` y revisión `0207`. La app real
mantenía un cliente, recibía un estado válido del mando mediante WinMM y
publicaba `can_launch_game=false`, sin error, juego activo ni transferencia.
La entrada está respaldada por el usuario y su captura; este sondeo corrobora
el estado conectado. La vuelta a espera está respaldada por la confirmación
explícita del usuario, no por una traza del cambio en el backend.

El archivo de observación `data/windows-manual-entry/post-stop-20261102.json`
conserva `result=state_requires_review` porque el comprobador esperaba encontrar
la PSP detenida. Su contenido registra una conexión de diagnóstico válida;
ese marcador no demuestra un fallo ni permite inferir cuándo cambió la conexión.
No se repitió la aceptación de almacenamiento ni se muestreó movimiento HID.
La Memory Stick real continúa pendiente.

### Pasos para repetirla

Se debe distinguir entre pruebas de código, vista de interfaz y conexión
física. Para verificar una instalación:

1. Abrir el acceso sin Dock HID: comprobar la pantalla de espera.
2. Abrir de nuevo: comprobar que se reutiliza la misma instancia y ventana.
3. Entrar en Dock en la PSP: comprobar transición y navegación HID.
4. Con 0207, comprobar el aviso de diagnóstico y `can_launch_game=false`.
5. Cerrar la ventana sin operaciones activas: comprobar que termina su
   proceso y deja de detectar; volver a abrir restaura la espera.
6. Revisar que no queda tarea PSP Dock ni entrada de inicio de sesión.

No afirmar que la Memory Stick real está disponible porque el menú se abra.
El criterio físico superado para 0207 sigue siendo almacenamiento sintético
más HID simultáneo; el acceso real a juegos y partidas necesita otra fase.
