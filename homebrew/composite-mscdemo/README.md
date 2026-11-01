# PSP Dock: USBSTOR + HID, prueba 0205

Esta aplicación prueba **dos interfaces simultáneas del mismo cable USB**:
un mando HID y almacenamiento estándar MSC/SCSI/Bulk-Only. Windows debe
usar sus controladores `usbccgp`, `HidUsb` y `USBSTOR`. El producto de
diagnóstico `054C:01CC`, revisión `0205`, es distinto al `01CB` del mando
estable y del prototipo de archivos WinUSB, para separar sus asociaciones.

**El disco es ficticio, de 512 KiB y de solo lectura.** El controlador
genera sus sectores FAT12 en memoria; no abre dispositivos, juegos ni
partidas de la Memory Stick. Esta fase comprueba enumeración y lecturas
simultáneas con el mando. Dar acceso a la Memory Stick real, devolver
partidas y completar el ciclo del Dock siguen siendo pasos posteriores.

## Thumbnail y fondo incluidos

El EBOOT incorpora el diseño existente de PSP Dock:

| Sección XMB | Recurso | Dimensiones |
|---|---|---|
| Thumbnail | `assets/ICON0.PNG` | 144 × 80 |
| Presentación | `assets/PIC0.PNG` | 310 × 180 |
| Fondo | `assets/PIC1.PNG` | 480 × 272 |

Los archivos se reutilizan sin modificación del trabajo de interfaz
`homebrew/psp-dock-app`. Se conserva su generador y la licencia de DejaVu.
La auditoría compara los bytes originales con las secciones incrustadas
en el PBP; el instalador vuelve a verificar el EBOOT completo por SHA-256.
El título visible es **PSP Dock USB Storage + HID TEST**. La pantalla de
ejecución muestra estado USB, contadores y códigos útiles para esta prueba.

## Arquitectura y límite de la prueba

- `msc_scsi.c`: motor C puro de BOT/SCSI y generador FAT12, sin API de archivos.
- `msc_driver.c`: wrapper kernel del bus Sony, endpoints y callbacks USB.
- `app.c`: aplicación USER, carga diferida del PRX y pantalla de diagnóstico.
- `audit_elf.py`: comprueba el ELF MIPS compilado, las tablas ABI de Sony,
  los imports, las imágenes XMB y una imagen FAT12 de referencia.
- `psp_msc_acceptance.py` en la raíz: monitor y aceptación física de Windows.
- `psp_msc_package.py` / `psp_msc_install.py`: paquete verificable y copia en
  una carpeta nueva de una PSP identificada con backup crítico vigente.

La interfaz 0 es HID, endpoint `81`, con intervalo declarado de 4 ms tanto
en full speed como en high speed. La interfaz 1 es MSC `08/06/50`, con
endpoints `82` IN y `03` OUT. El intervalo es una declaración del descriptor,
no una medición de latencia física. Esta variante no publica descriptores
Microsoft OS para WinUSB.

El kernel no enlaza ningún `sceIo*`. Rechaza comandos de escritura y
publica el bit SCSI de protección contra escritura. Un CBW inválido deja
el transporte esperando Reset Recovery; un reset BOT conserva los HALT y
toggles hasta las operaciones estándar del host. La implementación exacta
de esas operaciones por el bus Sony requiere comprobación en hardware.

| Archivo del disco ficticio | Contenido |
|---|---|
| `PROBE.DAT` | 150.003 bytes, patrón `(offset * 17 + 3) & 255` |
| `SESSION.BIN` | 16 bytes, misma sesión que el estado HID PDK1 |

SHA-256 independiente de `PROBE.DAT`:

```text
e2d2ed8fba5bff4fc1e3e2cf9fd4508a6e3fd8f2d8429d44196c6b7ec8ba001e
```

## Compilar

Con PSPDEV instalado en WSL:

```bash
bash homebrew/composite-mscdemo/build-wsl.sh
```

