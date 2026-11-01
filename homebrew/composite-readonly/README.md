# PSP Dock Composite RO — controlador experimental ejecutable

**Estado: PRX y EBOOT compilados; enumeración física todavía no demostrada.**
Esta carpeta contiene un controlador PSP real, no solo descriptores o
una máquina de estados. No sustituye al cliente PSP Dock estable.

La configuración USB anuncia simultáneamente:

| Interfaz | Función | Endpoints |
|---|---|---|
| 0 | Mando HID compatible con el PSP Dock ya probado | interrupt IN 0x81 |
| 1 | Canal de archivos de solo lectura, WinUSB | bulk IN 0x82, OUT 0x03 |

La PSP conserva la propiedad de FAT. **No se inicia Sony USBSTOR**, no
se expulsa ningún volumen de Windows, y el módulo kernel no importa
funciones de escritura, borrado, renombrado, formateo o Devctl de archivos PSP.
El juego sigue controlándose por HID mientras el hilo de archivos lee
datos. START+SELECT y pérdida de cable solicitan la parada; **no hay
temporizador de sesión**. Los límites de espera al liberar buffers son
guardas de cierre, no un límite para jugar.

## Correcciones de bajo nivel revisadas

La tabla interna de Sony necesita **un registro de ajustes por interfaz**:
`{descriptor, alternateSetting, numDescriptors}`. El antiguo prototipo
usaba una estructura ambigua basada en `UsbInterfaces` del SDK. Ahora
hay dos registros de 12 bytes, cada uno con un alternate setting, y una
auditoría del **ELF MIPS enlazado** comprueba los punteros reales.
La estructura se contrastó con el controlador PSP UVC de xerpi y con
el adaptador FuSa que ya funciona en esta PSP; no se copió código UVC.

Se anuncia USB 2.0 también a velocidad full-speed: versión USB no es
lo mismo que velocidad del enlace. Windows puede no consultar los
Microsoft OS Descriptors si el dispositivo dice USB 1.0/1.1. La revisión
del dispositivo es **0x0204** y el cliente Windows exige esa revisión.

Los buffers USB pertenecen al controlador hasta completar/cancelar
las peticiones. La recepción OUT se reclama atómicamente; EP0 termina
de encolar antes de CancelAll; el módulo no se descarga con DMA activo.
La tabla de interfaces/dispositivo/endpoints está alineada a 64 bytes.
Si aparece USBSTOR mientras se lee, el hilo rechaza continuar.

## Protocolo de archivos

Comandos PDB2: HELLO, LIST, STAT y READ. El EOF contiene el SHA-256
calculado por la PSP sobre los bytes enviados. El PC lo verifica y
vuelve a leer su copia antes de publicarla en un nombre nuevo.

Cada paquete lleva sesión de 128 bits, secuencia, offset, longitud y
CRC32. Sesión equivocada, secuencia repetida, paquete parcial, timeout,
relleno no nulo o metadatos incoherentes invalidan la sesión, sin reintento
ciego. El nonce correlaciona interfaces; **no es autenticación criptográfica**.

Los archivos permitidos son recursos de juego/SAVEDATA en rutas acotadas.
El ensayo inicial usa exclusivamente `PROBE.DAT`, generado en el PC:

- 150.003 bytes, patrón determinista sin datos de juegos ni usuarios.
- SHA-256: `e2d2ed8fba5bff4fc1e3e2cf9fd4508a6e3fd8f2d8429d44196c6b7ec8ba001e`.
- Directorio previsto: `PSP/GAME/PSPDockCompositeTest`.

No hay devolución de guardados, cierre remoto ni comandos mutantes en
esta versión. El protocolo no certifica el número de serie de la Memory
Stick: ese dato se devuelve desconocido, en lugar de inventarlo.

## Compilación

En el entorno PSPDEV del proyecto:

```sh
bash homebrew/composite-readonly/build-wsl.sh
```

El script ejecuta pruebas nativas C, 20.000 casos de entrada dañada con
AddressSanitizer/UndefinedBehaviorSanitizer, pruebas cruzadas C→Python,
compila el PRX y el EBOOT, audita los imports de ambos y verifica las
tablas internas del ELF. Los binarios permanecen en `dist/`, ignorado
por Git. No hay paso de instalación en consola ni de registro de drivers.

