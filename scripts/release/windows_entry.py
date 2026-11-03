"""Entry point for the self-contained Windows release of PSP Dock.

Default mode starts the normal app window. Diagnostic flags are forwarded to
the dock CLI, so --demo --no-browser remains suitable for release smoke tests.
"""
from __future__ import annotations

import os
from pathlib import Path
import runpy
import sys
import traceback

if not getattr(sys, 'frozen', False):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def main() -> None:
    local = Path(os.environ.get('LOCALAPPDATA') or Path.home() / 'AppData' / 'Local')
    log_dir = local / 'PSP-Dock'
    log_dir.mkdir(parents=True, exist_ok=True)
    # PyInstaller --windowed supplies no standard streams. Keep useful errors
    # on the user's machine without writing into the temporary bundle.
    with (log_dir / 'launcher.log').open('a', encoding='utf-8', buffering=1) as log:
        sys.stdout = log
        sys.stderr = log
        try:
            if len(sys.argv) == 1:
                from start_dock import launch
                launch()
            else:
                # Dock's product adapters resolve the running API through
                # sys.modules['__main__']; preserve that contract in demo mode.
                runpy.run_module('dock', run_name='__main__', alter_sys=True)
        except SystemExit:
            raise
        except Exception:
            traceback.print_exc(file=log)
            raise


if __name__ == '__main__':
    main()
