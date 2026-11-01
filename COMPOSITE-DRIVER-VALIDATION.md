# Controlador compuesto PSP Dock: evidencia y siguiente ensayo

## Estado actual: USBSTOR + HID 0205 preparado e instalado

La nueva prueba **PSP Dock USB Storage + HID TEST** está copiada y
releída por SHA-256 en `PSP/GAME/PSPDockStorageTest`. Todavía requiere
que el usuario la abra físicamente en la consola; no se ha verificado
su enumeración ni el tráfico simultáneo en hardware.

La prueba incorpora el **thumbnail ICON0, la presentación PIC0 y el fondo
PIC1** existentes de PSP Dock dentro del EBOOT. Se compararon sus bytes
incrustados y dimensiones con los recursos originales antes de instalar.
El instalador publica únicamente una carpeta nueva y exige identidad
de PSP, backup crítico vigente e integridad del paquete.

| Variante | Interfaces | Contenido accesible | Estado |
|---|---|---|---|
| 0205, `054C:01CC` | HID + USBSTOR nativo, MSC `08/06/50` | Disco FAT12 ficticio de 512 KiB | Compilada, auditada e instalada; ejecución física pendiente |
| 0204, `054C:01CB` | HID + interfaz de archivos WinUSB | Servicio de archivos de solo lectura | Compilada y auditada; no instalada en esta prueba |

El identificador `01CC` separa la asociación de drivers de Windows del
mando estable y del prototipo WinUSB. La identidad emitida por el bus
Sony solo se confirmará mediante la captura física de PnP.

### Evidencia de esta revisión

- Compilación PSPDEV de ambas variantes con avisos tratados como errores.
- **104 pruebas focales en Windows superadas**, incluidas las ABI nativas
  HID/WinMM, paquetes, instalación, correlación, interrupciones y diagnósticos.
  La última revisión espera a que HID termine de enumerar antes de abrir
  la única aceptación: 100 casos iniciales y 21 de MSC reejecutados tras
  añadir cuatro casos de llegada tardía/ambigüedad de las interfaces.
- Motor MSC: 13 casos BOT, FAT12/SCSI, límites, residuos y **50.000 entradas
  malformadas**, también bajo AddressSanitizer y UndefinedBehaviorSanitizer.
- Wrapper USB real incluido en un harness con cinco escenarios asíncronos:
  varios buffers, completions pendientes, reset, cancelación y errores.
  La revisión corrigió dos carreras que podían perder una recepción o
  repetir un bloque IN; el harness cubre ambas.
- Auditoría del ELF MIPS: tablas Sony por interfaz, revisión y endpoints;
  **ningún símbolo/import `sceIo*` ni `IoFileMgr` en el kernel MSC**.
- Auditoría USER y verificación exacta de las tres secciones gráficas del PBP.
- Parser FAT12 independiente: `PROBE.DAT` de 150.003 bytes y `SESSION.BIN`
  de 16 bytes comprobados en la imagen de referencia generada.
- Backup crítico revalidado antes de publicar: 42 archivos, 4.908.072 bytes.
- Paquete instalado: cuatro archivos, 319.989 bytes, relectura SHA-256 correcta.

Hashes de los binarios instalados:

```text
EBOOT.PBP    8b4ad4643d287102e069b4a1866af5ecdde9172577eccb0e0d880be6577d3afa
dockmsc.prx  cf1ef97f32ef87862c424435bdfb931da7daa448441b1bef45ce9315fbfcc689
```

### Alcance que queda por demostrar

El disco es generado en memoria y de solo lectura. **No representa la
Memory Stick real y no ofrece juegos, partidas ni devolución de saves.**
La siguiente prueba debe confirmar que Windows monta USBSTOR, recibe HID
y lee los bytes esperados a la vez. El monitor usa archivos sin caché,
exige mismo padre USB y nonce, y solo acepta cambios de controles
observados durante la lectura. Un hash correcto sin movimiento no pasa
la aceptación combinada.

Las pruebas de software no establecen cómo el bus Sony maneja las colas
en endpoints detenidos, la terminación de un envío de 64 KiB ni el estado
EP0 diferido del reset BOT. Esos puntos quedan documentados como pendientes
de hardware; no se han añadido parches de firmware ni APIs supuestas.

Procedimiento y comandos vigentes:
[USBSTOR + HID 0205](homebrew/composite-mscdemo/README.md).

## Ruta alternativa WinUSB: revisión y evidencia previa

## Qué ha cambiado respecto al prototipo de descriptores

La rama `feat/composite-usb-driver` tiene ahora un **controlador kernel
PSP compilable y un cliente WinUSB real de Windows**. No modifica el
frontend de Claude ni la rama de interfaz nativa de PSP.

Parte de la implementación se recuperó de la rama de trabajo USB
existente y se revisó en un worktree aislado, conservando la fuente
original. Se corrigieron después problemas de ABI, registro/cierre de
USB, concurrencia de buffers y tratamiento de errores del cliente.

