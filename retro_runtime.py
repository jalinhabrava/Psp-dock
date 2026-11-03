"""Isolated RetroArch joypad profile and per-ROM save paths for PSP Dock.

The PSP PDK1 descriptor is verified by the existing HID gate before launch.
Everything here is written to PSP Dock's *PC-only* data directory. No global
RetroArch files, PSP firmware, or device drivers are ever modified.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import uuid

from bridge_hid import VID, PID, NATIVE_PID


LAYOUTS = ('nintendo', 'psp')
PROFILE_DEVICE = 'PSP Dock HID'


def _safe_tree(root: Path, path: Path) -> Path:
    root, path = Path(root).absolute(), Path(path).absolute()
    if not path.is_relative_to(root):
        raise ValueError('Archivo del perfil fuera de los datos del Dock.')
    parent = path
    while True:
        if parent.is_symlink() or (hasattr(parent, 'is_junction') and parent.is_junction()):
            raise ValueError('No se admiten enlaces o junctions en el perfil RetroArch.')
        if parent == root:
            break
        parent = parent.parent
    return path


def _cfg(items: dict[str, str | int | bool]) -> bytes:
    lines = []
    for key, value in items.items():
        if not key.replace('_','').isalnum():
            raise ValueError('Clave de configuración no permitida.')
        string = ('true' if value else 'false') if isinstance(value,bool) else str(value)
        if '\r' in string or '\n' in string or '"' in string:
            raise ValueError('Valor de configuración inválido.')
        lines.append(f'{key} = "{string}"')
    return ('\n'.join(lines)+'\n').encode('utf-8')


def joypad_autoconfig(layout: str = 'nintendo', *, product_id: int = PID) -> bytes:
    """PSP CROSS/CIRCLE/SQUARE/TRIANGLE → RetroPad, 0-based DInput buttons.

    On PSP: buttons 1..8 = X, O, □, △, L, R, SELECT, START; analogue axes
    = 0/1 and the D-pad is POV 0. SELECT+TRIANGLE opens RetroArch's menu;
    SELECT+START is *reserved* for PSP Dock's own HID exit and NEVER mapped.
    """
    if layout not in LAYOUTS:
        raise ValueError('Distribución RetroPad no admitida.')
    if type(product_id) is not int or product_id not in (PID, NATIVE_PID):
        raise ValueError('Identidad de mando RetroArch no admitida.')
    cross, circle = ('0','1') if layout == 'nintendo' else ('1','0')
    b_label, a_label = ('Cross','Circle') if layout == 'nintendo' else ('Circle','Cross')
    return _cfg({
        'input_driver': 'dinput', 'input_device': PROFILE_DEVICE,
        'input_device_display_name': 'PSP Dock HID (PDK2)' if product_id == NATIVE_PID else 'PSP Dock HID (PDK1)',
        'input_vendor_id': VID, 'input_product_id': product_id,
        'input_b_btn': cross, 'input_a_btn': circle,
        'input_y_btn': 2, 'input_x_btn': 3,
        'input_l_btn': 4, 'input_r_btn': 5,
        'input_select_btn': 6, 'input_start_btn': 7,
        'input_up_btn': 'h0up', 'input_down_btn': 'h0down',
        'input_left_btn': 'h0left', 'input_right_btn': 'h0right',
        'input_l_x_plus_axis': '+0', 'input_l_x_minus_axis': '-0',
        'input_l_y_plus_axis': '+1', 'input_l_y_minus_axis': '-1',
        'input_enable_hotkey_btn': 6, 'input_menu_toggle_btn': 3,
        'input_b_btn_label': b_label, 'input_a_btn_label': a_label,
        'input_y_btn_label': 'Square', 'input_x_btn_label': 'Triangle',
        'input_select_btn_label': 'Select', 'input_start_btn_label': 'Start',
        'input_l_btn_label': 'L', 'input_r_btn_label': 'R',
    })


def _private_write(root: Path, path: Path, payload: bytes) -> None:
    _safe_tree(root,path)
    path.parent.mkdir(parents=True,exist_ok=True)
    if path.exists() and path.read_bytes() == payload:
        return
    temp = _safe_tree(root,path.with_name('.dock-'+uuid.uuid4().hex+'.tmp'))
    try:
        with temp.open('xb') as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp,path)
    finally:
        temp.unlink(missing_ok=True)


def prepare_joypad_profile(storage_root: Path, *, layout: str='nintendo', product_id: int=PID) -> Path:
    """Produce a session overlay with one private PDK1 DInput autoconfig.

    --appendconfig loads the player's existing RetroArch config, but the
    per-session `config_save_on_exit=false` prevents leaking these settings
    back to the global retroarch.cfg on a normal exit.
    """
    root = Path(storage_root).absolute()
    directory = _safe_tree(root,root/'retro-profiles')
    suffix = '-native' if product_id == NATIVE_PID else ''
    auto = _safe_tree(root,directory/'autoconfig'/'dinput'/(PROFILE_DEVICE+suffix+'.cfg'))
    overlay = _safe_tree(root,directory/('psp-dock-retro'+suffix+'.cfg'))
    _private_write(root,auto,joypad_autoconfig(layout, product_id=product_id))
    _private_write(root,overlay,_cfg({
        'config_save_on_exit': False,
        'input_joypad_driver': 'dinput',
        'input_autodetect_enable': True,
        'joypad_autoconfig_dir': (directory/'autoconfig').as_posix(),
    }))
    return overlay


def private_save_paths(storage_root: Path, volume: str, rom_id: str) -> tuple[Path,Path]:
    """Stable, volume-scoped paths: no two Memory Sticks share retro SRAM."""
    if not isinstance(volume,str) or not volume or len(volume)>512:
        raise ValueError('Memory Stick sin identidad para guardados retro.')
    if not isinstance(rom_id,str) or len(rom_id)!=24 or any(c not in '0123456789abcdef' for c in rom_id):
        raise ValueError('Identificador retro inválido.')
    key = hashlib.sha256(volume.encode('utf-8')).hexdigest()[:24]
    root = Path(storage_root).absolute()
    saves = _safe_tree(root,root/'retro-saves'/key)
    states = _safe_tree(root,root/'retro-states'/key)
    return _safe_tree(root,saves/(rom_id+'.srm')), _safe_tree(root,states/(rom_id+'.state'))


def private_save_relative(volume: str, rom_id: str) -> str:
    """Lexical metadata lookup only. Actual reads/writes still guard paths."""
    if not isinstance(volume,str) or not volume or len(volume)>512:
        raise ValueError('Memory Stick sin identidad para guardados retro.')
    if not isinstance(rom_id,str) or len(rom_id)!=24 or any(c not in '0123456789abcdef' for c in rom_id):
        raise ValueError('Identificador retro inválido.')
    key = hashlib.sha256(volume.encode('utf-8')).hexdigest()[:24]
    return 'retro-saves/'+key+'/'+rom_id+'.srm'


def snapshot_private_saves(storage_root: Path, volume: str, rom_ids) -> dict:
    from catalog_paths import ReadOnlyTree
    from retro_saves import _sha
    tree = ReadOnlyTree(storage_root)
    result = {}
    for rom_id in rom_ids:
        path, info = tree.entry(private_save_relative(volume, rom_id))
        result[rom_id] = (_sha(private_save_paths(storage_root, volume, rom_id)[0])
                          if info is not None else None)
    return result


def retroarch_arguments(exe: Path, core: Path, rom: Path, overlay: Path,
                        save: Path, state: Path) -> list[str]:
    """RetroArch CLI: per-game SRAM and core-bound savestate never mixed."""
    return [str(exe),'--appendconfig',str(overlay),
            '--save',str(save),'--savestate',str(state),
            '-L',str(core),'-f',str(rom)]
