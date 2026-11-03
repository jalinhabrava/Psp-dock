# PSP Dock — firmware nativo 0308

## Revisión actual: sonidos de la propia PSP

0308 prepara una copia local del recurso XMB de la PSP antes de iniciar USB.
El cliente Windows puede extraer de esa copia los sonidos compatibles durante
el primer inventario. El paquete distribuible incluye el código de exportación,
pero no incluye el recurso de Sony ni sonidos extraídos de él.

| Identidad | Valor de 0308 |
|---|---|
| Módulos USER / kernel | 3.8 / 3.8 |
| Revisión / build | `0308` / `03080001` |
| Launch magic | `PDN8` (`384E4450`) |
| Estado compartido USER / kernel | 320 bytes |
| Protocolo USB / FEATURE | PDK2/v3 / 64 bytes, sin cambio de formato |
| Capacidades | `000F` |
| Informes HID de entrada / salida | 8 / 4 bytes, sin cambio de formato |

### Exportación previa al almacenamiento USB

El kernel comprueba que USB y almacenamiento estén detenidos, carga las cuatro
dependencias Sony obligatorias y vuelve a comprobar el estado. Entonces solicita
por memoria compartida el trabajo al EBOOT USER. Todavía no inicia el bus ni
`USBStor_Driver`. El EBOOT sólo puede leer esta ruta de origen:

`flash0:/vsh/resource/system_plugin.rco`

No monta ni escribe flash. Acepta un recurso con cabecera RCO y tamaño de hasta
128 KiB; la validación del perfil concreto y de los sonidos corresponde al
importador Windows. Publica los bytes originales, sin prefijo ni contenedor, en:

`ms0:/PSP/SYSTEM/PSPDock/xmb/system_plugin.rco`

La ruta queda fuera de `PSP/GAME/PSPDockNative` y de `SAVEDATA`. Si el archivo ya
coincide byte a byte con el origen, no se vuelve a escribir. En otro caso se crea
exclusivamente `.system_plugin.rco.pspdock-tmp` dentro de esa misma carpeta,
se copia y se verifica de nuevo. Después de cerrar todos los descriptores y
sincronizar la Memory Stick se publica mediante rename, seguido de otra
sincronización. Nunca se borra previamente el archivo final. Si el filesystem
rechaza reemplazar un archivo anterior, se conserva y se omite la actualización.

La limpieza sólo elimina el temporal que esta ejecución haya creado con
`O_EXCL`; un temporal preexistente se conserva y provoca una omisión segura.
Origen inexistente, inaccesible o incompatible son resultados opcionales: no
impiden usar el dock cuando toda la limpieza terminó correctamente. Un fallo de
cierre, eliminación del temporal propio o sincronización bloquea el arranque
USB. Ningún resultado inseguro se convierte en una omisión válida.

El kernel espera como máximo diez segundos. Timeout, cancelación o respuesta
inválida revocan la autorización de exportación y nunca arrancan almacenamiento.
Después de un resultado seguro, vuelve a comprobar que USB y los drivers sigan
detenidos antes de iniciar el bus. Una respuesta tardía no vuelve a abrir esa
puerta. Durante USB activo, el EBOOT no realiza operaciones de archivos.
El kernel conserva cero imports de filesystem.

El FEATURE de estado incluye `UI_SOUNDS_READY` (`0x40`) sólo cuando esta
ejecución terminó con READY, junto al nonce actual de la sesión. Windows exige
esa señal antes de importar un recurso nuevo. SKIPPED no la publica: un archivo
de una PSP anterior que permanezca en la tarjeta no demuestra el origen del
recurso de la PSP conectada. La caché local previamente validada puede seguir
usándose sin una nueva extracción. No cambian los 64 bytes ni los opcodes.

### Construcción y evidencia de 0308

`bash homebrew/native-dock/build-wsl.sh` compila en una copia separada en
`/home/<usuario>/projects/psp-dock-build/native-dock-0308` y publica los resultados en
`homebrew/native-dock/dist`. No instala ni abre una PSP. En WSL donde LeakSanitizer
no sea compatible se puede usar `ASAN_OPTIONS=detect_leaks=0:halt_on_error=1`;
AddressSanitizer y UndefinedBehaviorSanitizer siguen ejecutándose.

