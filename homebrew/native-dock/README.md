# PSP Dock Native 0300

Variante separada de `composite-mscdemo` 0207. Pide al bus de Sony componer
`USBStor_Driver` (Memory Stick real) y `PSPDockNativeHID` (mando y control de fases).
El módulo Sony mantiene la propiedad del almacenamiento durante toda la sesión.
Nuestra aplicación PSP no abre archivos, directorios ni sectores mientras USB está
activo. Las fases son lógicas: permiten bloquear el mando durante una copia sin
reconectar el USB ni disputar la FAT entre PSP y Windows.

**La composición nativa está preparada para prueba física; una compilación o un
test con dobles no demuestra que Sony la exponga correctamente.** La versión 0207
y su disco ficticio permanecen intactos y sirven de referencia de HID/USBBus.

## Flujo

1. Abrir PSP Dock en Windows; su pantalla espera a la PSP. No se instala servicio,
   tarea al conectar USB ni proceso residente cuando se cierra la aplicación.
2. Abrir **PSP Dock - Memory Stick + Mando 0300** en la PSP, con el cable retirado,
   y pulsar X. Carga los módulos Sony y prepara ambas funciones USB. Conectar sólo
   cuando aparece `LISTO`.
3. Windows vincula el HID y el volumen del mismo dispositivo, inventaría juegos
   y partidas compatibles, y ofrece partidas / juegos / todo / nada. `STORAGE` habilita
   botones para esa elección. La PSP muestra `SINCRONIZANDO PSP`.
4. Windows pide `COPYING` antes de escribir o `CONTROLLER` para navegar/jugar.
   Debe esperar el ACK de fase. En `COPYING` los informes del mando son neutros.
5. Copias posteriores usan `COPYING` y vuelven a `CONTROLLER` al terminar.
6. Salir del dock Windows usa `EXIT_SYNC`: sólo partidas y guardados de emuladores
   compatibles, nunca el catálogo de juegos. Terminado el trabajo se pide `SAFE`, se completan las
   operaciones de cierre/expulsión en Windows y se indica retirar el cable.
7. La PSP detiene USB sólo después de ausencia física durante 500 ms. Entonces
   muestra el resumen y O vuelve al XMB. Un cambio de configuración USB no sale
   de la aplicación.

La sincronización retro admite SRAM de los perfiles reconocidos. Los estados
instantáneos sin un adaptador de formato verificado permanecen en su dispositivo;
su respaldo opaco en el PC no equivale a convertirlos para otro emulador.

`SAFE` significa que el cliente ha terminado su trabajo. **No es prueba de
expulsión de Windows**, y no apaga el almacenamiento. START+SELECT durante un
segundo únicamente publica `EXIT_REQUESTED`; tampoco corta una copia activa.
La UI pide finalizar desde Windows. El cliente puede convertir esa señal en el
flujo normal de salida. La solicitud local permanece hasta desenchufar.

## PDK2: contrato de 32 bytes

Se conserva el descriptor de mando FuSa/PSP Dock: entrada 8 bytes, salida 4 bytes
sin efectos, FEATURE 32 bytes. `hidapi` para Windows añade un byte inicial de
report ID 0, por lo que solicita/envía un buffer de **33 bytes**. El formato del
payload es Python `struct.Struct('<4sBBBB16sIi')`, little endian.

| Offset | Campo | Valor |
|---:|---|---|
| 0 | magic, 4 bytes | `PDK2` |
| 4 | versión, u8 | 2 |
| 5 | operación, u8 | STATUS=1; SET_PHASE=2 |
| 6 | fase, u8 | BOOT=0; STORAGE=1; CONTROLLER=2; COPYING=3; EXIT_SYNC=4; SAFE=5; ERROR=255 |
| 7 | flags, u8 | CONNECTED=1; PENDING=2; SONY_STORAGE_STARTED=4; EXIT_REQUESTED=8 |
| 8 | sesión, 16 bytes | Nueva, no nula, por arranque; correlación, no autenticación |
| 24 | secuencia, u32 | Último comando aceptado; comienza en 0 |
| 28 | resultado, i32 | IDLE=0; QUEUED=1; APPLIED=2; negativo=fallo |

`SET_PHASE` exige sesión exacta, flags 0 y resultado 0. La siguiente secuencia
debe ser `status.sequence + 1`, sin desbordamiento. Una repetición idéntica de la
última secuencia/fase devuelve el mismo resultado; un replay o la misma secuencia
con distinta fase se rechaza. Sólo hay un comando pendiente. Los rechazos no
reescriben el ACK del último comando aceptado; su contador/código aparece en el
diagnóstico PSP. El cliente confirma sesión, secuencia, fase, `APPLIED` y ausencia
de `PENDING` antes de continuar; no basta con que `SET_REPORT` haya terminado.

| Fase actual | Destinos nuevos permitidos |
|---|---|
| STORAGE / CONTROLLER / COPYING | STORAGE / CONTROLLER / COPYING / EXIT_SYNC |
| EXIT_SYNC | CONTROLLER (cancelar salida desde Windows) / SAFE |
| SAFE | Ninguna otra fase durante esa sesión |

