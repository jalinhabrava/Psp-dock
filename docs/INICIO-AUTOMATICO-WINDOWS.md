# PSP Dock · iniciar con Windows y abrir al conectar (opcional)

Estado: **preparado en el código, NO instalado ni activado en el PC del
usuario**. No se han creado tareas programadas ni entradas de autoarranque.

PSP Dock ya puede quedarse ejecutándose como servidor local en segundo plano.
Con la opción --open-on-connect observa cada nueva conexión de almacenamiento
PSP y abre su launcher XMB en el navegador una sola vez por conexión
detectada. La detección es lectura pasiva: no envía ningún comando USB.

## Instalación opcional

El acceso Activar-inicio-automatico-Windows.cmd propone instalar una sola
entrada marcada, exclusiva del usuario, en su carpeta Inicio de Windows.
Requiere un Python 3.12 con pythonw.exe disponible.

El instalador busca una carpeta LIVE de PSP Dock existente, incluyendo la
biblioteca del contenedor Codex si es la única. Si encuentra varias o ninguna,
se detiene **sin crear una biblioteca en blanco**: ejecutar

    py -3 -m psp_dock.core.setup_startup --install --data "RUTA_LIVE_REAL"

La entrada inicia en el próximo login:

    pythonw.exe dock.py --no-browser --open-on-connect --port 0 --data "RUTA_LIVE_REAL"

El servicio escucha solo en 127.0.0.1. Windows asigna un puerto
disponible (el 8765 puede estar reservado o bloqueado). PSP Dock escribe
su puerto en server.json y el acceso Iniciar-PSP-Dock.cmd lo recupera
mediante una comprobación de identidad HTTP, abriendo la ventana XMB
sin lanzar otra instancia ni un juego automáticamente.

Si ya se ejecuta Dock sobre esa misma carpeta LIVE, el bloqueo de instancia
impide que otro proceso la gestione a la vez. El instalado NO se pone en
marcha inmediatamente al pulsar Activar: lo hará en el próximo inicio
de sesión Windows.

Para comprobar estado sin cambios:

    py -3 -m psp_dock.core.setup_startup --status

Para desinstalar el inicio (sin borrar juegos, partidas ni configuración),
usar Desactivar-inicio-automatico-Windows.cmd o:

    py -3 -m psp_dock.core.setup_startup --remove

El script solo elimina una entrada que contiene su marcador de propiedad
PSP_DOCK_STARTUP_V1; se niega a reemplazar o borrar lanzadores ajenos.

## Qué automatiza y qué no

Sí: dejar el servicio PC abierto al entrar en Windows, detectar la inserción
USBSTOR y abrir la pantalla XMB/preflight; preguntar por transferencias
y partidas antes de modificar archivos.

No: activar el homebrew HID de PSP por sí solo, expulsar Windows,
montar/desmontar FAT ni devolver la Memory Stick automáticamente. El
handoff físico USBSTOR↔HID requiere un protocolo kernel con ACK real que
aún no existe. La biblioteca cacheada o el HID observado no sustituyen
esa evidencia.

Durante los ensayos de laboratorio, la PSP sigue iniciándose en modo
almacenamiento y pasando manualmente a HID-only LAB, que tiene límite
de unos 90–105 segundos. PSP Dock prepara los recursos de PPSSPP en
el PC ANTES de cambiar de modo para no desperdiciar ese tiempo.
