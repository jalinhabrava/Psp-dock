# PSP Dock para Linux

## Alcance de esta rama

El host Linux conserva el XMB y el flujo de producto del MVP: inventario,
elección de copias, modo mando, copia puntual y devolución al salir del Dock.
Incluye transporte PDK2 mediante `hidraw`, asociación del mando y la Memory
Stick mediante su padre USB de `sysfs`, perfiles privados de PPSSPP SDL y
RetroArch, apertura manual y desmontaje final mediante UDisks2.

**Es un port de software pendiente de aceptación con la PSP física.** Los
tests usan dispositivos y archivos sintéticos; no demuestran que el firmware
0301 haya enumerado correctamente la Memory Stick, ni miden latencia, vídeo,
audio o la compatibilidad de guardados con una consola real. Esta rama no
recompila ni instala firmware y no modifica la versión Windows del controlador.

Se admite únicamente `054c:01cd`, revisión USB **0301**, descriptor nativo
exacto y sesión **PDK2**. La 0300 retirada y los prototipos PDK1 no autorizan
juegos ni escrituras en Linux. El `wValue=0x0300` de un FEATURE report USB
significa tipo FEATURE + report ID 0; no es la revisión del dispositivo.

## Requisitos

| Componente | Contrato inicial |
| --- | --- |
| Host | Linux de escritorio x86_64 o aarch64, usuario local activo |
| Python | 3.10 o posterior; host sin dependencias pip |
| Entrada | Kernel `usbhid`/`hidraw`, `evdev` y SDL2 2.24 o posterior |
| Ventana | Chromium o Chrome para `--app`; navegador predeterminado como alternativa |
| PSP | PSP Dock nativo 0301 y Memory Stick FAT montada por el escritorio |
| Salida | UDisks2 con `udisksctl` y permiso de desmontaje de la sesión local |
| PPSSPP | Frontend nativo **PPSSPPSDL** / **ppsspp-sdl**, contrato de CLI de v1.20.4 |
| RetroArch | Opcional; binario nativo `retroarch` con driver SDL2 y núcleos `.so` de la misma arquitectura |
| Biblioteca | Disco fijo local: ext2/3/4, XFS, Btrfs o F2FS; LUKS/LVM con discos físicos verificables |

El detector comprueba formato ELF de 64 bits, arquitectura, nombre del
frontend, permisos de ejecución y huella del ejecutable preparado. **No
certifica la versión de PPSSPP a partir de esos datos.** Instala un frontend
SDL compatible con v1.20.4; ese parser no proporciona un sondeo `--version`
fiable y el Dock no intenta ejecutarlo durante la preparación. Qt, Flatpak,
Snap, AppImage, Wine, scripts de lanzamiento y SDL3 no están cubiertos por
este contrato inicial. El PPSSPP instalado por la distribución debe conservar
sus recursos y bibliotecas en las rutas que espera su propia compilación.

La biblioteca no puede estar en la PSP, otro disco USB, un montaje de red,
un volumen de sólo lectura, un enlace de directorio ni una cadena de discos
virtuales que el host no pueda verificar. En Btrfs se comprueban todos los
miembros que declara `sysfs`, y en LUKS/LVM se recorren sus dispositivos
subyacentes. No se ofrece el selector de carpetas de Windows: introduce una
ruta absoluta cuando quieras mover la biblioteca.

## Preparación y apertura manual

1. Instala Python, SDL2, UDisks2, un navegador y PPSSPP SDL con el gestor de
   paquetes de tu distribución o mediante una compilación oficial compatible.
   RetroArch y los núcleos son opcionales y se instalan por separado.
2. Para dar acceso al mando al usuario local activo, instala una vez la regla
   incluida; estos comandos requieren administración del sistema:

   ```sh
   sudo install -m 0644 linux/70-psp-dock.rules /etc/udev/rules.d/70-psp-dock.rules
   sudo udevadm control --reload-rules
   ```

   Después reconecta la PSP. La regla concede acceso sólo al PID y revisión
   indicados y no hace que todos los dispositivos HID sean escribibles por
   todos los usuarios. El Dock se ejecuta como usuario normal.
