# PSP Dock K2 — historial de estados USB (código experimental)

Nuevo componente kernel **solo de lectura**, separado del K1 que ya
está instalado en la PSP real. Observa cada 250 ms tres valores:
`sceUsbGetState`, `sceUsbGetDrvState(USBBusDriver)` y
`sceUsbGetDrvState(USBStor_Driver)`. Guarda en **RAM kernel** únicamente
los últimos 16 cambios de estado, con ticks y una marca de qué campo
varió. Ofrece un snapshot mediante `pspDockK2GetSnapshot` para un futuro
consumidor *kernel*, no para procesos USER.

No cambia USBSTOR/HID, no registra eventos USBStor, no detecta de forma
concluyente la liberación de FAT ni envía comandos a Windows. No escribe
en la Memory Stick ni en flash. `capabilities=0` y no permite expulsión.
Si el módulo se descarga, su historial en RAM se pierde.

**Todavía NO copiar a la PSP ni incluirlo en PLUGINS.TXT**. Primero
demostrar que K1 ON arranca bien en hardware, diseñar cómo consultar
estos datos de forma segura mientras VSH permanece activo y verificar
su compatibilidad con ARK-5. Los tests nativos con `test_timeline.c`
y la compilación PSPDEV solo demuestran el comportamiento del código,
no el funcionamiento físico.

La documentación pública de `sceUsbstorBootRegisterNotify` no detalla
los bits del evento. K2 deliberadamente no lo registra todavía; no se
deduce que un aviso de ese driver sea el ACK de expulsión de Windows.
