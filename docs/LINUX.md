# PSP Dock para Linux

## Base actual y alcance

Esta rama parte de Windows **v0.1.3**, commit
`ff3033351bd9901041eb6b42a639d1810e85bb81`, con el paquete Python actual
`psp_dock/` y firmware PSP **0308**. Conserva su XMB, idiomas inglés/español,
catálogo incremental recordado, medios originales, importación de sonidos,
perfiles por juego, elección de copias y devolución verificada de partidas.
Los adaptadores Linux añaden hidraw, sysfs, SDL2, perfiles privados de
PPSSPP/RetroArch y cierre con UDisks2.

**Es una versión de desarrollo pendiente de aceptación con PSP física.**
Las pruebas sintéticas verifican el software y sus rechazos; no certifican
la enumeración USB, latencia, vídeo, audio ni guardados en una consola real.
Esta rama no modifica, recompila ni instala el firmware PSP.

Se exige `054c:01cd`, descriptor HID nativo exacto y sesión **PDK2/v3**.
La identidad se obtiene de la extensión `NAT3` del Feature report:

| Firmware | Capacidades | Build | Entrega de órdenes | Salida |
| --- | --- | --- | --- | --- |
| 0303–0304 | `0x0007` | `0x03030001` / `0x03040001` | Feature de 64 bytes | SAFE + desmontaje, compatibilidad anterior |
| 0305 | `0x000f` | `0x03050001` | Feature de 64 bytes | Desmontaje + HOST_SHUTDOWN |
| 0306–0308 | `0x000f` | `0x03060001` / `0x03070001` / `0x03080001` | Output fragmentado | Desmontaje + HOST_SHUTDOWN |

El objetivo actual es **0308**. Se rechazan 0300, 0301, 0302, PDK1, un
descriptor de 32 bytes y cualquier revisión/build/capacidad desconocida.
Sony puede publicar `bcdDevice=0100`; también se admiten los valores USB
0303–0308, pero ninguno demuestra por sí solo la versión del firmware.

GET Feature conserva 64 bytes de payload, 65 al incluir el report ID cero
en hidraw. Para 0306–0308, cada orden lógica de 64 bytes se transmite como
32 fragmentos Output de cuatro bytes y un commit CRC16. La API hidraw
`HIDIOCSOUTPUT(5)` incluye el report ID cero adicional. No se sustituye ese
contrato por un SetFeature largo. Una entrega corta o incierta detiene la
operación y obliga a observar la misma sesión antes de reintentar.

## Requisitos

| Componente | Contrato inicial |
| --- | --- |
| Host | Linux de escritorio x86_64 o aarch64, usuario local activo |
| Python | 3.12 o posterior; el host básico utiliza la biblioteca estándar |
| Entrada | Kernel `usbhid`/`hidraw` con `HIDIOCSOUTPUT`, `evdev` y SDL2 2.24 o posterior |
| Ventana | Chromium o Chrome para `--app`; navegador predeterminado como alternativa |
| Sonidos XMB | Opcional: `pw-play`, `paplay` o `aplay` y los sonidos originales importados desde la PSP |
| PSP | PSP Dock nativo 0308 con PDK2/v3 y Memory Stick FAT montada por el escritorio |
| Salida | UDisks2 con `udisksctl` y permiso de desmontaje de la sesión local |
| PPSSPP | Frontend nativo **PPSSPPSDL** / **ppsspp-sdl**, contrato de CLI de v1.20.4 |
| RetroArch | Opcional; binario nativo `retroarch` con driver SDL2 y núcleos `.so` de la misma arquitectura |
| Biblioteca | Disco fijo local: ext2/3/4, XFS, Btrfs o F2FS; LUKS/LVM con discos físicos verificables |