3. Abre el host manualmente:

   ```sh
   ./start-linux.sh
   # También admite una biblioteca del Dock ya existente en disco fijo:
   ./start-linux.sh --data /ruta/absoluta/psp-dock
   ```

   La ruta predeterminada es `$XDG_DATA_HOME/psp-dock/live`, o
   `~/.local/share/psp-dock/live` cuando XDG_DATA_HOME no es una ruta absoluta.
   No se crean servicios, tareas ni entradas de autoarranque.
   `--data` en el lanzador manual exige una sesión existente del Dock y
   rechaza enlaces antes de resolver la ruta; no inicializa una carpeta
   ajena como si fuera una biblioteca previa.
4. En Ajustes selecciona el ejecutable PPSSPPSDL y OpenGL o Vulkan. El host
   busca PPSSPPSDL / ppsspp-sdl en PATH si todavía no hay ajustes. El perfil
   inicial usa OpenGL y escala 2×. Direct3D no se ofrece en Linux.
5. Inicia el Dock 0301 en la PSP y monta su Memory Stick en el gestor de
   archivos si el escritorio no la monta automáticamente. Debe verse la raíz
   FAT completa, con `PSP/`; el host no solicita montaje automático ni usa
   permisos de administrador para acceder a la tarjeta.

Se mantiene una única instancia por carpeta de datos mediante `flock`. Si
ya hay una ventana con heartbeat activo, una segunda apertura no crea otra
UI. En X11 y Wayland se respeta al compositor: el Dock no inyecta teclas ni
intenta activar ventanas ajenas. El cierre normal del emulador deja el menú
del Dock abierto. **Salir del Dock** es la acción que autoriza devolver las
partidas; cerrar sólo un juego nunca las devuelve.

## Qué sucede en una sesión

1. El host lee el FEATURE report, comprueba descriptor/revisión/sesión y une
   `hidraw` con el bloque montado por el **mismo padre físico USB**. No basta
   con encontrar un directorio llamado PSP o un dispositivo con VID Sony.
2. Identifica el volumen por su serie FAT publicada en `/dev/disk/by-uuid`.
   Convierte `ABCD-1234` al mismo número decimal usado por Windows para que
   los registros conserven una identidad de volumen coherente.
3. Presenta el inventario y la elección de copias. Los cambios de fase
   STORAGE / CONTROLLER / COPYING / EXIT_SYNC / SAFE usan la sesión y una
   secuencia PDK2. Cada orden se envía una sola vez y necesita confirmación;
   un timeout no autoriza repetirla ni escribir.
4. En modo mando la PSP sigue siendo un gamepad del kernel. El XMB lee su
   propia cola hidraw sin `EVIOCGRAB`, sin quitar `usbhid` y sin interceptar
   los eventos de SDL. Una fase de copia o identidad cambiada neutraliza la
   navegación del host.
5. El juego usa la copia local verificada. HOME abre el menú de PPSSPP;
   salir del juego devuelve al XMB y conserva pendiente la devolución.
6. Al elegir Salir del Dock se conservan las verificaciones, respaldos y
   conflictos del flujo existente. Tras devolver las partidas se solicita
   SAFE, se vuelve a comprobar la identidad y se ejecuta únicamente:

   ```text
   udisksctl unmount --block-device /dev/<partición-verificada> --no-user-interaction
   ```

   La salida sólo se confirma después de que desaparezcan todos los montajes
   del mismo padre USB. Otros montajes, archivos abiertos, un veto de permisos
   o un timeout dejan la salida pendiente. **No se usa PowerOff, extracción
   forzada, desmontaje perezoso ni desvinculación del driver USB.** Se valida
   un desmontaje del filesystem; no se afirma que el puerto haya perdido
   alimentación.

## Perfiles de emulación y guardados

PPSSPP SDL v1.20.4 obtiene su Memory Stick de
`$XDG_CONFIG_HOME/ppsspp`. Por eso el hijo recibe un XDG_CONFIG_HOME privado
en la carpeta del Dock, cuyo único enlace `ppsspp` apunta a la Memory Stick
privada configurada. No se cambia HOME ni el entorno del proceso padre.
No se pasan flags Windows `--config`, `--controlconfig`, `--memstick` o
`--graphics`. Los gráficos se escriben en el INI privado y la CLI usa
`--fullscreen --pause-menu-exit <juego>`.

Un enlace XDG que apunta inesperadamente a otra carpeta bloquea la
preparación. Un cambio explícito de carpeta o la migración verificada
invalidan solamente el enlace conocido; nunca borran los guardados de origen.
El ejecutable se vuelve a verificar por SHA-256 antes de abrir cada juego.

