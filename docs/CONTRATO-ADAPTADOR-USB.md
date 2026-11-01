# PSP Dock: contrato de adaptador USBSTOR ↔ HID (sin operaciones físicas)

**Estado: contrato de seguridad y pruebas de PC, NO gestor de roles operativo.**
`usb_role_adapter.py` es una interfaz pura para un posible adaptador futuro.
En el proyecto actual, `UnavailableRoleAdapter` es la única implementación:
siempre informa de que el puente no está disponible, nunca emite órdenes USB y
nunca produce un `RoleBridgeProof` cualificado. No se ha utilizado la PSP ni su
Memory Stick para desarrollar o probar este módulo.

La PSP ha funcionado físicamente como gamepad HID al iniciarse desde un
homebrew y Windows ha podido observarla como almacenamiento Sony. Esos dos
resultados **no demuestran todavía** una transición autónoma de roles en el
mismo cable, la exclusión del FAT por ambos sistemas, ni un reconocimiento
vivo y verificable del firmware para autorizar esa transición. La
implementación se mantiene bloqueada hasta demostrarlo.

## Separación de observaciones y prueba certificada

| Información | Representación en código | Qué acredita |
|---|---|---|
| Windows ve almacenamiento Sony, un HID candidato, ambos o ninguno | `HostUsbStatus`, `ObservedRole` | Presencia observable, sin facultad de cambiar roles |
| Volumen y nodo USB Sony esperado | `StorageIdentity(volume_serial, usb_parent)` | Comparación exacta de identificadores; no certifica que sea el mismo hardware físico ante un ataque de suplantación |
| Token HID candidato para esa identidad | `SessionExpectation(storage, hid_session)` | Comparación exacta, no reconocimiento vivo del firmware |
| Lock de volumen, FAT liberado, transición y ACK kernel | **Aún sin proveedor** | Solo un futuro atestador vivo podría demostrar estas propiedades |
| Prueba consumible por `dock_experience.Experience` | `RoleBridgeProof` de `usb_handoff.py` | Debe llegar únicamente desde el futuro atestador auditado, nunca desde observaciones, formularios o simulaciones |

`HostUsbStatus.from_host_report(...)` interpreta de forma **pura** los estados
del observador `usb_transport_observer.py`. Rechaza una entrada que atribuya
`live_firmware_ack`, `exclusive_fat_confirmed`, `role_transition_confirmed` o
`hardware_roundtrip_verified` a los datos de Windows. Para este contrato,
incluso `STORAGE_VISIBLE`, `HID_VISIBLE_UNBOUND`, `DUAL_PRESENT_UNSAFE`, el
código PnP 47 o la desaparición del volumen son exclusivamente observaciones.
La clase siempre devuelve esos indicadores como `False`. Un `BOOT.TXT`,
marcador K1/VSH, captura, serial de volumen o lectura PDK1 no se eleva a prueba
de liberación del FAT.

`UsbRoleAdapter` define `observe()`, `verified_live_proof(expected)`,
`request_prepare(expected, polling=...)`,
`request_storage_stop(expected, polling=...)` y
`request_return_once(attempt, polling=...)`. El adaptador indisponible solo
devuelve observación `UNAVAILABLE` y `None` para la prueba viva. Las otras
tres operaciones lanzan `UnavailableRoleError`, sin acceder al sistema ni
cambiar estados del sondeador.

**La interfaz no contiene un constructor de prueba real ni un atestador de
hardware.** Aunque `RoleBridgeProof` es una `dataclass` que Python permite
construir desde otros módulos, sus campos y el método `qualifies()` solo
verifican la *forma* de la prueba. No son autenticación criptográfica: el
integrador real deberá aceptar exclusivamente resultados producidos y
verificados dentro de un adaptador físico auditado. Nunca debe permitir que
cliente web, usuario, `experience_simulator.py` o un snapshot suministren esos
campos como autoridad.

## Pausar el sondeo de volúmenes de Windows

`VolumePollingContract` coordina **en memoria** el sondeo futuro. No llama a
`GetLogicalDrives`, no enumera Windows y no detiene ningún hilo por sí mismo.
Su máquina de estados impone esta secuencia al integrador:

1. Antes de cada sondeo normal, registrar un `begin_volume_poll()` y cerrarlo
   con `finish_poll(ticket)` en un `finally`. Los tickets duplicados o ajenos
   se rechazan. Los sondeos reales deberán estar encapsulados en este contrato.
