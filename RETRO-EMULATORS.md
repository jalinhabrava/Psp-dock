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
| Snes9xTYL, Snes9x Euphoria | SNES | `bsnes_libretro.dll` (GPL) |
| NesterJ, FCEUltra PSP | NES | `mesen_libretro.dll` |
| PicoDrive | Mega Drive, Master System, Game Gear, 32X | `blastem_libretro.dll` (MD/32X), `gearsystem_libretro.dll` (SMS/GG), GPL |
| SMS Plus PSP | Master System, Game Gear | `gearsystem_libretro.dll` (GPL) |
| DaedalusX64 | Nintendo 64 | `mupen64plus_next_libretro.dll` |
| PCE for PSP | PC Engine | `mednafen_pce_fast_libretro.dll` |
| PSP2600 | Atari 2600 | `stella_libretro.dll` |
| CPS1PSP, CPS2PSP, MVSPSP | CPS1, CPS2, Neo Geo MVS | `fbneo_libretro.dll` (**licencia no comercial, descarga automática bloqueada**) |
| MAME4All PSP | Arcade | `mame2003_plus_libretro.dll` (**licencia no comercial, descarga automática bloqueada**) |
| PSPHugo | PC Engine | `mednafen_pce_fast_libretro.dll` |
| RACE! PSP | Neo Geo Pocket | `race_libretro.dll` |

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
4. En **Ajustes → Instalación de emuladores retro** elige `Preguntar`,
   `Automática` o `No instalar`. Si la PSP conectada tiene emuladores
   reconocidos, Dock podrá instalar RetroArch portable y los núcleos de
   licencia aprobada en `<storage_root>/emulators/retroarch/`. También se
   puede indicar un RetroArch externo, que **no se modificará**.
   Los núcleos arcade con licencia no comercial se muestran como pendientes
   de licencia; no se incluyen en el instalador. Más detalles en
   [RETRO-AUTO-INSTALL.md](RETRO-AUTO-INSTALL.md).
5. Prepara la comparación de partidas normal del Dock. Cambia manualmente la
   PSP al **mando HID PDK1** (el cambio físico automático USB↔HID sigue
   pendiente). En Retro → ROM pulsa Jugar; PSP Dock comprueba de nuevo PDK1 y
   abre `retroarch.exe -L <core> -f <rom verificada>`.
6. Al cerrar RetroArch, PSP Dock recupera el XMB. Finaliza desde **Salir del
   Dock**, vuelve al USB Sony y completa el flujo seguro existente.

## Seguridad y compatibilidad

- **Nunca** se descargan ROMs, BIOS, ejecutables de PSP ni juegos. RetroArch
  portable y sus núcleos aprobados pueden descargarse exclusivamente desde
  el servidor oficial cuando el usuario habilita la instalación automática
  o confirma su instalación. Los núcleos sujetos a licencia no comercial
  están excluidos de ese flujo.
- Los `.gba`, `.gb`, `.gbc`, `.sfc`, `.smc`, `.nes`, `.md`, `.smd`, `.sms`, `.gg`,
  `.32x`, `.z64`, `.n64`, `.v64`, `.pce`, `.a26` y ZIPs con **una sola ROM
  reconocible** se clasifican exclusivamente si corresponden a la plataforma
  del emulador PSP encontrado. Las extensiones `.bin` / `.cue`, carpetas BIOS,
  guardados, capturas y ZIPs ambiguos se ignoran deliberadamente.
- Los ZIPs se inspeccionan mediante su directorio central, sin extraerlos.
  La capacidad real de ejecutar un ZIP depende del núcleo de RetroArch.
- Los guardados internos de **PPSSPP/PSP** conservan su sincronización normal.
  Las SRAM compatibles de gpSP Kai/TempGBA y Snes9xTYL/Euphoria pueden
  copiarse con confirmación explícita, comparación SHA-256 y respaldo;
  no se convierten *savestates*. Ver
  [RETRO-CONTROLS-SAVES.md](RETRO-CONTROLS-SAVES.md).
- El mando HID PDK1 está verificado para el XMB y PPSSPP. Dock crea un perfil
  DirectInput aislado para RetroArch, pero **su uso en juegos retro reales
  necesita validación física** y puede requerir ajustes según el controlador.
  No se modifica la configuración global de RetroArch.
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
