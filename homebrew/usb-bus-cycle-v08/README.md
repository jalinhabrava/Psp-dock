# PSP Dock — USB Bus Cycle 0.8 (prueba de laboratorio)

**Estado:** compilación PSPDEV correcta, auditoría de importaciones aprobada
y guardas probadas en Linux con GCC. **NO PROBADO EN LA PSP FÍSICA.**
Este ejecutable no es el dock tipo Switch y no se instalará automáticamente.

## Evidencia previa obtenida en PSP real (USB Status 0.7)

Foto del usuario:

- Load API = Start API = Unload API = 0x041B7371
- Start status = 0x00000000
- USB raw state = 0x00000111
- USB bus driver = 0x00000002 (detenido)
- MS driver = 0x80243005 (SCE_USB_ERROR_NOENT: no existe driver registrado)
- Stop API = Stop status = 0x00000000
- Active = Cable = Linked = 0

El estado del controlador de almacenamiento es normal en esta fase,
porque no se han cargado los módulos de almacenamiento; no es una
indicación de corrupción de la tarjeta de memoria.

## Propósito y límites de 0.8

El primer controlador que necesitamos probar es USBBusDriver, sin cargar
USBStor_Driver ni exponer el FAT al ordenador. El binario está compuesto por
un EBOOT USER sin ninguna importación USB ni kernel y un PRX USER cargado de
forma dinámica. Todo ocurre **una sola vez**, tras la combinación
L+R+X. Pulsar X sin L+R no tiene ningún efecto.

El PRX lee, antes de efectuar ninguna operación, el estado USB, del bus y
del controlador de almacenamiento. Se NIEGA A INICIAR EL BUS si:

1. La consulta USB devuelve error.
2. El cable está conectado, USB activado o la conexión establecida.
3. El bus no está explícitamente detenido (2).
4. El almacenamiento no devuelve detenido (2) ni NOENT (0x80243005).

Si las cuatro guardas pasan, ejecuta exclusivamente:

    sceUsbStart(PSP_USBBUS_DRIVERNAME, 0, 0)
    sceUsbGetDrvState(PSP_USBBUS_DRIVERNAME)
    sceUsbStop(PSP_USBBUS_DRIVERNAME, 0, 0)
    sceUsbGetDrvState(PSP_USBBUS_DRIVERNAME)

La llamada Stop solo ocurre si nuestra propia llamada Start devolvió éxito.
Se capturan los códigos antes/después y también se comprueba al final el
estado USB y del almacenamiento. Nunca se llama a sceUsbActivate,
sceUsbDeactivate, sceUsbStart/Stop USBStor_Driver ni a funciones de memoria
flash o de ficheros. No se modifica firmware, partidas ni plugins ARK.

**Riesgo:** iniciar el bus USB es una operación real de hardware, aun sin
cable ni almacenamiento. Puede fallar o bloquearse; un compilador y un
emulador NO prueban compatibilidad con ARK-5. Si falla la parada, NO repetir
el ensayo: revisar los estados antes de otras operaciones.

## Validación en PC

Compilación: bash homebrew/usb-bus-cycle-v08/build-wsl.sh

El script compila y ejecuta 10 casos C nativos de la misma función de
precondiciones usada por el PRX, y PSPDEV audita con psp-readelf que no
existan imports ForKernel ni USB en el EBOOT, ni ForKernel/USBSTOR en el PRX.

SHA-256:

- EBOOT.PBP: 2d53b122e2ab72039f73c7c34e29e3e0564d80560cf8ba9843b43c8f4ca0cac8
- usbstate.prx: 012d994bb123428a10bc78fc1112f8a0567ec7714cf7547253ba5be9e29ac4b1

## Procedimiento cuando se acuerde realizar prueba física

1. Confirmar que PSP vuelve al XMB normalmente, que no hay sesiones de
   juego ni transferencias y que Windows está conectado por USB nativo.
2. Desde el proyecto Windows, ejecutar Instalar-usb-bus-08.cmd y confirmar
   PREPARAR 08. El script verifica identidad Sony PSP y que USB Status 0.7
   está instalado, hashes y nueva carpeta PSP/GAME/PSPDockBusCycle08.
   No toca ni sobreescribe versiones anteriores.
3. Expulsar el volumen con el procedimiento NORMAL de Windows, salir de
   USB del XMB, **retirar el cable**.
4. Abrir PSP Dock - USB Bus Test 0.8. Confirmar primero que se muestra la
   interfaz y que START+SELECT permite salir, sin pulsar ninguna combinación.
5. Si abre correctamente, reabrir el homebrew y pulsar L+R+X una sola vez.
   Si hay cualquier bloqueo o apagado, no repetir.
6. Fotografiar los resultados completos de Module Start, Guard, BUS Start,
   BUS after on, BUS Stop, BUS after off y Module Unload. Si el resultado
   indica BUS NOT VERIFIED STOPPED, **no intentar conectar USB** antes de
   analizarlo.

La siguiente fase tras un ciclo completo de bus sería estudiar la carga de
driver USBStor_Driver con seguridad para FAT y la posibilidad de switch a HID;
el ciclo tipo Switch sigue sin estar demostrado.
