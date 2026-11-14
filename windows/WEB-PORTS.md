# PSP Dock: origen correcto de los ports

## Fuente y alcance

El directorio que indicó el usuario es **https://ported2browser.com/**.
No es equivalente a ported2browser.org, ported2browser.sbs, ported2browser.co
ni otras webs con nombre parecido. La PR #19, que importaba 1.439 fichas desde
.sbs, fue cerrada y descartada: sus datos no acreditaban que los juegos fueran
ports del directorio original. PSP Dock no es un portal de minijuegos web.

El dominio original devuelve actualmente **HTTP 403 / Cloudflare Turnstile**
a las solicitudes automatizadas y su robots.txt impide acceso de agentes.
No se ha sorteado la comprobación humana ni se ha supuesto disponer de un
feed oficial. La próxima importación masiva debe tener como fuente primaria
los datos del .com obtenidos por un medio autorizado, con autor, plataforma,
URL de juego real y requisitos documentados.

## Lista conservada mientras no haya fuente oficial

| Juego clásico | Origen directo conocido | Estado en PSP Dock |
| --- | --- | --- |
| Need for Speed Underground 2 (PC) | https://nfsu2.xyz/ | Jugable; confirmado por el usuario con Xbox |
| WipEout (PS1) | https://phoboslab.org/wipegame/ | Runtime original directo; mando y salida por validar físicamente |
| Tony Hawk's Pro Skater 3 (PS2) | https://tonyhawkpro3.gamingretro.online/ | URL NO verificable; bloqueado, advierte L2/R2 |
| Dragon Ball Z Budokai Tenkaichi 3 (PS2) | https://dragonballbt3.online/runtime.html | Requiere una ISO original y un selector soportado por el port; no jugable directamente con el mando |
| Diablo/Diabloweb (PC) | https://d07riv.github.io/diabloweb/ | Runtime del proyecto; necesita puntero |

**No se presenta que estas cinco URLs hayan sido exportadas del .com**: se
conservan las que se revisaron anteriormente de forma independiente. Se
excluyen 2048 y HexGL por ser juegos web nativos, no ports de consola/PC.

## Arquitectura esperada para ampliar correctamente

El futuro importador debería leer exclusivamente el listado original,
extraer de cada ficha el enlace de juego de quien realizó el port (no el
botón que abre una guía o presentación), clasificar plataforma de origen,
controles y archivos originales necesarios, y verificar su funcionamiento
antes de marcarlo 'jugable'. El índice se debe refrescar de forma explícita,
con deduplicación y sin inventar destinos ni descargar contenido comercial.
Un bloqueo de acceso no autoriza sustituir el origen por clones.

## ISOs del usuario

La carpeta local configurable solo permite escanear metadatos de ISO, CSO,
BIN, CUE y CHD; no sirve archivos por HTTP, no escribe en la PSP y no
sube una imagen al sitio del autor. Se muestran sugerencias por nombre,
siempre pendientes de confirmación. Integrar la selección real dentro de
cada port requiere saber exactamente cómo pide archivos su runtime y obtener
consentimiento para esa operación concreta.

## Pruebas

    node tests/xmb_web_ports.test.js
    node tests/xmb_guest_mode.test.js
    python -m unittest tests.test_original_port_directory tests.test_web_iso_library tests.test_http -q
    dotnet build windows/WebDockShell/PspDockWebShell.csproj -c Release

No fusionar en main sin pruebas físicas del flujo PSP, saves y Xbox.