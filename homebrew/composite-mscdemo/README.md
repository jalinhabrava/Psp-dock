# PSP Dock: USBSTOR + HID, prueba 0207

**Estado: 0207 instalada y releída por SHA-256; aceptación física pendiente.**
Nombre XMB: **PSP Dock - USB Storage + HID 0207**. La carpeta sigue siendo
`PSP/GAME/PSPDockStorageTest`, separada del PSP Dock estable.

La instalación contiene cuatro archivos y **368.243 bytes**. El título SFO
y los bytes exactos de ICON0, PIC0 y PIC1 están comprobados dentro del EBOOT.
Recibo: `data/msc-0207-evidence/installation.json`. La 0206 quedó archivada
antes del reemplazo. Hashes y evidencias en
[la validación del controlador](../../COMPOSITE-DRIVER-VALIDATION.md).

La aplicación presenta un mando HID y almacenamiento estándar
MSC/SCSI/Bulk-Only como dos interfaces del mismo dispositivo USB.
Windows utiliza sus controladores `usbccgp`, `HidUsb` y `USBSTOR`.
El producto diagnóstico `054C:01CC`, revisión `0207`, es distinto al
`01CB` del mando estable y del prototipo de archivos WinUSB.

**El disco es ficticio, de 512 KiB y de solo lectura.** El controlador
genera sus sectores FAT12 en memoria; no abre dispositivos, juegos ni
partidas de la Memory Stick. La fase actual debe confirmar montaje y
lecturas simultáneas con el mando. Dar acceso a la Memory Stick real,
devolver partidas y completar el ciclo del Dock siguen pendientes.

## Qué aportó la prueba física 0206 y qué cambia en 0207

El usuario confirmó esta vez una ejecución estable. La foto conserva
PSP DOCK y la parada solicitada por START+SELECT: 1.187 informes HID,
26 CSW completados y 18.284 bytes relevantes. Windows enumeró el disco
PSPDOCK y llegó a iniciar un PDO de volumen, pero el monitor no observó
una unidad asociada y no ejecutó lecturas de aceptación (`probe_attempts=0`).

El último estado conservado fue `BOT 4 / CDB 1A / Datos 32/192`:
MODE SENSE(6), esperando CSW tras respuesta IN corta. Los eventos SCSI
Windows aportan el mismo CDB con un reintento; no prueban por sí solos
el defecto exacto del endpoint Sony.

0207 envía relleno cero en BOT caso 5 hasta la longitud pedida por el host,
y conserva como residuo los bytes no relevantes. MODE SENSE 32/192 termina
192 bytes USB y declara residuo 160; EVPD 7/255 declara 248. No genera CSW
antes de confirmar el envío completo. Los contadores útiles excluyen el
relleno. La opción está permitida por USB-IF BOT 1.0, sección 6.7.2.
Añade READ CAPACITY(16) servicio 0x10, que Windows solicitó en este intento.

Los casos de fase, consultas IN sin datos y CBW inválidos conservan sus
reglas HALT/reset. El diagnóstico Windows ahora distingue disco CIM,
particiones y letras; los volúmenes globales sin padre correlacionado no
habilitan el probe. La UI usa coordenadas en píxeles y separación vertical;
se verificó con el bitmap real del SDK, pendiente de verlo en la PSP.

## Qué se observó en la PSP con 0205

El 1 de noviembre de 2026, a las 22:19:45.83–22:19:45.93 de Madrid,
Windows registró el padre `usbccgp`, `MI_00` con `HidUsb`, `MI_01` con
`USBSTOR` y el mando HID. A las 22:20:06.028 apareció el disco SCSI
`PSPDOCK MSC_HID_TEST REV0001`; unos 47 ms después se retiraron los
dispositivos. El homebrew volvió al XMB sin reiniciar la consola.

La identidad del disco coincide con nuestra respuesta SCSI INQUIRY,
por lo que se observó tráfico SCSI sobre BULK. **No se verificaron montaje
del volumen, lectura de `PROBE.DAT` ni cambios HID durante una lectura.**
El monitor anterior había expirado antes de ese intento. Un timeout de
transporte seguido de reset/desconfiguración es una hipótesis; la captura
PnP no demuestra por sí sola la causa de la salida.

