# Controlador compuesto PSP Dock: evidencia y siguiente ensayo

## Estado actual: 0207 instalada; aceptación física pendiente

**PSP Dock - USB Storage + HID 0207** está compilada, auditada e instalada
con relectura SHA-256 de sus cuatro archivos: **368.243 bytes** en
`PSP/GAME/PSPDockStorageTest`. El PSP Dock estable conserva su carpeta.
El disco de esta prueba sigue siendo FAT12 ficticio de 512 KiB, solo
lectura y generado en memoria. No ofrece la Memory Stick real.

### Resultado físico de 0206: estabilidad y punto pendiente

El usuario informó «esta vez todo bien» y envió una fotografía de la
pantalla final. Se ve **PSP DOCK**, revisión 0206, resultado retenido y
parada **START+SELECT / solicitud del usuario**. En este intento no se
repitió la salida espontánea al XMB que había descrito para 0205.

| Telemetría conservada en la foto | Valor |
|---|---:|
| Informes HID completados | 1.187 |
| CSW SCSI completados, incluidos posibles estados fallidos | 26 |
| Bytes relevantes contabilizados | 18.284 |
| Errores RX / código RX / código TX | 0 / 0 / 0 |
| Configuraciones / desconfiguraciones / generación | 7 / 5 / 5 |
| Último estado BOT / comando / datos relevantes frente a petición | 4 (CSW_WAIT) / 0x1A (MODE SENSE 6) / 32 de 192 |

El monitor estuvo activo durante el intento. Su evidencia está en
`data/msc-0206-evidence/physical-20261101-214307/`: `transitions.jsonl`,
`windows-events.json` y `user-photo-0206.jpg`. La foto original tiene SHA-256
`2c17f357b3f6d128fdd956acf52e78894ede0fd3996c8ce6965cfcd819053324`.

| Hora UTC del 1 de noviembre de 2026 | Observación de Windows |
|---|---|
| 21:45:55 | Padre `usbccgp`, MI00 `HidUsb` y MI01 `USBSTOR`, revisión 0206, operativos. |
| 21:47:06 | Disco `PSPDOCK MSC HID TEST REV0001` presente y operativo en PnP. |
| 21:47:08.715 | Cuatro eventos Disk 11, error de controlador en Harddisk6/DR33; StorDiag identifica el disco 6 como PSPDOCK. |
| 21:47:30.005 | Se inicia el dispositivo PnP `STORAGE/Volume` con `volume.inf`; esto no acredita un sistema FAT montado. |
| 21:47:33.428 | StorDiag registra MODE SENSE `1A003F00C000`, con un reintento, fallido al retirarse el dispositivo. |
| 21:47:42 | La observación de descubrimiento ya no contiene los dispositivos de la prueba. |
| 21:53:51 | Vuelve el almacenamiento Sony nativo. |

Las horas JSONL de 0206 se escribían después de consultas que podían
ser lentas; no son timestamps exactos de llegada/retirada del bus.
La observación no devolvió volúmenes asociados y `probe_attempts` quedó
en **0**. No hubo lectura de aceptación, hash de PROBE.DAT ni verificación
de movimiento durante copias. Un PDO de volumen no equivale a FAT con letra.

La foto señala la ruta **respuesta IN corta, HALT y CSW**: MODE SENSE
produjo 32 bytes para una petición de 192 y el último estado conservado
esperaba completar CSW. Es una hipótesis de transporte sustentada por la
foto y los eventos, no una traza que pruebe un defecto concreto del bus
Sony. El CDB EVPD página 0 del log es válido y lo acepta el motor; su
sense 5/24 no demuestra rechazo del parser. READ CAPACITY(16) sí estaba
sin implementar y devolvía 5/20.

### Qué cambia en 0207

