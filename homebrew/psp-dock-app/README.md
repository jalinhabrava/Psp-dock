# PSP Dock — cliente HID persistente

Este cliente de PSP solo existe para el modo **HID**, reutiliza el
controlador FuSa adaptado y probado físicamente, y no tiene temporizador.

Mientras el cable HID permanezca conectado, el mando seguirá operativo
tantas horas como el usuario quiera. Para salir, mantener START+SELECT
1,5 segundos. El kernel deja de presentar HID si se desenchufa el
cable después de haberlo conectado.

**Límites actuales:** la entrada a este modo sigue siendo manual,
desde Juegos, con USB Sony apagado y cable inicialmente desconectado.
El plugin NO pasa automáticamente desde USBSTOR a HID, NO monta
ni modifica FAT, NO apaga la pantalla y NO enciende almacenamiento
al salir. El puente de roles necesita un protocolo con ACK vivo y
exclusividad FAT, todavía pendiente.

Se compila en WSL mediante build-wsl.sh en una carpeta separada.
Dist contiene EBOOT.PBP, dockcore.prx, bridgehid.prx y su licencia.
El proyecto anterior de HID-only LAB 90s se conserva en PC para
investigación; no forma parte de la instalación definitiva en PSP.

Sin instalaciones silenciosas: antes de copiar a una PSP auténtica,
verificar identificación Sony USBSTOR, serial, ausencia de PPSSPP
activo, backup de estado y hashes, y copiar solo a la nueva carpeta
PSP/GAME/PSPDock. Tras la primera instalación, no modificar
plugins ARK, firmware flash ni las partidas del usuario.

Fuente HID: adaptación de FuSa GamePad, GPL-3.0, créditos
incluidos en LICENSE-FuSa-GPL-3.0.txt.
