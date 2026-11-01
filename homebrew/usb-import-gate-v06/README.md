# USB Import Gate 0.6 — corrección del ciclo de vida del PRX

Estado: compilado y validado de forma estática en Windows/WSL, todavía
NO instalado ni ejecutado en la PSP real. No sustituye Import Gate 0.5.

## Hallazgo de Import Gate 0.5

El usuario consiguió cargar el EBOOT y hacer una petición al PRX. Los
cuatro valores comunicados (sin etiquetas completas) fueron:

- 0x041C3071
- 0x00000111
- 0x8002012E
- 0xFFFFFFFF

El SDK identifica 0x8002012E como SCE_KERNEL_ERROR_UNKNOWN_MODULE.
El valor 0x111 es compatible con un estado USB inactivo: los bits de
activo, cable y conexión (0x200, 0x020, 0x002) están a cero.

El antiguo usbstate.c devolvía directamente sceUsbGetState() desde
module_start(). Esa respuesta es parte del protocolo de ciclo de vida
del gestor de módulos y no se debe usar como transporte arbitrario de
datos. Es una explicación plausible de que después no se pueda detener
ni descargar el módulo. Sin etiquetas de pantalla completas no se puede
afirmar qué campo individual mostró cada valor.

## Corrección arquitectónica

- El EBOOT es USER y no importa ninguna función USB/USBSTOR ni ForKernel.
- El PRX es USER e importa solo sceUsb. module_start recibe un mensaje
  de dos palabras con magic y dirección del resultado en memoria USER,
  valida la alineación y el sentinela, y guarda allí sceUsbGetState().
- module_start devuelve 0 para indicar éxito de ciclo de vida (separado
  del estado USB). module_stop también devuelve 0.
- El EBOOT muestra: resultado de API Start, start_status del callback,
  estado USB, resultado API Stop, stop_status y resultado API Unload.
- Solo intenta Stop si Start devuelve el ID de módulo residente y solo
  intenta Unload si Stop devuelve éxito.
- Una sola operación por sesión, después de pulsar X. No inicia ni activa
  USB, no monta USBSTOR ni accede a archivos o firmware de la PSP.

La comunicación mediante dirección USER compartida está documentada en
el código pero necesita validación real en PSP. Un build correcto no
demuestra que funcione en ARK-5.

Compilar con homebrew/usb-import-gate-v06/build-wsl.sh. Comprueba con
psp-readelf que no haya imports USB/ForKernel en EBOOT, ni ForKernel/
USBSTOR en PRX.

SHA-256 EBOOT.PBP:
e183d8c95aaf5bdbf1992c5667e0ffeece781cfb15f656ad6729d6b5d41d7b21

SHA-256 usbstate.prx:
358554a1c490be4f3bd08c8d8ea542b0ec187609ecee0b51973312b488a40a9b

Instalador preparado pero SIN ejecutar: Instalar-import-gate-06.cmd.
Requiere una PSP Sony conectada por USB nativo y confirmación PREPARAR 06.
Crea PSP/GAME/PSPDockUsbGate06, sin sobrescribir PSPDockUsbGate (v0.5).

Próxima validación física en dos fases: instalar y abrir 0.6 SIN pulsar
X para comprobar que dibuja y sale con START+SELECT; solo después probar
una lectura USB al pulsar X y copiar todas las líneas con etiquetas.
Si se apaga, NO repetir. Expulsión experimental y modo HID suspendidos.