2. Pedir `request_pause()`. Desde ese momento se deniegan **nuevos** sondeos,
   aunque los existentes deban terminar. El resultado es una *petición*, no
   una confirmación de que Windows ha suspendido algo.
3. Una vez detenido de verdad el bucle externo y drenados todos sus trabajos,
   llamar a `acknowledge_pause(petición, poller_stopped=True)`. El contrato
   exige el token original, estado correcto, ausencia de trabajos pendientes y
   ACK explícito del componente que ejecuta el sondeo. Solo entonces
   `require_paused()` permite al futuro adaptador considerar la preparación.
4. Durante HID el sondeo normal permanece bloqueado. Después de observar una
   sesión HID **candidata**, `bind_observed_hid_candidate(...)` vincula su
   identificador, sin afirmar que sea una prueba física.
5. Tras **reservar** la orden de retorno única, `begin_return_watch(...)`
   permite exclusivamente el sondeo del almacenamiento de la PSP original.
   Cada `begin_original_storage_probe()` proporciona la identidad que debe
   acotar la consulta externa. La ausencia de volumen no acredita nada; si
   aparece un serial o padre USB distinto, el contrato entra en `HALTED`.
6. Después de reconocer observacionalmente la identidad original, comprobar
   de forma independiente backups, conflictos, journals y operaciones
   pendientes. `resume_after_verified_return(...)` solo reabre el sondeo
   normal cuando el integrador declara la sincronización final terminada y
   no hay E/S pendiente. Estas banderas son **obligaciones del integrador**,
   nunca atestaciones hardware emitidas por el propio contrato.

Si el flujo se interrumpe, `halt()` mantiene el sondeo deshabilitado. Se debe
realizar una recuperación manual y recrear el contrato con una nueva
comprobación de identidad, sin asumir que un `acknowledge_pause()` anterior
sigue siendo válido después de cerrar el programa.

Este diseño evita que el sondeo recurrente del launcher remonte o vuelva a
usar la unidad durante la transición. Según la documentación de Windows,
`FSCTL_DISMOUNT_VOLUME` por sí solo no bloquea nuevos accesos y una enumeración
como `GetLogicalDrives` puede reactivar un volumen. Por eso se exige el drenaje
del sondeador antes de estudiar una estrategia de lock. **Este módulo no
implementa ni llama a `FSCTL_LOCK_VOLUME`, `FSCTL_DISMOUNT_VOLUME`,
`DeviceIoControl`, expulsión PnP, comandos SCSI ni controles USB de PSP.**

## Retorno único HID → USBSTOR

`OneShotReturnLedger` modela el intento de retorno **sin enviarlo**.
Está vinculado a `SessionExpectation`, que conserva exactamente el serial del
volumen, el padre USB Sony y el token HID de 32 caracteres hexadecimales.
No admite cambiar la sesión durante el retorno.

- Estado inicial `UNKNOWN`: bloquea por defecto; una recuperación sin registro
  duradero nunca interpreta la falta de pruebas como «no enviado».
- `NOT_ATTEMPTED`: solo debe elegirse al reconstruir un **registro duradero
  externo y fiable** que garantice que esa sesión todavía no envió la orden.
- `reserve_before_dispatch(hid_session)` cambia inmediatamente a
  `ATTEMPT_RECORDED` y devuelve `ReturnAttemptIntent` marcado
  `LOCAL_INTENT_ONLY_NOT_DISPATCHED`. El transporte futuro debe persistir el
  estado `ATTEMPT_RECORDED` **antes** de emitir cualquier comando físico.
- La construcción directa de `ReturnAttemptIntent` se rechaza: la reserva
  normal solo la crea el ledger. Es una protección frente a usos accidentales,
  no una autenticación criptográfica ni una garantía de persistencia.
- Después de cualquier intento, incluso si la confirmación de entrega se
  pierde o el proceso falla, no se vuelve a enviar ciegamente el retorno. Se
  observa si regresa el **mismo** volumen y se solicita recuperación si no.

Ni el ledger ni el `ReturnAttemptIntent` autentican un ACK, demuestran
entrega o garantizan persistencia en disco. Para integrarlo físicamente se
necesita un diario duradero, una forma de distinguir sesión anterior y nueva,
y verificación de la secuencia de firmware por un atestador independiente.
**Nunca se debe usar un `ReturnAttemptIntent` construido directamente por
otro código como autorización física de envío.**

## Evidencias físicas que faltan para un adaptador real

