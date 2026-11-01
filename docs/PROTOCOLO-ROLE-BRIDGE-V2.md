# PSP Dock — puente USB automático y apagado de pantalla

ESTADO: DISEÑO Y CÓDIGO DE SEGURIDAD; SIN CONTROLADOR FÍSICO INSTALADO.

La PSP-2003 con ARK-5 ya actuó como mando HID y controló PPSSPP.
Sin embargo, su homebrew HID-only inició sin almacenamiento y duró
unos 90–105 segundos. No hemos probado la transición automática desde
USBSTOR con el mismo cable, ni el retorno del dispositivo por software.

## El obstáculo principal

El USB Sony nativo aparece como VID054C/PID02D2 y expone la Memory Stick,
pero PSP Dock no dispone todavía de un canal host→PSP INDEPENDIENTE
del FAT compartido para solicitar el cambio de modo y recibir un ACK
auténtico del kernel. La desaparición de la letra G: no prueba que
Windows y PSP hayan liberado por completo su acceso a FAT. Un eject
PnP del nodo padre puede dejar Windows en código 47, esperando quitar
físicamente el cable. No se debe usar a ciegas.

## Arquitecturas candidatas

1. USB compuesto propio: desde un plugin PSP residente en VSH,
   proporcionar una interfaz de control/HID y otra de almacenamiento.
   Así los botones PSP podrían controlar decisiones de sincronización
   ANTES de liberar el medio. El HID no tocaría FAT y el almacenamiento
   solo cambiaría de dueño tras ambos ACK. Requiere nuevo driver,
   descriptores compuestos y verificar si ARK-5/PSPUSB lo admite.
2. Extensión de USBSTOR Sony: agregar un canal vendor de control
   fuera de FAT, con desafío de sesión y reconocimiento de firmware.
   No se ha demostrado que el controlador USB Sony pueda ofrecerlo.
3. Notificaciones del kernel PSP al host: explorar
   sceUsbstorBootRegisterNotify, pero sus señales documentadas no
   equivalen a exclusividad FAT ni a ACK del sistema operativo.

## Secuencia de seguridad antes de activar cualquier HID

1. Reconocer una única PSP Sony, Memory Stick y USB padre.
2. Auditar juegos y partidas. Si todos los saves son idénticos,
   preparar automáticamente SOLO metadatos del PC; con diferencias,
   solicitar decisión explícita.
3. Cerrar toda E/S, suspender los sondeos de volumen del Dock y
   conseguir lock/flush verificados en Windows. Un dismount aislado
   o una expulsión PnP no son suficientes.
4. Transmitir petición por canal NO FAT con sesión y secuencia nuevas.
5. Recibir respuesta genuina del firmware PSP (no un BOOT.TXT).
6. Solo entonces parar USBSTOR y habilitar HID, sin FAT simultáneo.
7. Comprobar descriptor HID PDK1, sesión y mismo puerto USB.
8. Esperar XMB preparado y SOLO ENTONCES apagar backlight.
9. Usar PSP para navegar, elegir juegos, jugar y volver al XMB.
10. Al elegir Salir, cerrar PPSSPP y emitir una orden de retorno
    HID→USBSTOR única, registrada antes de intentar enviarla.
11. Restaurar brillo original en salida, cable perdido, watchdog
    o error, sin bloquear el control USB.
12. Volver a reconocer Sony USBSTOR original y devolver guardados
    desde PC con backup/journal, bloqueando conflictos.

## Pantalla PSP negra sin suspender el mando

PSPSDK local define sceDisplayGetBrightness y sceDisplaySetBrightness,
con nivel 0 como pantalla negra. La compatibilidad de esas llamadas
kernel con ARK-5/6.61 no ha sido comprobada en esta PSP y NO se
invocarán sobre ella sin un ensayo reversible. No usar modo espera:
la suspensión detendría también el HID.

Se ha creado homebrew/display-guard con una política pura en C:
no oscurece si USBSTOR sigue activo o falta ACK; conserva el brillo
original; vuelve a encender y bloquea descarga de módulo cuando
la restauración falla. No es un PRX ni tiene llamadas PSP.

## Próxima prueba física agrupada (solo Memory Stick prescindible)

- Copia completa de los datos de ensayo, plan de restauración y
  recuperación ARK-5 por START comprobada.
- Primero validar observador y control de pantalla sin USB mutante.
- Luego demostrar canal host→PSP y ACK vivo fuera de FAT.
- Después host lock/flush y reenumeración USB sin PnP eject del padre.
- Finalmente retorno one-shot, restauración de brillo, apagado/cable
  inesperado y sincronización con guardados de prueba, nunca originales.

Hasta entonces los adaptadores físicos deben seguir cerrados por defecto;
usb_role_adapter.py y dock_experience.py modelan INTENCIONES,
no cambian USB ni prueban propiedad FAT.

## Fuentes y contexto

- https://pspdev.github.io/pspsdk/pspusb_8h.html
- https://github.com/pspdev/pspsdk/blob/master/src/samples/usb/storage/main.c
- https://learn.microsoft.com/en-us/windows/win32/api/winioctl/ni-winioctl-fsctl_lock_volume
- https://learn.microsoft.com/en-us/windows-hardware/drivers/ifs/fsctl-dismount-volume
- docs/HANDSHAKE-USB-SIN-DESCONECTAR.md