El harness usa `native_driver.c` real y el exportador real con APIs simuladas.
Cubre finalización diferida, timeout, ACK tardío, cancelación, resultados
inseguros, cambios externos de estado USB, lecturas y escrituras parciales,
verificación de bytes, límites, fallos de cierre/sync y conservación del archivo
previo. Los tests de transporte y exportación se ejecutan normales y con
ASan/UBSan; también se ejecutan los de UI. La compilación MIPS y la auditoría ELF
verifican la ABI PDN8, las rutas compiladas, los contratos `PDEX/1` y `PDXG/1`,
la lista acotada de imports USER y la ausencia de filesystem en el kernel.
La auditoría general del EBOOT permanece separada del adaptador de exportación.

La auditoría registra `physical_export_verified=false`: estos tests no prueban
que el firmware real permita leer ese recurso desde USER ni que la copia y el
sonido funcionen en hardware. La validación física de 0308 sigue pendiente.
La identidad de protocolo, la propiedad de almacenamiento Sony y el handshake
`HOST_BEGIN` continúan siendo obligatorios antes de usar el volumen en Windows.

## Registro histórico: diseño de 0302

Las secciones siguientes conservan la explicación y la evidencia de 0302.
Sus números de versión y estado compartido son históricos; la tabla anterior
y los headers/auditoría de 0308 definen la revisión actual.

El nombre visible en XMB y en la aplicación es **PSP Dock**. La revisión, los
módulos y códigos USB sólo aparecen en diagnóstico o al producirse un error.
El botón de nota musical abre/cierra el diagnóstico local; no se transmite al
juego y no cambia los ejes del mando. Los assets de esta variante están en
`assets/` y se incluyen exactamente en el PBP. No se modifica la variante 0207.

Sony `USBStor_Driver` mantiene la propiedad de la Memory Stick durante toda la
sesión. Nuestro kernel sólo añade HID y control de sesión al bus. La aplicación
PSP no abre archivos, directorios ni sectores mientras USB está activo. Las
fases son lógicas: las copias pueden bloquear el mando sin reenumerar USB ni
compartir la FAT entre PSP y Windows.

## Evidencia y motivo de esta revisión

En la observación física de 0301, Windows enumeró almacenamiento Sony en MI_00,
HID en MI_01 y el volumen real bajo el mismo padre USB. El bus Sony anunció
**bcdDevice=0100**, aunque el descriptor aportado por nuestro HID llevaba 0301.
Por ello la protección de revisión del cliente detuvo el inicio: la PSP tenía
USB configurado, pero el cliente Windows nunca había confirmado una sesión.
La fase STORAGE inicial no era evidencia de sincronización.

0302 conserva la composición nativa e identifica el firmware mediante un
FEATURE de 64 bytes, independiente del bcdDevice global. Sólo `HOST_BEGIN`,
posterior a la vinculación del dispositivo en Windows, confirma la sesión.
Leer descriptores o `GET_FEATURE` nunca inicia trabajo ni habilita botones.
**La nueva identidad y sesión 0302 requieren su propia prueba física.** Las
pruebas con dobles y la auditoría MIPS no la sustituyen.

## Flujo del producto

1. Abrir PSP Dock en Windows. Espera la PSP; no se instala un servicio ni una
   tarea residente y no queda un monitor al cerrar la aplicación.
2. Abrir **PSP Dock** en la PSP, con el cable retirado, y pulsar X. Conectar
   cuando aparece LISTO. La PSP muestra **Esperando a Windows** hasta el ACK de
   `HOST_BEGIN`.
3. Windows comprueba el FEATURE y la identidad de firmware, vincula HID y
   volumen al mismo padre USB y confirma el volumen esperado. Dentro de la
   intención de inventario registrada envía `HOST_BEGIN` y espera su ACK.
4. `INVENTORY` informa **Sincronizando PSP** mientras lee juegos y partidas.
   `CHOOSING` presenta partidas / juegos / todo / continuar sin copiar, y
   habilita cruceta/X para elegir. `SYNC_IN` pausa el mando durante las copias.
5. `CONTROLLER` habilita navegación/juego. Una copia posterior usa `COPYING` y
   vuelve a `CONTROLLER` al terminar; el mando permanece neutral durante la copia.
6. Salir usa `EXIT_SYNC`: se devuelven únicamente partidas y datos compatibles
   de emuladores, nunca el catálogo completo de juegos. `SAFE` indica que el
   cliente terminó su trabajo; Windows aún debe completar su expulsión normal.
