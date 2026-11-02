"""Native Linux emulator profiles kept entirely in PSP Dock's data folders.

PPSSPP SDL v1.20.4 reads XDG_CONFIG_HOME/ppsspp as its Memory Stick.
It does not implement the Windows --config/--controlconfig/--memstick CLI.
The environment is changed only for the child; the user's HOME is retained.
Source: https://github.com/hrydgard/ppsspp/blob/v1.20.4/UI/NativeApp.cpp
"""
from __future__ import annotations

import configparser
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import shutil
import uuid

from retro_runtime import _cfg, _private_write, _safe_tree, LAYOUTS

PPSSPP_NAMES = ('PPSSPPSDL', 'ppsspp-sdl')


def native_elf(path):
    try:
        with Path(path).open('rb') as stream:
            header = stream.read(20)
        machine = {'x86_64': 62, 'aarch64': 183}.get(platform.machine().lower())
        return (len(header) == 20 and header[:6] == b'\x7fELF\x02\x01' and
                machine is not None and int.from_bytes(header[18:20], 'little') == machine)
    except OSError:
        return False


def native_executable(path, names):
    if not isinstance(path, (str, Path)) or not str(path):
        return None
    candidate = Path(path)
    if not candidate.is_absolute() or candidate.name not in names:
        return None
    try:
        if not candidate.is_file() or not os.access(candidate, os.X_OK):
            return None
        # Native x86_64/aarch64 ELF only, never a shell wrapper, Windows
        # binary, AppImage launcher, Flatpak entrypoint or Qt frontend.
        if not native_elf(candidate):
            return None
        return candidate.resolve()
    except OSError:
        return None


def default_ppsspp():
    return next((found for name in PPSSPP_NAMES
                 if (found := shutil.which(name)) and native_executable(found, PPSSPP_NAMES)), '')


def emulator_spec(config):
    exe = native_executable(config.get('emulator', ''), PPSSPP_NAMES)
    if exe is None or config.get('backend') not in ('gles', 'vulkan'):
        return None
    info = exe.stat()
    return {'source': str(exe), 'source_size': info.st_size,
            'source_mtime_ns': info.st_mtime_ns,
            'memstick': str(Path(config['memstick']).resolve()),
            'backend': config['backend'], 'scale': config['scale'],
            'platform': 'linux-sdl2', 'cli_contract': 'ppsspp-v1.20.4'}


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def private_xdg(data, memstick, *, create=False):
    data, memstick = Path(data).absolute(), Path(memstick).absolute()
    _safe_tree(memstick, memstick)
    for parent in memstick.parents:
        if parent.is_symlink():
            raise ValueError('Las partidas privadas no pueden usar enlaces de directorio.')
    root = _safe_tree(data, data / 'linux-xdg')
    link = root / 'ppsspp'
    if create:
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        memstick.mkdir(parents=True, exist_ok=True)
        if link.is_symlink():
            if link.resolve() == memstick.resolve():
                return root
            raise ValueError('El perfil XDG apunta a otra carpeta; revisa el cambio antes de preparar PPSSPP.')
        if link.exists() and not link.is_symlink():
            raise ValueError('La carpeta XDG privada ya contiene datos ajenos al perfil del Dock.')
        temporary = root / ('.ppsspp-' + uuid.uuid4().hex)
        try:
            temporary.symlink_to(memstick, target_is_directory=True)
            os.replace(temporary, link)
        finally:
            temporary.unlink(missing_ok=True)
    if not link.is_symlink() or link.resolve(strict=True) != memstick.resolve(strict=True):
        raise ValueError('El perfil XDG no apunta a las partidas privadas verificadas.')
    return root


def forget_xdg(data, old_memstick):
    """Invalidate only our known pointer after an explicit folder change.

    SAVEDATA stays in place. Preparation later creates a new pointer only
    after the regular storage-layout validation accepts the new location.
    """
    data = Path(data).absolute()
    root = _safe_tree(data, data / 'linux-xdg')
    link = root / 'ppsspp'
    if not link.exists() and not link.is_symlink():
        return
    if not link.is_symlink() or link.resolve() != Path(old_memstick).resolve():
        raise ValueError('No se cambia un perfil XDG que apunta a una carpeta desconocida.')
    link.unlink()


def prepare(data, config):
    spec = emulator_spec(config)
    if spec is None:
        raise ValueError('Selecciona PPSSPPSDL nativo (x86_64/aarch64), con el contrato SDL de PPSSPP 1.20.4.')
    memstick = Path(spec['memstick'])
    private_xdg(data, memstick, create=True)
    ini = _safe_tree(memstick, memstick / 'PSP/SYSTEM/ppsspp.ini')
    profile = configparser.ConfigParser(interpolation=None)
    profile.optionxform = str
    if ini.exists():
        profile.read(ini, encoding='utf-8-sig')
    for section in ('General', 'Graphics', 'Control'):
        if not profile.has_section(section):
            profile.add_section(section)
    profile['General'].update({'CheckForNewVersion': 'False', 'FirstRun': 'False'})
    profile['Graphics'].update({'GraphicsBackend': {'gles': '0', 'vulkan': '3'}[spec['backend']],
                                'InternalResolution': str(spec['scale']), 'FullScreen': 'True'})
    profile['Control']['ShowTouchControls'] = 'False'
    stream = io.StringIO()
    profile.write(stream)
    _private_write(memstick, ini, stream.getvalue().encode('utf-8'))
    record = {'spec': spec, 'exe': spec['source'], 'sha256': sha256(spec['source'])}
    _private_write(Path(data), Path(data) / 'runtime-prepared.json',
                   (json.dumps(record, indent=2) + '\n').encode())
    return Path(spec['source'])


