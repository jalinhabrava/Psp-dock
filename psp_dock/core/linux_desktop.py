"""Manual Linux desktop launch, XDG data paths and conservative recovery checks.

No autostart entries, service, input injection or window-manager bypasses.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import subprocess
import webbrowser


class LinuxDesktopError(ValueError):
    pass


def default_data_dir(*, demo=False, environ=None, home=None) -> Path:
    env = os.environ if environ is None else environ
    user_home = Path.home() if home is None else Path(home)
    candidate = env.get('XDG_DATA_HOME', '')
    # The XDG specification ignores relative values.
    base = Path(candidate) if candidate and Path(candidate).is_absolute() else user_home / '.local/share'
    return base / 'psp-dock' / ('demo' if demo else 'live')


def has_connected_ui(health: dict) -> bool:
    clients = health.get('ui_clients')
    return (health.get('ui_lifecycle') is True and type(clients) is int
            and 0 < clients <= 8)


def launch_xmb_window(url, *, app_window=False, reuse_existing=True,
                      include_handles=None, which=None, popen=None, open_url=None):
    """Open the verified loopback service in Chromium's application mode.

    Existing UI clients are handled by start_dock before this function. Linux
    window handles are not Win32 HWNDs; no unverified window is activated.
    """
    match = re.fullmatch(r'http://127[.]0[.]0[.]1:([0-9]{1,5})/?', url)
    if not match or not 1024 <= int(match.group(1)) <= 65535:
        raise LinuxDesktopError('La ventana XMB sólo admite la URL local del Dock.')
    opener = open_url or webbrowser.open
    if not app_window:
        return opener(url)
    locate = which or shutil.which
    browser = next((found for name in ('chromium', 'chromium-browser',
                                       'google-chrome', 'google-chrome-stable',
                                       'microsoft-edge')
                    if (found := locate(name))), None)
    if browser is None:
        return opener(url)
    return (popen or subprocess.Popen)(
        [browser, '--new-window', '--app=' + url, '--start-maximized'],
        cwd=str(Path(browser).parent),
    )


_EMULATOR_NAMES = {
    'ppssppsdl', 'ppsspp-sdl', 'ppsspp', 'ppssppqt', 'retroarch',
    'ppssppwindows64.exe', 'ppssppwindows.exe', 'retroarch.exe',
}


def running_emulators(*, proc_root=Path('/proc'), uid=None, executables=()):
    """Read current-user processes; an unreadable process is not proof of exit.

    Used only for explicit recovery. This never kills a process and does not
    authorize save writes or replay a previous USB phase command.
    """
    root = Path(proc_root)
    owner = os.getuid() if uid is None else uid
    expected = {str(Path(p).resolve()) for p in executables if p}
    found = []
    try:
        entries = list(root.iterdir())
    except OSError as exc:
        raise LinuxDesktopError('No se pueden comprobar los procesos desde /proc.') from exc
    for directory in entries:
        if not directory.name.isdecimal():
            continue
        try:
            if directory.stat().st_uid != owner:
                continue
            status = (directory / 'status').read_text(encoding='utf-8')
            state = next((line.split()[1] for line in status.splitlines()
                          if line.startswith('State:')), None)
            if state in ('Z', 'X'):
                continue  # A zombie cannot still write saves.
            if state is None:
                raise LinuxDesktopError('Estado de proceso ilegible; no se recupera la sesión.')
            name = (directory / 'comm').read_text(encoding='utf-8').strip()
            exe = os.readlink(directory / 'exe').removesuffix(' (deleted)')
            with (directory / 'cmdline').open('rb') as stream:
                raw = stream.read(65537)
            if len(raw) > 65536:
                raise LinuxDesktopError('Línea de proceso demasiado grande; no se recupera la sesión.')
            args = [os.fsdecode(item) for item in raw.split(b'\0') if item]
            names = {name.casefold(), Path(exe).name.casefold()}
            if args:
                names.add(Path(args[0]).name.casefold())
                if Path(args[0]).name.casefold() in ('wine', 'wine64') and len(args) > 1:
                    names.add(Path(args[1]).name.casefold())
            if names & _EMULATOR_NAMES or str(Path(exe).resolve()) in expected:
                found.append({'pid': int(directory.name), 'name': name, 'executable': exe})
        except FileNotFoundError as exc:
            if directory.exists():
                raise LinuxDesktopError('Un proceso cambió mientras se comprobaba el cierre; reintenta.') from exc
        except (OSError, UnicodeError) as exc:
            raise LinuxDesktopError('No se pudo comprobar el cierre de todos los procesos del usuario.') from exc
    return sorted(found, key=lambda process: process['pid'])


def assert_emulators_stopped(**kwargs) -> None:
    active = running_emulators(**kwargs)
    if active:
        names = ', '.join(f"{row['name']} (PID {row['pid']})" for row in active)
        raise LinuxDesktopError('Cierra el emulador normalmente antes de recuperar la sesión: ' + names)