7. Sólo después de retirada física del cable durante 500 ms la PSP detiene
   USB, drena callbacks y permite O para regresar al XMB.

Los estados instantáneos sin adaptador de formato verificado no se convierten
ni se sincronizan como si fueran compatibles. Su respaldo opaco en el PC sirve
como copia de seguridad, no como conversión para otro emulador.

START+SELECT durante un segundo publica `EXIT_REQUESTED`, sin cortar USB.
Antes de confirmar Windows, la pantalla indica **Conexion cancelada**, expulsar
la PSP en Windows y después retirar el cable. Si no hay cable se cierra sin
esperar Windows. Tras una sesión real la solicitud queda pendiente de que el
cliente termine juego/copias y cierre normalmente. Después de un reset que ya
había tenido sesión, la UI pide revisar Windows, sin afirmar que nunca hubo
trabajo. El latch de salida no se borra al recibir `HOST_BEGIN` ni al reconectar.

## PDK2/v3: contrato exacto de 64 bytes

Entrada del mando: **8 bytes**; salida: **4 bytes**, consumida sin efecto;
FEATURE: **64 bytes**. Windows añade report ID cero y obtiene tamaños **9/5/65**.
No hay nuevos report IDs. `native_report_descriptor.h` es independiente del
header heredado de 0104/0207. Formato LE completo:
`struct.Struct('<4sBBBB16sIi4sHHIBBHQQ')`.

| Offset | Campo | Valor |
|---:|---|---|
| 0 | magic, 4 bytes | `PDK2` |
| 4 | versión, u8 | 3 |
| 5 | operación, u8 | STATUS=1; SET_PHASE=2; HOST_BEGIN=3; HOST_PROGRESS=4 |
| 6 | fase, u8 | BOOT=0; STORAGE=1; CONTROLLER=2; COPYING=3; EXIT_SYNC=4; SAFE=5; ERROR=255 |
| 7 | flags, u8 | CONNECTED=1; PENDING=2; SONY_STORAGE=4; EXIT_REQUESTED=8; HOST_CONFIRMED=16 |
| 8 | sesión, 16 bytes | Nonce no nulo por arranque/generación; correlación, no autenticación |
| 24 | secuencia, u32 | Último comando aceptado; inicial 0 |
| 28 | resultado, i32 | IDLE=0; QUEUED=1; APPLIED=2; negativo=fallo |
| 32 | magic de identidad | `NAT3` |
| 36 | firmware_revision, u16 | `0302` |
| 38 | capabilities, u16 | `0007`: native storage=1; host begin=2; host progress=4 |
| 40 | build_id, u32 | `03020001`, discriminador de build/ABI, no firma criptográfica |
| 44 | activity, u8 | WAITING=0; INVENTORY=1; CHOOSING=2; SYNC_IN=3; COPY_GAME=4; CONTROLLER=5; SYNC_OUT=6; SAFE=7 |
| 45 | progress_unit, u8 | UNKNOWN=0; BYTES=1; FILES=2 |
| 46 | reservado, u16 | 0 |
| 48 | completed, u64 | Cantidad realmente completada |
| 56 | total, u64 | Cantidad total realmente conocida |

Windows puede aceptar el contenedor USB `054C:01CD` con bcdDevice `0100` o `0302`
sólo después de validar tamaños, protocolo y toda la identidad explícita. Una
revisión USB por sí sola no identifica el build. El nombre, la letra de unidad
y el número MI tampoco prueban la pertenencia del volumen.

Todos los comandos llevan sesión e identidad exactas, flags y resultado cero,
reservados cero, y siguiente secuencia sin desbordamiento. `HOST_BEGIN` y
`SET_PHASE` llevan actividad/unidad/contadores cero. `HOST_BEGIN` sólo acepta
STORAGE sin claim previo; permite una salida solicitada y no limpia ese latch.
Se publica HOST_CONFIRMED únicamente cuando el worker aplica el comando tras
completar EP0 y drenar los informes antiguos del mando.