def prepared_runtime(data, config, *, verify_digest=False):
    try:
        spec = emulator_spec(config)
        record = json.loads((Path(data) / 'runtime-prepared.json').read_text())
        if not spec or record.get('spec') != spec or record.get('exe') != spec['source']:
            return None
        private_xdg(data, spec['memstick'])
        if not (Path(spec['memstick']) / 'PSP/SYSTEM/ppsspp.ini').is_file():
            return None
        if verify_digest and sha256(spec['source']) != record.get('sha256'):
            return None
        return Path(spec['source'])
    except (OSError, ValueError, TypeError):
        return None


def ppsspp_launch(data, config, usb_parent, game, *, profile=None):
    from linux_sdl import launch_profile
    exe = prepared_runtime(data, config, verify_digest=True)
    if exe is None:
        raise ValueError('Prepara primero el perfil privado de PPSSPPSDL.')
    env, controls, _ = (profile or launch_profile)(usb_parent)
    memstick = Path(config['memstick'])
    target = _safe_tree(memstick, memstick / 'PSP/SYSTEM/controls.ini')
    if target.is_file() and target.read_bytes() != controls:
        previous = target.read_bytes()
        backup = Path(data) / 'profile-backups' / ('linux-controls-' + hashlib.sha256(previous).hexdigest() + '.ini')
        _private_write(Path(data), backup, previous)
    _private_write(memstick, target, controls)
    env['XDG_CONFIG_HOME'] = str(private_xdg(data, memstick))
    # PPSSPP may launch from /usr/bin, which is not writable and may not
    # contain assets. Its compiled system data directory remains in use.
    return [str(exe), '--fullscreen', '--pause-menu-exit', str(game)], env


def retroarch_core(executable, system, cores_directory=''):
    from psp_retro import SYSTEMS
    exe = native_executable(executable, ('retroarch',))
    if exe is None or system not in SYSTEMS:
        return None
    name = SYSTEMS[system][1].replace('.dll', '.so')
    roots = [Path(cores_directory)] if cores_directory else [
        exe.parent / 'cores', Path('/usr/lib/libretro'), Path('/usr/lib64/libretro'),
        Path('/usr/lib/x86_64-linux-gnu/libretro'), Path('/usr/lib/aarch64-linux-gnu/libretro')]
    for root in roots:
        core = root / name
        if root.is_absolute() and core.is_file() and not core.is_symlink():
            if native_elf(core):
                return exe, core
    return None


def retro_profile(storage, usb_parent, *, layout='nintendo', profile=None):
    from linux_sdl import launch_profile
    if layout not in LAYOUTS:
        raise ValueError('Distribución RetroPad no admitida.')
    env, _, pad = (profile or launch_profile)(usb_parent)
    root = Path(storage).absolute()
    directory = _safe_tree(root, root / 'retro-profiles/linux')
    settings = {
        'config_save_on_exit': False, 'input_joypad_driver': 'sdl2',
        'input_autodetect_enable': False, 'input_player1_joypad_index': pad.index,
        # SDL GameController enumerations, not raw HID/DInput button IDs.
        'input_player1_b_btn': 0 if layout == 'nintendo' else 1,
        'input_player1_a_btn': 1 if layout == 'nintendo' else 0,
        'input_player1_y_btn': 2, 'input_player1_x_btn': 3,
        'input_player1_l_btn': 9, 'input_player1_r_btn': 10,
        'input_player1_select_btn': 4, 'input_player1_start_btn': 6,
        'input_player1_up_btn': 11, 'input_player1_down_btn': 12,
        'input_player1_left_btn': 13, 'input_player1_right_btn': 14,
        'input_player1_l_x_plus_axis': '+0', 'input_player1_l_x_minus_axis': '-0',
        'input_player1_l_y_plus_axis': '+1', 'input_player1_l_y_minus_axis': '-1',
        'input_enable_hotkey_btn': 4, 'input_menu_toggle_btn': 3,
        'input_exit_emulator_btn': 'nul', 'input_menu_toggle_gamepad_combo': 0,
        'input_quit_gamepad_combo': 0, 'quit_on_close_content': 1,
        'system_directory': str(directory / 'system'),
    }
    path = directory / 'retroarch.cfg'
    _private_write(root, path, _cfg(settings))
    xdg = _safe_tree(root, root / 'retro-linux-xdg')
    xdg.mkdir(parents=True, exist_ok=True, mode=0o700)
    env['XDG_CONFIG_HOME'] = str(xdg)
    return path, env


def retro_arguments(exe, core, rom, config, save, state):
    return [str(exe), '--config', str(config), '--save', str(save),
            '--savestate', str(state), '-L', str(core), '-f', str(rom)]
