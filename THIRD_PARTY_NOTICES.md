# Componentes y licencias de terceros

La licencia [PolyForm Noncommercial 1.0.0](LICENSE.md) se aplica al código **original** para el que el autor de PSP Dock puede concederla. No sustituye ni restringe las licencias de terceros ni los derechos de sus titulares. Los archivos con encabezado `SPDX-License-Identifier` explícito y las licencias en subcarpetas tienen prioridad para esos materiales y obras derivadas. Un ejecutable que combine varias piezas debe satisfacer las obligaciones de **todas** las licencias aplicables.

## FuSa GAMEPAD — GPL-3.0-only

El driver HID y partes del homebrew de PSP se desarrollaron a partir de FuSa GAMEPAD (Andy_maN, referencia de origen `97a1fccb8cb561b14964de4d4dbab4fcd8618297`). Las adaptaciones que conservan avisos **GPL-3.0-only** se distribuyen conforme a esa licencia, **no** bajo la prohibición comercial de PolyForm.

En la versión final se incluyen los archivos derivados bajo `homebrew/native-dock/`, especialmente el controlador HID, las tablas de descriptores y los componentes relacionados. Los avisos de origen permanecen en sus cabeceras. El texto completo de la GPL utilizada se conserva en:

- [homebrew/native-dock/LICENSE-FuSa-GPL-3.0.txt](homebrew/native-dock/LICENSE-FuSa-GPL-3.0.txt)

**La GPL permite el uso comercial** sujeto a sus condiciones; no se añade un veto comercial a esos materiales. Si se distribuye el homebrew combinado con el driver GPL, hay que respetar las obligaciones de copyleft y disponibilidad del código fuente correspondiente.

## PSPSDK y tipografías

Los componentes que reutilizan material PSPSDK conservan su aviso BSD-compatible en [homebrew/native-dock/LICENSE-PSPSDK.txt](homebrew/native-dock/LICENSE-PSPSDK.txt). Los recursos de tipografía basados en DejaVu/Bitstream conservan sus avisos en [homebrew/native-dock/assets/LICENSE-DejaVu.txt](homebrew/native-dock/assets/LICENSE-DejaVu.txt).

## PPSSPP, RetroArch y núcleos externos

PSP Dock **no incluye** juegos, BIOS, archivos de firmware de Sony ni los ejecutables de PPSSPP y RetroArch. Sus licencias, las de los núcleos y las de cualquier componente descargado se aplican separadamente; no se relicencian por figurar aquí. Los prototipos Android TV y otros experimentos permanecen en las ramas y commits históricos de Git, no en la versión final mantenida en `main`.

## Alcance práctico

La prohibición de uso comercial y el aviso obligatorio de crédito del proyecto original **no deben interpretarse como una limitación adicional sobre los materiales GPL u otras obras de terceros**. Para usos comerciales del código original de PSP Dock, solicita una licencia al titular correspondiente y revisa por separado todas las dependencias. Esta publicación no garantiza que un paquete compuesto pueda relicenciarse íntegramente bajo un único documento.
