# USB Driver Status 0.7 — lectura pasiva de controladores

**Estado:** compilado con PSPDEV, auditado por psp-readelf, comprobado
al arrancar en PPSSPP aislado. **No probado en la PSP física** ni
instalado; Import Gate 0.6 se conserva sin modificar.

## Punto de partida confirmado

Foto real en PSP-2003 con ARK-5: Import Gate 0.6 ejecutó con éxito
Load / Start / Stop / Unload de un PRX de usuario, con Start status 0
y Stop status 0, y leyó USB raw state 0x00000111.
Pantalla: Active 0, Cable 0, Linked 0.
No demuestra todavía arranque de USBSTOR ni reenumeración HID.

## Propósito de 0.7

Usar exactamente el mismo patrón de PRX USER y memoria compartida,
añadiendo otras dos consultas, todas de solo lectura:

- sceUsbGetState() — estado USB general.
- sceUsbGetDrvState(PSP_USBBUS_DRIVERNAME) — estado de USB bus.
- sceUsbGetDrvState(PSP_USBSTOR_DRIVERNAME) — estado del driver de almacenamiento.

PSPSDK documenta para GetDrvState: 1 = driver iniciado, 2 = detenido;
cualquier otro valor necesita interpretarse como resultado bruto.
NO se cargan ni arrancan módulos, no se inicia ni activa USBSTOR,
no hay expulsión Windows ni operaciones sobre la tarjeta o el flash.

El EBOOT principal no importa sceUsb/USBSTOR/ForKernel; el PRX solo
importa sceUsb. El código es distinto al EBOOT anterior y se distribuye
en carpeta nueva para mantener íntegro el v0.6 probado en la consola.

Archivos y SHA-256:

- dist/EBOOT.PBP: 0b45940b6281884825dbf7b0dc93a0ad445d8e6b6b052f072b50b5eb390c3cc1
- dist/usbstate.prx: dcb2a33e7418a8f4d48d93485c9d53db2173e0bf059a857caf8f440d80dff457

## Ensayo físico siguiente (con aprobación del usuario)

1. Conectar PSP por USB nativo del XMB; ejecutar Instalar-usb-status-07.cmd
   en la carpeta raíz del proyecto y confirmar PREPARAR 07. El instalador
   verifica la identidad Sony, hashes y la existencia del Import Gate 0.6
   anterior. Crea PSP/GAME/PSPDockUsbStatus07 sin reemplazar nada.
2. Expulsar la unidad con Windows de forma convencional y salir de modo USB.
3. Abrir PSP Dock - USB Status 0.7 y comprobar la primera pantalla; si
   falla o apaga la consola, no repetir.
4. Si la interfaz abre, pulsar X UNA vez, fotografiar los tres estados
   de USB y los resultados Load/Start/Stop/Unload; salir START+SELECT.
   Si la consola se apaga o se bloquea, NO repetir.

El modo tipo Switch (almacenamiento automático ↔ HID, sincronización
bidireccional probada) sigue pendiente.
