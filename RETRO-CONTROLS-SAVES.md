# PSP Dock · controles PDK1 y continuidad segura de partidas retro

Esta PR es incremental y depende de **PR #4** (biblioteca Retro). No cambia
firmware, EBOOT, drivers, USB, globales de RetroArch ni archivos de juego.
La prueba física del emulador PSP + núcleo PC sigue siendo imprescindible.

## Mando PSP → RetroArch en Windows

- Antes del lanzamiento se exige el mismo dispositivo HID PDK1 en dos
  comprobaciones. El driver de mandos Windows es `dinput`. El perfil local
  hace *autoconfig* usando el nombre del descriptor `PSP Dock HID` y el par
  Sony `VID 054C / PID 01CB`; otras identidades no se aceptan.
- El perfil se genera en `<almacenamiento PSP Dock>/retro-profiles/autoconfig/dinput/`
  y se pasa mediante `--appendconfig` con `config_save_on_exit = false`.
  Nunca se altera `retroarch.cfg` de la instalación del usuario.
- Botones físicos PSP: **✕ → B**, **○ → A**, **□ → Y**, **△ → X**;
  **L, R, SELECT, START** conservan su equivalente RetroPad. Cruceta con
  POV y joystick analógico con ejes 0/1. Puedes cambiar solo ✕/○ por la
  alternativa **PSP (✕ → A, ○ → B)** desde `Ajustes → Controles RetroArch`.
- **SELECT + △ abre el menú RetroArch**. **START + SELECT queda reservado
  para salir del modo HID desde el firmware del Dock**; jamás se configura
  como combinación para RetroArch. Para guardar SRAM correctamente, utiliza
  el menú del juego y cierra contenido/RetroArch de forma normal.
- El detector no puede garantizar el orden DirectInput con varios mandos
  enchufados. Se recomienda empezar la prueba física con solo la PSP HID.
  Es obligatorio validar cruceta, botones y menú en un emulador real antes
  de anunciar el mapeo como totalmente probado.

## SRAM de PSP ↔ PC: flujo y controles de seguridad

El lanzamiento no usa carpetas globales de guardados de RetroArch:

| Datos | Destino **solo PC** |
| --- | --- |
| SRAM/batería | `retro-saves/<hash del volumen>/<rom-id>.srm` |
| Savestate RetroArch | `retro-states/<hash del volumen>/<rom-id>-<core>.state` |
| Perfil controlador | `retro-profiles/autoconfig/dinput/PSP Dock HID.cfg` |
| Copias anteriores | `retro-save-backups/` |
| Journals interrumpidos | `retro-save-journals/` |

**Importar para seguir jugando:** conecta la PSP en almacenamiento Sony,
deja que PSP Dock verifique el contenido y abre `Partidas → Retro · <ROM>`
o `Retro → <Emulador> → <ROM> → Partida retro (SRAM)`. Si se reconoce una
SRAM de PSP que no está todavía en Windows, elige **Conservar SRAM de PSP**.
Se verifica SHA-256 antes y después de la copia; jamás se copia por el mero
hecho de conectar la consola. Un conflicto bloquea iniciar esa ROM para no
crear progresos divergentes.

**Devolver después de jugar:** sal normalmente de RetroArch desde su menú,
después pulsa **Salir del Dock** y cambia manualmente a USB Sony de la misma
Memory Stick. PSP Dock *pausa la devolución* mientras existan SRAM retro
pendientes. En `Partidas` puedes elegir cuál conservar, con confirmación por
dirección (**PSP → PC / PC → PSP**), o **Omitir** para conservar ambos archivos
sin sincronizar durante esa salida. Solo después continúa la devolución
existente de las partidas PSP y se cierra el Dock cuando corresponda.

Cada archivo se compara usando una línea base SHA-256 por volumen y ROM.
Si ambos lados han cambiado, se exige resolver el conflicto manualmente. Al
reemplazar un archivo se guarda un backup verificado **en el PC**, se utiliza
un archivo temporal en el destino y se conserva un journal durable durante
la publicación. Si la operación queda interrumpida, **no se reintenta ni
limpia el journal automáticamente**; la persona debe revisar el estado y la
copia de seguridad antes de continuar. No se propagan borrados.

## Compatibilidad que sí se ofrece

| Homebrew PSP | Consola | Archivo de batería | Estado |
| --- | --- | --- | --- |
| gpSP / gpSP Kai | GBA | `*.sav` ↔ `*.srm` | Copia binaria exacta de SRAM sin compresión ni cabecera |
| TempGBA4PSP | GBA | `*.sav` ↔ `*.srm` | La misma vía, requiere validación física por versión |
| Snes9xTYL | SNES | `*.srm` ↔ `*.srm` | SRAM cruda sin cabecera, requiere validación física |
| Snes9x Euphoria | SNES | `*.srm` ↔ `*.srm` | Experimental, necesita validar la versión |

Los archivos deben tener tamaños de SRAM plausibles para GBA/SNES. Se
buscan por el nombre exacto de la ROM **dentro del mismo emulador**, en
carpetas `SAVES`, `SAVE`, `SRAM`, `SAVERAM` o como archivo al lado de la ROM.
Si hay varios candidatos o dos ROMs del mismo emulador comparten el nombre,
no se elige ninguno automáticamente. Los `.zip` tienen guardados ambiguos y
se excluyen por ahora. Si la PSP todavía no tiene SRAM, se puede crear una
en la carpeta `SAVES` propuesta, con advertencia y confirmación explícita:
el homebrew PSP podría utilizar otra ubicación; conviene guardar antes una
partida real en la PSP para que su ruta aparezca en el inventario.

**Sin conversor seguro todavía:** NesterJ/Mesen, MasterBoy/Gambatte,
DaedalusX64/Mupen64Plus-Next, PicoDrive/Genesis Plus GX y los demás.
RetroArch conserva sus SRAM en Windows pero PSP Dock no las sobrescribe en
la PSP hasta disponer de prueba de formato. Tampoco copia estados de
emulación (`.state`, `.svs`, etc.) entre núcleos: incluyen memoria de la
máquina virtual y su formato depende de implementación y versión.

## Cómo se verificó

Se han creado tests con ficheros sintéticos (no ROM comerciales) para:
descriptor PDK1, mapeo 0-based y SELECT+△, idempotencia y aislamiento,
revisión SRAM desde USB, importación, edición en PC, exportación a PSP,
conflictos, rechazo de snapshots obsoletos, diferencias de volumen,
fallos de publicación con backups/journal, rutas enlazadas, nombres
repetidos, retorno pausado hasta resolución/omisión, y transferencia del
almacenamiento local en migraciones sucesivas.

**Aún pendiente:** prueba física del perfil DirectInput y de las SRAM
de cada homebrew instalado; inspeccionar de forma segura qué versiones de
gpSP/Snes9xTYL hay en la Memory Stick antes de habilitar más adaptadores.

Referencias oficiales:

- [RetroArch CLI: `--appendconfig`, `--save`, `--savestate`](https://docs.libretro.com/guides/cli-intro/)
- [RetroArch controller autoconfig y VID/PID](https://docs.libretro.com/guides/controller-autoconfiguration/)
- [Controladores Windows dinput](https://docs.libretro.com/guides/input-controller-drivers/)
- [gpSP: cambios de extensión SRAM](https://github.com/libretro/gpsp/issues/228)
- [Snes9xTYL: SAVES contiene SRAM y savestates](https://www.gamebrew.org/wiki/Snes9xTYL_PSP)
