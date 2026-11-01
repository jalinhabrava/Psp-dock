# Controlador compuesto PSP Dock: evidencia y siguiente ensayo

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
revisión 0x0203, con avisos de compilación tratados como errores.
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
