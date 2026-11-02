# Cierre de PSP Dock con el cable conectado

## Requisito de David, 2 de noviembre de 2026

Cerrar PSP Dock en Windows debe terminar las operaciones pendientes, devolver
y verificar los guardados cuando corresponde, liberar la conexión y dejar en
la PSP el aviso de que ya puede volver al XMB. No debe requerir START+SELECT,
expulsión manual desde Windows ni retirar físicamente el cable. Si se usa
START+SELECT como atajo, debe conducir al mismo cierre coordinado.

El recorrido de retirar cable de0303/0304 es una limitación actual, no el flujo
aceptado para el producto. La instalación diagnóstica0304 no la corrige.
El aviso final puede permitir regresar al XMB desde la consola; David no pidió
que se abandone automáticamente esa pantalla. No convertir el requisito de no
usar START+SELECT en una obligación inventada de salto automático al menú.

## Defecto localizado

`NativeDockTransport.finish_session()` lleva la sesión admitida a SAFE y
solicita `CM_Request_Device_EjectW` sobre el padre USB compuesto. La cancelación
de una entrada no admitida evita HOST_BEGIN y solicita la misma expulsión.
Tras CR_SUCCESS marca `host_eject_accepted` y elimina su enlace.

La retirada del padre incluye el canal HID. No hay una orden posterior que
comunique a la PSP que Windows ya liberó el almacenamiento. En el firmware,
`observe_cable`, `stop_usb` y `module_stop` todavía exigen ausencia de cable para
terminar. SAFE no prueba por sí mismo la liberación del almacenamiento. El
aviso que manda expulsar de nuevo desde Windows es por tanto incoherente con
una expulsión ya aceptada.

El arranque nativo también reutiliza una guarda del laboratorio HID que trata
el bit de cable conectado como USB ocupado. Una futura guarda propia puede
permitir cable pasivo sólo si no hay activación/configuración USB ni otro dueño
del almacenamiento. No modificar la guarda de los laboratorios antiguos ni
retirar esa distinción sin pruebas. La0304 instalada conserva la guarda anterior.

Además, la X de la ventana solicita retorno mediante `NativeWindowClose`, pero
el botón de atención «Cerrar app» usa una salida deliberada de diagnóstico que
conserva journals y no finaliza la conexión. El cierre normal debe compartir la
intención de retorno; cualquier salida incompleta debe identificarse claramente.

## Contrato necesario

Se deben distinguir la devolución verificada (o cancelación inicial sin trabajo
de archivos admitido), la liberación efectiva del almacenamiento por Windows y
la confirmación terminal de la PSP antes de detener y descargar módulos.

La orden terminal debe estar ligada a la sesión, firmware y secuencia observados;
no puede inventar HOST_BEGIN ni convertir Host0 en una sesión admitida. También
debe poder cerrar un estado de error, conservando el diagnóstico y drenando las
transferencias. La PSP sólo ofrecerá volver al XMB después de Stop/Unload reales.

Un veto del host conserva el estado pendiente; no autoriza la parada del USB.
Si se libera el almacenamiento pero falla la confirmación terminal, un reintento
debe retomar esa finalización sin copiar otra vez ni capturar otro baseline.
Después de retirar el volumen no puede usarse `_fresh()` para reenlazar la unidad
montada: el canal de cierre necesita conservar y volver a comprobar su identidad.

## Dato de hardware pendiente antes de elegir la operación de liberación

Hay que consultar BusRelations, EjectionRelations y RemovalRelations del disco,
su interfaz de almacenamiento, HID y el padre. Seleccionar el nodo de
almacenamiento no garantiza que Windows conserve HID. Las listas vacías tampoco
acreditan independencia. El inspector de lectura debe publicar cada código CR
y la identidad concreta, sin ejecutar ninguna retirada.

API primaria:
https://learn.microsoft.com/en-us/windows/win32/api/cfgmgr32/nf-cfgmgr32-cm_get_device_id_listw

El inspector `tools/inspect_native_ejection.py` implementa esa consulta sin
escrituras ni SET de HID. Revalida la identidad al terminar y publica errores
de consulta separados de relaciones vacías. La consulta real del USB XMB02D2
encontró una sola partición/volumen; el disco rechazó la expulsión normal con
CR23/tipo8. Esto no acredita el comportamiento del compuesto01CD todavía.

Una alternativa basada en volumen requiere más que desmontarlo: LOCK_VOLUME
comprueba que no haya archivos abiertos y vacía cachés, pero el bloqueo termina
al cerrar el handle. DISMOUNT sin bloqueo permite que se remonte la unidad.
No se ha elegido ni ejecutado una sustitución de la expulsión PnP en esta PSP.

Referencias primarias:
https://learn.microsoft.com/en-us/windows/win32/api/winioctl/ni-winioctl-fsctl_lock_volume
https://learn.microsoft.com/en-us/windows/win32/api/winioctl/ni-winioctl-fsctl_dismount_volume

## Comprobación de aceptación pendiente

- Cierre desde Windows después de uso normal: devolución verificada y PSP lista
  para XMB con cable conectado.
- Cancelación inicial con Host0: mismo final sin importar ni devolver partidas.
- Solicitud desde START+SELECT: una sola intención de salida atendida por el host.
- Veto de Windows: aviso concreto, sin parada prematura ni falsas confirmaciones.
- Confirmación perdida: reanudar sólo la finalización conservando la identidad.
- Juego abierto: esperar su cierre normal; no terminar el emulador por fuerza.
- Error de firmware: conservar causa y permitir salida coordinada cuando el
  almacenamiento esté realmente liberado.

Estado: análisis y captura preparados; no declarar todavía este cierre resuelto.
