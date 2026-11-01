# PSP Dock USB Probe 0.1

**CRASH REPORTADO EN PSP REAL, NO EJECUTAR NI REINSTALAR.** El usuario
observó pantalla negra y apagado. El EBOOT original está archivado en
legacy-unsafe/ dentro del proyecto de Windows (no se ha alterado su copia
física de PSP). La alternativa de diagnóstico empieza por
homebrew/screen-check y solo después homebrew/usb-steps. Consultar
docs/FALLO-HOMEBREW-PSP.md. Este README se conserva como antecedente histórico.

Diagnóstico de laboratorio para PSP con CFW. Arranca USBSTOR al abrirse y observa estados/notificaciones en RAM y pantalla. No lee ni escribe archivos mientras ofrece almacenamiento. No implementa todavía la conmutación a mando ni interpreta bits de notificación desconocidos.

Se usa para comprobar si la expulsión segura de Windows produce un evento distinguible en la PSP con el cable conectado. Un bit de conexión por sí solo no acredita liberación de FAT; hay que contrastar expulsión correcta, veto y reset. No activar transiciones automáticas basándose solo en este diagnóstico.

Compilar con PSPSDK (`make`) y copiar `EBOOT.PBP` a `PSP/GAME/PSPDockProbe/`. En la prueba inicial hay que abrirlo desde Juegos; ese paso de laboratorio no define la UX diaria del producto. La salida START+SELECT se permite solo sin cable detectado.

Inicio USB derivado del ejemplo BSD de PSPSDK `src/samples/usb/storage/main.c` (John Kelley/PspPet), con licencia adjunta. No instala plugins ni escribe en flash. Compatibilidad ARK-5/6.61 pendiente de prueba física.
