# PSP Dock — un único cable USB: HID + archivos sin FAT compartida

Estado: diseño y prototipo ejecutable/compilable en PC, **no probado en hardware**.
Fecha: 1 de noviembre de 2026.

## Qué está demostrado

Físicamente: la PSP-2003 con ARK controla el XMB de Windows y PPSSPP
por HID, permite elegir juegos, abrir menú HOME y volver al launcher.
El homebrew persistente no tiene temporizador.

NO demostrado: cambio Sony USBSTOR ↔ HID con mismo cable, pantalla
PSP apagada al conectar, descarga de ISOs desde PSP por USB BULK
ni devolución de SAVEDATA sin modo almacenamiento Sony. No se
han cambiado los controladores del usuario ni el firmware.

## Enfoque propuesto

En vez de que Windows monte FAT y la PSP tenga que detener/iniciar
USBSTOR, PSP Dock usaría UN dispositivo USB compuesto:

1. Interfaz 0: HID joystick con botones físicamente comprobados.
2. Interfaz 1: vendor-specific BULK con endpoints IN y OUT.
3. La PSP mantiene la propiedad exclusiva de su Memory Stick.
   Windows no monta FAT: solicita los archivos por el canal BULK.
4. El PC consulta lista de juegos, PIC1/SND0 y SAVEDATA, copia ISOs
   por bloques verificados y permite decisiones desde el XMB.
5. Al salir, el PC verifica backups y diferencias de partidas;
   la PSP escribe mediante transacciones y confirma el resultado,
   restaura el brillo, vuelve a su XMB y Windows cierra su Dock.

Este diseño elimina la necesidad de alternar físicamente las funciones
de almacenamiento Sony y mando. Es una hipótesis de arquitectura,
no una capacidad demostrada de PSP Dock actual.

## Prototipo de protocolo PDB2

Archivos Python psp_bulk_protocol.py y C puro
homebrew/psp-bulk-prototype/pdb2_wire.c y pdb2_wire.h:

- Encabezado 40 bytes + CRC32 4 bytes + hasta 60 KiB de contenido.
- Versión, 16 bytes de sesión, secuencia, offset 64-bit y longitud.
- HELLO/HELLO_ACK validan el serial de la Memory Stick en la simulación;
  STAT devuelve tamaño/SHA256; READ devuelve DATA por fragmentos.
- Rechazo de tramas corruptas, secuencias repetidas, sesión equivocada,
  traversal, rutas fuera de juegos/SAVEDATA y sobrescritura de juegos PC.
- Un test de servidor PSP ficticio copió una ISO de varios fragmentos
  verificando SHA256, sin montar FAT como volumen de Windows.
- El parser C compila con GCC y pasa prueba de CRC estándar:
  mensaje 123456789 → CRC CB F4 39 26 (hex CBF43926).

CRC es detección de errores, **no autenticación criptográfica**.
La sesión tampoco demuestra por sí sola identidad del firmware real.
El protocolo es de SOLO LECTURA hoy. La devolución PC→PSP de partidas
necesita consentimiento, copia previa verificada en PC, journal durable
y escritura/rollback probado sobre medio de laboratorio.

## Descriptores USB propuestos

psp_usb_composite.py produce dos interfaces y tres endpoints dentro de
una configuración (longitud 57 bytes):

| Campo | HID | Bulk |
|---|---|---|
| Clase | 0x03 | 0xFF |
| Endpoint | 0x81 IN interrupt | 0x82 IN y 0x03 OUT |
| High speed | 8 bytes / 10 ms | 512 bytes |
| Full speed | 8 bytes / 10 ms | 64 bytes |

homebrew/psp-bulk-prototype/composite_layout.c presenta ese layout
en las estructuras USB de PSPSDK, y **compila a objeto MIPS Allegrex
con psp-gcc**. El objeto se borra sin ser instalado; no contiene
driver registrado, PRX ejecutable ni función de arranque.

La implementación de USB de PSP 6.61/ARK puede tener restricciones
en el número de interfaces y endpoints. La compilación no prueba
enumeración real. En Windows un dispositivo compuesto se separa
en interfaces por usbccgp.sys; HID tendría driver propio, pero la
interfaz vendor necesita WinUSB y un mecanismo de vinculación
compatible (Microsoft OS descriptors o INF autorizado). Esto
está PENDIENTE.

Para un futuro producto distribuible habrá que aclarar el derecho
a utilizar el VID/PID; el prototipo usa el del HID PSP conocido solo
para comparar descriptores. El driver derivado FuSa tiene GPL-3.0.

## Siguiente prueba física reversible

1. Verificar el comportamiento de interfaces/endpoints del kernel
   PSP mediante código revisado y Memory Stick de pruebas.
2. Compilar un driver de demostración con HID + BULK sin operaciones
   de escritura a FAT y con salida START+SELECT y recuperación.
3. Registrar temporalmente la interfaz WinUSB en Windows, sin
   interferir con el HID ni con otros dispositivos.
4. Leer un único archivo ficticio, probar CRC/sha, desconexión,
   reinicio, bloqueo y recuperación. No usar partidas personales.
5. Implementar PC→PSP con backups/versiones/journal antes de
   permitir devolución automática de SAVEDATA.
6. Añadir brillo de pantalla negro/restauración solo cuando el
   host confirme HID+BULK operativos y pueda recuperarse un fallo.

NO instalar todavía el prototipo en la PSP real, ni usar expulsiones
PnP del dispositivo padre, comandos SCSI, flash0 o almacenamiento FAT
simultáneo. Una prueba previa USB Step Probe apagó la PSP y se retiró.

## Fuentes para esta arquitectura

- PSPSDK UsbDriver: https://pspdev.github.io/pspsdk/structUsbDriver.html
- Microsoft USB composite:
  https://learn.microsoft.com/en-us/windows-hardware/drivers/usbcon/descriptors-on-composite-usb-devices
- Microsoft WinUSB:
  https://learn.microsoft.com/en-us/windows-hardware/drivers/usbcon/automatic-installation-of-winusb
- OASIS USB BULK en otra PSP:
  https://andrewaltimit.github.io/oasis-os/journal/05-psp-usb-research.html

El último proyecto demuestra bulk funcional en **otra PSP**, no que
nuestro dispositivo HID+BULK funcione ya en PSP-2003 con ARK.