El build utiliza su propia carpeta WSL `composite-reviewed-v3`, separada
de otras ramas activas. PSPDEV se puede indicar mediante `PSPDEV`.

## Cliente y ensayo Windows

Desde la raíz del repositorio:

```powershell
python psp_composite_diagnostics.py
python psp_composite_acceptance.py
```

Ambos comandos, por defecto, solo consultan presencia/estado Windows.
No cambian drivers. Tras iniciar físicamente el controlador de prueba:

```powershell
python psp_composite_diagnostics.py --firmware
python psp_composite_acceptance.py --run --rounds 16 --output C:\PSP-Dock-Tests\sesion-nueva
```

El directorio de salida debe ser nuevo y estar en un disco fijo del PC.
El ensayo coteja nonce HID/BULK, exige ausencia de USBSTOR, repite la
lectura del archivo ficticio y toma muestras del mando durante la copia.
Genera evidencia tanto si termina como si falla. Una prueba simulada
nunca informa `physical_usb_transfer_verified=true`.

## Preparación de paquete, sin instalarlo

```powershell
python psp_composite_package.py --prepare C:\PSP-Dock-Tests\paquete-nuevo
```

El paquete incluye EBOOT, PRX, PROBE.DAT y licencia. Verifica hashes y el
ELF antes de prepararlo. Este paso no toca la Memory Stick.

Para preparar la ejecución física existe un instalador separado:

```powershell
python psp_composite_install.py --drive G:\ --package C:\PSP-Dock-Tests\paquete-nuevo --backup C:\PSP-Dock-Backups\snapshot-verificado --install --receipt C:\PSP-Dock-Tests\instalacion.json
```

Comprueba la identidad Sony y del volumen, relee el respaldo y coteja los
archivos críticos con su estado actual. Solo crea la carpeta nueva
`PSP/GAME/PSPDockCompositeTest`, verifica todos sus hashes y rechaza un
destino existente. La aplicación estable, los plugins y las partidas
no forman parte de la instalación. Copiar el ensayo no lo ejecuta.

El observador de Windows puede esperar a que se abra el homebrew y ejecutar
una sola prueba agrupada al aparecer la interfaz:

```powershell
python psp_composite_watch.py --wait --seconds 1800 --rounds 32 --output C:\PSP-Dock-Tests\ensayo-nuevo
```

Guarda el estado PnP incluso si WinUSB no llega a aparecer, además de la
evidencia de transferencia cuando existe. Un fallo termina el ensayo,
sin reinstalar drivers ni repetir comandos de archivos. La pantalla PSP
muestra contadores de peticiones de control y los campos del último
SETUP para ayudar a localizar fallos de enumeración.

## Pendientes que solo puede resolver la prueba física

1. Que USBBus/ARK entregue los descriptores y peticiones de control como
   esperamos, incluyendo la cadena Microsoft OS 0xEE.
2. Que Windows cree el padre compuesto, HID y WinUSB sin reconfigurar
   manualmente el controlador antiguo.
3. Que HID continúe respondiendo durante lecturas BULK reales.
4. Que START+SELECT y la pérdida de cable liberen todos los buffers.

Si no aparece WinUSB, registrar la enumeración y corregir firmware;
no recurrir a Zadig, rebind del padre, eject o AutoRun a ciegas.

## Licencia y referencias

El descriptor y codificación HID derivan del adaptador FuSa GamePad
del proyecto, upstream `97a1fccb8cb561b14964de4d4dbab4fcd8618297`.
Estos archivos se distribuyen como GPL-3.0-only; la licencia completa
está en `../bridge-hid/LICENSE` y se incluye en el paquete binario.

Referencias para ABI y protocolo USB:

- https://github.com/xerpi/psp-uvc-usb-video-class/blob/68f950aeee405e8a0dbec24cce0deddfce96aaec/include/usb.h
- https://github.com/xerpi/psp-uvc-usb-video-class/blob/68f950aeee405e8a0dbec24cce0deddfce96aaec/include/usb_descriptors.h
- https://pspdev.github.io/pspsdk/pspusbbus_8h.html
- https://learn.microsoft.com/en-us/windows-hardware/drivers/usbcon/automatic-installation-of-winusb
- https://techcommunity.microsoft.com/blog/microsoftusbblog/how-does-usb-stack-enumerate-a-device/270685