SDL2 vincula su joystick por `/dev/input/eventN` al mismo padre USB verificado
por PDK2. El descriptor debe conservar cuatro ejes, doce botones y una
cruceta. El Dock añade una entrada por GUID a `SDL_GAMECONTROLLERCONFIG`
**sólo en el entorno del hijo** y genera los códigos SDL propios de PPSSPP.
Los controles anteriores del perfil privado se respaldan por SHA-256 antes
de sustituirlos; no se usa el mapeo DirectInput de Windows.

Para RetroArch indica su ejecutable nativo y, si no usa una ubicación normal
de la distribución, la carpeta de núcleos `.so`. Se buscan también `cores/`
junto al ejecutable y las rutas `/usr/lib[/64]/libretro` y las variantes
multiarch x86_64/aarch64. El host no descarga paquetes `.exe` o `.dll`.
La sesión carga un `--config` privado con driver SDL2 y el índice de la PSP,
sin guardar cambios sobre la configuración global. SELECT+TRIANGLE abre el
menú de RetroArch; SELECT+START queda reservado al Dock. SRAM y estados del
emulador quedan en rutas privadas por tarjeta, ROM y núcleo. Se conservan
las reglas existentes de compatibilidad: no se convierten automáticamente
savestates de PPSSPP o de distintos emuladores PSP/RetroArch.

## Recuperación y límites de escritorio

Si el host se interrumpe, no se repiten copias ni órdenes USB. La sesión
persistida permanece bloqueada. Tras cerrar los emuladores normalmente puede
usarse la recuperación explícita:

```sh
python3 dock.py --data /ruta/absoluta/psp-dock --recover-session
```

La comprobación Linux inspecciona los procesos del usuario en `/proc`; una
lectura incompleta no se interpreta como prueba de que el emulador terminó.
Desbloquear la sesión no devuelve partidas ni borra journals. Vuelve a abrir
el Dock y resuelve la devolución con la tarjeta original. Si se cierra la
ventana de Linux sin elegir Salir del Dock, queda esa recuperación pendiente;
no se reproduce la restauración de HWND de Windows.

WSL sirve para ejecutar las pruebas sintéticas de Python; no sustituye la
aceptación en un escritorio Linux con USB, UDisks, audio y gráficos reales.
La validación pendiente debe cubrir al menos permisos udev, montaje FAT,
elección sin copiar, copia solicitada, controles en XMB/PPSSPP/RetroArch,
cierre de juego sin retorno, conflicto de partidas, veto de desmontaje y
desconexión. No ejecutes esa aceptación mientras otra sesión use la PSP.

## Pruebas sin dispositivos físicos

```sh
PSP_DOCK_TEST_NO_REAL_USB=1 python3 -m unittest discover -s tests -p 'test_linux*.py' -v
PSP_DOCK_TEST_NO_REAL_USB=1 python3 -m unittest discover -s tests -p 'test_native_dock.py' -v
PSP_DOCK_TEST_NO_REAL_USB=1 python3 -m unittest discover -s tests -p 'test_dock_product_session.py' -v
```

El guard desactiva descubrimiento real y aperturas predeterminadas de HID.
Las pruebas inyectan árboles sysfs, tablas de montaje, respuestas ioctl,
procesos y ejecutables ELF sintéticos; no arrancan emuladores reales ni
escriben en configuraciones globales. La CI además ejecuta regresión Windows,
flujo de producto, guardados retro, migración y las suites JavaScript del XMB.

## Fuentes del contrato

- [PPSSPP v1.20.4: NativeApp.cpp y rutas Linux](https://github.com/hrydgard/ppsspp/blob/v1.20.4/UI/NativeApp.cpp).
- [PPSSPP v1.20.4: SDLJoystick.cpp y códigos del mando](https://github.com/hrydgard/ppsspp/blob/v1.20.4/SDL/SDLJoystick.cpp).
- [Linux: API hidraw](https://docs.kernel.org/hid/hidraw.html).
- [SDL2: SDL_JoystickPathForIndex](https://wiki.libsdl.org/SDL2/SDL_JoystickPathForIndex).
- [UDisks2: udisksctl](https://storaged.org/doc/udisks2-api/latest/udisksctl.1.html).
- [Libretro: autoconfiguración y SDL2](https://github.com/libretro/docs/blob/master/docs/guides/controller-autoconfiguration.md).
