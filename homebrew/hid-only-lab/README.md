# PSP Dock — HID-only LAB

Laboratorio autocontenido para dar un salto hacia PSP como mando USB.
NO es el dock automático ni prueba USBSTOR→HID sobre la misma sesión:
**jamás activa USBSTOR**, ni lee o escribe FAT, SAVEDATA o flash.
Se lanza manualmente desde Juegos **fuera del modo USB nativo**.

Incluye EBOOT USER sin importaciones USB/kernel, el kernel
`hidonlycore.prx` y el controlador `bridgehid.prx` existente
derivado de FuSa GamePad (GPL-3.0). Hay que mantener sus créditos
y condiciones de licencia.

La prueba HID solo comienza si no hay cable ni USB activo, y
`USBStor_Driver` está ausente o parado. El kernel puede iniciar
`USBBusDriver` si está parado, pero **no** inicia/detiene USBSTOR.
Abre PSPDockHID v1 con sesión fresca, envía botones y ejes,
y detiene HID automáticamente a los 90 segundos como máximo,
al mantener START+SELECT o cuando se quita el cable después de
haber estado conectado.

## Primera prueba física (después del ensayo K1 Proof)

1. En el XMB, fuera del modo USB de Sony, y con el cable desconectado,
   abrir `PSP Dock - HID Only LAB` desde Juegos. Si hay pantalla
   negra o apagado, no repetir la prueba: apagar completamente y
   reiniciar con START para saltar plugins; la aplicación no se
   autoarranca y no toca flash.
2. Si aparece la pantalla de instrucciones sin error de carga,
   conectar el cable USB a Windows.
3. En el PC consultar `bridge_hid.py` o el comprobador HID específico
   para ver si Windows reconoce `PSP Dock HID` (PDK1) y los botones.
   **No utilizar expulsión PnP**, pues no existe almacenamiento
   expuesto durante este test.
4. La prueba caduca automáticamente a los 90 segundos. También se
   detiene con START+SELECT o al desenchufar después de conexión.
5. Volver al XMB. Para reabrir USB nativo, esperar a que la aplicación
   haya terminado, y entonces seleccionar el modo USB de Sony.

El primer arranque es un test con riesgos de compatibilidad PSP
kernel: puede fallar la carga del PRX, o requerir un reinicio para
restaurar el bus. No se debe probar antes de completar el checkpoint
K1 Proof armado y verificar un backup reciente. No hay pruebas
físicas realizadas todavía con este modo.

**Criterio de éxito:** Windows enumera VID 054C/PID 01CB,
descriptor gamepad de PSP Dock e informe de estado PDK1 válido,
sin una unidad `G:` PSP montada simultáneamente, y puede
observar cambios de entrada al pulsar botones.
