# PSP Dock K1 — kernel USB observer (solo compilación)

Este es un PRX kernel **nuevo, aislado e inerte para el Dock**. Se limita
a leer USB con `sceUsbGetState` y `sceUsbGetDrvState`, tras una espera
de tres segundos. Guarda el resultado **solo en memoria** y lo publica
a través de una exportación kernel de solo lectura:
`pspDockK1Read(PspDockK1Snapshot*)`.

Nunca instala controladores, cambia modos, modifica flash, toca FAT,
monta discos, envía HID, lee partidas ni escribe archivos de telemetría.
La estructura informa `capabilities=0`, y **NO sirve para autorizar
USBSTOR→HID ni la expulsión de Windows**.

No se instala automáticamente, no dispone de instalador y no debe
copiarse a `SEPLUGINS` en esta fase. El observador USER VSH ya validado
en la PSP real debe mantenerse intacto. Esta prueba K1 solo verifica
que se puede **compilar** un observador kernel de estado con imports
USB estrictamente de consulta. La carga en VSH, las exportaciones
de kernel y la compatibilidad con el ARK-5 de esta PSP **NO han sido
probadas físicamente**. Cualquier ensayo futuro requiere revisión,
backup, control de versión y vía de recuperación.

Compilar sin tocar la PSP:
`wsl.exe -d Ubuntu-22.04 -- bash <ruta>/build-wsl.sh`.