`SET_PHASE` exige claim. `HOST_PROGRESS` también lo exige, además de fase actual
exacta y actividad válida. En STORAGE se admiten INVENTORY/CHOOSING/SYNC_IN; en
cada otra fase se admite su actividad correspondiente. Actualiza sólo la UI:
no concede acceso FAT, cambia fase, inicia almacenamiento ni prueba expulsión.
UNKNOWN exige contadores cero; BYTES/FILES exigen total mayor que cero y
completed menor o igual a total. La PSP muestra porcentaje sólo en ese último
caso, con división exacta sin overflow y lectura coherente de los 64 bits.

Sólo hay un comando pendiente. Una repetición byte a byte de la última
secuencia conserva su ACK. Reutilizar secuencia con distinto opcode, fase,
actividad o contador se rechaza. Un comando malformado no sustituye el ACK
aceptado. El host confirma nonce/secuencia/fase/claim, APPLIED y ausencia de
PENDING antes de continuar. Una entrega incierta requiere reconciliación;
no autoriza repetir operaciones sobre archivos.

| Fase actual | Destinos nuevos permitidos |
|---|---|
| STORAGE / CONTROLLER / COPYING | STORAGE / CONTROLLER / COPYING / EXIT_SYNC |
| EXIT_SYNC | CONTROLLER / SAFE |
| SAFE | Ninguna otra fase durante esa sesión |

La fase actual también se puede pedir con una secuencia nueva. No hay salto
directo STORAGE→SAFE. La cancelación temprana puede confirmar `HOST_BEGIN`,
pedir EXIT_SYNC y SAFE sin copiar, y efectuar la expulsión normal en Windows.
SAFE nunca desactiva el USB mientras el cable esté presente.

Errores: FRAME=-1, SESSION=-2, SEQUENCE=-3, BUSY=-4, TRANSITION=-5, LINK=-6,
HANDSHAKE=-7, PROGRESS=-8. Al desconfigurar se invalida el claim y se neutraliza
el mando. Un comando pendiente termina en LINK. Sólo cuando se drenan callbacks
de la generación antigua se crea otro nonce, STORAGE y secuencia0; el cliente
debe vincular de nuevo y resolver cualquier intención anterior. Durante el
drenaje puede verse la fase anterior con CONNECTED=false, claim=false y
actividad WAITING; eso no autoriza una operación.

## Arranque y propiedad

El EBOOT es USER y sólo carga `ms0:/PSP/GAME/PSPDockNative/docknative.prx` antes de
activar USB. El kernel carga y arranca en orden los PRX oficiales `semawm`,
`usbstor`, `usbstormgr`, `usbstorms`. Cada uno sigue siendo obligatorio. Sony debe
haber registrado `USBStor_Driver` y estar detenido antes de iniciar el bus.
No se cargan Boot/Pcact/NPDRM/EFlash/USBDevice ni se usan API de filesystem,
sectores, redirección, flash o UMD. Esta variante está dirigida a la PSP de
desarrollo con Memory Stick extraíble; no añade soporte PSP Go/EFlash.

0300 falló en StartModule de `usbstorboot.prx`, dependencia5/etapa3, error
8002013C LIBRARY_NOTFOUND. En 6.61 ese módulo importa Pcact/NPDRM de activación,
mientras que los cuatro módulos del transporte no importan Boot y `usbstor`
registra su driver por sí mismo. 0301 lo retiró de la cadena obligatoria y se
observó la composición nativa real. No se ignora un fallo de las cuatro
bibliotecas necesarias ni se inventa un NID para ocultarlo.

0302 usa módulos versión3.2 y launch magic `PDN2` (`324E4450`). Launcher/kernel
mezclados con 0300/0301 se rechazan antes de crear workers. Módulos prestados no
se paran/descargan; sólo se detiene el bus iniciado por esta ejecución. Si un
Sony PRX propio no admite descarga se conserva junto a sus dependencias sin
mantener callbacks del HID propio. Un error de mando, timeout, flag SAFE o
estado USB desconocido jamás autoriza cortar el volumen conectado.

Sony storage inicia antes que HID, que solicita interfaz automática. El bus
encamina peticiones y se registra wIndex para diagnóstico, sin exigir cero.
EP0 tiene buffer exclusivo de256bytes y control de generación. No se ejecutan
órdenes tardías sobre otra sesión. Los informes antiguos se cancelan/drenan
antes de ACK; después de cambiar de pantalla se deben soltar botones y centrar
el analógico para armar nuevamente el mando.

## Construcción y verificación