## Recuperación y resultado visible en 0206

PSPSDK documenta `attach`/`detach` como configuración establecida/retirada.
0205 interpretaba `detach` como desconexión física y cerraba la prueba.
0206 pausa HID y BULK, cancela las peticiones de la configuración anterior
y espera sus callbacks antes de reutilizar los buffers. Una nueva
configuración puede reanudar la sesión; EP0 y los resets BOT se distinguen
por generación para no cancelar una solicitud nueva al limpiar la antigua.

La pantalla identifica PSP DOCK con una cabecera de 21 píxeles y muestra
**Windows reconfigura USB; espera...** durante ese cambio. El cierre por
cable exige 500 ms continuos de ausencia de la señal física.
START+SELECT durante un segundo sigue solicitando una salida
explícita, y los errores de transporte conservan su código.

Al terminar, la aplicación mantiene la pantalla con motivo de parada,
configuraciones/desconfiguraciones, último SCSI, bytes y resultados RX/TX.
**Fotografiar o anotar ese resultado antes de pulsar O para volver al XMB.**
La aplicación exige liberar y volver a pulsar O; no cierra inmediatamente
al completar la descarga del módulo.

## Nombre, thumbnail y fondo

El nuevo diseño usa grafito/negro, texto blanco y un acento teal discreto.
**PSP DOCK** aparece grande y junto en ICON0 y PIC0. El fondo PIC1 no
lleva texto: así la composición del XMB no duplica el nombre.

| Sección XMB | Recurso | Dimensiones | Diseño |
|---|---|---|---|
| Thumbnail | `assets/ICON0.PNG` | 144 × 80 | Nombre PSP DOCK grande |
| Presentación | `assets/PIC0.PNG` | 310 × 180 | Placa grafito con nombre/subtítulo y margen exterior transparente |
| Fondo | `assets/PIC1.PNG` | 480 × 272 | Grafito sin texto y espacio despejado para el XMB |

Los tres recursos se incrustan en `EBOOT.PBP`. La auditoría compara sus
bytes y dimensiones con las fuentes de `assets/`; el instalador verifica
el EBOOT completo por SHA-256. El título XMB contiene la revisión para
identificar la prueba aunque todavía no se haya mostrado el fondo.

## Arquitectura y límite de la prueba

- `msc_scsi.c`: motor C puro de BOT/SCSI y generador FAT12, sin API de archivos.
- `msc_driver.c`: wrapper kernel del bus Sony, endpoints y callbacks USB.
- `app.c`: aplicación USER, carga diferida del PRX y pantalla de diagnóstico.
- `audit_elf.py`: comprueba el ELF MIPS compilado, las tablas ABI Sony,
  los imports, los recursos XMB y una imagen FAT12 de referencia.
- `psp_msc_acceptance.py` en la raíz: monitor y aceptación física de Windows.
- `psp_msc_package.py` / `psp_msc_install.py`: paquete verificable, instalación
  nueva y actualización de la prueba mediante su recibo previo.

La interfaz 0 es HID, endpoint `81`, con intervalo declarado de 4 ms tanto
en full speed como en high speed. La interfaz 1 es MSC `08/06/50`, con
endpoints `82` IN y `03` OUT. El intervalo es una declaración del descriptor,
no una medición de latencia física. No se publican descriptores Microsoft
OS para WinUSB.

El kernel no enlaza ningún `sceIo*`. Rechaza comandos de escritura y
publica el bit SCSI de protección contra escritura. Un CBW inválido deja
el transporte esperando Reset Recovery; un reset BOT conserva los HALT y
toggles hasta las operaciones estándar del host. La conducta exacta del
bus Sony respecto a endpoints detenidos, fin de paquetes/64 KiB y estado
EP0 diferido requiere comprobación física.

| Archivo del disco ficticio | Contenido |
|---|---|
| `PROBE.DAT` | 150.003 bytes, patrón `(offset * 17 + 3) & 255` |
| `SESSION.BIN` | 16 bytes, misma sesión que el estado HID PDK1 |

