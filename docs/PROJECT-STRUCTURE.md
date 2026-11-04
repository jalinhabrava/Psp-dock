# Python project layout / Estructura Python

The Windows host keeps two stable entrypoints at the repository root: `dock.py`
for the local API/XMB and `start_dock.py` for the desktop launcher. All other
Python implementation modules are grouped by responsibility.

El host de Windows conserva dos puntos de entrada en la raíz: `dock.py` para
la API y el XMB, y `start_dock.py` para iniciar la ventana. El resto de los
módulos Python se agrupa por responsabilidad.

| Directory | English | Español |
| --- | --- | --- |
| `psp_dock/core/` | Session, lifecycle and product orchestration | Sesiones y coordinación del producto |
| `psp_dock/devices/` | USB, HID and PSP device communication | Comunicación USB, HID y PSP |
| `psp_dock/emulation/` | PPSSPP, retro emulators and saves | PPSSPP, emuladores retro y partidas |
| `psp_dock/media/` | Artwork, audio, XMB media and textures | Arte, sonido, XMB y texturas |
| `tools/psp/` | Verified backup and native 0308 package/install/upgrade utilities | Copias y herramientas verificadas del homebrew nativo 0308 |
| `scripts/release/` | Windows and PSP release packaging | Generación de paquetes de distribución |
| `homebrew/native-dock/` | Final PSP 0308 source, audit, artwork and GPL notices | Fuente PSP 0308 final, auditoría, arte y licencias GPL |
| `tests/` | Automated checks | Pruebas automatizadas |

## Running from source / Ejecución desde fuentes

Run commands from the repository root (ejecuta desde la raíz del repositorio):

```powershell
py -3 start_dock.py
py -3 dock.py --demo
py -3 -m tools.psp.psp_backup --scope critical --run
py -3 -m psp_dock.core.setup_startup --status
py -3 -m unittest discover -s tests
```

Use `python -m tools.psp.<module>` for maintained PSP backup and native
package utilities, or `python -m psp_dock.<area>.<module>` for modules with a CLI.
All retired drivers, VSH experiments and old firmware variants are available
in Git history, not included as runnable code in this tree.

Usa `python -m tools.psp.<modulo>` para utilidades de copia de seguridad o
instalación del homebrew final. Los controladores de laboratorio y los firmwares
anteriores permanecen accesibles en el historial Git, sin mezclarse con `main`.

## Packaging / Distribución

The Windows bundle uses `scripts/release/windows_entry.py` and
`scripts/release/build_windows.ps1`. The launcher imports the stable root
entrypoints; the application modules are collected from `psp_dock/`.

```powershell
powershell -ExecutionPolicy Bypass -File scripts/release/build_windows.ps1
```

The PSP application uses PSPDEV and its own `homebrew/` build recipes. Follow
the [installation guide](../INSTALL.md) or [guía de instalación](../INSTALL.es.md)
for the hardware release. The directory structure alone does not change PSP
firmware or install anything on a connected device.
