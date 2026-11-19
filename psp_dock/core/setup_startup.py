"""Legacy Windows startup cleanup and LIVE data location helpers.

David chose manual Windows launch: the visible app waits for the PSP and
closes its detector when the app is closed. New startup registration is
retired. --status and --remove remain for reversible legacy cleanup.
Pure data-location helpers also serve the normal manual launcher.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
import uuid

MARKER = "REM PSP_DOCK_STARTUP_V1"
FILENAME = "PSP Dock - Inicio con Windows.cmd"
UNSAFE = '\r\n\"%!?^'


def _safe_path(value: Path) -> str:
    path = str(Path(value).absolute())
    if any(char in path for char in UNSAFE):
        raise ValueError("Ruta con caracteres incompatibles con CMD; no se instalará inicio automático.")
    return path


def candidate_live_directories(localappdata: Path) -> list[Path]:
    """Find existing private PC data rather than creating a blank new library.

    The original Codex AppContainer library predates settings.json and has
    session.json, library/, memstick/ instead. Do not exclude it merely
    because its configuration used the implicit default values.
    """
    local = Path(localappdata)
    candidates = [local / "PSP-Dock/live"]
    candidates.extend(local.glob("Packages/OpenAI.Codex_*/LocalCache/Local/PSP-Dock/live"))
    return sorted({p.resolve() for p in candidates if
                   (p/"session.json").is_file() and
                   ((p/"settings.json").is_file() or (p/"library").is_dir())})


def choose_live_dir(localappdata: Path, explicit: Path | None = None) -> Path:
    if explicit:
        path = Path(explicit).resolve()
        if (not (path/"session.json").is_file() or
                not ((path/"settings.json").is_file() or (path/"library").is_dir())):
            raise ValueError("La carpeta de datos elegida no contiene una sesión PSP Dock existente.")
        return path
    choices = candidate_live_directories(localappdata)
    if len(choices) != 1:
        raise ValueError(
            "No se puede elegir de forma inequívoca la biblioteca LIVE del PC. "
            "Indica --data RUTA; jamás se creará una biblioteca vacía en su lugar."
        )
    return choices[0]


def render_startup_command(pythonw: Path, dock_script: Path, data: Path, port: int) -> str:
    if not isinstance(port, int) or not (port == 0 or 1024 <= port <= 65535):
        raise ValueError("Puerto de servicio no permitido.")
    exe, source, storage = (_safe_path(x) for x in (pythonw, dock_script, data))
    return (
        "@echo off\r\n" + MARKER + "\r\n" +
        'start "" /min "' + exe + '" "' + source +
        '" --no-browser --open-on-connect --app-window --port ' + str(port) +
        ' --data "' + storage + '"\r\n' +
        "exit /b\r\n"
    )


def startup_file(appdata: Path) -> Path:
    return Path(appdata) / "Microsoft/Windows/Start Menu/Programs/Startup" / FILENAME


def install_entry(path: Path, payload: str) -> bool:
    """Create/refresh ONLY an entry with our exact owner marker."""
    path = Path(path)
    if path.is_symlink() or path.parent.is_symlink():
        raise ValueError("No se permiten enlaces simbólicos en el inicio automático.")
    if path.exists():
        previous = path.read_text(encoding="utf-8")
        if MARKER not in previous:
            raise ValueError("Ya existe otro inicio automático con ese nombre; no se sobrescribe.")
        # Path.read_text() performs universal newline normalization on
        # Windows. CRLF created by the marked .cmd is still idempotent.
        if previous.replace('\r\n', '\n') == payload.replace('\r\n', '\n'):
            return False
    path.parent.mkdir(parents=True, exist_ok=True)
    stage = path.with_name("." + uuid.uuid4().hex + ".tmp")
    try:
        with stage.open("x", encoding="utf-8", newline="") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        os.replace(stage, path)
    finally:
        stage.unlink(missing_ok=True)
    return True


def remove_entry(path: Path) -> bool:
    path = Path(path)
    if not path.exists():
        return False
    if path.is_symlink() or MARKER not in path.read_text(encoding="utf-8"):
        raise ValueError("La entrada no pertenece a PSP Dock; no se eliminará.")
    path.unlink()
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Inicio automático opcional de PSP Dock")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--install", action="store_true")
    group.add_argument("--remove", action="store_true")
    group.add_argument("--status", action="store_true")
    parser.add_argument("--data", type=Path, help="Ubicación LIVE existente, si hay varias")
    # Port 8765 is reserved/blocked on some users' Windows installations.
    # A free OS-assigned loopback port is persisted to server.json, where
    # start_dock.py can discover it in future sessions.
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()
    if args.install:
        print("Inicio con Windows retirado. Abre Iniciar-PSP-Dock.cmd; "
              "la aplicación esperará a que conectes la PSP.")
        return 1
    if os.name != "nt":
        parser.error("El inicio automático es exclusivo de Windows.")
    if not os.environ.get("APPDATA"):
        parser.error("APPDATA del usuario no disponible.")
    entry = startup_file(Path(os.environ["APPDATA"]))
    try:
        if args.status:
            print("Inicio con Windows: " + ("ACTIVADO" if
                  entry.is_file() and MARKER in entry.read_text(encoding="utf-8")
                  else "DESACTIVADO"))
        elif args.remove:
            print("Inicio con Windows retirado." if remove_entry(entry)
                  else "No había entrada PSP Dock instalada.")
        else:
            localappdata = os.environ.get("LOCALAPPDATA")
            if not localappdata:
                raise ValueError("LOCALAPPDATA no disponible.")
            data = choose_live_dir(Path(localappdata), args.data)
            pythonw = Path(sys.executable).with_name("pythonw.exe")
            script = Path(__file__).resolve().parents[2] / "dock.py"
            if not pythonw.is_file() or not script.is_file():
                raise ValueError("No se encuentra pythonw.exe o el launcher PSP Dock.")
            payload = render_startup_command(pythonw, script, data, args.port)
            print("Instalado para el próximo inicio de Windows." if install_entry(entry,payload)
                  else "La entrada ya estaba instalada con la misma configuración.")
            print("Datos existentes conservados: " + str(data))
        return 0
    except (OSError, ValueError) as exc:
        print("No se cambió el inicio automático: " + str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