SHA-256 independiente de `PROBE.DAT`:

```text
e2d2ed8fba5bff4fc1e3e2cf9fd4508a6e3fd8f2d8429d44196c6b7ec8ba001e
```

## Compilar y verificar en PC

Con PSPDEV instalado en WSL:

```bash
bash homebrew/composite-mscdemo/build-wsl.sh
```

El build usa una carpeta propia y trata los avisos como errores. Ejecuta
los 13 casos de fase BOT, comandos SCSI, límites, residuos, reset, FAT12 y
50.000 entradas malformadas bajo AddressSanitizer y UndefinedBehaviorSanitizer.
El wrapper real se compila en un harness con 14 escenarios asíncronos,
incluidos desconfiguración/reconfiguración y cancelación de EP0. Un parser
Python separado recorre las cadenas FAT y comprueba los dos archivos de
la imagen generada. También se auditan los imports reales del kernel y
de la aplicación USER y los bytes incrustados de los tres recursos XMB.

Estas comprobaciones pasan en WSL para 0207. En Windows pasan también
**66 pruebas focales: 18 del instalador y 48 de aceptación, diagnóstico y
paquetes**. Los otros recuentos históricos pertenecen a revisiones
anteriores, como detalla
[la validación del controlador](../../COMPOSITE-DRIVER-VALIDATION.md).

`dist/` contiene `EBOOT.PBP`, `dockmsc.prx`, los ELF y evidencia de build.
Compilar no instala ni ejecuta la aplicación en una PSP.

## Preparar el paquete

En Windows, elegir una carpeta nueva de un disco fijo del PC. Sustituir
las rutas de ejemplo y la letra de unidad por las verificadas en esa sesión:

```powershell
python psp_msc_package.py --prepare C:\ruta\paquete-msc-0207
```

### Instalación nueva

Utilizar esta opción solo si no existe `PSP/GAME/PSPDockStorageTest`:

```powershell
python psp_msc_install.py --package C:\ruta\paquete-msc-0207 --backup C:\ruta\backup-verificado --drive G:\
python psp_msc_install.py --package C:\ruta\paquete-msc-0207 --backup C:\ruta\backup-verificado --drive G:\ --install --receipt C:\ruta\instalacion-msc-0207.json
```

La primera llamada valida el plan sin instalar. Con `--install` se copian
y releen los cuatro archivos del paquete. Una carpeta existente se
rechaza: para reemplazar una prueba revisada se utiliza la actualización
por recibo descrita a continuación.

### Actualizar la prueba 0206 instalada

Se necesita el recibo real de aquella instalación y una carpeta de archivo
**nueva en un disco fijo del PC**, fuera de la PSP:

```powershell
python psp_msc_install.py --package C:\ruta\paquete-msc-0207 --backup C:\ruta\backup-verificado --drive G:\ --upgrade-from-receipt C:\ruta\instalacion-msc-0206.json --archive C:\ruta\archivo-msc-0206
python psp_msc_install.py --package C:\ruta\paquete-msc-0207 --backup C:\ruta\backup-verificado --drive G:\ --upgrade-from-receipt C:\ruta\instalacion-msc-0206.json --archive C:\ruta\archivo-msc-0206 --install --receipt C:\ruta\instalacion-msc-0207.json
```

El plan verifica identidad USB/volumen, backup crítico vigente y los
cuatro archivos exactos del recibo anterior. Rechaza archivos distintos,
extras o enlaces. La actualización guarda primero la versión anterior en
`archivo-msc-0206/files/`, verifica sus hashes y deja las fases en
`archivo-msc-0206/upgrade.jsonl`.

Después prepara la nueva carpeta y publica mediante dos renombrados en
la misma Memory Stick. Si falla la publicación o el journal tras el primer
renombrado, intenta restaurar la versión anterior solo cuando la identidad
sigue siendo la misma y el destino está libre. Si cambia la identidad,
conserva los archivos y registra la necesidad de recuperación. La copia
anterior del PC permanece después de una actualización verificada.