**Pista técnica contrastada (PSPSDK):** el ejemplo oficial de almacenamiento
USB de PSP incluye una acción con el botón X que llama a
sceUsbActivate(0x1c8) / sceUsbDeactivate(0x1c8) y consulta el estado del
cable y la conexión. Eso confirma que el SDK contempla activar/desactivar
lógicamente un controlador USB, y que estos pasos pueden estudiarse
SIN cambiar de cable en un experimento futuro. **No demuestra** que
Windows libere FAT, que un controlador HID diferente pueda enumerarse
inmediatamente, ni el retorno desde HID a USBSTOR con ACK verificable.
No confundir cambiar el estado de un controlador con realizar el protocolo
seguro completo. Fuente:
https://github.com/pspdev/pspsdk/blob/master/src/samples/usb/storage/main.c

Una futura implementación de `UsbRoleAdapter` solo podrá emitir un
`RoleBridgeProof(source="live_psp_role_bridge", ...)` cuando un atestador vivo
independiente compruebe, al menos:

1. Identidad de PSP Sony, Memory Stick y puerto físico, con serial y padre USB
   exactamente asociados a la misma sesión; ausencia de otras candidatas.
2. Versión negociada del protocolo y capacidades realmente implementadas:
   `exclusive_fat`, `release_storage`, `activate_hid`, `restore_storage` y
   `live_role_ack`; no basta una tabla declarativa o un módulo compilado.
3. Canal vivo fuera del FAT montado que responda a un desafío nuevo de esa
   sesión, ligando la respuesta al kernel/firmware y evitando marcadores
   antiguos, repeticiones y sesiones anteriores. Se necesita un mecanismo de
   confianza verificable para la respuesta; un texto o booleano suministrado
   por el host no cumple esta condición.
4. Prueba efectiva de exclusión mutua de FAT: Windows sin E/S abierta y con
   escrituras vaciadas, PSP sin acceso simultáneo, acuse kernel de preparación
   y paro real de USBSTOR antes de iniciar HID. No se debe inferir esa prueba
   por ausencia de letra de unidad o por un `sceUsbStop` aislado.
5. Enumeración de HID PDK1 comprobada en el mismo hardware y token de sesión,
   con almacenamiento ausente, sin estado PnP 47 que requiera quitar el cable.
6. Retorno físico único a USBSTOR, recuperación del volumen original y
   sincronización de partidas con backups, resolución de conflictos,
   validación de copia e inactividad de PPSSPP. La entrega del comando HID
   podría ser incierta aunque el sistema operativo informe un error.

Hasta entonces, `verified_live_proof()` seguirá devolviendo `None`, por lo
que `dock_experience.Experience.start_dock` rechazará correctamente una
transición automática. La seguridad del futuro adaptador exige pruebas de
laboratorio con hardware, recuperación reversible y guardados ficticios; las
pruebas de PC de este contrato son exclusivamente lógicas.

### Comprobación local

Desde `/psp-dock`:

```powershell
python -m unittest discover -s tests -p test_usb_role_adapter.py -v
python -m unittest discover -s tests
```

Los tests verifican ausencia de E/S en el adaptador indisponible, rechazo de
observaciones elevadas falsamente a ACK, identidad Sony y token HID exactos,
reserva de retorno única con recuperación conservadora, pausa con sondeos en
vuelo, vigilancia limitada del retorno y bloqueo ante volumen cambiado.

### Referencias técnicas para el futuro (solo lectura)

- Microsoft, [FSCTL_LOCK_VOLUME](https://learn.microsoft.com/en-us/windows/win32/api/winioctl/ni-winioctl-fsctl_lock_volume): lock exclusivo de un volumen; puede fallar con archivos abiertos.
- Microsoft, [FSCTL_DISMOUNT_VOLUME](https://learn.microsoft.com/en-us/windows-hardware/drivers/ifs/fsctl-dismount-volume): desmontaje no equivale a un lock y puede volver a montarse.
- Microsoft, [CM_PROB_HELD_FOR_EJECT / código 47](https://learn.microsoft.com/en-us/windows-hardware/drivers/install/cm-prob-held-for-eject): dispositivo preparado para retirada física.
- PSPSDK, [USB API](https://pspdev.github.io/pspsdk/pspusb_8h.html): llamadas de driver USB; sus retornos no demuestran por sí solos la exclusividad FAT requerida por el Dock.
- Documentación del proyecto: `docs/HANDSHAKE-USB-SIN-DESCONECTAR.md`, `docs/HITO-USBSTOR-HID.md` y `docs/PRUEBA-INTEGRAL-SIMULADA.md`.