`bash homebrew/native-dock/build-wsl.sh` compila una copia separada en
`/home/<usuario>/projects/psp-dock-build/native-dock-0302`. No instala ni abre la PSP.
Incluye título exacto **PSP Dock**, ICON0/PIC0/PIC1 locales y sus licencias. No lee
arte desde ms0 cuando Sony USBSTOR ya está activo.

El harness compila `native_driver.c` real con dobles asíncronos. Cubre orden y
propiedad, fallos de cada dependencia obligatoria, Boot8002013C que no se carga,
registros Sony, mezclas de launcher, EP0 exclusivo, Feature64, GET sin claim,
ACK/replay, progreso64bit, input sólo tras claim y elección/mando, diagnóstico
sin fuga a juego, reset con nonce nuevo y drenaje pendiente, salida temprana y
salida real sin cortar USB. Se ejecuta normal y ASan/UBSan. Los tests de UI
cubren estados anteriores y posteriores al handshake, errores, instrucciones
de cancelación, porcentajes reales/overflow/lectura parcial y cierre al XMB.

`audit_elf.py` revisa los ELF MIPS, versión3.2, ABI/punteros/alineación,
descriptor64 y tamaños8/4/64 calculados, identidad NAT3 y policy ABI compiladas,
imports USER/kernel, cuatro dependencias exactas y ausencia de las API
prohibidas. PARAM.SFO se parsea para verificar TITLE exacto y se comparan los
assets embebidos byte a byte. El JSON distingue identidad del protocolo y
revisión USB, y mantiene la validación física de0302 como pendiente.

La prueba siguiente debe verificar identificación PDK2/v3, confirmación de
Windows, inventario, elección con mando, copia verificada, vuelta al mando,
sincronización final, expulsión y vuelta al XMB. El código nunca eleva un test
con dobles a prueba de comportamiento físico.

## Fuentes de implementación

- PSPSDK `src/usb/pspusbbus.h`, ABI real de drivers y callbacks:
  <https://github.com/pspdev/pspsdk/blob/master/src/usb/pspusbbus.h>
- ARK-5 `extras/menus/arkMenu/src/usb.cpp`, referencia de uso de USB Sony;
  copia consultada en `references/ark-usb/menu-usb.cpp`. Ese código no comprueba
  el resultado de StartModule; su listado de PRX no demuestra que Boot arranque.
- PSPLibDoc, imports/exports extraídos de firmware 6.61, commit
  `c2834ef4abf881b95fb2eb798b85bb94cf195eb6`:
  [semawm](https://github.com/Spenon-dev/PSPLibDoc/blob/c2834ef4abf881b95fb2eb798b85bb94cf195eb6/PSPLibDoc/6.61/Import/kd/semawm.xml),
  [usbstor: registro USBBus](https://github.com/Spenon-dev/PSPLibDoc/blob/c2834ef4abf881b95fb2eb798b85bb94cf195eb6/PSPLibDoc/6.61/Import/kd/usbstor.xml),
  [usbstormgr](https://github.com/Spenon-dev/PSPLibDoc/blob/c2834ef4abf881b95fb2eb798b85bb94cf195eb6/PSPLibDoc/6.61/Import/kd/usbstormgr.xml),
  [usbstorms](https://github.com/Spenon-dev/PSPLibDoc/blob/c2834ef4abf881b95fb2eb798b85bb94cf195eb6/PSPLibDoc/6.61/Import/kd/usbstorms.xml),
  [Boot: imports Pcact/NPDRM](https://github.com/Spenon-dev/PSPLibDoc/blob/c2834ef4abf881b95fb2eb798b85bb94cf195eb6/PSPLibDoc/6.61/Import/kd/usbstorboot.xml).
- PSPSDK `pspkerror.h`, definición del código `8002013C`:
  <https://pspdev.github.io/pspsdk/pspkerror_8h_source.html>.
- uOFW `src/kd/usbacc/usbacc.c`, encaminamiento de peticiones de interfaz por
  USBBus, commit `28306bb5bd4fcdcc0c1d469bf1f3bdb885dde3e9`:
  <https://github.com/uofw/uofw/blob/28306bb5bd4fcdcc0c1d469bf1f3bdb885dde3e9/src/kd/usbacc/usbacc.c>
- FuSa GAMEPAD `97a1fccb8cb561b14964de4d4dbab4fcd8618297`, descriptor y mapping
  adaptados a partir de `homebrew/bridge-hid`, GPL-3.0.