Repetir la fase actual con una secuencia nueva es válido. Se parte de `STORAGE`.
No se permite saltar directamente a `SAFE` sin `EXIT_SYNC`. En las transiciones
se cancelan/drenan los informes HID antiguos antes de publicar `APPLIED`. Al
volver a una fase interactiva hay que soltar botones y centrar el analógico para
armar de nuevo la entrada. No se dejan botones pulsados de una pantalla anterior.

Errores de protocolo: FRAME=-1, SESSION=-2, SEQUENCE=-3, BUSY=-4,
TRANSITION=-5, LINK=-6. Una desconfiguración que interrumpe un comando pendiente
marca ese comando `LINK`; el host puede reconciliar el estado y usar la siguiente
secuencia. No hay cambios de archivos ni de propietario FAT en callbacks USB.

## Arranque, identidad y cierre

El EBOOT es USER y sólo carga `ms0:/PSP/GAME/PSPDockNative/docknative.prx` antes de
activar USB. El kernel carga, comprueba y arranca en orden los PRX oficiales
`semawm`, `usbstor`, `usbstormgr`, `usbstorms`, `usbstorboot`. Nunca importa
`sceUsbstorBoot*` antes de cargar esas dependencias ni necesita sus funciones.
No carga `usbdevice.prx`, no selecciona flash/UMD, no usa `usbstoreflash.prx` y no
escribe en flash. Esta primera variante está dirigida a la PSP con Memory Stick
extraíble del desarrollo, no incorpora soporte PSP Go/EFlash.

Cada módulo previamente presente se presta y nunca se para/descarga. Sólo se
detiene un bus que esta ejecución inició. Un fallo de carga/arranque se presenta
con código, etapa e índice de módulo, y revierte únicamente recursos propios.
Si un PRX Sony propio no admite descarga se mantiene residente con sus
dependencias y se muestra `cleanup_error`; esto no mantiene callbacks de nuestro
HID ni impide descargar el controlador una vez drenado y desregistrado.

Se inicia Sony storage antes de HID para permitir a Sony reservar la interfaz 0.
HID solicita una interfaz automática y atiende únicamente peticiones HID que el
bus le ha encaminado, sin exigir `wIndex=0`. Registra el índice observado para
diagnóstico. EP0 tiene buffer exclusivo y generación; reconfiguraciones drenan
transferencias antiguas y no ejecutan comandos de una configuración obsoleta.

Se activa el PID de laboratorio **01CD** bajo el VID suministrado por el bus Sony.
El descriptor propio lleva `bcdDevice=0300`. **Sony puede elegir su descriptor
global** al componer; el PID, revisión, interfaces, endpoints y ruta de control
reales se deben observar en la prueba. No se debe aceptar otro volumen por letra
de unidad ni usar el nombre del dispositivo como prueba de pertenencia. Antes
de copiar, Windows debe vincular la función de almacenamiento y el HID PDK2 a
su mismo padre USB y confirmar que el volumen es la Memory Stick esperada.

## Construcción y pruebas

`bash homebrew/native-dock/build-wsl.sh` compila una copia separada en
`/home/david/projects/psp-dock-build/native-dock-v1`. No instala ni abre la PSP.
Incluye en PBP el título y los `ICON0/PIC0/PIC1` aprobados en 0207 y sus licencias.
Mantiene los archivos del runtime en memoria; no lee imágenes de `ms0:` cuando
Sony USBSTOR está activo.

El harness compila **native_driver.c real**, con dobles asíncronos, y verifica:
orden de arranque, fallos de cada dependencia, rollback, módulos/bus prestados,
EP0 exclusivo, wIndex 1, ACK sólo tras cancelar input, protección frente a replay,
reset de configuración sin salida y prohibición de apagar USB con cable presente
o estado desconocido. Se ejecuta normal y con AddressSanitizer/UndefinedBehaviorSanitizer.

`audit_elf.py` comprueba los ELF MIPS, tablas/punteros/alineación, descriptor HID,
imports de USER/kernel, ausencia de filesystem/Boot/redirección y PBP con assets
exactos. Su JSON marca explícitamente la validación física como pendiente.

La prueba física siguiente debe confirmar simultáneamente montaje nativo real y
HID PDK2, inventario, elección con mando, copia verificada, vuelta al mando,
sincronización final, expulsión y vuelta al XMB tras retirar cable. Los tests del
PC no pueden resolver por sí solos la composición interna de Sony USBBus.

## Fuentes de implementación

- PSPSDK `src/usb/pspusbbus.h`, ABI real de drivers y callbacks:
  <https://github.com/pspdev/pspsdk/blob/master/src/usb/pspusbbus.h>
- ARK-5 `extras/menus/arkMenu/src/usb.cpp`, arranque de módulos Sony sin import
  previo de Boot; copia consultada en `references/ark-usb/menu-usb.cpp`.
- uOFW `src/kd/usbacc/usbacc.c`, encaminamiento de peticiones de interfaz por
  USBBus, commit `28306bb5bd4fcdcc0c1d469bf1f3bdb885dde3e9`:
  <https://github.com/uofw/uofw/blob/28306bb5bd4fcdcc0c1d469bf1f3bdb885dde3e9/src/kd/usbacc/usbacc.c>
- FuSa GAMEPAD `97a1fccb8cb561b14964de4d4dbab4fcd8618297`, descriptor y mapping
  adaptados a partir de `homebrew/bridge-hid`, GPL-3.0.