Ambas opciones se limitan a `PSP/GAME/PSPDockStorageTest`, sin sustituir
`PSP/GAME/PSPDock` ni configurar plugins. El thumbnail, la presentación
y el fondo viajan dentro del EBOOT.

## Prueba física agrupada

Después de instalar y justo antes del intento, abrir un monitor nuevo en
el PC y confirmar que sigue esperando. Su espera máxima es de 30 minutos;
una ejecución que ya terminó no captura un intento posterior.

```powershell
python psp_msc_acceptance.py --run --output C:\ruta\evidencia-msc-0207-nueva --rounds 8 --min-duration-seconds 15 --wait-seconds 1800
```

1. En la PSP, salir de Conexión USB y retirar el cable.
2. En Juego → Memory Stick, seleccionar **PSP Dock - USB Storage + HID 0207**.
   Comprobar thumbnail y fondo; abrir y pulsar X cuando lo solicite.
3. Esperar **LISTO: conecta el cable USB** y conectar una vez.
4. Mover la cruceta y pulsar botones durante un minuto mientras
   Windows monta el disco y el monitor lee `PROBE.DAT`. Si la pantalla
   indica reconfiguración, dejar que termine y conservar su diagnóstico.
5. Mantener START+SELECT un segundo para solicitar el cierre. Esperar a
   que el controlador complete sus callbacks y descargue el módulo.
6. Fotografiar o anotar la pantalla final. Liberar y pulsar **O** para
   volver al XMB.

El monitor conserva `before.json`, `latest.json`, `after.json` y
`monitor.json` aunque no llegue a montarse una unidad. Añade
`transitions.jsonl`: estados observados que cambian, con secuencia,
marca UTC y tiempo transcurrido, sin reemplazar los anteriores. Así una
captura final vacía no borra los dispositivos vistos previamente.
También conserva las etapas CIM y los inventarios globales no correlacionados,
con hora inicial y final de cada observación. Un error de asociación CIM
bloquea la aceptación, y los candidatos globales nunca abren archivos.
El límite del historial es 8 MiB; si se alcanza, se detiene la observación
con la evidencia ya registrada.

Cuando una unidad candidata y su HID están preparados, el monitor ejecuta
una única aceptación y guarda los archivos y `evidence.json` en `probe/`.
Exige mismo padre USB, revisión `0207`, producto `01CC`, etiqueta/serial
del disco y nonce PDK1. Las lecturas usan archivos normales con
`FILE_FLAG_NO_BUFFERING` para comprobar los bytes a través del dispositivo.
También deben observarse cambios de botones o cruceta durante las lecturas:
sin ellos el resultado es `motion_not_observed`, aunque los hashes coincidan.

La aceptación combinada requiere `result=verified`,
`physical_usb_transfer_verified=true` y
`simultaneous_hid_storage_verified=true`. Si no se completa, conservar el
resultado del PC y la pantalla PSP para localizar el punto de interrupción.
El monitor no cambia controladores ni repite transferencias fallidas.

`synthetic_storage=true` y `real_memory_stick_access=false` permanecen
explícitos en la evidencia. La compilación y los tests no sustituyen esta
aceptación física ni prueban el Dock completo con juegos y partidas reales.

## Referencias primarias

- [USB-IF, Bulk-Only Transport 1.0](https://www.usb.org/sites/default/files/usbmassbulk_10.pdf)
- [T10, formato READ CAPACITY(16)](https://www.t10.org/ftp/t10/document.03/03-387r2.pdf)
- [PSPSDK, UsbDriver y callbacks de configuración](https://pspdev.github.io/pspsdk/structUsbDriver.html)
- [PSPSDK, estados USB y señal de cable](https://pspdev.github.io/pspsdk/pspusb_8h_source.html)
- [Microsoft, identificadores generados por USBSTOR](https://learn.microsoft.com/en-us/windows-hardware/drivers/install/identifiers-generated-by-usbstor-sys)
- [PSP UVC, precedente de tablas de interfaces Sony](https://github.com/xerpi/psp-uvc-usb-video-class)