El detector comprueba formato ELF de 64 bits, arquitectura, nombre del
frontend, permisos de ejecución y huella del ejecutable preparado. **No
certifica la versión de PPSSPP a partir de esos datos.** Instala un frontend
SDL compatible con v1.20.4. La preparación no ejecuta el binario para sondear
su versión; el hash registra la copia que el usuario ha seleccionado. Qt, Flatpak,
Snap, AppImage, Wine, scripts de lanzamiento y SDL3 no están cubiertos por
este contrato inicial. El PPSSPP instalado por la distribución debe conservar
sus recursos y bibliotecas en las rutas que espera su propia compilación.

La biblioteca no puede estar en la PSP, otro disco USB, un montaje de red,
un volumen de sólo lectura, un enlace de directorio ni una cadena de discos
virtuales que el host no pueda verificar. En Btrfs se comprueban todos los
miembros que declara `sysfs`, y en LUKS/LVM se recorren sus dispositivos
subyacentes. Se rechazan alias bind que impidan comprobar solapamientos. No se ofrece el selector de carpetas de Windows: introduce una
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

   Después reconecta la PSP. La regla limita acceso al VID/PID y los valores
   bcdDevice admitidos; el host comprueba después el firmware por FEATURE.
   No hace que todos los dispositivos HID sean escribibles por
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
   inicial usa OpenGL y escala manual de respaldo 2×. Direct3D no se ofrece en Linux.
5. Inicia el Dock 0308 en la PSP y monta su Memory Stick en el gestor de
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
3. Registra la intención de iniciar sesión, solicita `HOST_BEGIN` y exige
   confirmación `HOST_CONFIRMED` antes de autorizar almacenamiento o mando.
   Presenta el inventario y la elección de copias. `HOST_PROGRESS` comunica
   actividad y contadores reales a la PSP; no concede permisos de escritura.
   La navegación durante STORAGE requiere la actividad CHOOSING confirmada.
   Los cambios de fase
   STORAGE / CONTROLLER / COPYING / EXIT_SYNC / SAFE usan la sesión y una
   secuencia PDK2. Cada orden se envía una sola vez y necesita confirmación;
   un timeout no autoriza repetirla ni escribir.
4. En modo mando la PSP sigue siendo un gamepad del kernel. El XMB lee su
   propia cola hidraw sin `EVIOCGRAB`, sin quitar `usbhid` y sin interceptar
   los eventos de SDL. Un botón mantenido permanece pulsado aunque el firmware
   no envíe nuevos Input reports: se exige un Feature report válido de la misma
   sesión en cada lectura. Desconexión, error, salida o fase de copia neutralizan
   el estado. No se cambia SET_IDLE ni se reclama la interfaz USB.
5. El juego usa la copia local verificada. HOME abre el menú de PPSSPP;
   salir del juego devuelve al XMB y conserva pendiente la devolución.
6. Al elegir Salir del Dock se conservan las verificaciones, respaldos y
   conflictos del flujo existente. Tras devolver las partidas se solicita
   SAFE, se vuelve a comprobar la identidad y se ejecuta únicamente:

   ```text
   udisksctl unmount --block-device /dev/<partición-verificada> --no-user-interaction
   ```

   Antes del desmontaje se guarda su intención en `native-close-linux.json`.
   El recibo une número mayor/menor del bloque, UUID FAT, ruta e inode del
   padre USB y nonce de sesión. Se comprueba que no quede ningún montaje de
   ese dispositivo y que la identidad de sysfs/udev siga siendo la misma.
   Después se persiste y envía **HOST_SHUTDOWN**. La salida 0308 sólo se
   confirma cuando desaparecen tanto el nodo hidraw como su padre USB físico;
   la PSP puede volver al XMB con el cable conectado.

   Un veto conocido de UDisks permite volver a intentar el desmontaje de forma
   explícita. Si hubo timeout o caída después de solicitarlo, la recuperación
   sólo acepta un desmontaje que pueda demostrar mediante el recibo y el estado
   actual; no repite una operación incierta. Una orden terminal pendiente se
   observa sin volver a enviarla ni repetir copias. Un remount, otra tarjeta,
   otra sesión o un registro ilegible mantiene bloqueada la salida.

   No se usa PowerOff, extracción forzada, desmontaje perezoso ni desvinculación
   del driver USB. Se acredita un desmontaje y la retirada lógica del dispositivo;
   no se afirma que el puerto haya perdido alimentación. La compatibilidad
   anterior 0303/0304 sólo acredita SAFE y desmontaje; para la salida con cable
   conectado utiliza el firmware actual 0308.

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
de sustituirlos; no se usa el mapeo DirectInput de Windows. Antes de crear el
proceso se reenumera SDL y se comprueba de nuevo la ruta, GUID, descriptor e
índice de la PSP. Un hotplug que cambie el índice cancela el lanzamiento y
conserva la devolución pendiente. El comportamiento frente a desconexiones
durante la inicialización del propio emulador todavía requiere aceptación real.

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

