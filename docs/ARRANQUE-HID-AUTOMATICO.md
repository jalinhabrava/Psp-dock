# PSP Dock — Arranque de Windows bajo demanda desde PSP (piloto)

## Objetivo y limitación

El usuario no quiere un monitor ejecutándose cada vez que inicia Windows.
Prefiere abrir PSP Dock en la consola, seleccionar **Iniciar PSP Dock en
PC**, conectar el cable y ver el XMB en el ordenador sin ratón ni teclado.

La PSP no tiene autoridad para iniciar directamente un ejecutable del PC
mediante USB HID ordinario. Windows necesita **un registro previo del
receptor**: un servicio iniciado por evento de interfaz USB, una tarea
por evento de Plug and Play, o una aplicación residente. No se usa
autorun.inf ni la PSP simula un teclado que escriba comandos (comportamiento
tipo BadUSB, inseguro y dependiente del foco).

Microsoft documenta los servicios iniciados por llegada de interfaz USB
como mecanismo robusto. Su inconveniente es que el servicio se ejecuta
en sesión 0 y necesita otra pieza para abrir la UI del usuario.
Para el MVP se estudia una tarea EventTrigger menos intrusiva.

## Implementación disponible

- **PSP homebrew homebrew/psp-dock-app/main.c**: menú ANTES de cargar
  el driver HID. X acepta iniciar el Dock en Windows; O vuelve a XMB
  PSP. Requiere cable desenchufado inicialmente porque el controlador
  actual rechaza arrancar HID sobre USBSTOR montado (seguridad FAT).
  Después de X, PSP carga HID y pide conectar cable. No hay autoplay.
- **Windows setup_usb_event.py**: registra reversiblemente una tarea
  del usuario PSP Dock - USB HID con EventTrigger, **sin disparador
  al iniciar sesión ni arrancar Windows**. No hay proceso residente.
- **Windows on_psp_hid_arrival.py**: tras el evento, comprueba brevemente
  el mando real Sony VID054C/PID01CB por WinMM. Solo entonces abre
  o reutiliza el XMB a través de start_dock.launch(). No inicia PPSSPP.
- El autoarranque anterior setup_startup.py está DESACTIVADO en este PC;
  queda en el código como una alternativa que el usuario no desea usar.

## Tarea Windows registrada

Nombre PSP Dock - USB HID, usuario normal sin elevación, evento 410
del canal Microsoft-Windows-Kernel-PnP/Configuration. Windows ha
confirmado una EventTrigger, cero LogonTrigger y estado Ready.
La tarea permanece inactiva hasta recibir un evento.

**ADVERTENCIA:** el evento 410 significa «Device configured». Puede
producirse solo durante instalación/configuración del controlador,
NO necesariamente en cada reconexión a un puerto USB conocido.
Tenemos registros reales del HID PSP en ese canal pero TODAVÍA NO
se ha demostrado que aparezca en cada reconexión. El evento no es
un protocolo host/PSP. Por limitación del filtro XPath actual,
el trigger detecta configuraciones PnP en general, y su acción solo
abre XMB tras comprobar que el mando real es el PSP Dock Sony.

Si 410 no reaparece, NO activar AutoRun ni simular teclas. La
alternativa robusta es un servicio Windows de tipo trigger-start,
SERVICE_TRIGGER_TYPE_DEVICE_INTERFACE_ARRIVAL, limitado al Hardware
ID Sony, con un puente explícito a la sesión gráfica del usuario.
Requiere instalación administrativa y prueba de ida y vuelta.
No hay que afirmar que esa alternativa ya esté implementada.

### Estado actual de pruebas

- El nuevo menú PSP está compilado en el PC; NO instalado todavía
  en la Memory Stick real porque la última sesión LIVE tenía
  pending_return=true. No sustituir EBOOT con guardados pendientes.
- Tarea Windows registrada; no hay monitor en el inicio de sesión.
- Pruebas del guard HID y del XML superadas.
- **NO se ha probado físicamente aún el arranque Windows desde una
  nueva inserción de cable**. Ese será el siguiente ensayo de PnP.

### Instalación reversible

Desde la raíz del proyecto:

    python setup_usb_event.py           # Vista previa
    python setup_usb_event.py --install # Registrar tarea
    python setup_usb_event.py --remove  # Retirar solo nuestra tarea

La tarea abre SOLO el selector XMB. No copia ROMs ni saves y
nunca lanza un juego por cuenta propia.

### Flujo previsto

1. En PSP abrir Juegos → PSP Dock.
2. Con cable desconectado pulsar X: Iniciar PSP Dock en PC.
3. Conectar cable cuando la consola lo indique.
4. Windows reconoce HID, tarea PnP abre XMB si el evento existe.
5. Navegar con PSP, elegir juego y volver al XMB al cerrarlo.
6. Elegir Salir en el PC, terminar HID con START+SELECT, regresar
   manualmente al almacenamiento Sony hasta tener bridge real.

**La sincronización inicial USBSTOR→HID con un solo cable sigue
pendiente del bridge kernel y ACK auténtico.** Un primer HID directo
no puede acceder mágicamente a juegos/saves que están solo en PSP.

Referencias:

- https://learn.microsoft.com/en-us/windows/desktop/taskschd/task-scheduler-schema
- https://learn.microsoft.com/en-us/windows/win32/services/service-trigger-events
- https://learn.microsoft.com/en-us/windows-hardware/drivers/install/best-practices-win32services-interacting-with-devices
- https://learn.microsoft.com/en-us/windows/win32/shell/autoplay-reg
