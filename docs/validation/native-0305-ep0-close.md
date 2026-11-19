# PSP Dock 0305 — EP0 completo y cierre con cable conectado

## Resultado posterior de hardware

0305 se instaló a las 20:10:19 UTC del 2 de noviembre y **no resolvió** la recepción de las órdenes en esta PSP. La tarjeta sí se expulsó; HOST_BEGIN y el cierre terminal continuaron sin ACK. [0306](native-0306-short-reports.md) cambia el transporte de órdenes y corrige la bienvenida y el reintento visible. El resto de este documento conserva el estado histórico previo a ejecutar 0305, no su resultado final.

## Estado de validación

Firmware 0305/build `03050001`, capacidades `000F`. Compilado y auditado el 2 de noviembre de 2026. Paquete verificado en el PC. **Todavía no instalado ni ejecutado en la PSP** en el momento de preparar este informe. La confirmación física de HOST_BEGIN y del retorno al XMB sigue pendiente.

## Evidencia que motivó el cambio

La foto de diagnóstico RAM 0304 y la consulta de Windows mostraron la misma sesión, fase STORAGE, secuencia 0 y Windows sin confirmar. El callback EP0 recibió exactamente los 64 bytes de HOST_BEGIN y el contenido coincide con la orden construida por el host. La recepción terminó con `retcode=-3`, por lo que el parser no se ejecutó: SET=1, envío=1, completion=1, parser=0, drop=3. Las generaciones coincidían y no había reset o quiesce local que justificara la cancelación.

En Windows, el SET de 64 bytes tardó 5015,312 ms; la espera de confirmación añadió 1987,809 ms. La identificación del almacenamiento tardó 1380,863 ms. Son medidas de este intento, no una promesa de latencia de detección futura. El frame Output inocuo de 4 bytes completó en 0,196 ms por el mismo transporte. La comparación sitúa la investigación en la finalización de EP0 al llegar al límite de 64 bytes.

El request EP0 se inicializaba a cero y dejaba `UsbdDeviceReq.unkc=0`. El encabezado de PSPSDK conserva ese nombre desconocido. El layout coincidente publicado por xerpi llama a ese campo `isControlRequest`; el código reconstruido de Sony usbacc establece el campo a 1 en su request EP0 compartido entre IN y OUT. Esto fundamenta el cambio a `ctl_req.unkc=1`. El código de xerpi se usa como evidencia del nombre/layout, no como ejemplo del valor 1.

Fuentes primarias consultadas:

