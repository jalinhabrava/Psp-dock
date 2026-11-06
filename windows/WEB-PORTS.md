# PSP Dock · Juegos Web / WebView2

## Arquitectura

PSP Dock ofrece una categoría horizontal «Juegos Web», con cada port en una
fila vertical. El catálogo fijo web/ports.json enlaza a las URL originales
de los autores, no descarga juegos ni redistribuye ROMs.

Varios juegos de PS2 impiden incrustarse en un iframe externo mediante
X-Frame-Options: SAMEORIGIN. El contenedor WebView2 usa una sola ventana
con dos vistas: XMB local y documento principal del autor. Respeta esas
políticas y no abre una segunda ventana de navegador.

El lector WinMM de PSP Dock transmite el mando PSP PDK1 a un Gamepad API
virtual en la vista del juego y sus subframes. Los mandos externos completos
se presentan antes que la PSP virtual, para que L2, R2 y segundo stick
funcionen cuando un juego los necesita. El puente no envía teclas globales
al sistema y el juego remoto no recibe permisos de acceso a USB, al servidor
de sincronización ni al sistema de archivos local. L + R + SELECT mantenidos
1,2 segundos devuelve el XMB. Los guardados web quedan en el perfil aislado
de WebView2; no se mezclan con SAVEDATA de la PSP.

## Instalación en Windows

Requisitos: Windows x64, SDK .NET 8+ para compilar, Microsoft Edge WebView2
Runtime para ejecutar. Desde PowerShell, en la raíz del repositorio:

    powershell -ExecutionPolicy Bypass -File windows/build-web-shell.ps1

Publica un ejecutable self-contained en dist/web-shell/win-x64. Para
instalarlo en el perfil de Windows:

    powershell -ExecutionPolicy Bypass -File windows/build-web-shell.ps1 -Install

La próxima ejecución de PSP Dock detecta automáticamente el host en
LOCALAPPDATA/PSP-Dock/runtime/web-shell/PspDockWebShell.exe. Si no existe,
mantiene el navegador anterior (Chrome/Edge en modo app). En esa variante,
solo pueden probarse los ports que permiten iframe; los otros muestran
un aviso de que requieren WebView2.

Se puede fijar la ruta absoluta del ejecutable mediante la variable de
entorno PSP_DOCK_WEBVIEW2_SHELL. No instalar encima de una sesión activa.
El empaquetador estable PyInstaller todavía no incluye este host nuevo;
se debe validar en hardware antes de añadirlo a una release.

## Orígenes verificados por HEAD (6 noviembre 2026)

| Host | HTTP | Política | Carga |
| --- | --- | --- | --- |
| tonyhawkpro3.gamingretro.online | 200 | SAMEORIGIN | WebView2 principal |
| dragonballbt3.online | 200 | SAMEORIGIN | WebView2 principal |
| nfsu2.org | 200 | SAMEORIGIN | WebView2 principal |
| play2048.co | 200 | frame-ancestors sin localhost | WebView2 principal |
| hexgl.bkcore.com/play | 200 | sin bloqueo iframe en HEAD | iframe candidato |
| phoboslab.org/wipeout | 200 | sin bloqueo iframe en HEAD | iframe candidato |
| d07riv.github.io/diabloweb | 200 | sin bloqueo iframe en HEAD | iframe candidato |

Una respuesta HEAD sin bloqueo no demuestra que el juego funcione dentro
de iframe: faltan pruebas de WebGL, WebGPU, audio, SharedArrayBuffer,
controles físicos y persistencia de guardados. La página ported2browser.com
respondió 403 a las peticiones automatizadas, por lo que también se
contrastaron las páginas públicas alternativas y los hosts originales.

THPS3 documenta L2/R2: se avisa antes de lanzar, pero se permite continuar
y conectar otro mando. Para otros PS2 con controles no verificados, no
se debe prometer compatibilidad total de PSP.

## Validación

    node tests/xmb_web_ports.test.js
    python -m unittest tests.test_http tests.test_xmb_window -q
    dotnet build windows/WebDockShell/PspDockWebShell.csproj -c Release

Esta validación es de código y contratos. Una prueba física con la PSP y
otro mando es imprescindible antes de fusionar o desplegar la funcionalidad.
