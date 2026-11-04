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
| `tools/labs/` | Development experiments and diagnostics | Laboratorios y diagnósticos |
| `tools/psp/` | PSP setup, packaging and maintenance tools | Instalación, paquetes y mantenimiento PSP |
| `tools/system/` | Host setup and operating-system helpers | Configuración del host y sistema operativo |
| `scripts/release/` | Windows and PSP release packaging | Generación de paquetes de distribución |
| `homebrew/` | PSPDEV source and build scripts | Código y compilación PSPDEV |
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

Use `python -m tools.<area>.<module>` for one-off utilities, or
`python -m psp_dock.<area>.<module>` for library modules with a CLI. This ensures Python
loads package imports consistently and avoids relying on an installed copy of
the repository. Usa `python -m tools.<area>.<module>` para utilidades puntuales.

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