- [PSPSDK: UsbdDeviceReq y retcode -3](https://pspdev.github.io/pspsdk/structUsbdDeviceReq.html).
- uofw, revisión `28306bb5bd4fcdcc0c1d469bf1f3bdb885dde3e9`, código Sony usbacc.
- xerpi PSP USB, revisión `68f950aeee405e8a0dbec24cce0deddfce96aaec`, definición del request USB.
- [Microsoft: HidD_SetOutputReport](https://learn.microsoft.com/windows-hardware/drivers/ddi/hidsdi/nf-hidsdi-hidd_setoutputreport).

## Transferencia y protocolo

Se marca explícitamente el request EP0 como control, tanto para IN como para OUT. EP1 no cambia. Se conservan el tamaño exacto de 64 bytes, el éxito real del callback, la identidad de firmware, el nonce, la secuencia y las comprobaciones de caché/generación. Un payload completo con completion cancelada continúa rechazándose.

La identidad 0305 incorpora `HOST_SHUTDOWN=5`, destino SAFE, y el flag de estado `SHUTDOWN_REQUESTED=32`. Los firmware 0303/0304 conservan capacidades 7 y su contrato anterior; 0305 usa 15. La nueva orden no está disponible en el helper genérico de comandos: sólo el coordinador de cierre puede enviarla después de la liberación comprobada del medio.

En la PSP, la autorización terminal se aplica en el worker, después del callback correcto y del drenaje de entrada. GET, SAFE, detach, suspend y START+SELECT no conceden por sí solos permiso para detener almacenamiento. El cierre inicial no inventa HOST_BEGIN ni convierte una sesión Host0 en confirmada. Se mantienen los controles efectivos de desactivación, Stop y Unload antes de ofrecer Círculo para regresar al XMB.

## Liberación real del medio observada en 0304

A las 19:36:05 UTC se probó la secuencia normal sobre la única partición y volumen del dispositivo Sony ya identificado:

1. Apertura del volumen con lectura/escritura.
2. `FSCTL_LOCK_VOLUME`: éxito, 18,235 ms.
3. `FSCTL_DISMOUNT_VOLUME`: éxito, 1,131 ms, después del bloqueo.
4. `IOCTL_STORAGE_EJECT_MEDIA`: éxito, 0,247 ms.
5. Desbloqueo y cierre del mismo handle.

Después, el mismo HID y nonce seguían disponibles. Las consultas de volumen devolvieron ERROR_NOT_READY (21), también por su GUID estable. Esto demuestra que en esta PSP existe una ruta que libera el medio conservando el canal necesario para enviar la orden terminal. No prueba todavía que el firmware 0305 cierre y descargue sus módulos correctamente.

Referencias de las operaciones:

- [Microsoft: FSCTL_LOCK_VOLUME](https://learn.microsoft.com/windows/win32/api/winioctl/ni-winioctl-fsctl_lock_volume).
- [Microsoft: FSCTL_DISMOUNT_VOLUME](https://learn.microsoft.com/windows/win32/api/winioctl/ni-winioctl-fsctl_dismount_volume).
- [Microsoft: IOCTL_STORAGE_EJECT_MEDIA](https://learn.microsoft.com/windows/win32/api/winioctl/ni-winioctl-ioctl_storage_eject_media).

## Cierre y recuperación en Windows

El botón Cerrar app comparte la intención de salida de la ventana. Espera la operación admitida y, cuando corresponde, la devolución verificada de partidas. Mantiene la ventana si necesita revisión. La salida antes de confirmar la entrada no inicia nuevas copias.

El cierre 0305 conserva una asociación exacta entre HID, padre USB, tarjeta, volumen y disco. Requiere una única partición y volumen del disco antes de expulsar el medio completo. LOCK, DISMOUNT y EJECT se ejecutan sobre el mismo handle, con checkpoints persistentes antes de las operaciones. Un bloqueo rechazado impide el desmontaje; no hay expulsión forzada ni cambios de servicios.

El recibo conserva los resultados reales de las tres operaciones y la comprobación de medio ausente para su GUID. Sólo después se persiste y envía HOST_SHUTDOWN. Una respuesta SET perdida puede deberse a la propia retirada del dispositivo; sólo la ausencia comprobada del HID exacto y de su padre físico permite concluir que terminó esa conexión. Un GET fallido no es prueba suficiente.

Si el cierre queda pendiente, el reintento usa la identidad retenida. No vuelve a identificar una tarjeta montada ni a copiar archivos. Si la PSP consumió y rechazó la orden terminal, un reintento explícito puede registrar la siguiente secuencia, conservando el recibo de expulsión; una entrega incierta conserva los bytes originales. Una expulsión cuyo resultado quedó incierto no se convierte en éxito por encontrar después un medio ausente.

Durante el cierre, incluida la recuperación al abrir la app, se bloquean los escaneos de unidades, la lectura desde rutas PSP guardadas por la vista XMB y la navegación del mando. Los recursos en caché y del PC siguen disponibles. La pantalla distingue cierre pendiente y ofrece el reintento de finalización cuando corresponde.

La guarda de arranque de 0305 permite un cable pasivo cuando el USB está inactivo. Continúa rechazando una conexión activada/configurada o almacenamiento en uso.

## Validación de software

- Compilación MIPS del módulo kernel y de la aplicación de usuario: PASS.
- Auditoría del ELF, imports, ABI PDNA de 17 bytes, identidad 0305 y recursos del PBP: PASS.
- Transporte/worker nativo con GCC estricto: PASS; ASan y UBSan: PASS. LSan desactivado por las restricciones conocidas del entorno de pruebas.
- UI de la PSP con sus guardas de salida: PASS.
- JavaScript de producto: 302 comprobaciones PASS; conexión/ventana: 156 PASS.
- Suite focal Windows: resultado final pendiente de registrar.

Las pruebas sintéticas cubren retcode cancelado con 64 bytes, generaciones, detach antes/después de aplicar, drenaje incompleto, fallo de desactivación, reintento terminal, identidad incorrecta, error de persistencia, medio recargado y recuperación sin repetir la expulsión. No sustituyen la prueba de hardware.

## Paquete generado

| Archivo | Bytes | SHA-256 |
|---|---:|---|
| EBOOT.PBP | 313169 | `fafb600d37fe69709d47f57e955420751de9e86aed3cce9999103cb1e0161320` |
| docknative.prx | 25538 | `cfe213fa7581b11a6f6d2d5a0c879435629ab9844db2c5aef141a8727010840f` |

El paquete PC es `data/native-dock-0305-package`. Se conservan las licencias, los backups anteriores y los anclajes de versiones conocidas para actualización/rollback. La instalación sólo se realizará mediante USB normal del XMB, fuera de la aplicación nativa.

## Comprobación física restante y límites

Con 0305 instalada, verificar HOST_BEGIN real y medir de nuevo la detección/sincronización. Después cerrar desde Windows, comprobar la liberación del medio y la retirada USB y confirmar en la pantalla de la PSP que Stop/Unload terminaron y Círculo devuelve al XMB sin retirar el cable. Comprobar también la cancelación inicial sin copias.

Un ERROR de firmware anterior a la creación del registro de cierre no se convierte automáticamente en autorización para finalizar: el host conserva el bloqueo existente. El reintento terminal desde ERROR cubierto aquí requiere un cierre ya registrado y la expulsión comprobada. La desaparición de USB por sí sola no acredita la descarga de módulos de la PSP.