## Sonidos, gráficos y límites del port

Los sonidos originales de 0308 se importan con las mismas comprobaciones de
manifiesto, SHA-256 y PCM del host Windows. Linux los reproduce con `pw-play`,
`paplay` o `aplay`, por ese orden; sólo se termina el proceso de sonido creado
por el Dock. Sin backend de audio o sin assets originales válidos, la interfaz
permanece silenciosa. No se incluyen archivos de sonido de Sony.

Los ajustes gráficos por juego se aplican al INI privado con copia de respaldo,
OpenGL/Vulkan y las opciones de textura de PPSSPP. La detección automática de
monitor/GPU todavía es la de Windows: en Linux se muestra un monitor genérico
de referencia 1920×1080. Selecciona calidad **Manual** y ajusta su escala para tu
pantalla hasta validar detección nativa. Los paquetes HD y el generador Lanczos
comparten la implementación existente; el procesamiento PNG opcional requiere
Pillow. La descarga/preparación del ejecutable Windows Real-ESRGAN no constituye
un backend Linux validado.

La CI genera `PSP-Dock-Linux-preview.tar.gz` y su SHA-256 después de las pruebas.
Contiene el host Python, XMB, lanzador ejecutable, regla udev, documentación y
licencias. Necesita los requisitos del sistema anteriores; no incorpora Python,
PPSSPP, RetroArch, juegos ni firmware PSP. Extrae el archivo y ejecuta
`./PSP-Dock-Linux/start-linux.sh` como usuario normal.

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
PSP_DOCK_TEST_NO_REAL_USB=1 python3 -m unittest discover -s tests -p 'test_*.py' -v
PSP_DOCK_TEST_NO_REAL_USB=1 PSP_DOCK_CHROME=/usr/bin/google-chrome node --test 'tests/*.test.js'
```

El guard desactiva descubrimiento real y aperturas predeterminadas de HID.
Las pruebas inyectan árboles sysfs, tablas de montaje, respuestas ioctl,
procesos y ejecutables ELF sintéticos; no arrancan emuladores reales ni
escriben en configuraciones globales. La CI además ejecuta regresión Windows,
flujo de producto, guardados retro, migración y todas las suites JavaScript del
XMB, incluidas las que abren un Chromium real. Los adaptadores Linux se prueban
en Ubuntu 24.04; la misma matriz ejecuta las regresiones Windows en Windows
2022. El cierre y los permisos requieren además una comprobación física posterior.

## Fuentes del contrato

- [PPSSPP v1.20.4: NativeApp.cpp y rutas Linux](https://github.com/hrydgard/ppsspp/blob/v1.20.4/UI/NativeApp.cpp).
- [PPSSPP v1.20.4: SDLJoystick.cpp y códigos del mando](https://github.com/hrydgard/ppsspp/blob/v1.20.4/SDL/SDLJoystick.cpp).
- [Linux: API hidraw](https://docs.kernel.org/hid/hidraw.html).
- [SDL2: SDL_JoystickPathForIndex](https://wiki.libsdl.org/SDL2/SDL_JoystickPathForIndex).
- [UDisks2: udisksctl](https://storaged.org/doc/udisks2-api/latest/udisksctl.1.html).
- [Libretro: autoconfiguración y SDL2](https://github.com/libretro/docs/blob/master/docs/guides/controller-autoconfiguration.md).
