# Componentes y licencias de terceros

La licencia [PolyForm Noncommercial 1.0.0](LICENSE.md) se aplica al código **original** para el que el autor de PSP Dock puede concederla. No sustituye ni restringe las licencias de terceros ni los derechos de sus titulares. Los archivos con encabezado `SPDX-License-Identifier` explícito y las licencias en subcarpetas tienen prioridad para esos materiales y obras derivadas. Un ejecutable que combine varias piezas debe satisfacer las obligaciones de **todas** las licencias aplicables.

## FuSa GAMEPAD — GPL-3.0-only

El driver HID y partes del homebrew de PSP se desarrollaron a partir de FuSa GAMEPAD (Andy_maN, referencia de origen `97a1fccb8cb561b14964de4d4dbab4fcd8618297`). Las adaptaciones que conservan avisos **GPL-3.0-only** se distribuyen conforme a esa licencia, **no** bajo la prohibición comercial de PolyForm.

Se incluyen, entre otros, los archivos GPL y derivados bajo `homebrew/bridge-hid/`, `homebrew/native-dock/`, `homebrew/composite-mscdemo/` y `homebrew/composite-readonly/` que muestran ese encabezado, así como las partes correspondientes del homebrew PSP Dock. Los avisos originales y el texto de la GPL están disponibles en:

- [homebrew/bridge-hid/LICENSE](homebrew/bridge-hid/LICENSE)
- [homebrew/psp-dock-app/LICENSE-FuSa-GPL-3.0.txt](homebrew/psp-dock-app/LICENSE-FuSa-GPL-3.0.txt)

**La GPL permite el uso comercial** sujeto a sus condiciones; no se añade un veto comercial a esos materiales. Si se distribuye el homebrew combinado con el driver GPL, hay que respetar las obligaciones de copyleft y disponibilidad del código fuente correspondiente.

## PSPSDK y tipografías

Los componentes que reutilizan material PSPSDK conservan su aviso BSD-compatible en [homebrew/usb-probe/LICENSE-PSPSDK.txt](homebrew/usb-probe/LICENSE-PSPSDK.txt). Los recursos de tipografía basados en DejaVu/Bitstream llevan sus respectivos avisos, por ejemplo [homebrew/native-dock/assets/LICENSE-DejaVu.txt](homebrew/native-dock/assets/LICENSE-DejaVu.txt). También se conservan los avisos en las otras carpetas de recursos.

## PPSSPP, RetroArch y núcleos externos

PSP Dock **no incluye** el binario comercial de un juego, BIOS, archivos de firmware de Sony ni los ejecutables de PPSSPP y RetroArch. Sus licencias, las de los núcleos y las de cualquier componente descargado se aplican separadamente; no se relicencian por figurar aquí. Android TV contiene un módulo experimental de integración con PPSSPP y debe someterse a revisión de licencias y compilación antes de distribuir una aplicación binaria; los avisos del SDK usados por ese módulo están en [android-tv/licenses/PPSSPP-LICENSE.txt](android-tv/licenses/PPSSPP-LICENSE.txt).

## Alcance práctico

La prohibición de uso comercial y el aviso obligatorio de crédito del proyecto original **no deben interpretarse como una limitación adicional sobre los materiales GPL u otras obras de terceros**. Para usos comerciales del código original de PSP Dock, solicita una licencia al titular correspondiente y revisa por separado todas las dependencias. Esta publicación no garantiza que un paquete compuesto pueda relicenciarse íntegramente bajo un único documento.