- BOT caso 5 (`Hi > Di`) envía los datos relevantes y rellena con ceros
  hasta la longitud del host. MODE SENSE 32/192 termina la fase USB de
  192 bytes y conserva residuo 160. EVPD 7/255 conserva residuo 248.
  El CSW no se genera hasta confirmar todos los bytes por callback.
  El relleno no incrementa el contador de bytes relevantes. Esta opción
  está permitida por [USB-IF BOT 1.0, sección 6.7.2](https://www.usb.org/sites/default/files/usbmassbulk_10.pdf).
- READ CAPACITY(16), servicio 0x10, devuelve último LBA 1023 y bloques
  de 512 bytes, con allocation length y validaciones de CDB/LBA/PMI.
  El formato se contrasta con [T10](https://www.t10.org/ftp/t10/document.03/03-387r2.pdf).
- La UI coloca texto en píxeles, con fondo opaco y separación vertical.
  Se comprobó con un render de app.c y el bitmap real del SDK; no se
  presenta ese render como una captura física. Mantiene la identidad
  visual de 0206 y el resultado retenido.
- Windows registra cada etapa CIM: disco, partición y unidad lógica.
  Los candidatos globales sin padre correlacionado quedan solo como
  diagnóstico y nunca habilitan una lectura. Las observaciones llevan
  hora inicial/final y se guardan antes del diagnóstico pesado.

El caso 4 (`Hi > Dn`), los errores de fase y CBW inválidos conservan sus
reglas HALT/reset. No se cambian flags Sony desconocidos. El nuevo
comportamiento de caso 5 requiere la siguiente comprobación física.

### Verificación e instalación de 0207

- Build PSPDEV/WSL con `-Werror`; 13 casos BOT, FAT12/SCSI, respuestas
  observadas, límites UINT32_MAX y 50.000 entradas malformadas, con ASan/UBSan.
- **14 escenarios asíncronos** del wrapper real con ambos sanitizadores,
  incluidos relleno, transferencia incompleta y reset durante el envío.
- Auditoría ELF MIPS/USER, disco FAT12 y tres recursos XMB: PASS.
  El kernel no importa `sceIo*` ni `IoFileMgr`.
- **66 tests focales Windows:** 31 aceptación, 10 paquete, 18 instalación
  y 7 diagnóstico. El script PowerShell real también ejecuta el preflight
  y devuelve `waiting_pnp_disk` sin errores con Sony nativo conectado.
- Backup crítico revalidado contra la PSP actual: 42 archivos, 4.908.072 bytes.
- Upgrade por recibo de 0206; cuatro archivos nuevos releídos y verificados.

| Binario 0207 instalado | Tamaño | SHA-256 |
|---|---:|---|
| `EBOOT.PBP` | 300.364 bytes | `be1a6a8cd62f1c7f6c4f004dbecc237128fa7ee36704611e55acb1a5824107fa` |
| `dockmsc.prx` | 29.546 bytes | `22474a2490052c770292543be66d90c7d76ed35d08a7a2dbcbc3f214096f244d` |

Recibo: `data/msc-0207-evidence/installation.json`. Build y tests están
en esa misma carpeta. La 0206 anterior queda archivada y verificada en
`C:\Users\ladir\Documents\PSP-Dock-Backups\msc-0206-before-0207-20261101-220716`.
El título SFO es **PSP Dock - USB Storage + HID 0207**; ICON0, PIC0 y PIC1
coinciden byte a byte con las fuentes revisadas de 0206.

**Pendiente:** montaje FAT, lecturas sin caché con SHA-256/nonce correctos
y cambios HID durante esas lecturas. El próximo intento debe seguir el
[procedimiento 0207](homebrew/composite-mscdemo/README.md#prueba-física-agrupada).
La compilación y la instalación verificadas no acreditan esa aceptación.

## Historial: instalación y cambios de 0206

La revisión **PSP Dock - USB Storage + HID 0206** está compilada, auditada
e instalada en `PSP/GAME/PSPDockStorageTest`, con relectura SHA-256 de
los cuatro archivos. **Su aceptación física sigue pendiente.** Mantiene
el producto diagnóstico
`054C:01CC`, dos interfaces HID + MSC/SCSI/Bulk-Only y el disco ficticio
FAT12 de 512 KiB. **No ofrece acceso a la Memory Stick real.**

| Variante | Interfaces | Contenido accesible | Estado |
|---|---|---|---|
| 0206, `054C:01CC` | HID + controlador Windows USBSTOR, MSC `08/06/50` | Disco FAT12 ficticio de 512 KiB | Ejecutada; parada explícita conservada; lectura de archivos no verificada |
| 0205, `054C:01CC` | HID + controlador Windows USBSTOR, MSC `08/06/50` | El mismo disco ficticio | Instalada y ejecutada; PnP e identidad SCSI observados antes de la retirada |
| 0204, `054C:01CB` | HID + interfaz de archivos WinUSB | Servicio de archivos de solo lectura | Compilada y auditada; no instalada en este ensayo |

El PID `01CC` separa la asociación de controladores del mando estable y
del prototipo WinUSB. La captura física de 0205 ya confirma ese producto
y las dos interfaces. Esto no acredita todavía un volumen utilizable ni
la aceptación combinada de lecturas y movimiento.

### Qué cambia en 0206

PSPSDK define los callbacks `attach` y `detach` como configuración
establecida y configuración retirada, respectivamente; no equivalen a
conectar o quitar físicamente el cable
([fuente primaria](https://pspdev.github.io/pspsdk/structUsbDriver.html)).
En 0205, un `detach` posterior a la primera conexión solicitaba cerrar la
prueba. Esa interpretación podía convertir una reconfiguración de Windows
en una salida del homebrew. El callback concreto del intento físico no
se registró, por lo que no se presenta como causa confirmada de la salida.

0206 conserva la aplicación al retirarse una configuración, pausa HID y
BULK, cancela las peticiones de esa configuración y espera sus callbacks
antes de reutilizar buffers. Si llega una nueva configuración, reanuda el
transporte. Las generaciones separan las peticiones EP0 antiguas de las
actuales y conservan un reset BOT nuevo que llegue durante la cancelación.
La salida por cable exige 500 ms continuos de ausencia de la señal física;
START+SELECT sigue solicitando el cierre explícito.

La pantalla muestra USB, configuraciones/desconfiguraciones, generación,
último comando SCSI, bytes completados, resultado RX/TX y motivo de parada.
Al terminar conserva el resultado hasta liberar y volver a pulsar **O**;
ya no vuelve al XMB inmediatamente. El título incluye **0206** y la
cabecera de ejecución usa letras de 21 píxeles. ICON0 y la placa PIC0
hacen legible **PSP DOCK**; PIC1 es un fondo grafito sin texto para evitar
duplicaciones al componer las capas del XMB.

El instalador añade actualización por recibo: verifica la instalación
anterior exacta y su identidad, guarda sus cuatro archivos en una carpeta
nueva del PC y prepara la versión nueva por separado. Publica con dos
renombrados recuperables, verifica la relectura y conserva un journal.
Si falla la publicación, solo intenta restaurar sobre la misma PSP
identificada. No modifica `PSP/GAME/PSPDock` ni acepta archivos extra en la
carpeta de prueba.

### Verificación de software de 0206

- Compilación PSPDEV/WSL con avisos tratados como errores.
- Motor MSC: los 13 casos BOT, FAT12/SCSI, límites, residuos y **50.000
  entradas malformadas**, también bajo AddressSanitizer y
  UndefinedBehaviorSanitizer.
- Wrapper USB real: **11 escenarios asíncronos** bajo ambos sanitizadores,
  incluidos buffers pendientes, reset BOT, desconfiguración/reconfiguración,
  cancelación de EP0 antiguo y conservación de una solicitud nueva.
- ELF MIPS auditado: revisión `0206`, tablas Sony por interfaz, endpoints e
  imports; **ningún `sceIo*` ni `IoFileMgr` en el kernel MSC**. Aplicación
  USER auditada por separado. El build comprueba las tres secciones XMB
  frente a sus archivos fuente.
- Parser FAT12 independiente: `PROBE.DAT` de 150.003 bytes y `SESSION.BIN`
  de 16 bytes verificados en la imagen de referencia.
- **60 pruebas focales superadas en Windows: 18 del instalador y 42 de
  aceptación, diagnóstico y paquetes.** Incluyen el historial
  `transitions.jsonl`, las comprobaciones de identidad y la restauración
  si falla el journal tras mover la versión anterior.
- Backup crítico renovado y verificado antes del upgrade: **42 archivos,
  4.908.072 bytes**, en
  `C:\Users\ladir\Documents\PSP-Dock-Backups\20261101-223608-84466104`.

Estos tests de 0206 verifican software en PC; no equivalen a su
aceptación física. La suite histórica de 104 casos Windows pertenece a
0205 y se conserva en su apartado, sin sumarla al recuento actual.

### Instalación verificada de 0206

El instalador terminó con código 0 y releyó **cuatro archivos, 367.483
bytes**, en `PSP/GAME/PSPDockStorageTest`. El EBOOT contiene exactamente
los tres recursos XMB nuevos revisados y el título SFO **PSP Dock - USB
Storage + HID 0206**. El recibo está en
`data/msc-0206-evidence/installation.json`.

| Binario 0206 instalado | Tamaño | SHA-256 de la relectura |
|---|---:|---|
| `EBOOT.PBP` | 300.196 bytes | `9f78063b00fa177262139eb496a681669d0dde072f40e73534bc6e713c1b4023` |
| `dockmsc.prx` | 28.954 bytes | `40e710910b20c76fe1230254abc74070b085734e5f859b004980c188039cd6a7` |

La instalación anterior 0205 se verificó y quedó archivada en
`C:\Users\ladir\Documents\PSP-Dock-Backups\msc-0205-before-0206-20261101-214211`.
El journal del upgrade conserva las fases de la sustitución. El PSP Dock
estable permanece en su carpeta independiente. Estos datos prueban la
instalación y su integridad; el montaje, la lectura y el movimiento HID
concurrentes de 0206 siguen pendientes de la prueba física.

### Evidencia física de 0205: 1 de noviembre de 2026

Los siguientes tiempos proceden del historial PnP de Windows y están en
hora de Madrid:

| Hora | Observación |
|---|---|
| 22:19:45.83–22:19:45.93 | Llegan el padre compuesto `usbccgp`, `MI_00` con `HidUsb`, `MI_01` con `USBSTOR` y el mando HID. |
| 22:20:06.028 | Llega el disco SCSI identificado como `PSPDOCK MSC_HID_TEST REV0001`. |
| Aproximadamente 22:20:06.07 | Se retiran los dispositivos; la llegada del disco precede a la retirada en unos 47 ms. |

No apareció un nuevo dispositivo USB desconocido. El usuario observó que
el homebrew volvió al XMB **sin reiniciar la PSP**. La identidad del disco
coincide con los bytes de nuestro SCSI INQUIRY: existe evidencia de esa
respuesta sobre BULK, además de la enumeración de descriptores.

No se confirmó un sistema de archivos montado, el SHA-256 de `PROBE.DAT`
ni cambios de controles durante una lectura. El monitor de aceptación
anterior **había expirado antes del intento físico**; su timeout no mide
este intento. El intervalo de unos 20 segundos hace plausible un timeout
de transporte seguido de reset/desconfiguración, pero es una inferencia.
La nueva telemetría PSP y `transitions.jsonl` deben distinguir esas causas.

### Registro de compilación e instalación histórica de 0205

Antes de ese intento se completaron:

- **104 pruebas focales en Windows**, incluidas ABI HID/WinMM, paquetes,
  instalación, correlación, interrupciones y diagnósticos. El registro
  previo describía 100 casos iniciales y 21 casos MSC reejecutados tras
  añadir cuatro casos de llegada tardía o ambigüedad de interfaces.
- Motor MSC con 13 casos BOT y 50.000 entradas malformadas bajo
  AddressSanitizer y UndefinedBehaviorSanitizer.
- Harness del wrapper con cinco escenarios asíncronos. Se corrigieron dos
  carreras que podían perder una recepción o repetir un bloque IN.
- Auditoría MIPS/USER y comparación exacta de los tres recursos XMB de
  aquella revisión con sus fuentes.
- Backup crítico revalidado: 42 archivos, 4.908.072 bytes.
- Instalación de cuatro archivos, 319.989 bytes, releídos por SHA-256.

Hashes históricos de los binarios **0205** instalados:

```text
EBOOT.PBP    8b4ad4643d287102e069b4a1866af5ecdde9172577eccb0e0d880be6577d3afa
dockmsc.prx  cf1ef97f32ef87862c424435bdfb931da7daa448441b1bef45ce9315fbfcc689
```

### Alcance que queda por demostrar

La aceptación debe confirmar que Windows monta el disco, lee los bytes
esperados y recibe cambios del mando durante esas lecturas. El monitor
exige mismo padre USB, revisión `0207`, nonce común y lecturas sin caché.
Un hash correcto sin movimiento no pasa la aceptación combinada.

El disco sigue generado en memoria y protegido contra escritura. Dar
acceso a juegos o partidas reales y devolver saves requiere otra fase.
Las pruebas de software tampoco establecen cómo Sony USBBus trata las
colas en endpoints detenidos, la terminación de un envío de 64 KiB o el
estado EP0 diferido del reset BOT. Son comprobaciones de hardware aún
pendientes; no se han presentado como comportamientos demostrados.

Procedimiento y comandos vigentes:
[USBSTOR + HID 0207](homebrew/composite-mscdemo/README.md).

## Ruta alternativa WinUSB: revisión y evidencia previa

## Qué ha cambiado respecto al prototipo de descriptores

La rama `feat/composite-usb-driver` tiene ahora un **controlador kernel
PSP compilable y un cliente WinUSB real de Windows**. No modifica el
frontend de Claude ni la rama de interfaz nativa de PSP.

Parte de la implementación se recuperó de la rama de trabajo USB
existente y se revisó en un worktree aislado, conservando la fuente
original. Se corrigieron después problemas de ABI, registro/cierre de
USB, concurrencia de buffers y tratamiento de errores del cliente.

El controlador mantiene el gamepad HID y el canal BULK de lectura como
dos interfaces del mismo dispositivo. De este modo no necesita que el
PC monte la Memory Stick: solo el firmware de PSP accede al sistema de
archivos. No se envían comandos SCSI ni se manipula flash.

### Fallo de ABI encontrado y corregido

El prototipo anunciaba dos interfaces, pero su tabla de ajustes no tenía
el formato utilizado por el controlador Sony. No basta con un array de
dos punteros: hace falta un registro `{descriptor, alternate, count}`
por interfaz. El fallo podía compilar perfectamente y aun así impedir
enumeración o provocar lecturas inválidas del controlador.

La auditoría `homebrew/composite-readonly/audit_elf.py` analiza el ELF
MIPS final, comprueba ambos registros de 12 bytes, punteros, tamaños,
direcciones de endpoints, alineación y descriptores full/high-speed.
Los tests mutan deliberadamente el binario para demostrar que detecta
la estructura antigua y un endpoint con dirección incorrecta.

### Guardas que siguen activas

- Entrada solo sin USBSTOR activo; no se inicia el almacenamiento Sony.
- Lecturas a rutas acotadas, cero imports de escritura/remoción/renombrado.
- Misma sesión y secuencia, CRC, longitud y relleno comprobados.
- SHA-256 al final de la transferencia y verificación de la copia PC.
- Publicación en nombre nuevo; nunca sobreescritura de un archivo existente.
- El módulo no libera memoria mientras haya peticiones USB pendientes.
- No hay límite artificial de sesión. START+SELECT o cable perdido paran.
- El ensayo no puede declarar identidad de FAT ni devolución de saves.

## Evidencia obtenida en PC

La compilación PSPDEV produce `dockcomposite.prx` y un EBOOT separado,
revisión 0x0204, con avisos de compilación tratados como errores.
El build incluye:

1. Vectores SHA-256 y pruebas nativas de parser, directorios, sesión y EOF.
2. **20.000 iteraciones de mensajes dañados**, bajo AddressSanitizer y
   UndefinedBehaviorSanitizer, sin errores observados.
3. Dos pruebas cruzadas: el servicio C realmente enlazado en el PRX
   transmite 150.003 bytes al cliente Python y se valida el SHA; si
   cambia el archivo de origen, el destino parcial no se publica.
4. Auditoría de imports y tablas del ELF real.

En Windows se han superado **39 pruebas focales** de cliente, aceptación,
ABI, límites, diagnóstico y empaquetado. La regresión completa contiene
**484 casos: 482 pasan en Windows y los dos casos cruzados C/Python se
ejecutan y pasan por separado en WSL**. Las tres pruebas JavaScript del
XMB también pasan. No se ha alterado la interfaz de Claude.

El primer intento desde el checkout limpio encontró dependencias locales
ausentes: binarios históricos, el perfil de controles y un acceso retirado.
Se copiaron exclusivamente esos fixtures de prueba del PC al worktree,
comprobando hashes y manteniéndolos excluidos de Git; no se ejecutaron
instaladores ni binarios históricos. Una clonación limpia sigue necesitando
esos fixtures para la suite histórica completa. Las pruebas de este nuevo
controlador y su build son independientes de las ROMs y partidas reales.

## Todavía NO demostrado

La PSP física no ha ejecutado este controlador revisado. No hay todavía
evidencia de que Windows enlace automáticamente WinUSB, de rendimiento
por el cable ni de lectura simultánea con el HID en hardware real.

Tampoco se implementaron escritura/devolución de guardados por BULK,
apagado de pantalla, arranque automático del producto a partir del
canal nuevo o cierre remoto de la PSP. El cliente y pruebas los marcan
como no disponibles; no se simula su éxito.

## Una sola prueba física agrupada

Con una Memory Stick de prueba respaldada, conservar el PSP Dock estable
y añadir únicamente la carpeta `PSP/GAME/PSPDockCompositeTest` del paquete
verificado. No modificar ARK, plugins ni drivers Windows.

Abrir la aplicación de prueba con USB Sony apagado y cable retirado;
pulsar X y esperar `READY - now connect USB`. Conectar una vez y comprobar
la enumeración con `psp_composite_diagnostics.py --firmware`.

Si hay HID y WinUSB válidos, ejecutar `psp_composite_acceptance.py --run`
con un directorio de salida nuevo del PC. Mover la cruceta mientras se
lee repetidamente el fichero ficticio. La evidencia debe indicar:

- `result=verified`;
- `physical_usb_transfer_verified=true`;
- `same_hid_bulk_session=true`;
- `gamepad_motion_observed=true`;
- cero escritura PSP y SHA-256 idéntico.

Terminar con START+SELECT, observar la salida segura y repetir una
reconexión controlada en una sesión nueva. Si falta WinUSB, falla la
copia o la PSP queda negra, guardar el código y parar ese ensayo;
no reinstalar/expulsar drivers ni repetir operaciones a ciegas.

Los comandos, el formato y las limitaciones están detallados en
`homebrew/composite-readonly/README.md`.