El controlador mantiene el gamepad HID y el canal BULK de lectura como
dos interfaces del mismo dispositivo. De este modo no necesita que el
PC monte la Memory Stick: solo el firmware de PSP accede al sistema de
archivos. No se envían comandos SCSI ni se manipula flash.

### Fallo de ABI encontrado y corregido

El prototipo anunciaba dos interfaces, pero su tabla de ajustes no tenía
el formato utilizado por el controlador Sony. No basta con un array de
dos punteros: hace falta un registro `{descriptor, alternate, count}`
por interfaz. El fallo podía compilar perfectamente y aun así impedir
enumeración o provocar lecturas inválidas del controlador.

La auditoría `homebrew/composite-readonly/audit_elf.py` analiza el ELF
MIPS final, comprueba ambos registros de 12 bytes, punteros, tamaños,
direcciones de endpoints, alineación y descriptores full/high-speed.
Los tests mutan deliberadamente el binario para demostrar que detecta
la estructura antigua y un endpoint con dirección incorrecta.

### Guardas que siguen activas

- Entrada solo sin USBSTOR activo; no se inicia el almacenamiento Sony.
- Lecturas a rutas acotadas, cero imports de escritura/remoción/renombrado.
- Misma sesión y secuencia, CRC, longitud y relleno comprobados.
- SHA-256 al final de la transferencia y verificación de la copia PC.
- Publicación en nombre nuevo; nunca sobreescritura de un archivo existente.
- El módulo no libera memoria mientras haya peticiones USB pendientes.
- No hay límite artificial de sesión. START+SELECT o cable perdido paran.
- El ensayo no puede declarar identidad de FAT ni devolución de saves.

## Evidencia obtenida en PC

La compilación PSPDEV produce `dockcomposite.prx` y un EBOOT separado,
revisión 0x0204, con avisos de compilación tratados como errores.
El build incluye:

1. Vectores SHA-256 y pruebas nativas de parser, directorios, sesión y EOF.
2. **20.000 iteraciones de mensajes dañados**, bajo AddressSanitizer y
   UndefinedBehaviorSanitizer, sin errores observados.
3. Dos pruebas cruzadas: el servicio C realmente enlazado en el PRX
   transmite 150.003 bytes al cliente Python y se valida el SHA; si
   cambia el archivo de origen, el destino parcial no se publica.
4. Auditoría de imports y tablas del ELF real.

En Windows se han superado **39 pruebas focales** de cliente, aceptación,
ABI, límites, diagnóstico y empaquetado. La regresión completa contiene
**484 casos: 482 pasan en Windows y los dos casos cruzados C/Python se
ejecutan y pasan por separado en WSL**. Las tres pruebas JavaScript del
XMB también pasan. No se ha alterado la interfaz de Claude.

El primer intento desde el checkout limpio encontró dependencias locales
ausentes: binarios históricos, el perfil de controles y un acceso retirado.
Se copiaron exclusivamente esos fixtures de prueba del PC al worktree,
comprobando hashes y manteniéndolos excluidos de Git; no se ejecutaron
instaladores ni binarios históricos. Una clonación limpia sigue necesitando
esos fixtures para la suite histórica completa. Las pruebas de este nuevo
controlador y su build son independientes de las ROMs y partidas reales.

## Todavía NO demostrado

La PSP física no ha ejecutado este controlador revisado. No hay todavía
evidencia de que Windows enlace automáticamente WinUSB, de rendimiento
por el cable ni de lectura simultánea con el HID en hardware real.

Tampoco se implementaron escritura/devolución de guardados por BULK,
apagado de pantalla, arranque automático del producto a partir del
canal nuevo o cierre remoto de la PSP. El cliente y pruebas los marcan
como no disponibles; no se simula su éxito.

## Una sola prueba física agrupada

Con una Memory Stick de prueba respaldada, conservar el PSP Dock estable
y añadir únicamente la carpeta `PSP/GAME/PSPDockCompositeTest` del paquete
verificado. No modificar ARK, plugins ni drivers Windows.

Abrir la aplicación de prueba con USB Sony apagado y cable retirado;
pulsar X y esperar `READY - now connect USB`. Conectar una vez y comprobar
la enumeración con `psp_composite_diagnostics.py --firmware`.

Si hay HID y WinUSB válidos, ejecutar `psp_composite_acceptance.py --run`
con un directorio de salida nuevo del PC. Mover la cruceta mientras se
lee repetidamente el fichero ficticio. La evidencia debe indicar:

- `result=verified`;
- `physical_usb_transfer_verified=true`;
- `same_hid_bulk_session=true`;
- `gamepad_motion_observed=true`;
- cero escritura PSP y SHA-256 idéntico.

Terminar con START+SELECT, observar la salida segura y repetir una
reconexión controlada en una sesión nueva. Si falta WinUSB, falla la
copia o la PSP queda negra, guardar el código y parar ese ensayo;
no reinstalar/expulsar drivers ni repetir operaciones a ciegas.

Los comandos, el formato y las limitaciones están detallados en
`homebrew/composite-readonly/README.md`.