El build usa su propia carpeta temporal y trata los avisos como errores.
Ejecuta los 13 casos de fase BOT, comandos SCSI, límites de lectura,
residuos de CSW, reset, FAT12 y 50.000 entradas malformadas bajo
AddressSanitizer y UndefinedBehaviorSanitizer. Un parser Python separado
recorre las cadenas FAT y compara los dos archivos de la imagen generada.
También audita los imports reales del kernel y de la aplicación USER.

`dist/` contiene `EBOOT.PBP`, `dockmsc.prx`, los ELF y evidencia de build.
No se instala ni ejecuta firmware como efecto de compilar.

## Preparar e instalar la carpeta nueva

Desde Windows, elegir destinos **nuevos del PC**:

```powershell
python psp_msc_package.py --prepare C:\ruta\paquete-msc-0205
python psp_msc_install.py --package C:\ruta\paquete-msc-0205 --backup C:\ruta\backup-verificado --drive G:\
python psp_msc_install.py --package C:\ruta\paquete-msc-0205 --backup C:\ruta\backup-verificado --drive G:\ --install --receipt C:\ruta\instalacion-msc-0205.json
```

La primera llamada al instalador solo valida el plan. Con `--install`
copia y relee cuatro archivos en `PSP/GAME/PSPDockStorageTest`.
Una carpeta existente se rechaza. La identidad USB y el backup se
comprueban antes de la publicación; no se configura ningún plugin.
El thumbnail, presentación y fondo viajan dentro del EBOOT.

## Prueba física agrupada

Preparar antes el monitor del PC:

```powershell
python psp_msc_acceptance.py --run --output C:\ruta\evidencia-msc-nueva --rounds 8 --min-duration-seconds 8 --wait-seconds 1800
```

1. En la PSP, salir de Conexión USB y retirar el cable.
2. En Juego → Memory Stick, seleccionar **PSP Dock USB Storage + HID TEST**.
   Comprobar thumbnail y fondo; abrir y pulsar X cuando lo solicite.
3. Esperar **LISTO: conecta el cable USB** y conectar una vez.
4. Mover la cruceta y pulsar botones durante al menos 10 segundos mientras
   Windows monta el disco y el monitor copia `PROBE.DAT`.
5. Mantener START+SELECT un segundo para solicitar la salida. La aplicación
   espera a los callbacks USB antes de descargar el módulo.

El monitor guarda `before.json`, `latest.json`, `after.json` y
`monitor.json` aunque Windows no llegue a montar la unidad. Si hay una
unidad candidata, ejecuta una única aceptación y guarda sus archivos en
`probe/`. No cambia drivers ni repite transferencias fallidas a ciegas.

Para aceptar la sesión deben coincidir el padre USB de HID y USBSTOR,
revisión `0205`, producto `01CC`, etiqueta/serial del disco y nonce PDK1.
Las lecturas usan archivos normales con `FILE_FLAG_NO_BUFFERING`, para
no confundir la caché de Windows con tráfico físico. Además, deben
observarse cambios de botones o cruceta durante las lecturas. Sin movimiento
el resultado será `motion_not_observed`, aunque los hashes sean correctos.

`synthetic_storage=true` y `real_memory_stick_access=false` permanecen
explícitos en la evidencia. La compilación y los tests no sustituyen esta
aceptación física ni prueban el Dock completo con juegos y partidas reales.

## Referencias primarias

- [USB-IF, Bulk-Only Transport 1.0](https://www.usb.org/sites/default/files/usbmassbulk_10.pdf)
- [PSPSDK, UsbDriver](https://pspdev.github.io/pspsdk/structUsbDriver.html)
- [Microsoft, identificadores generados por USBSTOR](https://learn.microsoft.com/en-us/windows-hardware/drivers/install/identifiers-generated-by-usbstor-sys)
- [PSP UVC, precedente de tablas de interfaces Sony](https://github.com/xerpi/psp-uvc-usb-video-class)
