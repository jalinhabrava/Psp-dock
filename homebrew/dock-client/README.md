# PSP Dock Client 0.1 · laboratorio

Homebrew que monta USBSTOR, cambia a HID de PSP Dock bajo supervisión y vuelve
a almacenamiento al recibir el comando RETURN_STORAGE desde Windows.

**No es la UX final.** El primer cambio a HID requiere que Windows haya aceptado
la expulsión segura de la PSP con el cable conectado. Si Windows veta esa
expulsión, no se debe pulsar X en la PSP. Este paso manual desaparece solo
después de demostrar un evento fiable y automatizar la transición desde Windows.

Construir con bash homebrew/dock-client/build-wsl.sh. Los binarios EBOOT.PBP y
bridgehid.prx se empaquetan en dist/; para un futuro ensayo físico se colocarían
en ms0:/PSP/GAME/PSPDockClient/. No instalar hasta observar antes PSPDockProbe.
No escribe flash, no instala plugins de arranque y no accede a FAT bajo USBSTOR.
