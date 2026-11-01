# PSP Dock USB Import Gate 0.5 — diagnóstico con carga diferida

**Motivo:** PSPDockProbe 0.1 y USB Step Probe 1.0 provocaron pantalla
negra/apagado en la PSP-2003 real antes de pintar nada. El EBOOT de pantalla
sin USB sí funciona en esa consola.

Comparando símbolos de los ELF con psp-readelf se observó que el EBOOT
defectuoso importaba ModuleMgrForKernel, LoadExecForKernel,
ThreadManForKernel, SysclibForKernel y sceUsbstorBoot aun cuando se
declaraba PSP_MODULE_USER. El importador intenta resolver esas bibliotecas
ANTES de entrar en main(). Esta es una **hipótesis sólida sobre el fallo**,
no una traza física que demuestre una causa exclusiva.

Esta alternativa separa el programa en:

1. **EBOOT.PBP:** módulo/hilo USER, sin importaciones USB/USBSTOR ni ForKernel.
   Presenta su interfaz inmediatamente y solo sale al XMB con START+SELECT
   hasta que se pulse X.
2. **usbstate.prx:** módulo USER, solo importa sceUsb y llama a
   sceUsbGetState para devolver estado al iniciador. No inicia, activa,
   desactiva ni detiene drivers, ni escribe en flash o Memory Stick.

Si la carga diferida falla, el iniciador puede mostrar errores del gestor
de módulos; no obstante, **ningún software no validado garantiza que la
consola no pueda bloquearse**. La primera prueba física debe limitarse a
ver que el EBOOT principal pinta y sale, **SIN pulsar X todavía**.

Se compila con build-wsl.sh, que aborta si psp-readelf encuentra importaciones
ForKernel/sceUsb/sceUsbstor en el EBOOT, o kernel/USBSTOR en el PRX.

SHA-256 EBOOT.PBP:
7586403b47b316db1126bcce05b899f84864a2b9ebbdd4c31bb3ae0964240125

SHA-256 usbstate.prx:
09e39d1459463cb043eb1b0f7a8a8cb472bedd7b856746011da5be47f70c1151

**Preparado pero no instalado** tras el segundo apagado. Instalador nuevo:
Instalar-import-gate-PSP.cmd, solo cuando la PSP haya arrancado normalmente y
vuelva a montar su almacenamiento USB nativo en Windows. Requiere confirmación
PREPARAR y copia dos archivos nuevos a PSP/GAME/PSPDockUsbGate; se niega a
sobrescribir carpetas existentes. NO se ha ejecutado la prueba en PSP real.

Última evidencia en PC: compilación PSPDEV correcta, auditoría de importaciones
correcta, PPSSPP 1.20.4 cargó el EBOOT y mostró su título de ventana sin
apagarse durante cinco segundos (sin pulsar X). No extrapolar ese resultado
a compatibilidad con ARK-5 ni a la carga diferida del PRX.
