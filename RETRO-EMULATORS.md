# PSP Dock · emuladores retro PSP → Windows

## Objetivo y alcance de esta PR

Los `EBOOT.PBP` de emuladores **reconocidos** dejan de confundirse con juegos
de PSP y pasan a la categoría **Retro** del XMB. PSP Dock inventaría de forma
limitada sus ROMs y las asocia a un **sistema emulado**, no al ejecutable PSP.
En el PC se usa RetroArch para Windows con un *core* libretro equivalente al
sistema. No se ejecuta el EBOOT PSP como si fuera una ISO de PPSSPP.

| Homebrew de PSP reconocido | Consola(s) detectadas | Núcleo de RetroArch para Windows |
| --- | --- | --- |
| gpSP / gpSP Kai, TempGBA4PSP | GBA | `mgba_libretro.dll` |
| MasterBoy, RIN / PSP GnuBoy | Game Boy, Game Boy Color | `gambatte_libretro.dll` |
| Snes9xTYL, Snes9x Euphoria | SNES | `snes9x_libretro.dll` |
| NesterJ, FCEUltra PSP | NES | `mesen_libretro.dll` |
| PicoDrive | Mega Drive, Master System, Game Gear, 32X | `genesis_plus_gx_libretro.dll` (MD/SMS/GG), `picodrive_libretro.dll` (32X) |
| SMS Plus PSP | Master System, Game Gear | `genesis_plus_gx_libretro.dll` |
| DaedalusX64 | Nintendo 64 | `mupen64plus_next_libretro.dll` |
| PCE for PSP | PC Engine | `mednafen_pce_fast_libretro.dll` |
| PSP2600 | Atari 2600 | `stella_libretro.dll` |

Esta equivalencia es por **plataforma**, no por la compatibilidad binaria de
los emuladores. No implica que sus estados o partidas sean intercambiables.
Un EBOOT desconocido sigue apareciendo como homebrew PSP: no se reclasifica
mediante heurísticas de nombres de ROM o por extensiones ambiguas.

## Flujo operativo

1. Conecta tu PSP en **almacenamiento USB Sony**. La detección lee sus
   `PSP/GAME/*/EBOOT.PBP`, identifica nombres conocidos y, opcionalmente,
   `PARAM.SFO`. Busca ROMs en esas carpetas y subcarpetas hasta profundidad
   limitada. No copia nada durante el inventario.
2. En el XMB, selecciona **Retro → Emulador PSP → ROM**. El menú contextual
   derecho ofrece `Jugar en Windows`, `Copiar ROM al PC` e `Información`.
3. Si la ROM falta en el PC, elige copiar y confirma en el diálogo que muestra
   el progreso. El archivo se copia en `Biblioteca del PC/PSP/GAME/...`.
   Se verifican SHA-256 del origen y del destino; nunca se sobrescribe un
   archivo local diferente. La ROM original no se modifica.
4. En **Ajustes**, introduce la ruta absoluta a `retroarch.exe` de Windows.
   Descarga de manera independiente RetroArch e instala desde su interfaz los
   núcleos correspondientes: deben estar en `cores/` junto al ejecutable.
5. Prepara la comparación de partidas normal del Dock. Cambia manualmente la
   PSP al **mando HID PDK1** (el cambio físico automático USB↔HID sigue
   pendiente). En Retro → ROM pulsa Jugar; PSP Dock comprueba de nuevo PDK1 y
   abre `retroarch.exe -L <core> -f <rom verificada>`.
6. Al cerrar RetroArch, PSP Dock recupera el XMB. Finaliza desde **Salir del
   Dock**, vuelve al USB Sony y completa el flujo seguro existente.

## Seguridad y compatibilidad

- **Nunca** se descargan ROMs, BIOS, ejecutables de PSP, núcleos o juegos.
  Solo se lanza un `retroarch.exe` seleccionado por el usuario y un core local
  con nombre concreto de la lista soportada.
- Los `.gba`, `.gb`, `.gbc`, `.sfc`, `.smc`, `.nes`, `.md`, `.smd`, `.sms`, `.gg`,
  `.32x`, `.z64`, `.n64`, `.v64`, `.pce`, `.a26` y ZIPs con **una sola ROM
  reconocible** se clasifican exclusivamente si corresponden a la plataforma
  del emulador PSP encontrado. Las extensiones `.bin` / `.cue`, carpetas BIOS,
  guardados, capturas y ZIPs ambiguos se ignoran deliberadamente.
- Los ZIPs se inspeccionan mediante su directorio central, sin extraerlos.
  La capacidad real de ejecutar un ZIP depende del núcleo de RetroArch.
- Los guardados internos de **PPSSPP/PSP** conservan su sincronización normal.
  Los `.sav`, `.srm`, SRAM y *savestates* de emuladores retro NO se importan,
  renombran ni devuelven automáticamente a la PSP: existen diferencias de
  formato entre gpSP, mGBA, Snes9xTYL, Snes9x, Daedalus y RetroArch. Hacerlo
  sin un conversor probado podría corromper partidas. RetroArch guardará sus
  propios datos según su configuración en Windows.
- El mando HID PDK1 está verificado por PSP Dock para el selector XMB y para
  PPSSPP. **El perfil de botones dentro de RetroArch puede requerir una
  configuración inicial manual**, según su controlador DirectInput y los
  perfiles instalados. No se modifica el perfil global de RetroArch en esta PR.
- Algunos núcleos pueden necesitar BIOS o configuración adicional; PSP Dock
  no los obtiene. El runtime de RetroArch y sus cores se comprueba por
  existencia de archivo; no es prueba de compatibilidad de cada ROM.
- Los datos retro recordados mientras la PSP está en modo HID no vuelven a
  leer la Memory Stick; solo una copia local antes verificada permite jugar.
  Se conserva el volumen Sony y se evita disparar cualquier comando USB.

## Alcance probado y siguientes iteraciones

Los tests sintéticos cubren clasificación, mapeo, exclusión de homebrew no
reconocido, ZIP sin extracción, SHA-256, rechazo de sobrescrituras, biblioteca
offline, volumen ajeno, core ausente y verificación HID doble antes del spawn;
las pruebas JS aseguran que el menú no copia ni ejecuta al abrirse.

Queda pendiente una **prueba end-to-end en PSP física** de cada familia y
el **perfil de botones RetroArch PDK1** para conseguir cero configuración
manual. También queda pendiente una futura capa de conversores de guardados
por emulador, con copias de seguridad y confirmación antes de escribir en la
PSP, y adaptadores para emuladores desconocidos o ROMs situadas fuera de la
carpeta del homebrew.

Fuentes técnicas: [Homebrew PSP/PPSSPP](https://www.ppsspp.org/docs/getting-started/how-to-get-demos-and-homebrew/),
[RetroArch CLI](https://docs.libretro.com/guides/cli-intro/),
[mGBA](https://docs.libretro.com/library/mgba/),
[mapeo de controladores RetroArch](https://docs.libretro.com/guides/controller-autoconfiguration/).
