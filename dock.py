"""PSP Dock: local Windows prototype. Python 3.12+, standard library only."""
from __future__ import annotations
import argparse
import ctypes
import configparser
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import threading
import time
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from dock_events import EventJournal
from dock_preflight import assess_storage
from psp_artwork import read_icon, valid_png
from psp_retro import SYSTEMS, emulator_inventory, emulator_profile, retroarch_core
from retro_runtime import (LAYOUTS, prepare_joypad_profile,
                           private_save_paths, retroarch_arguments)
from retro_saves import (plan_retro_saves, copy_sram_verified, _baseline_key,
                         _sha as retro_sha, RAW_PROFILES)

ROOT = Path(__file__).resolve().parent

def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()

def inside(root, relative):
    original = Path(root).absolute()
    for part in (original, *original.parents):
        if part.is_symlink() or (hasattr(part, 'is_junction') and part.is_junction()):
            raise ValueError('No se permiten enlaces ni junctions en la raíz de datos.')
    root = original.resolve()
    p = root / relative
    # Reject junctions/symlinks even when pointing back inside the tree.
    for part in (p, *p.parents):
        if part == root.parent:
            break
        if part.is_symlink() or (hasattr(part, 'is_junction') and part.is_junction()):
            raise ValueError('No se permiten enlaces ni junctions en los datos del Dock.')
    if not p.resolve().is_relative_to(root):
        raise ValueError('Ruta fuera de la carpeta permitida.')
    return p

def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.tmp-' + uuid.uuid4().hex)
    try:
        with tmp.open('w', encoding='utf-8') as f:
            json.dump(value, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)

def read_json(path, default):
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else default

def tree_signature(path):
    path = Path(path)
    if not path.exists():
        return None
    rows = []
    for p in sorted(path.rglob('*')):
        p = inside(path, p.relative_to(path))
        if p.is_file():
            rows.append((p.relative_to(path).as_posix(), digest(p)))
    return hashlib.sha256(json.dumps(rows).encode()).hexdigest()

def slots(root):
    if not root.exists():
        return {}
    return {p.name: tree_signature(inside(root, p.name)) for p in root.iterdir()
            if p.is_dir() and not p.name.startswith('.dock-')}

def sync_plan(psp, pc, baseline):
    a, b = slots(psp), slots(pc)
    actions, conflicts = [], []
    for slot in sorted(a.keys() | b.keys() | baseline.keys()):
        x, y, old = a.get(slot), b.get(slot), baseline.get(slot)
        if x == y:
            continue
        if old is not None and (x is None or y is None):
            conflicts.append({'slot': slot, 'reason': 'Falta una versión antes sincronizada; no se propagan borrados.'})
        elif x == old or (x is None and old is None):
            actions.append({'slot': slot, 'direction': 'to_psp', 'source_hash': y, 'target_hash': x})
        elif y == old or (y is None and old is None):
            actions.append({'slot': slot, 'direction': 'to_pc', 'source_hash': x, 'target_hash': y})
        else:
            conflicts.append({'slot': slot, 'reason': 'Las dos versiones han cambiado.'})
    return {'actions': actions, 'conflicts': conflicts,
            'unchanged': sum(a.get(k) == b.get(k) for k in a.keys() | b.keys())}

def copy_slot(source, target, backups, expected_source, expected_target):
    """Stage and verify the WHOLE slot; keep durable backup + rollback journal.

    Directory rename isn't a cross-file power-loss transaction on FAT. A leftover
    journal deliberately blocks further writes until recovery is reviewed.
    """
    source, target, backups = Path(source), Path(target), Path(backups)
    if expected_source is None:
        raise ValueError('No se borran partidas. Elige la versión que existe.')
    if tree_signature(source) != expected_source or tree_signature(target) != expected_target:
        raise ValueError('La partida cambió durante la operación; vuelve a comparar.')
    target.parent.mkdir(parents=True, exist_ok=True)
    if list(target.parent.glob('.dock-journal-*.json')):
        raise ValueError('Hay una operación interrumpida. Revisa el registro de recuperación antes de escribir.')
    uid = uuid.uuid4().hex
    stage = target.parent / ('.dock-stage-' + uid)
    rollback = target.parent / ('.dock-old-' + uid)
    journal = target.parent / ('.dock-journal-' + uid + '.json')
    backups.mkdir(parents=True, exist_ok=True)
    backup = backups / (target.name + '-' + uid)
    shutil.copytree(source, stage)
    try:
        if tree_signature(stage) != expected_source or tree_signature(source) != expected_source:
            raise ValueError('Origen inestable o copia incompleta. No se ha sustituido la partida.')
        if tree_signature(target) != expected_target:
            raise ValueError('El destino cambió antes de escribir.')
        if target.exists():
            shutil.copytree(target, backup)
            if tree_signature(backup) != expected_target:
                raise ValueError('No se pudo verificar la copia de seguridad.')
        if tree_signature(target) != expected_target:
            raise ValueError('El destino cambió durante la copia de seguridad; no se ha sustituido.')
        atomic_json(journal, {'target': str(target), 'stage': str(stage),
                             'rollback': str(rollback), 'backup': str(backup)})
        if target.exists():
            os.replace(target, rollback)
        try:
            os.replace(stage, target)
        except OSError:
            if rollback.exists() and not target.exists():
                os.replace(rollback, target)
            raise
        if tree_signature(target) != expected_source:
            raise ValueError('Verificación final fallida. Conserva el registro y la copia de seguridad.')
        if rollback.exists():
            shutil.rmtree(rollback)
        journal.unlink()
    finally:
        if stage.exists() and not journal.exists():
            shutil.rmtree(stage)

def game_inventory(root):
    root = Path(root)
    found = []
    for folder in ('ISO', 'PSP/GAME'):
        base = inside(root, folder)
        if not base.exists():
            continue
        for p in base.rglob('*'):
            if not p.is_file():
                continue
            rel = p.relative_to(root).as_posix()
            p = inside(root, rel)
            if (folder == 'ISO' and p.suffix.lower() in ('.iso', '.cso')) or (folder == 'PSP/GAME' and p.name.upper() == 'EBOOT.PBP'):
                # An emulator is a launcher for *another console*, not a PSP
                # game to feed PPSSPP. Unknown homebrew stays as a PSP title.
                if p.suffix.lower() == '.pbp' and emulator_profile(p.parent, p):
                    continue
                stat = p.stat()
                found.append({'id': hashlib.sha256(rel.encode()).hexdigest()[:24],
                              'relative': rel, 'title': p.parent.name if p.suffix.lower() == '.pbp' else p.stem,
                              'format': p.suffix[1:].upper(), 'size': stat.st_size,
                              'modified_ns': stat.st_mtime_ns})
    return sorted(found, key=lambda g: g['relative'].lower())

class TransferCancelled(ValueError):
    """Cancelled BEFORE publication of the current staged game."""


def copy_game(source, target, on_chunk=None, cancel=None, on_verifying=None):
    """Verified, cancellable transfer; never overwrite different PC content.

    Only the PC target and a temporary PC stage are written. An abort or
    unexpected exception removes an incomplete stage. Previously committed
    games, if any, remain intact and show as ready on next inventory.
    """
    source, target = Path(source), Path(target)
    if target.exists():
        raise ValueError('El destino ya existe: conserva ambas versiones con nombres distintos antes de transferir.')
    target.parent.mkdir(parents=True, exist_ok=True)
    stage = target.parent / ('.dock-transfer-' + uuid.uuid4().hex)
    copied_source_digest = None
    def check_cancel():
        if cancel is not None and cancel.is_set():
            raise TransferCancelled('Transferencia cancelada; se ha descartado la copia parcial del juego actual.')

    def copy_file_verified(source_file, destination_file):
        nonlocal copied_source_digest
        original = Path(source_file)
        destination = Path(destination_file)
        if (original.is_symlink() or
                hasattr(original, 'is_junction') and original.is_junction()):
            raise ValueError('No se transfieren enlaces simbólicos ni junctions.')
        check_cancel()
        streaming_hash = hashlib.sha256()
        with original.open('rb') as read, destination.open('xb') as write:
            for block in iter(lambda: read.read(1024 * 1024), b''):
                check_cancel()
                write.write(block)
                streaming_hash.update(block)
                if on_chunk is not None:
                    on_chunk(len(block))
            write.flush()
            os.fsync(write.fileno())
        shutil.copystat(original, destination)
        if original == source and source.is_file():
            # Hash the original stream AS it is copied. This avoids a full
            # extra PSP read before the progress bar can even move.
            copied_source_digest = streaming_hash.hexdigest()
        return str(destination)

    try:
        check_cancel()
        if source.is_dir():
            for item in source.rglob('*'):
                inside(source, item.relative_to(source))
                if item.is_symlink() or (hasattr(item, 'is_junction') and item.is_junction()):
                    raise ValueError('El juego contiene enlaces o junctions no permitidos.')
            before = tree_signature(source)
            shutil.copytree(source, stage, copy_function=copy_file_verified)
            check_cancel()
            if on_verifying is not None:
                on_verifying()
            if tree_signature(stage) != before or tree_signature(source) != before:
                raise ValueError('El juego cambió o la transferencia quedó incompleta.')
        else:
            if source.is_symlink():
                raise ValueError('El juego no puede ser un enlace simbólico.')
            copy_file_verified(source, stage)
            before = copied_source_digest
            if before is None:
                raise ValueError('No se pudo verificar el flujo original del juego.')
            check_cancel()
            if on_verifying is not None:
                on_verifying()
            if digest(stage) != before or digest(source) != before:
                raise ValueError('El juego cambió o la transferencia quedó incompleta.')
        check_cancel()
        # This folder is exclusively owned by Dock; lock serializes Dock writes.
        if target.exists():
            raise ValueError('El destino apareció durante la transferencia.')
        os.rename(stage, target)
        return before
    finally:
        if stage.is_dir():
            shutil.rmtree(stage)
        elif stage.exists():
            stage.unlink()

def windows_devices():
    # Opt-in isolation for PC test runs performed WHILE the user's real PSP
    # is connected. Test fixtures use explicit temporary psp_root paths;
    # never accidentally auto-select a physical Memory Stick from an
    # unrelated unit test.
    if os.environ.get('PSP_DOCK_TEST_NO_REAL_USB') == '1':
        return []
    if os.name != 'nt':
        return []
    mask = ctypes.windll.kernel32.GetLogicalDrives()
    result = []
    for i in range(26):
        if not mask & (1 << i):
            continue
        drive = chr(65 + i) + ':\\'
        if ctypes.windll.kernel32.GetDriveTypeW(drive) == 2 and Path(drive, 'PSP').is_dir():
            result.append({'root': drive, 'label': 'Memory Stick candidata · ' + drive})
    return result

def volume_identity(root):
    if os.name == 'nt':
        serial = ctypes.c_ulong()
        if not ctypes.windll.kernel32.GetVolumeInformationW(str(Path(root).anchor), None, 0, ctypes.byref(serial), None, None, None, 0):
            raise OSError('No se puede identificar el volumen de la PSP.')
        return str(serial.value)
    return str(Path(root).resolve())


def windows_drive_type(anchor):
    return ctypes.windll.kernel32.GetDriveTypeW(str(anchor))


def require_psp_identity_before_writing(root):
    """Fail closed on removable Windows volumes, never trust a PSP folder.

    Local fixture directories on a fixed PC drive are supported for offline
    tests. Writes to a removable drive require the real physical Sony PSP
    parent, its exact USBSTOR VID/PID and matching Memory Stick volume serial.
    This is ONLY an identity check; it is not a FAT-lock or role-switch ACK.
    """
    if os.name != 'nt':
        return
    root = Path(root).absolute()
    anchor = Path(root.anchor)
    drive_type = windows_drive_type(anchor)
    if drive_type == 2:
        if root != anchor:
            raise ValueError('La PSP extraíble debe identificarse desde la raíz del volumen.')
        from psp_usb import identify, UsbTransitionError
        try:
            device, _ = identify(str(anchor))
        except (OSError, UsbTransitionError) as exc:
            raise ValueError('La unidad extraíble no tiene identidad Sony PSP verificada.') from exc
        if (device.volume_serial != volume_identity(root) or
                not device.usb_device_id.upper().startswith('USB\\VID_054C&PID_02D2\\')):
            raise ValueError('El volumen/USB no coincide con la PSP Sony identificada.')
    elif drive_type != 3 or root == anchor:
        raise ValueError('No se autoriza escritura a dispositivos no identificados como PSP.')

def prepare_emulator(exe, data, memstick, scale, backend):
    """Create Dock-owned portable distribution, preserving the user's installation.

    Works with 1.20.4's documented installed.txt contract; no future CLI flags.
    Assets and DLLs are copied from the selected official distribution.
    """
    exe, data, memstick = Path(exe), Path(data), Path(memstick)
    assets = inside(exe.parent, 'assets')
    if not assets.is_dir():
        raise ValueError('La distribución de PPSSPP debe incluir la carpeta assets junto al ejecutable.')
    files = [exe, *exe.parent.glob('*.dll')]
    signature = hashlib.sha256(json.dumps([(p.name,digest(inside(exe.parent,p.name))) for p in files] + [('assets',tree_signature(assets))]).encode()).hexdigest()[:24]
    runtime = data / 'runtime' / signature
    if not runtime.exists():
        stage = data / 'runtime' / ('.stage-' + uuid.uuid4().hex)
        stage.mkdir(parents=True)
        try:
            for p in files:
                shutil.copy2(p, stage/p.name)
            shutil.copytree(assets,stage/'assets')
            if tree_signature(stage/'assets') != tree_signature(assets) or any(digest(stage/p.name) != digest(p) for p in files):
                raise ValueError('No se pudo verificar la copia aislada de PPSSPP.')
            for p in exe.parent.glob('LICENSE*'):
                if p.is_file():
                    shutil.copy2(p,stage/p.name)
            os.rename(stage,runtime)
        finally:
            if stage.exists():
                shutil.rmtree(stage)
    memstick.mkdir(parents=True,exist_ok=True)
    # Fail before PPSSPP can fall back to its shared Documents folder.
    probe = memstick/('.dock-write-test-' + uuid.uuid4().hex)
    probe.write_bytes(b'')
    probe.unlink()
    (runtime/'installed.txt').write_text(str(memstick)+'\n',encoding='utf-8')
    ini = inside(memstick,'PSP/SYSTEM/ppsspp.ini')
    ini.parent.mkdir(parents=True,exist_ok=True)
    profile = configparser.ConfigParser(interpolation=None,strict=False)
    profile.optionxform = str
    if ini.exists():
        profile.read(ini,encoding='utf-8-sig')
    if not profile.has_section('Graphics'):
        profile.add_section('Graphics')
    profile['Graphics']['InternalResolution'] = str(scale)
    profile['Graphics']['GraphicsBackend'] = {'gles':'0','directx11':'2','vulkan':'3'}[backend]
    tmp = ini.with_suffix('.ini.tmp')
    with tmp.open('w',encoding='utf-8') as f:
        profile.write(f)
    os.replace(tmp,ini)
    return runtime/exe.name


def prepare_psp_controls(memstick, backups):
    """Configure PSP's physically verified button order, PC profile ONLY.

    A previously customized DirectInput profile must never be clobbered.
    The correction helper will only rewrite the exact PPSSPP stock layout
    with an atomic PC backup, or validate an already-correct one. On first
    launch, seed from the independently verified PSP Dock laboratory profile.
    """
    from ppsspp_psp_button_map import MappingError, apply_profile, remap_controls

    memstick, backups = Path(memstick), Path(backups)
    profile = inside(memstick, 'PSP/SYSTEM/controls.ini')
    if profile.is_file():
        try:
            return apply_profile(profile, backups, approve=True)
        except MappingError as exc:
            raise ValueError('No se modificó el perfil de mandos del PC: ' + str(exc)) from exc
    if profile.exists():
        raise ValueError('controls.ini del PC no es un archivo normal.')
    source = inside(ROOT, 'data/ppsspp-hid-lab/memstick/PSP/SYSTEM/controls.ini')
    if not source.is_file():
        raise ValueError('Falta el perfil PSP Dock de botones verificado; no se abrirá PPSSPP sin mapeo.')
    template = source.read_bytes()
    try:
        _, needs_change = remap_controls(template)
    except MappingError as exc:
        raise ValueError('El perfil PSP Dock de referencia no es verificable.') from exc
    if needs_change:
        raise ValueError('El perfil de referencia aún contiene el mapeo incorrecto.')
    profile.parent.mkdir(parents=True, exist_ok=True)
    with profile.open('xb') as out:
        out.write(template)
        out.flush()
        os.fsync(out.fileno())
    if profile.read_bytes() != template:
        raise ValueError('No se pudo verificar el mapeo privado de PPSSPP.')
    return {'changed': True, 'status': 'PSP-CORRECT', 'created': str(profile)}

class Dock:
    def __init__(self, data, demo=False):
        self.data = Path(data).resolve()
        self.data.mkdir(parents=True, exist_ok=True)
        self.events = EventJournal(self.data / 'events.jsonl')
        self.demo = demo
        self.lock = threading.RLock()
        self.config_path = self.data / 'settings.json'
        bundled = ROOT / 'tools/PPSSPP/PPSSPPWindows64.exe'
        defaults = {'emulator': str(bundled) if bundled.is_file() else '', 'retroarch': '',
                    'retro_button_layout':'nintendo', 'library': str(self.data / 'library'),
                    'memstick': str(self.data / 'memstick'), 'psp_root': '', 'scale': 3,
                    'backend': 'directx11','storage_root':str(self.data)}
        self.config = read_json(self.config_path, defaults)
        self.config.setdefault('storage_root',str(self.data))
        self.config.setdefault('retroarch', '')
        self.config.setdefault('retro_button_layout','nintendo')
        self.process = None
        self.session = read_json(self.data / 'session.json', {'running': False, 'pending_return': False})
        # A previous process may still have the dedicated memstick open.
        self.recovery_required = self.session.get('running', False)
        # A durable *read-only* catalog lets the XMB mirror titles after
        # storage mode disappears. It never authorizes copying/syncing from
        # an unmounted volume, nor asserts the games are still installed.
        cached = read_json(self.data / 'inventory-cache.json', {'games': []})
        entries = cached.get('games') if isinstance(cached, dict) else None
        self.last_games = []
        if isinstance(entries, list) and len(entries) <= 2048:
            for g in entries:
                if (isinstance(g, dict) and
                    all(isinstance(g.get(k), str) for k in ('id', 'relative', 'title', 'format'))
                    and isinstance(g.get('size'), int) and g['size'] >= 0
                    and not g['relative'].startswith('/') and '..' not in g['relative'].split('/')
                    and g['format'] in ('ISO', 'CSO', 'PBP')):
                    entry = {k: g[k] for k in ('id', 'relative', 'title', 'format', 'size')}
                    if type(g.get('modified_ns')) is int and g['modified_ns'] >= 0:
                        entry['modified_ns'] = g['modified_ns']
                    signature = g.get('signature')
                    if isinstance(signature, str) and re.fullmatch('[0-9a-f]{64}', signature):
                        entry['signature'] = signature
                    # Purge old incorrectly categorized emulator entries.
                    if (not emulator_profile(Path(entry['relative']).parent) and
                            not emulator_profile(Path(entry['title']))):
                        self.last_games.append(entry)
        self.catalog_volume = cached.get('volume_serial', '') if isinstance(cached, dict) else ''
        retro_cache = read_json(self.data / 'retro-inventory-cache.json', {})
        self.last_emulators, self.last_roms, self.retro_volume = [], [], ''
        if isinstance(retro_cache, dict):
            self.retro_volume = retro_cache.get('volume_serial', '') if isinstance(retro_cache.get('volume_serial'), str) else ''
            for emulator in retro_cache.get('emulators', [])[:64] if isinstance(retro_cache.get('emulators'), list) else []:
                if (isinstance(emulator, dict) and
                        all(isinstance(emulator.get(key), str) for key in ('id', 'name', 'relative')) and
                        isinstance(emulator.get('systems'), list) and
                        all(system in SYSTEMS for system in emulator['systems']) and
                        re.fullmatch('[0-9a-f]{24}', emulator['id']) and
                        emulator['relative'].startswith('PSP/GAME/') and
                        '..' not in emulator['relative'].split('/')):
                    self.last_emulators.append(emulator)
            valid_ids = {emu['id'] for emu in self.last_emulators}
            for rom in retro_cache.get('roms', [])[:2048] if isinstance(retro_cache.get('roms'), list) else []:
                if (isinstance(rom, dict) and
                        all(isinstance(rom.get(key), str) for key in
                            ('id','relative','title','system','system_name','emulator_id','format')) and
                        rom['system'] in SYSTEMS and rom['emulator_id'] in valid_ids and
                        re.fullmatch('[0-9a-f]{24}', rom['id']) and
                        rom['relative'].startswith('PSP/GAME/') and
                        '..' not in rom['relative'].split('/') and
                        type(rom.get('size')) is int and 0 <= rom['size'] <= 256 * 1024 * 1024 and
                        type(rom.get('modified_ns')) is int and rom['modified_ns'] >= 0):
                    self.last_roms.append(rom)
        self.cached_retro_results = None
        self.retro_save_baselines_path = self.data/'retro-save-baselines.json'
        self.retro_save_cache_path = self.data/'retro-save-catalog.json'
        save_cache = read_json(self.retro_save_cache_path,{})
        self.retro_save_volume = save_cache.get('volume_serial','') if isinstance(save_cache,dict) else ''
        self.last_retro_save_rows = []
        if isinstance(save_cache,dict) and isinstance(save_cache.get('rows'),list):
            for row in save_cache['rows'][:2048]:
                if (isinstance(row,dict) and
                    isinstance(row.get('rom_id'),str) and
                    re.fullmatch('[0-9a-f]{24}',row['rom_id']) and
                    row.get('status') in ('none','identical','pc_to_psp','psp_to_pc','conflict','unsupported','blocked') and
                    (row.get('source_relative') is None or isinstance(row.get('source_relative'),str))):
                    self.last_retro_save_rows.append(row)
        self.error = None
        self.cache = {}
        # Materialized catalog for artwork requests. Re-enumerating dozens of
        # PSP/GAME folders and their assets for EVERY thumbnail can monopolize
        # the USB worker and the launcher for minutes.
        self.cached_game_results = None
        self.last_storage_scan_at = 0.0
        self.last_storage_scan_identity = ''
        self.storage_scan_interval = 300.0
        self.last_device = None
        # Host-side launch trigger on first VERIFIED PSP PDK1 HID attach.
        # No host background process exists before Windows user logon;
        # setup_startup.py installs the opt-in lightweight watcher.
        self.last_hid_present = False
        # User-owned game preview assets live on the PC while PSP is HID.
        self.xmb_media_progress = {'active':False,'done':0,'total':0,'errors':0}
        self.xmb_media_worker = None
        self.xmb_media_catalog_key = None
        # Transfer progress must have its OWN lock because Dock.lock is held
        # for the verified copy, while the UI polls /api/transfer-status and
        # can request a one-shot cancellation from another HTTP thread.
        self.transfer_state_lock = threading.Lock()
        self.transfer_cancel = threading.Event()
        self.transfer_status = {
            'active': False, 'phase': 'idle', 'total_bytes': 0,
            'copied_bytes': 0, 'game_index': 0, 'games_total': 0,
            'title': '', 'error': '',
        }
        self.copy_queue_path=self.data/'copy-queue.json'
        self.copy_queue=read_json(self.copy_queue_path,{})
        # Optional, opt-in Windows logon helper uses this callback to open
        # a localhost browser tab only on a NEW storage attachment.
        self.on_connected = None
        self.on_verified_exit = None
        # Future kernel role bridge: disable ALL GetLogicalDrives / PSP FAT
        # discovery while a host volume lock is being acquired/held. This
        # flag itself does NOT acquire a Windows lock or prove exclusivity.
        self.storage_polling_paused = False
        self.stop = threading.Event()
        # An existing PSP manifest can be painted immediately at startup
        # WITHOUT treating those titles as ready or mounting the Memory
        # Stick for every thumbnail. The worker replaces these provisional
        # rows with a fully verified live inventory as soon as it finishes.
        provisional = [
            {
                **{key:g[key] for key in
                   ('id','relative','title','format','size')},
                'availability':'checking',
                'local_relative':g['relative'],
                **({'modified_ns':g['modified_ns']}
                   if 'modified_ns' in g else {}),
            }
            for g in self.last_games
        ]
        self.snapshot = {'demo':demo,'devices':[],'selected_device':None,'games':[],
                         'retro': {'emulators': list(self.last_emulators),
                                   'roms': [{**{k:v for k,v in rom.items() if k!='signature'},
                                             'availability':'checking'} for rom in self.last_roms]},
                         'retro_saves': {'rows': [], 'pending':0,'conflicts':0,'offline':True},
                         'saves':{'actions':[],'conflicts':[],'unchanged':0},
                         'session':dict(self.session),'emulator':{'path':self.config['emulator'],'available':bool(self.config['emulator'])},
                         'error':None,'recovery_required':self.recovery_required,'scanning':True,
                         'library_source':'cached_manifest' if self.last_games else 'empty'}
        self.snapshot['games']=provisional

    def devices(self):
        if self.storage_polling_paused:
            return []
        if self.demo:
            return [{'root': str(self.data / 'sample-psp'), 'label': 'PSP de demostración · archivos sintéticos'}]
        devices = windows_devices()
        explicit = self.config['psp_root']
        if explicit and Path(explicit, 'PSP').is_dir() and not any(Path(d['root']) == Path(explicit) for d in devices):
            devices.append({'root': explicit, 'label': 'Carpeta elegida en ajustes (identidad PSP sin verificar)'})
        return devices

    def root(self):
        if self.storage_polling_paused:
            return None
        devices = self.devices()
        configured = self.config['psp_root']
        if configured:
            return next((Path(d['root']) for d in devices if Path(d['root']) == Path(configured)), None)
        return Path(devices[0]['root']) if len(devices) == 1 else None

    def pause_storage_polling(self, paused):
        """For a future role adapter. Never mistake this for an OS lock."""
        if type(paused) is not bool:
            raise ValueError('Indica si se debe suspender el sondeo de almacenamiento.')
        with self.lock:
            self.storage_polling_paused = paused

    def roots(self):
        psp = self.root()
        if psp is None:
            raise ValueError('Conecta el almacenamiento de la PSP; con varias unidades, elige una.')
        self.validate_layout(psp)
        inside(psp, 'PSP/SAVEDATA')
        return psp / 'PSP/SAVEDATA', inside(Path(self.config['memstick']), 'PSP/SAVEDATA')

    def validate_layout(self, psp=None, config=None):
        config = config or self.config
        paths = [inside(Path(config[k]), '') for k in ('library','memstick')]
        if psp is not None:
            paths.append(inside(psp, ''))
        if any(a.is_relative_to(b) or b.is_relative_to(a) for i,a in enumerate(paths) for b in paths[i+1:]):
            raise ValueError('Las carpetas de juegos, partidas y PSP deben estar separadas.')
        if any(self.data.is_relative_to(p) for p in paths[:2]):
            raise ValueError('La biblioteca y las partidas no pueden contener la carpeta de configuración del Dock.')

    def baseline_path(self):
        key = hashlib.sha256(volume_identity(self.root()).encode()).hexdigest()[:24]
        return self.data / ('baseline-' + key + '.json')

    def hash(self, p):
        # Cache only library inspection; transfer and sync always rehash uncached.
        stat = p.stat()
        key = (str(p), stat.st_size, stat.st_mtime_ns)
        if key not in self.cache:
            self.cache[key] = digest(p)
        return self.cache[key]

    def games(self):
        root = self.root()
        library = Path(self.config['library'])
        candidates = game_inventory(library) if library.exists() else []
        if root:
            serial = volume_identity(root)
            previous = ({g['relative']: g for g in self.last_games}
                        if serial == self.catalog_volume else {})
            found = game_inventory(root)
            for game in found:
                # An empty PC library is already proof of absence. Never read
                # gigabytes over PSP USB just to display an absent game.
                comparable = any(c['format']==game['format'] and c['size']==game['size'] for c in candidates)
                if comparable:
                    source = inside(root,game['relative'])
                    game['signature'] = tree_signature(source.parent) if game['format'] == 'PBP' else self.hash(source)
                elif (game['relative'] in previous and
                      previous[game['relative']].get('size') == game['size'] and
                      previous[game['relative']].get('modified_ns') == game['modified_ns']):
                    game['signature'] = previous[game['relative']].get('signature')
            # Publish a new catalog only after finishing the whole inventory,
            # including signatures. A cable loss or changed source must leave
            # the previous complete mirror intact, not persist a partial one.
            if volume_identity(root) != serial or any(
                (lambda stat: stat.st_size != g['size'] or stat.st_mtime_ns != g['modified_ns'])(
                    inside(root, g['relative']).stat()
                ) for g in found
            ):
                raise ValueError('El contenido de la PSP cambió durante el inventario; vuelve a comparar.')
            essentials = [
                {k: g[k] for k in ('id', 'relative', 'title', 'format', 'size', 'modified_ns')}
                | ({'signature': g['signature']} if g.get('signature') else {})
                for g in found
            ]
            old_essentials = [
                {k: g[k] for k in ('id', 'relative', 'title', 'format', 'size')
                 if k in g} | ({'modified_ns': g['modified_ns']} if 'modified_ns' in g else {})
                | ({'signature': g['signature']} if g.get('signature') else {})
                for g in self.last_games
            ]
            if serial != self.catalog_volume or essentials != old_essentials:
                atomic_json(self.data / 'inventory-cache.json',
                            {'volume_serial': serial, 'games': essentials})
            self.catalog_volume = serial
            self.last_games = found
        result = []
        for game in self.last_games:
            local = inside(library, game['relative'])
            status = 'different' if local.exists() else 'missing'
            local_relative = game['relative']
            matches = sorted(candidates,key=lambda g:g['relative'] != game['relative'])
            for candidate in matches:
                if candidate['format'] != game['format'] or candidate['size'] != game['size']:
                    continue
                p = inside(library,candidate['relative'])
                signature = tree_signature(p.parent) if game['format']=='PBP' else self.hash(p)
                if signature == game.get('signature'):
                    status, local_relative = 'ready', candidate['relative']
                    break
            result.append({**{k:v for k,v in game.items() if k!='signature'}, 'availability': status, 'local_relative':local_relative})
        self.cached_game_results = result
        return result

    def retro_catalog(self):
        """Verified local-ROM mirror separate from PSP titles and PPSSPP.

        A disconnected PSP never grants ROM access merely because a path
        exists on Windows: only an earlier SHA-256 PSP/PC match can be ready.
        """
        root = self.root()
        library = Path(self.config['library'])
        if root is not None:
            serial = volume_identity(root)
            old = ({r['relative']: r for r in self.last_roms}
                   if serial == self.retro_volume else {})
            found = emulator_inventory(root)
            for rom in found['roms']:
                source = inside(root, rom['relative'])
                local = inside(library, rom['relative'])
                if local.is_file() and local.stat().st_size == rom['size']:
                    rom['signature'] = self.hash(source)
                elif (rom['relative'] in old and
                      old[rom['relative']].get('size') == rom['size'] and
                      old[rom['relative']].get('modified_ns') == rom['modified_ns']):
                    rom['signature'] = old[rom['relative']].get('signature')
            if volume_identity(root) != serial or any(
                (lambda stat: stat.st_size != rom['size'] or stat.st_mtime_ns != rom['modified_ns'])(
                    inside(root, rom['relative']).stat()) for rom in found['roms']
            ):
                raise ValueError('Las ROMs cambiaron durante el inventario PSP; vuelve a comparar.')
            if (serial != self.retro_volume or found['emulators'] != self.last_emulators or
                    found['roms'] != self.last_roms):
                atomic_json(self.data / 'retro-inventory-cache.json',
                            {'volume_serial':serial,'emulators':found['emulators'],
                             'roms':found['roms']})
            self.retro_volume = serial
            self.last_emulators, self.last_roms = found['emulators'], found['roms']
        roms = []
        for rom in self.last_roms:
            local = inside(library, rom['relative'])
            status = 'missing'
            if local.exists():
                status = 'different'
                if (local.is_file() and local.stat().st_size == rom['size'] and
                        re.fullmatch('[0-9a-f]{64}', rom.get('signature') or '') and
                        self.hash(local) == rom['signature']):
                    status = 'ready'
            core = retroarch_core(self.config['retroarch'],rom['system'])
            roms.append({**{k:v for k,v in rom.items() if k!='signature'},
                         'availability':status,'local_relative':rom['relative'],
                         'windows_emulator':'RetroArch · ' + SYSTEMS[rom['system']][1].replace('_libretro.dll',''),
                         'core_available':core is not None})
        self.cached_retro_results = {'emulators':list(self.last_emulators),'roms':roms,
                                     'retroarch_available':any(retroarch_core(self.config['retroarch'], system)
                                           for system in SYSTEMS)}
        return self.cached_retro_results

    def retro_save_plan(self):
        """Read-only SRAM plan; USB source is accessed only when mounted.

        Offline/HID rows are merely last-observed metadata, never permission
        to write to the PSP. Each mutation recomputes its fresh revision.
        """
        root = self.root()
        if (root is not None and not self.running() and
                volume_identity(root) == self.retro_volume):
            baselines = read_json(self.retro_save_baselines_path,{})
            if not isinstance(baselines,dict):
                baselines={}
            rows = plan_retro_saves(
                root=root,storage_root=Path(self.config['storage_root']),
                volume=self.retro_volume,roms=self.last_roms,
                emulators=self.last_emulators,baselines=baselines)
            # Like the existing PSP/SAVEDATA three-way sync: two already
            # identical SRAMs establish a safe common ancestor by changing
            # PC METADATA only. This never copies/overwrites any save.
            established=False
            for row in rows:
                if (row['status']=='identical' and row.get('psp_signature') and
                        row['psp_signature']==row.get('pc_signature')):
                    key=_baseline_key(self.retro_volume,row['rom_id'])
                    if baselines.get(key)!=row['psp_signature']:
                        baselines[key]=row['psp_signature']
                        established=True
            if established:
                atomic_json(self.retro_save_baselines_path,baselines)
            if self.retro_save_volume != self.retro_volume or rows != self.last_retro_save_rows:
                atomic_json(self.retro_save_cache_path,{
                    'volume_serial':self.retro_volume,'rows':rows})
            self.last_retro_save_rows=rows
            self.retro_save_volume=self.retro_volume
        else:
            rows=(self.last_retro_save_rows if self.retro_save_volume == self.retro_volume else [])
        offline = root is None or self.storage_polling_paused
        skipped = set(self.session.get('retro_save_skipped_ids') or []) if self.session.get('exit_requested') else set()
        pending = ('psp_to_pc','pc_to_psp','conflict')
        view=[{k:v for k,v in row.items() if k not in ('pc_signature','psp_signature')}
              | {'skipped':row['rom_id'] in skipped} for row in rows]
        return {'rows':view,'offline':offline,
                'pending':sum(row['status'] in pending and row['rom_id'] not in skipped for row in rows),
                'conflicts':sum(row['status']=='conflict' and row['rom_id'] not in skipped for row in rows),
                'unmanaged':sum(row['status'] in ('blocked','unsupported') for row in rows)}

    def sync_retro_save(self, rom_id, choice, revision):
        """Explicit one-slot copy with SHA preconditions and recoverable backup.

        `skip` only means leave both existing versions unchanged for this
        current return. It never establishes a baseline or deletes a save.
        """
        if self.demo or self.running() or self.recovery_required or self.storage_polling_paused:
            raise ValueError('Cierra los emuladores y revisa cualquier recuperación antes de sincronizar SRAM.')
        root=self.root()
        if root is None:
            raise ValueError('Vuelve a almacenamiento USB Sony para sincronizar guardados retro.')
        require_psp_identity_before_writing(root)
        self.validate_layout(root)
        volume=volume_identity(root)
        if volume!=self.retro_volume or self.session.get('prepared_volume') not in (None,volume):
            raise ValueError('Memory Stick distinta: nunca se mezclan SRAM entre dispositivos.')
        storage=Path(self.config['storage_root']).absolute()
        if storage.is_relative_to(root.absolute()) or root.absolute().is_relative_to(storage):
            raise ValueError('Los guardados retro deben almacenarse fuera de la PSP.')
        report=self.retro_save_plan()
        row=next((item for item in report['rows'] if item['rom_id']==rom_id),None)
        if row is None or row['revision'] is None or row['revision']!=revision:
            raise ValueError('La comparación de guardados ha cambiado; revísala de nuevo.')
        if row['status'] not in ('psp_to_pc','pc_to_psp','conflict'):
            raise ValueError('No existe una copia SRAM pendiente y compatible.')
        if choice == 'skip':
            if not self.session.get('exit_requested'):
                raise ValueError('La omisión solo está disponible al devolver el Dock.')
            skips=self.session.get('retro_save_skipped_ids') or []
            if rom_id not in skips:
                self.session['retro_save_skipped_ids']=[*skips,rom_id][:2048]
                atomic_json(self.data/'session.json',self.session)
            self.events.record('retro_sram_return_skipped',evidence='pc_action',
                               game_id=rom_id,volume_serial=volume)
            return self.retro_save_plan()
        if choice not in ('psp','pc'):
            raise ValueError('Debes elegir guardar la SRAM de PSP o la del PC.')
        if choice == 'psp' and row['status']=='pc_to_psp' or choice == 'pc' and row['status']=='psp_to_pc':
            raise ValueError('Esa versión de SRAM no existe o está obsoleta.')
        original=next((rom for rom in self.last_roms if rom['id']==rom_id),None)
        emulator=next((emu for emu in self.last_emulators if original and emu['id']==original['emulator_id']),None)
        if not original or not emulator or (emulator['name'],original['system']) not in RAW_PROFILES:
            raise ValueError('El guardado del emulador no es compatible con la copia directa.')
        source_relative=row['source_relative']
        if not isinstance(source_relative,str):
            raise ValueError('Ruta de SRAM PSP no verificada.')
        psp=inside(root,source_relative)
        pc,_=private_save_paths(storage,volume,rom_id)
        psp_sha,pc_sha=retro_sha(psp),retro_sha(pc)
        if choice=='psp':
            source,target,source_root,target_root=psp,pc,root,storage
            source_sha,target_sha=psp_sha,pc_sha
        else:
            source,target,source_root,target_root=pc,psp,storage,root
            source_sha,target_sha=pc_sha,psp_sha
        if not source_sha or len(source_sha)!=64:
            raise ValueError('No existe SRAM válida en el origen elegido.')
        key=_baseline_key(volume,rom_id)
        before=read_json(self.retro_save_baselines_path,{})
        if not isinstance(before,dict): before={}
        saved=copy_sram_verified(
            source=source,target=target,source_root=source_root,target_root=target_root,
            storage_root=storage,backup_dir=storage/'retro-save-backups'/key[:24],
            journal_dir=storage/'retro-save-journals',
            expected_source=source_sha,expected_target=target_sha,
            system=original['system'])
        before[key]=saved
        atomic_json(self.retro_save_baselines_path,before)
        self.events.record('retro_sram_synced',evidence='pc_action',
                           game_id=rom_id,direction='psp_to_pc' if choice=='psp' else 'pc_to_psp',
                           result='sha256_verified',volume_serial=volume)
        return self.retro_save_plan()

    def running(self):
        if self.process is not None and self.process.poll() is not None:
            self.process = None
            self.session['running'] = False
            atomic_json(self.data / 'session.json', self.session)
            self.events.record('game_closed', evidence='pc_observed',
                               game_id=self.session.get('last_game_id', ''),
                               result=('retroarch_exited' if self.session.get('launcher')=='retroarch'
                                       else 'ppsspp_exited'))
            # A game ended: restore the SAME XMB window automatically,
            # instead of leaving the player on another desktop window.
            # Never run Alt+Tab or focus an arbitrary process by title.
            from ppsspp_windows_flow import return_to_xmb
            restored = return_to_xmb()
            self.events.record('xmb_focus_return', evidence='pc_observed',
                               result='focused' if restored else 'not_found')
        return self.process is not None or self.recovery_required

    def save_plan(self):
        if not self.root():
            return {'actions': [], 'conflicts': [], 'unchanged': 0}
        a, b = self.roots()
        return sync_plan(a, b, read_json(self.baseline_path(), {}))

    def prepare_identical_saves_without_copy(self):
        """Automatically prepare ONLY an unchanged, verified save comparison.

        Requested UX: connect one cable and avoid a needless mouse click
        when there is NOTHING to synchronize. Strict precondition: not a
        single pending action/conflict, same Sony volume and at least one
        verified local PSP game or retro ROM. This writes *metadata to PC only*, no savedata on either
        machine, and never calls Dock.sync() or copy_slot().

        When files differ, the user still has to choose/approve copies.
        """
        if self.demo or self.running() or self.session.get('pending_return'):
            return False
        if self.session.get('exit_requested'):
            return False
        root=self.root()
        if root is None:
            return False
        identity=volume_identity(root)
        if self.session.get('prepared_volume')==identity:
            return False
        self.validate_layout(root)
        require_psp_identity_before_writing(root)
        # Reuse previously verified game catalog when possible.
        available=self.cached_game_results if self.cached_game_results is not None else self.games()
        retro = self.cached_retro_results or self.retro_catalog()
        if not (any(item['availability']=='ready' for item in available) or
                any(item['availability']=='ready' for item in retro['roms'])):
            return False
        plan=self.save_plan()
        if plan['actions'] or plan['conflicts']:
            return False
        a,b=self.roots()
        left,right=slots(a),slots(b)
        baseline_file=self.baseline_path()
        baseline=read_json(baseline_file,{})
        for slot in left.keys() & right.keys():
            if left[slot]==right[slot]:
                baseline[slot]=left[slot]
        # The only changes are PC metadata, not a transfer of game saves.
        atomic_json(baseline_file,baseline)
        self.session['prepared_volume']=identity
        atomic_json(self.data/'session.json',self.session)
        self.events.record('saves_identical_auto_prepared',evidence='pc_observed',
                           count=plan['unchanged'],actions=0,conflicts=0,
                           volume_serial=identity)
        return True

    def sync(self, resolution=None):
        if self.running():
            raise ValueError('Sincronización bloqueada: PPSSPP está abierto o una sesión anterior necesita revisión.')
        if self.session.get('exit_requested') and self.retro_save_plan()['pending']:
            raise ValueError('Revisa primero las partidas Retro pendientes de devolución o elige omitirlas.')
        a, b = self.roots()
        require_psp_identity_before_writing(self.root())
        identity = volume_identity(self.root())
        if self.session.get('pending_return') and self.session.get('prepared_volume') != identity:
            raise ValueError('La sesión pertenece a otra Memory Stick. Conecta la unidad original.')
        if not self.session.get('pending_return') and self.session.get('prepared_volume'):
            # Preparation is valid only after a FULL successful comparison.
            # Should a copy fail or user leave conflicts unresolved, an old
            # prepared serial may not later authorize playing stale PC saves.
            self.session.pop('prepared_volume', None)
            atomic_json(self.data / 'session.json', self.session)
        if list(a.glob('.dock-journal-*.json')) or list(b.glob('.dock-journal-*.json')):
            raise ValueError('Operación de partidas interrumpida. Revisa el registro de recuperación antes de continuar.')
        baseline_file = self.baseline_path()
        baseline = read_json(baseline_file, {})
        plan = sync_plan(a, b, baseline)
        self.events.record(
            'save_plan', evidence='pc_action', actions=len(plan['actions']),
            conflicts=len(plan['conflicts']),
            to_pc=sum(x['direction'] == 'to_pc' for x in plan['actions']),
            to_psp=sum(x['direction'] == 'to_psp' for x in plan['actions']),
            volume_serial=identity,
        )
        # Do not apply unrelated writes during a mixed plan with unresolved
        # save conflicts. Resolving a specific slot remains a separate
        # explicitly approved operation.
        if plan['conflicts'] and resolution is None:
            self.events.record('save_conflicts_pending',
                               conflicts=len(plan['conflicts']))
            return plan
        actions = list(plan['actions'])
        if resolution:
            slot, choice = resolution['slot'], resolution['choice']
            if choice not in ('pc', 'psp') or slot not in [c['slot'] for c in plan['conflicts']]:
                raise ValueError('Conflicto o elección inválidos.')
            src, dst = (inside(b, slot), inside(a, slot)) if choice == 'pc' else (inside(a, slot), inside(b, slot))
            actions = [{'slot': slot, 'direction': 'to_psp' if choice == 'pc' else 'to_pc',
                        'source_hash': tree_signature(src), 'target_hash': tree_signature(dst)}]
        for action in actions:
            slot = action['slot']
            src_root, dst_root = (a, b) if action['direction'] == 'to_pc' else (b, a)
            src, dst = inside(src_root, slot), inside(dst_root, slot)
            copy_slot(src, dst, Path(self.config['storage_root']) / 'backups',
                      action['source_hash'], action['target_hash'])
            baseline[slot] = action['source_hash']
            atomic_json(baseline_file, baseline)
        # Identical initial saves also establish a common ancestor.
        x, y = slots(a), slots(b)
        for slot in x.keys() & y.keys():
            if x[slot] == y[slot]:
                baseline[slot] = x[slot]
        atomic_json(baseline_file, baseline)
        final = sync_plan(a, b, baseline)
        if not final['conflicts'] and not final['actions']:
            self.session['pending_return'] = False
            self.session['exit_requested'] = False
            self.session['prepared_volume'] = volume_identity(self.root())
            atomic_json(self.data / 'session.json', self.session)
            self.events.record('save_sync_verified', evidence='pc_observed',
                               actions=len(actions), conflicts=0,
                               volume_serial=self.session['prepared_volume'],
                               result='verified')
        return final

    def transfer(self, ids):
        if self.running():
            raise ValueError('Espera a que termine PPSSPP.')
        root = self.root()
        if not root:
            raise ValueError('Almacenamiento PSP no disponible.')
        require_psp_identity_before_writing(root)
        self.validate_layout(root)
        games = {g['id']: g for g in self.games()}
        if not ids or len(set(ids)) != len(ids) or any(i not in games for i in ids):
            raise ValueError('Selección de juegos inválida.')
        selected = [games[i] for i in ids]
        if any(g['availability'] != 'missing' for g in selected):
            raise ValueError('Solo se transfieren juegos ausentes; nunca se sobrescriben versiones distintas.')
        library = Path(self.config['library'])
        library.mkdir(parents=True, exist_ok=True)
        required = sum(sum(p.stat().st_size for p in inside(root,g['relative']).parent.rglob('*') if p.is_file()) if g['format']=='PBP' else g['size'] for g in selected)
        if shutil.disk_usage(library).free < required + 16 * 1024 * 1024:
            raise ValueError('No hay espacio suficiente para la transferencia verificada.')
        self.transfer_cancel.clear()
        with self.transfer_state_lock:
            self.transfer_status = {
                'active': True, 'phase': 'copying', 'total_bytes': required,
                'copied_bytes': 0, 'game_index': 0, 'games_total': len(selected),
                'title': '', 'error': '',
            }
        try:
            for i, g in enumerate(selected, 1):
                if self.transfer_cancel.is_set():
                    raise TransferCancelled('Transferencia cancelada antes de iniciar el siguiente juego.')
                self._update_transfer(phase='copying', title=g['title'], game_index=i)
                source, target = inside(root, g['relative']), inside(library, g['relative'])
                if g['format'] == 'PBP':
                    source, target = source.parent, target.parent
                verified = copy_game(
                    source, target,
                    on_chunk=lambda size: self._update_transfer_delta(size),
                    on_verifying=lambda: self._update_transfer(phase='verifying'),
                    cancel=self.transfer_cancel,
                )
                if source.is_file():
                    for p in (source,target):
                        stat = p.stat()
                        self.cache[(str(p),stat.st_size,stat.st_mtime_ns)] = verified
                # A completed game becomes usable even if the user cancels
                # a later game in this batch. Publish the verified mirror
                # NOW, before starting the next potentially long copy.
                self.games()
        except TransferCancelled as exc:
            self._update_transfer(active=False, phase='cancelled', error=str(exc))
            raise
        except (OSError, ValueError) as exc:
            self._update_transfer(active=False, phase='failed', error=str(exc))
            raise
        else:
            self._update_transfer(active=False, phase='done', title='',
                                  copied_bytes=required)
        finally:
            self.transfer_cancel.clear()

    def transfer_retro(self, ids):
        """Consent-only verified ROM transfer to PC; no PSP writes or saves."""
        if self.demo or self.running() or self.session.get('pending_return'):
            raise ValueError('Termina la sesión antes de copiar ROMs.')
        root = self.root()
        if root is None or self.storage_polling_paused:
            raise ValueError('Conecta la PSP en modo almacenamiento para copiar ROMs.')
        require_psp_identity_before_writing(root)
        self.validate_layout(root)
        selection = {rom['id']: rom for rom in self.retro_catalog()['roms']}
        if (not isinstance(ids,list) or not ids or len(ids) > 100 or
                len(set(ids)) != len(ids) or any(i not in selection for i in ids)):
            raise ValueError('Selección de ROMs inválida.')
        selected = [selection[i] for i in ids]
        if any(rom['availability'] != 'missing' for rom in selected):
            raise ValueError('No se sobrescriben ROMs del PC diferentes o existentes.')
        library = Path(self.config['library'])
        library.mkdir(parents=True, exist_ok=True)
        total = sum(item['size'] for item in selected)
        if shutil.disk_usage(library).free < total + 16 * 1024 * 1024:
            raise ValueError('No hay espacio suficiente para la transferencia de ROMs.')
        self.transfer_cancel.clear()
        self._update_transfer(active=True,phase='copying',total_bytes=total,
                              copied_bytes=0,game_index=0,games_total=len(selected),
                              title='',error='')
        try:
            for index, rom in enumerate(selected,1):
                if self.transfer_cancel.is_set():
                    raise TransferCancelled('Copia de ROMs cancelada.')
                self._update_transfer(phase='copying',game_index=index,title=rom['title'])
                source = inside(root,rom['relative'])
                target = inside(library,rom['relative'])
                stat = source.stat()
                if (stat.st_size != rom['size'] or stat.st_mtime_ns != rom['modified_ns']):
                    raise ValueError('La ROM cambió en la PSP antes de la copia.')
                copy_game(source,target,
                          on_chunk=lambda size: self._update_transfer_delta(size),
                          on_verifying=lambda: self._update_transfer(phase='verifying'),
                          cancel=self.transfer_cancel)
                if (source.stat().st_size != rom['size'] or
                        source.stat().st_mtime_ns != rom['modified_ns']):
                    raise ValueError('La ROM cambió en origen; revisa la copia local.')
                # Publish each finished, fully verified ROM immediately.
                if not any(r['id']==rom['id'] and r['availability']=='ready'
                           for r in self.retro_catalog()['roms']):
                    raise ValueError('La copia de ROM terminó sin coincidencia SHA-256.')
        except (OSError, ValueError) as exc:
            self._update_transfer(active=False,
                                  phase='cancelled' if isinstance(exc,TransferCancelled) else 'failed',
                                  error=str(exc))
            raise
        else:
            self._update_transfer(active=False,phase='done',title='',copied_bytes=total)
        finally:
            self.transfer_cancel.clear()

    def _update_transfer(self, **changes):
        with self.transfer_state_lock:
            self.transfer_status.update(changes)

    def _update_transfer_delta(self, bytes_written):
        with self.transfer_state_lock:
            self.transfer_status['copied_bytes'] = min(
                self.transfer_status['total_bytes'],
                self.transfer_status['copied_bytes'] + bytes_written,
            )

    def transfer_progress(self):
        with self.transfer_state_lock:
            return dict(self.transfer_status)

    def copy_choice_key(self):
        """Fingerprint the missing-game offer on the original Sony volume.

        If the user installs another title on the PSP, changes library
        availability, or swaps Memory Sticks, the previous 'skip' decision
        no longer applies and the UI asks again. No game bytes are read.
        """
        root = self.root()
        if root is None or self.demo:
            return None
        current = (self.cached_game_results if self.cached_game_results is not None
                   else self.games())
        missing = sorted(
            (g['id'], g['size'], g.get('modified_ns'))
            for g in current if g['availability'] == 'missing')
        if not missing:
            return None
        value={'volume_serial':volume_identity(root),'missing':missing}
        return hashlib.sha256(
            json.dumps(value,sort_keys=True).encode('utf-8')
        ).hexdigest()

    def copy_policy(self):
        key=self.copy_choice_key()
        if key is None:
            return 'ask'
        choices=read_json(self.data/'copy-decisions.json',{})
        return 'skip_missing' if (isinstance(choices,dict) and
                                  choices.get(key)=='skip_missing') else 'ask'

    def choose_copy_policy(self, choice):
        """Save an explicit user choice, never start a transfer implicitly."""
        if self.demo or self.running() or self.session.get('pending_return'):
            raise ValueError('No se puede elegir la copia durante esta fase.')
        if choice not in ('skip_missing','ask'):
            raise ValueError('Política de copia desconocida.')
        key=self.copy_choice_key()
        if key is None:
            raise ValueError('No hay juegos pendientes en el USB Sony.')
        current=read_json(self.data/'copy-decisions.json',{})
        if not isinstance(current,dict):
            current={}
        if choice == 'skip_missing':
            current[key]='skip_missing'
        else:
            current.pop(key,None)
        if len(current)>128:
            current={key:value for key,value in list(current.items())[-128:]}
        atomic_json(self.data/'copy-decisions.json',current)
        self.events.record('games_copy_choice',evidence='pc_action',
                           result=choice,
                           count=sum(g['availability']=='missing' for g in
                                     self.cached_game_results or []))
        return choice

    def cancel_transfer(self):
        with self.transfer_state_lock:
            if not self.transfer_status['active']:
                raise ValueError('No hay ninguna copia en curso que cancelar.')
            self.transfer_cancel.set()
            return {'cancel_requested': True,
                    'message': 'La copia parcial actual se descartará; los juegos ya completados se conservarán.'}

    def request_game_copy_from_hid(self, game_id):
        """Queue explicit XMB choice, NOT an unverified USB role command."""
        if self.demo or self.running() or self.session.get('exit_requested'):
            raise ValueError('No se pueden solicitar copias en esta fase.')
        if not (self.snapshot.get('controller_status',{}).get('connected')
                and self.snapshot.get('selected_device') is None):
            raise ValueError('Esta solicitud requiere la PSP como mando HID.')
        if not isinstance(game_id,str) or not re.fullmatch('[0-9a-f]{24}',game_id):
            raise ValueError('Juego inválido.')
        if (self.copy_queue.get('status') in
                ('waiting_for_storage','copying','interrupted_review')):
            raise ValueError('Existe una copia pendiente que requiere resolución.')
        selected=next((g for g in self.cached_game_results or []
                       if g['id']==game_id),None)
        if selected is None or selected.get('availability')!='missing':
            raise ValueError('El juego ya existe en el PC o no está verificado.')
        if self.session.get('prepared_volume')!=self.catalog_volume:
            raise ValueError('La PSP no coincide con la sesión preparada.')
        self.copy_queue={
            'status':'waiting_for_storage',
            'game_id':game_id,
            'volume_serial':self.catalog_volume,
            'game_relative':selected['relative'],
            'requested_from':'verified_psp_hid',
        }
        atomic_json(self.copy_queue_path,self.copy_queue)
        self.events.record('xmb_game_copy_queued',evidence='pc_action',
                           game_id=game_id,volume_serial=self.catalog_volume)
        return dict(self.copy_queue)

    def cancel_game_copy_queue(self):
        if self.copy_queue.get('status')!='waiting_for_storage':
            raise ValueError('No hay copia pendiente cancelable.')
        self.copy_queue={'status':'cancelled'}
        atomic_json(self.copy_queue_path,self.copy_queue)
        return dict(self.copy_queue)

    def complete_queued_game_copy(self):
        """Copy only when original Sony storage returns and HID is absent.

        A durable COPYING marker is written before any game transfer,
        preventing blind retries after power loss or a partial copy.
        """
        queue=self.copy_queue
        if queue.get('status')!='waiting_for_storage':
            return False
        if self.running() or self.storage_polling_paused or self.session.get('exit_requested'):
            return False
        root=self.root()
        if root is None or volume_identity(root)!=queue.get('volume_serial'):
            return False
        from bridge_hid import probe
        report=probe()
        if not report.get('supported') or report.get('errors') or report.get('devices'):
            return False
        game=next((g for g in self.games() if g['id']==queue['game_id']),None)
        if (not game or game['availability']!='missing' or
                game['relative']!=queue['game_relative']):
            self.copy_queue={'status':'interrupted_review',
                             'reason':'El catálogo del juego cambió.'}
            atomic_json(self.copy_queue_path,self.copy_queue)
            return False
        self.copy_queue={**queue,'status':'copying'}
        atomic_json(self.copy_queue_path,self.copy_queue)
        try:
            self.transfer([queue['game_id']])
            if not any(g['id']==queue['game_id'] and g['availability']=='ready'
                       for g in self.games()):
                raise ValueError('La copia terminó sin validación del juego.')
            self.copy_queue={**queue,'status':'copied',
                             'resumption':'manual_hid_until_bridge_verified'}
            atomic_json(self.copy_queue_path,self.copy_queue)
            self.events.record('xmb_game_copy_verified',evidence='pc_observed',
                               game_id=queue['game_id'],result='copied')
            return True
        except (OSError,ValueError):
            self.copy_queue={**queue,'status':'interrupted_review'}
            atomic_json(self.copy_queue_path,self.copy_queue)
            return False

    def emulator_spec(self):
        """Cheap PC-only fingerprint for an explicitly selected emulator."""
        path = Path(self.config['emulator'])
        if not path.is_file() or path.name.lower() not in (
                'ppssppwindows64.exe', 'ppssppwindows.exe'):
            return None
        stat = path.stat()
        return {
            'source': str(path.resolve()), 'source_size': stat.st_size,
            'source_mtime_ns': stat.st_mtime_ns,
            'memstick': str(Path(self.config['memstick']).resolve()),
            'backend': self.config['backend'], 'scale': self.config['scale'],
        }

    def prepared_runtime_path(self, *, verify_digest=False):
        """Read-only quick check of prewarmed, isolated Windows PPSSPP."""
        try:
            spec = self.emulator_spec()
            record = read_json(self.data / 'runtime-prepared.json', {})
            if not spec or record.get('spec') != spec or not isinstance(record.get('exe'), str):
                return None
            exe = Path(record['exe'])
            if (not exe.is_file() or exe.name.lower() not in
                    ('ppssppwindows64.exe','ppssppwindows.exe') or
                    exe.is_symlink() or exe.parent.is_symlink() or
                    (self.data/'runtime').is_symlink() or
                    not exe.resolve().is_relative_to((self.data/'runtime').resolve())):
                return None
            if (exe.parent/'installed.txt').read_text(encoding='utf-8').strip() != spec['memstick']:
                return None
            controls = inside(Path(self.config['memstick']), 'PSP/SYSTEM/controls.ini')
            from ppsspp_psp_button_map import remap_controls
            if not controls.is_file() or remap_controls(controls.read_bytes())[1]:
                return None
            if verify_digest and digest(exe) != record.get('sha256'):
                return None
            return exe
        except (ValueError, TypeError, OSError):
            return None

    def graphics_view(self, game_id=None, *, include_gpu=False):
        """Propose PC display resolution and per-game settings; read-only."""
        from ppsspp_graphics import (
            GraphicsError, primary_monitor, graphics_hardware,
            preferences, per_game_choice, recommend,
        )
        if game_id is not None:
            if (not isinstance(game_id, str) or
                    not re.fullmatch('[0-9a-f]{24}', game_id) or
                    game_id not in {g['id'] for g in self.last_games}):
                raise GraphicsError('Juego desconocido en la biblioteca del Dock.')
        db=preferences(self.data/'graphics-profiles.json')
        choice=per_game_choice(db,game_id)
        monitor=primary_monitor()
        plan=recommend(monitor, **choice,backend=self.config['backend'],
                       manual_scale=self.config['scale'])
        result={'game_id':game_id,'choice':choice,'plan':plan,
                'automatic':choice['quality']!='manual',
                # The XMB marks each value as the game's OWN or inherited.
                'custom':game_id is not None and game_id in db['games'],
                'default_choice':dict(db['default'])}
        if game_id is not None:
            # Only read PARAM.SFO from the VERIFIED LOCAL PC game. A
            # graphics preview must never trigger an unapproved large
            # USB read or a PSP FAT write, especially when HID is active.
            from psp_game_metadata import read_metadata
            # Do NOT trigger a second full PSP inventory when another
            # thread is already scanning USBSTOR on first connection.
            # The first preview can show monitor/quality immediately and
            # metadata becomes available after the catalog is ready.
            catalog=self.cached_game_results or []
            selected=next((g for g in catalog if g['id']==game_id),None)
            root=self.root()
            if root is not None and volume_identity(root)!=self.catalog_volume:
                selected=None
            if selected and selected['availability']=='ready':
                local=inside(Path(self.config['library']),selected['local_relative'])
                info=read_metadata(local)
                game_disc=info.get('DISC_ID')
                result['game_metadata']={
                    'disc_id':game_disc,
                    'disc_version':info.get('DISC_VERSION'),
                    'title':info.get('TITLE'),
                }
                if game_disc:
                    texture_folder=inside(
                        Path(self.config['memstick']),
                        'PSP/TEXTURES/'+game_disc)
                    result['hd_texture_pack_installed']=(
                        (texture_folder/'textures.ini').is_file() or
                        (texture_folder/'textures.zip').is_file()
                    )
                    from psp_texture_packs import offers_for_disc
                    result['hd_texture_pack_offers']=offers_for_disc(game_disc)
                    result['hd_pack_community_url']='https://forums.ppsspp.org/forumdisplay.php?fid=36'
                else:
                    result['hd_texture_pack_installed']=False
        if include_gpu:
            result['hardware']=graphics_hardware()
        return result

    def set_graphics_view(self, changes):
        """Persist selected quality on the PC only. Never touch PSP FAT."""
        from ppsspp_graphics import set_preference, clear_preference
        if self.demo:
            raise ValueError('La demo no modifica perfiles de juegos reales.')
        if self.running() or self.session.get('exit_requested'):
            raise ValueError('Cierra PPSSPP y termina el retorno antes de cambiar los gráficos.')
        game_id=changes.get('id')
        if game_id is not None and game_id not in {g['id'] for g in self.last_games}:
            raise ValueError('No se puede configurar un juego desconocido.')
        if changes.get('reset') is True:
            if game_id is None:
                raise ValueError('El perfil predeterminado no se puede quitar.')
            clear_preference(self.data/'graphics-profiles.json', game_id=game_id)
            self.events.record('graphics_profile_reset', evidence='pc_action',
                               game_id=game_id)
            return self.graphics_view(game_id)
        set_preference(self.data/'graphics-profiles.json',
                       game_id=game_id, quality=changes['quality'],
                       aspect=changes['aspect'],
                       texture=changes.get('texture','off'))
        self.events.record('graphics_profile_saved', evidence='pc_action',
                           game_id=game_id or '', quality=changes['quality'],
                           aspect=changes['aspect'])
        return self.graphics_view(game_id)

    def apply_game_graphics(self, game_id):
        """Apply PC-only PPSSPP ini profile before the selected game starts."""
        from ppsspp_graphics import apply_private_ppsspp_ini
        preview=self.graphics_view(game_id)
        plan={**preview['plan'],
              'replace_textures':preview.get('hd_texture_pack_installed') is True}
        private=inside(Path(self.config['memstick']), 'PSP/SYSTEM/ppsspp.ini')
        return apply_private_ppsspp_ini(
            private,plan=plan,
            backup_dir=Path(self.config['storage_root'])/'profile-backups')

    def prepare_runtime(self):
        """Prewarm PPSSPP on the PC while USBSTOR is still available.

        This is not a USB transition and never reads or modifies PSP media.
        The HTTP action is invoked after EXPLICIT sync consent, or retried
        from the Windows launcher. Doing the expensive asset verification
        before HID avoids consuming the lab's limited watchdog window.
        """
        if self.demo:
            raise ValueError('La demo no prepara PPSSPP para juegos reales.')
        if self.running() or self.session.get('exit_requested'):
            raise ValueError('No se puede preparar PPSSPP mientras el Dock está en juego o retorno.')
        root = self.root()
        if not root or self.session.get('prepared_volume') != volume_identity(root):
            raise ValueError('Primero confirma la sincronización de esta PSP en almacenamiento.')
        self.validate_layout(root)
        saves = self.save_plan()
        if saves['actions'] or saves['conflicts']:
            raise ValueError('Resuelve y prepara primero las partidas.')
        if not any(g['availability'] == 'ready' for g in self.games()):
            raise ValueError('Copia al PC al menos un juego verificado.')
        spec = self.emulator_spec()
        if not spec:
            raise ValueError('Selecciona un ejecutable PPSSPP válido en ajustes.')
        existing = self.prepared_runtime_path(verify_digest=True)
        if existing is not None:
            self.runtime_error = ''
            return {'ready': True, 'exe': str(existing), 'cached': True}
        exe = Path(spec['source'])
        if os.name != 'nt':
            raise ValueError('Esta versión del launcher prepara PPSSPP en Windows.')
        env = {**os.environ, 'PSP_DOCK_EXE': str(exe)}
        version = subprocess.run(
            ['powershell.exe','-NoProfile','-NonInteractive','-Command',
             '(Get-Item -LiteralPath $env:PSP_DOCK_EXE).VersionInfo.ProductVersion'],
            env=env,capture_output=True,text=True,timeout=15,
            creationflags=0x08000000,
        ).stdout.strip()
        match = re.search(r'(\d+)\.(\d+)\.(\d+)', version)
        if not match or tuple(map(int, match.groups())) < (1, 20, 4):
            raise ValueError('PPSSPP requiere versión 1.20.4 o superior. Versión: ' +
                             (version or 'desconocida'))
        private = prepare_emulator(exe, self.data, Path(spec['memstick']),
                                   spec['scale'], spec['backend'])
        prepare_psp_controls(Path(spec['memstick']),
                             Path(self.config['storage_root'])/'profile-backups')
        # Map the PSP's own HOME (DirectInput button 9) to the PPSSPP
        # Pause menu, which supports leaving the game with controller
        # actions. No Windows-wide ESC injection is needed.
        from ppsspp_windows_flow import add_home_pause_mapping
        add_home_pause_mapping(
            inside(Path(spec['memstick']), 'PSP/SYSTEM/controls.ini'),
            Path(self.config['storage_root']) / 'profile-backups',
        )
        # Auto-match the primary Windows monitor while USBSTOR is active.
        # This writes the private PC PPSSPP ini only, not PSP or GPU drivers.
        from ppsspp_graphics import apply_private_ppsspp_ini
        apply_private_ppsspp_ini(
            inside(Path(spec['memstick']),'PSP/SYSTEM/ppsspp.ini'),
            plan=self.graphics_view()['plan'],
            backup_dir=Path(self.config['storage_root'])/'profile-backups',
        )
        atomic_json(self.data/'runtime-prepared.json',
                    {'spec': spec, 'exe': str(private.resolve()),
                     'sha256': digest(private)})
        self.runtime_error = ''
        return {'ready': True, 'exe': str(private), 'cached': False}

    def launch(self, game_id):
        if self.demo:
            raise ValueError('La demostración contiene archivos sintéticos; no son juegos ejecutables.')
        if self.running():
            raise ValueError('Ya hay una sesión abierta o pendiente de recuperación.')
        if self.session.get('exit_requested'):
            raise ValueError('La salida del Dock ya está solicitada. Devuelve las partidas antes de jugar.')
        if self.storage_polling_paused:
            raise ValueError('Hay una preparación de volumen en curso; no iniciar un juego.')
        self.validate_layout(self.root())
        local_saves = inside(Path(self.config['memstick']), 'PSP/SAVEDATA')
        if list(local_saves.glob('.dock-journal-*.json')):
            raise ValueError('Hay una operación interrumpida en las partidas del PC. Revisa la recuperación.')
        root = self.root()
        if root:
            # A launch is NEVER permission to copy saves. Until USBSTOR→HID
            # is implemented, the user must explicitly prepare/sync first.
            plan = self.save_plan()
            if plan['conflicts'] or plan['actions']:
                raise ValueError('Revisa y autoriza la sincronización de partidas antes de jugar.')
            if self.session.get('prepared_volume') != volume_identity(root):
                raise ValueError('Prepara las partidas explícitamente desde Windows antes de jugar.')
            raise ValueError('PSP todavía en almacenamiento USB. Entra en modo mando HID antes de iniciar un juego.')
        elif not self.session.get('prepared_volume'):
            raise ValueError('Sincroniza la PSP antes de cambiar al modo mando.')
        game = next((g for g in self.games() if g['id'] == game_id), None)
        if not game or game['availability'] != 'ready':
            raise ValueError('El juego debe estar transferido y coincidir con la PSP.')
        exe = Path(self.config['emulator'])
        if not exe.is_file() or exe.name.lower() not in ('ppssppwindows64.exe', 'ppssppwindows.exe'):
            raise ValueError('Selecciona el ejecutable oficial de PPSSPP en los ajustes.')
        if os.name != 'nt':
            raise ValueError('Este lanzamiento está preparado para Windows.')
        # Manual HID-only mode is a verified PC-only fallback while the
        # kernel USBSTOR<->HID role bridge remains unavailable. A PDK1
        # device proves a gamepad, not firmware FAT ownership or same USB.
        from ppsspp_hid_session import validate_hid_only
        first_hid = validate_hid_only()
        exe = self.prepared_runtime_path(verify_digest=True)
        if exe is None:
            raise ValueError('Prepara PPSSPP desde Windows MIENTRAS USBSTOR está activo antes de iniciar el mando HID.')
        # Existing prewarmed sessions are upgraded as needed. We only write
        # the private PC profile; PSP firmware, USB and SAVEDATA are untouched.
        from ppsspp_windows_flow import add_home_pause_mapping, focus_async
        add_home_pause_mapping(
            inside(Path(self.config['memstick']), 'PSP/SYSTEM/controls.ini'),
            Path(self.config['storage_root']) / 'profile-backups',
        )
        # A different title may have a custom graphics profile; quick
        # explicit per-game update on the PC, never game/save/PSP media.
        self.apply_game_graphics(game_id)
        # Verify the same HID session after reading the PC runtime. A
        # watchdog expiration or new USBSTOR attachment must block spawn.
        last_hid = validate_hid_only()
        if first_hid != last_hid:
            raise ValueError('El mando HID cambió durante la preparación: cancelo PPSSPP.')
        args = [str(exe), '--fullscreen', '--pause-menu-exit', '--graphics=' + self.config['backend'],
                str(inside(Path(self.config['library']), game['local_relative']))]
        self.events.record('game_launch_requested', evidence='pc_action',
                           game_id=game_id,
                           render_scale=self.graphics_view(game_id)['plan']['internal_resolution'])
        self.session.update(running=True, pending_return=True,launcher='ppsspp',
                            last_game_id=game_id)
        atomic_json(self.data / 'session.json', self.session)
        try:
            self.process = subprocess.Popen(args, cwd=exe.parent)
            self.events.record('game_launched', evidence='pc_observed',
                               game_id=game_id, result='process_started')
            # PPSSPP creates its window asynchronously. Select the actual
            # PID-owned window and foreground it without blocking HTTP.
            focus_async(self.process)
        except OSError:
            self.session['running'] = False
            atomic_json(self.data / 'session.json', self.session)
            self.events.record('game_launch_failed', evidence='pc_observed',
                               game_id=game_id, result='popen_error')
            raise

    def launch_retro(self, rom_id):
        """Launch an audited cartridge in an installed native Windows core.

        Does not execute a PSP EBOOT in PPSSPP, write PSP files or claim that
        incompatible emulator save states are interchangeable.
        """
        if self.demo or self.running() or self.recovery_required:
            raise ValueError('No se puede iniciar una ROM en esta sesión.')
        if self.storage_polling_paused or self.session.get('exit_requested'):
            raise ValueError('El Dock está saliendo o preparando el volumen.')
        self.validate_layout(self.root())
        if self.root() is not None:
            raise ValueError('Activa el mando HID antes de iniciar un juego retro.')
        if (not self.retro_volume or
                self.session.get('prepared_volume') != self.retro_volume or
                self.session.get('pending_return') and self.session.get('exit_requested')):
            raise ValueError('Prepara las partidas de esta misma Memory Stick antes de jugar.')
        if list(inside(Path(self.config['memstick']), 'PSP/SAVEDATA').glob('.dock-journal-*.json')):
            raise ValueError('Revisa primero una sincronización de partidas interrumpida.')
        rom = next((r for r in self.retro_catalog()['roms'] if r['id']==rom_id),None)
        if rom is None or rom['availability'] != 'ready':
            raise ValueError('La ROM debe estar copiada y verificada por SHA-256.')
        save_row=next((item for item in self.retro_save_plan()['rows'] if item['rom_id']==rom_id),None)
        emulator=next((emu for emu in self.last_emulators if emu['id']==rom['emulator_id']),None)
        if emulator and (emulator['name'],rom['system']) in RAW_PROFILES and save_row is None:
            raise ValueError('Vuelve al almacenamiento USB y comprueba primero la SRAM de este juego.')
        if save_row and save_row['status'] in ('psp_to_pc','conflict','blocked') and not save_row['skipped']:
            raise ValueError('Revisa la partida retro en PSP antes de abrir otra versión en Windows.')
        selected = retroarch_core(self.config['retroarch'],rom['system'])
        if selected is None:
            raise ValueError('Configura RetroArch con el núcleo ' + SYSTEMS[rom['system']][1] + ' instalado en Windows.')
        if os.name != 'nt':
            raise ValueError('El Dock inicia juegos retro únicamente en Windows.')
        from ppsspp_hid_session import validate_hid_only
        from ppsspp_windows_flow import focus_async
        before = validate_hid_only()
        exe, core = selected
        local = inside(Path(self.config['library']),rom['local_relative'])
        mirror = next((r for r in self.last_roms if r['id']==rom_id),None)
        if (not local.is_file() or mirror is None or
                not re.fullmatch('[0-9a-f]{64}',mirror.get('signature') or '') or
                digest(local) != mirror['signature']):
            raise ValueError('La ROM local ya no coincide con el catálogo.')
        storage=Path(self.config['storage_root'])
        profile=prepare_joypad_profile(storage,layout=self.config['retro_button_layout'])
        save,state=private_save_paths(storage,self.retro_volume,rom_id)
        save.parent.mkdir(parents=True,exist_ok=True)
        # A savestate belongs to its core; never recycle it after a core swap.
        state=state.with_name(state.stem+'-'+hashlib.sha256(core.name.encode()).hexdigest()[:12]+state.suffix)
        state.parent.mkdir(parents=True,exist_ok=True)
        after = validate_hid_only()
        if before != after:
            raise ValueError('El mando HID cambió antes de iniciar RetroArch.')
        self.events.record('retro_launch_requested',evidence='pc_action',
                           game_id=rom_id,system=rom['system'])
        self.session.update(running=True,pending_return=True,
                            launcher='retroarch',last_game_id=rom_id)
        atomic_json(self.data/'session.json',self.session)
        try:
            self.process = subprocess.Popen(
                retroarch_arguments(exe,core,local,profile,save,state),cwd=exe.parent)
            self.events.record('retro_launched',evidence='pc_observed',
                               game_id=rom_id,result='process_started')
            focus_async(self.process)
        except OSError:
            self.session['running']=False
            atomic_json(self.data/'session.json',self.session)
            self.events.record('retro_launch_failed',evidence='pc_observed',
                               game_id=rom_id,result='popen_error')
            raise

    def request_exit(self):
        """User explicitly authorizes save return, not a USB role command.

        A future bridge may deliver a verified one-shot HID return. Until
        then USBSTOR must be restored MANUALLY, but once the ORIGINAL
        Sony volume is back and HID is absent we can sync saves without
        requiring another mouse click. This explicit Exit action is consent.
        """
        if self.running():
            raise ValueError('Cierra el emulador de Windows normalmente antes de salir del Dock.')
        if (self.transfer_status.get('active') or
                self.copy_queue.get('status') in ('waiting_for_storage','copying')):
            raise ValueError('Cancela o termina primero la copia solicitada desde el XMB.')
        if self.demo or not self.session.get('prepared_volume'):
            raise ValueError('No hay una sesión PSP Dock preparada para devolver.')
        if self.storage_polling_paused:
            raise ValueError('No es seguro salir mientras se prepara el volumen.')
        self.session['exit_requested'] = True
        self.session['return_sync_authorized'] = True
        self.session['return_sync_attempted'] = False
        self.session['retro_save_skipped_ids'] = []
        # The user approved closing the Windows app AFTER the return
        # is complete and verified, never merely on pressing Exit.
        self.session['close_after_verified_return'] = True
        atomic_json(self.data / 'session.json', self.session)
        self.events.record('dock_exit_requested', evidence='pc_action',
                           manual=True,
                           volume_serial=self.session.get('prepared_volume', ''))

    def complete_authorized_return(self):
        """One-shot save return after manual reappearance of original USBSTOR.

        Fail closed for wrong volumes, remaining HID interfaces, conflicts,
        PPSSPP running or an old/crashed attempt. No USBSTOR→HID or eject.
        A durable attempt flag is written BEFORE beginning a possible
        mutating copy. On crash/failure it needs manual review, not retries.
        """
        if not (self.session.get('exit_requested') and
                self.session.get('return_sync_authorized') is True and
                self.session.get('return_sync_attempted') is False):
            return None
        if self.running() or self.storage_polling_paused:
            return None
        root=self.root()
        if root is None:
            return None
        if volume_identity(root) != self.session.get('prepared_volume'):
            raise ValueError('Otra Memory Stick: no se devolverán las partidas.')
        from bridge_hid import probe as hid_probe
        report=hid_probe()
        if (not report.get('supported') or report.get('errors')
                or report.get('devices')):
            raise ValueError('El modo HID no ha desaparecido de Windows; no sincronizar todavía.')
        # Battery saves are not PSP/SAVEDATA slots: they can have a different
        # format and must be reviewed separately before closing the XMB.
        # No automatic SRAM writes to the PSP, even after Exit was pressed.
        retro_plan=self.retro_save_plan()
        if retro_plan['pending']:
            if self.session.get('return_result')!='retro_review':
                self.session['return_result']='retro_review'
                atomic_json(self.data/'session.json',self.session)
                self.events.record('retro_sram_review_required',evidence='pc_observed',
                                   count=retro_plan['pending'],result='needs_explicit_choice')
            return None
        plan=self.save_plan()
        if plan['conflicts']:
            self.session['return_sync_authorized']=False
            self.session['return_result']='conflicts'
            atomic_json(self.data/'session.json',self.session)
            self.events.record('save_return_conflicts', evidence='pc_observed',
                               conflicts=len(plan['conflicts']), result='needs_review')
            return plan  # No writes: user must choose each conflicting save.
        self.session['return_sync_authorized']=False
        self.session['return_sync_attempted']=True
        self.session['return_result']='in_progress'
        atomic_json(self.data/'session.json',self.session)
        self.events.record('save_return_started', evidence='pc_action',
                           volume_serial=self.session.get('prepared_volume', ''),
                           actions=len(plan['actions']), attempted=True)
        try:
            final=self.sync()
        except (OSError, ValueError):
            self.events.record('save_return_interrupted', evidence='pc_observed',
                               result='manual_recovery_required')
            raise
        if not final['conflicts'] and not final['actions']:
            self.session['return_result']='verified'
            self.session['return_sync_attempted']=False
            atomic_json(self.data/'session.json',self.session)
            self.events.record('save_return_verified', evidence='pc_observed',
                               result='verified', actions=len(plan['actions']))
        return final

    def settings(self, new):
        if self.running() or self.session.get('pending_return'):
            raise ValueError('Cierra y devuelve la sesión a la PSP antes de cambiar las carpetas.')
        if ('storage_root' in new and
                new['storage_root'] != self.config['storage_root']):
            raise ValueError('Para mover los datos usa la migración verificada.')
        config = {**self.config, **{k:v for k,v in new.items() if k in self.config}}
        if config.get('retro_button_layout') not in LAYOUTS:
            raise ValueError('Distribución del mando retro desconocida.')
        if (not isinstance(config.get('retroarch'),str) or
                config['retroarch'] and
                (not Path(config['retroarch']).is_absolute() or
                 Path(config['retroarch']).name.casefold() != 'retroarch.exe')):
            raise ValueError('Selecciona un ejecutable retroarch.exe de Windows o deja la opción vacía.')
        if type(config['scale']) is not int or not 1 <= config['scale'] <= 6 or config['backend'] not in ('directx11','vulkan','gles'):
            raise ValueError('Perfil gráfico inválido.')
        for key in ('library','memstick'):
            if not isinstance(config[key], str) or not Path(config[key]).is_absolute():
                raise ValueError('Usa rutas absolutas para la biblioteca y las partidas.')
        effective = Path(config['psp_root']) if config['psp_root'] else self.root()
        self.validate_layout(effective, config)
        if any(config[k] != self.config[k] for k in ('library','memstick','psp_root')):
            self.session.pop('prepared_volume', None)
            atomic_json(self.data / 'session.json', self.session)
        previous_config = self.config
        self.config = config
        # Preserve the remembered PSP titles when only emulator graphics
        # change while the USB device is in HID mode. Re-inventory attached
        # devices under the new paths on the next refresh.
        if any(config[k] != previous_config[k] for k in ('library','psp_root')):
            self.last_games = []
            self.cached_game_results = None
            self.last_emulators = []
            self.last_roms = []
            self.retro_volume = ''
            self.cached_retro_results = None
            self.retro_save_volume=''
            self.last_retro_save_rows=[]
        self.cache.clear()
        atomic_json(self.config_path, config)

    def relocate_storage(self, target):
        """Copy PC games, saves, textures and media into an empty folder."""
        from storage_layout import migrate_pc_data
        if self.demo or self.running() or self.session.get('pending_return'):
            raise ValueError('Termina la sesión y devuelve partidas antes de cambiar la carpeta.')
        if (self.session.get('exit_requested') or self.transfer_status['active']
                or self.xmb_media_progress.get('active')):
            raise ValueError('Termina todas las copias antes de mover los datos.')
        if not isinstance(target,str) or not target.strip():
            raise ValueError('Selecciona una carpeta absoluta del PC.')
        report=migrate_pc_data(target=Path(target),
                               library=Path(self.config['library']),
                               memstick=Path(self.config['memstick']),
                               old_data=self.data,
                               old_storage=Path(self.config['storage_root']))
        updated={**self.config,'storage_root':report['path'],
                 'library':report['library'],'memstick':report['memstick']}
        self.validate_layout(self.root(),updated)
        atomic_json(self.config_path,updated)
        self.config=updated
        self.session.pop('prepared_volume',None)
        atomic_json(self.data/'session.json',self.session)
        self.cached_game_results=None
        self.retro_save_volume=''
        self.last_retro_save_rows=[]
        self.last_storage_scan_at=0
        self.xmb_media_catalog_key=None
        self.events.record('pc_storage_relocated',evidence='pc_action',
                           count=report['files'],result='verified')
        return report

    def install_curated_texture_pack(self, game_id, offer_id):
        """Explicit user download for the exact local verified game revision."""
        from psp_texture_packs import CURATED_OFFERS, download_and_install
        from psp_game_metadata import read_metadata
        if self.demo or self.running() or self.session.get('pending_return'):
            raise ValueError('No se instalan packs mientras juegas o devuelves partidas.')
        if self.transfer_status['active'] or self.session.get('exit_requested'):
            raise ValueError('Termina la transferencia de juegos antes del pack HD.')
        game=next((g for g in self.cached_game_results or []
                   if g['id']==game_id and g['availability']=='ready'),None)
        if not game:
            raise ValueError('El juego debe existir y estar verificado en el PC.')
        source=inside(Path(self.config['library']),game['local_relative'])
        disc=read_metadata(source).get('DISC_ID')
        offer=next((p for p in CURATED_OFFERS
                    if p.id==offer_id and p.disc_id==disc),None)
        if offer is None:
            raise ValueError('No hay pack de fuente verificada para esta edición.')
        report=download_and_install(offer,matching_disc_id=disc,
                                    private_memstick=Path(self.config['memstick']))
        self.events.record('hd_texture_pack_installed',evidence='pc_observed',
                           game_id=game_id,result='hash_verified',
                           count=report['textures'])
        return report

    def controller_status(self, storage_mounted):
        """Observe manual PDK1 HID without authorizing a USB role handoff."""
        if self.demo:
            return {'connected': False, 'mode': 'demo',
                    'reason': 'La demo no controla ningún mando real.'}
        if self.storage_polling_paused:
            return {'connected': False, 'mode': 'host_lock_preparation',
                    'reason': 'Sondeo FAT suspendido; no es prueba de bloqueo Windows.'}
        if storage_mounted:
            return {'connected': False, 'mode': 'usb_storage',
                    'reason': 'Primero termina en almacenamiento; HID y FAT no deben coexistir.'}
        if os.name != 'nt':
            return {'connected': False, 'mode': 'unsupported',
                    'reason': 'El mando PDK1 se comprueba únicamente en Windows.'}
        try:
            from ppsspp_hid_session import validate_hid_only
            validate_hid_only()
        except (OSError, ValueError) as exc:
            return {'connected': False, 'mode': 'await_hid',
                    'reason': str(exc)}
        return {'connected': True, 'mode': 'hid_manual',
                'reason': 'Mando HID PDK1 confirmado; no es prueba del bridge USB automático.'}

    def psp_launcher_input(self):
        """Read a single WinMM PSP gamepad frame for the XMB, never launch.

        This GET is intentionally outside Dock.lock so an expensive optional
        inventory refresh never blocks controller navigation for minutes.
        The watcher must first have verified one PDK1 HID session. A stale
        snapshot never creates a USB role bridge or grants FAT ownership.
        """
        from psp_launcher_input import PSPWinMMReader, idle
        if self.demo or self.storage_polling_paused:
            return idle("El mando PSP HID no está disponible en esta fase.")
        seen = self.snapshot.get('controller_status', {})
        if not isinstance(seen, dict) or seen.get('connected') is not True:
            return idle("El Dock todavía no ha verificado una PSP HID PDK1.")
        try:
            return PSPWinMMReader().read()
        except (OSError, ValueError, RuntimeError) as exc:
            return idle(str(exc))

    def state(self):
        running = self.running()
        device = self.root()
        games = self.games()
        retro = self.retro_catalog()
        retro_saves=self.retro_save_plan() if not running else {
            'rows':[], 'pending':0,'conflicts':0,'unmanaged':0,'offline':True}
        saves = (self.save_plan() if not running
                 else {'actions': [], 'conflicts': [], 'unchanged': 0})
        remembered_serial = volume_identity(device) if device is not None else (self.catalog_volume or self.retro_volume)
        controller = self.controller_status(device is not None)
        preflight = assess_storage(
            connected=device is not None, demo=self.demo, games=games,
            saves=saves, session={**self.session, 'running': running},
            recovering=self.recovery_required,
            current_volume_serial=remembered_serial,
            hid_connected=controller['connected'],
        )
        local_saves = inside(Path(self.config['memstick']), 'PSP/SAVEDATA')
        local_journal = bool(list(local_saves.glob('.dock-journal-*.json')))
        can_launch = bool(
            controller['connected'] and preflight.saves_prepared and
            preflight.ready_games > 0 and not running and
            not self.recovery_required and not local_journal and
            not self.session.get('exit_requested') and
            self.prepared_runtime_path() is not None
        )
        can_launch_retro = bool(
            controller['connected'] and preflight.saves_prepared and
            not running and not self.recovery_required and not local_journal and
            not self.session.get('exit_requested') and
            any(rom['availability']=='ready' and rom['core_available'] for rom in retro['roms'])
        )
        return {'demo': self.demo, 'devices': self.devices(), 'selected_device': str(device) if device else None,
                'games': games, 'retro':retro, 'retro_saves':retro_saves,
                'retro_button_layout':self.config['retro_button_layout'],
                'saves': saves,
                'copy_queue':dict(self.copy_queue),
                'copy_policy': self.copy_policy(),
                'preflight': preflight.snapshot(),
                'controller_status': controller,
                'can_launch_game': can_launch,
                'can_launch_retro': can_launch_retro,
                'runtime_prepared': self.prepared_runtime_path() is not None,
                'runtime_error': getattr(self, 'runtime_error', ''),
                'library_source': ('psp_live' if device else
                                   'cached_manifest' if self.last_games else 'empty'),
                'session': {**self.session, 'running': running},
                'emulator': {'path': self.config['emulator'], 'available': bool(self.config['emulator']) and Path(self.config['emulator']).is_file()},
                'error': self.error, 'recovery_required': self.recovery_required,
                'controller': 'PSP Dock HID probado en hardware: botones, cruceta y joystick operativos. Cambio USB automático pendiente.'}

    def icon(self, game_id):
        """Serve only ICON0.PNG from a game in the known catalog.

        Cache the image locally on the PC. A disconnected PSP can still
        display the last XMB artwork, but cannot be read or written while
        the controller owns USB. An arbitrary URL cannot select a path.
        """
        if not isinstance(game_id, str) or not re.fullmatch('[0-9a-f]{24}', game_id):
            raise ValueError('Identificador de juego inválido.')
        # While the first USB scan is in progress, the visual mirror is
        # explicitly NON-authoritative. Never show an old PSP's artwork as
        # if it came from the new device or run a second USB reader.
        root = self.root()
        # Explicitly changing settings clears this materialized catalog;
        # one on-demand inventory then establishes the NEW device identity.
        # For all subsequent images, avoid a full USB/FAT inventory.
        preview_only=False
        if self.cached_game_results is None:
            if self.snapshot.get('scanning') and self.last_games:
                if root is not None:
                    # A new mounted device may not be the last cached PSP.
                    # No artwork from it before the full live audit.
                    return None
                # Offline: previously cached PC artwork may still be shown,
                # but must not initiate a full inventory or FAT read.
                games=self.snapshot.get('games',[])
                preview_only=True
            else:
                games=self.games()
        else:
            # A cached catalog from another Memory Stick is never safe,
            # even when a game uses the same relative pathname.
            if root is not None and volume_identity(root) != self.catalog_volume:
                return None
            games = self.cached_game_results
        game = next((g for g in games if g['id'] == game_id), None)
        if game is None:
            return None
        art_dir = inside(Path(self.config.get('storage_root') or self.data), 'artwork')
        # The URL's game ID is based on the relative path; two different PSPs
        # can contain DIFFERENT games at that same path. Scope PC artwork by
        # the last verified volume identity plus a stable media fingerprint.
        mirror = next((g for g in self.last_games if g['id'] == game_id), game)
        fingerprint = {
            'cache_version': 2, 'volume_serial': self.catalog_volume,
            'id': game_id, 'relative': game['relative'],
            'format': game['format'], 'size': game['size'],
            'modified_ns': mirror.get('modified_ns'),
            'signature': mirror.get('signature'),
        }
        cache_key = hashlib.sha256(json.dumps(fingerprint, sort_keys=True).encode()).hexdigest()
        cached = inside(art_dir, cache_key + '.png')
        if cached.is_file():
            value = cached.read_bytes()
            if valid_png(value):
                return value
            raise ValueError('Icono cacheado no corresponde a PNG verificado.')
        if preview_only:
            return None
        source = None
        if game['availability'] == 'ready':
            source = inside(Path(self.config['library']), game['local_relative'])
        else:
            if root is not None:
                source = inside(root, game['relative'])
        if source is None or not source.is_file():
            return None
        artwork = read_icon(source)
        if not artwork:
            return None
        art_dir.mkdir(parents=True, exist_ok=True)
        temporary = inside(art_dir, '.icon-' + uuid.uuid4().hex)
        try:
            with temporary.open('xb') as output:
                output.write(artwork)
                output.flush()
                os.fsync(output.fileno())
            if not cached.exists():
                os.replace(temporary, cached)
        finally:
            temporary.unlink(missing_ok=True)
        return artwork

    def xmb_media(self, game_id, kind):
        """Read PIC0/PIC1 or browser-safe SND0 from audited game, PC cached.

        No PSP FAT reads once storage disappears. The cache key includes
        both Sony volume serial and game's verified catalog signature.
        """
        from psp_xmb_media import XMBAssetCache, media_key, RESOURCE_NAMES
        if kind not in RESOURCE_NAMES:
            raise ValueError('Recurso XMB desconocido.')
        if not isinstance(game_id,str) or not re.fullmatch('[0-9a-f]{24}',game_id):
            raise ValueError('Juego del XMB desconocido.')
        game=next((g for g in self.last_games if g.get('id')==game_id),None)
        if game is None or not self.catalog_volume:
            return None
        cache=XMBAssetCache(
            Path(self.config.get('storage_root') or self.data)/'xmb-assets')
        key=media_key(volume=self.catalog_volume,game=game)
        existing=cache.cached(key,kind)
        if existing is not None:
            return existing
        if self.demo or self.storage_polling_paused or self.running():
            return None
        if self.transfer_status.get('active'):
            return None
        source=None
        catalog=self.cached_game_results or []
        selected=next((g for g in catalog if g['id']==game_id),None)
        if selected and selected.get('availability')=='ready':
            source=inside(Path(self.config['library']),selected['local_relative'])
        else:
            attached=self.snapshot.get('selected_device')
            if attached:
                root=Path(attached)
                if root.is_dir() and volume_identity(root)==self.catalog_volume:
                    source=inside(root,game['relative'])
        return cache.ensure(key,kind,source)

    def start_xmb_media_prefetch(self):
        """Best-effort copy of XMB preview media only, not ROM or save files."""
        if self.demo or self.xmb_media_worker and self.xmb_media_worker.is_alive():
            return
        root=self.snapshot.get('selected_device')
        if (not root or not self.last_games or self.snapshot.get('scanning')
                or self.session.get('pending_return') or self.running()):
            return
        identity=self.catalog_volume
        token=(identity,tuple((g['id'],g.get('modified_ns')) for g in self.last_games))
        if self.xmb_media_catalog_key==token:
            return
        self.xmb_media_catalog_key=token
        self.xmb_media_progress={'active':True,'done':0,
                                 'total':len(self.last_games),'errors':0}
        def job():
            for game in list(self.last_games):
                try:
                    with self.lock:
                        if (self.stop.is_set() or self.storage_polling_paused or
                                self.transfer_status.get('active') or
                                self.snapshot.get('selected_device')!=root or
                                self.catalog_volume!=identity or self.running()):
                            break
                        for kind in ('icon','background','overlay','sound'):
                            self.xmb_media(game['id'],kind)
                except (OSError,ValueError):
                    self.xmb_media_progress['errors']+=1
                self.xmb_media_progress['done']+=1
            self.xmb_media_progress['active']=False
        self.xmb_media_worker=threading.Thread(
            target=job,daemon=True,name='PSPDockXMBMediaCache')
        self.xmb_media_worker.start()

    def watch(self):
        while not self.stop.is_set():
            open_on_attach = False
            close_on_verified = False
            with self.lock:
                try:
                    running = self.running()
                    root = self.root()
                    if root and not running:
                        identity = volume_identity(root)
                        if self.session.get('pending_return') and self.session.get('prepared_volume') != identity:
                            raise ValueError('La sesión pertenece a otra Memory Stick. No se sincroniza con esta unidad.')
                        if (self.copy_queue.get('status')=='waiting_for_storage'
                                and not self.session.get('exit_requested')):
                            self.complete_queued_game_copy()
                        # Explicit Exit from XMB is the consent to return saves.
                        # Only automatic on manual restoration of the SAME
                        # USBSTOR, with no HID simultaneously and one-shot
                        # durable attempt tracking. Never auto sync on attach.
                        previous_exit = bool(
                            self.session.get('close_after_verified_return') and
                            self.session.get('exit_requested'))
                        result = self.complete_authorized_return()
                        if (previous_exit and result is not None and
                                not result['actions'] and not result['conflicts'] and
                                self.session.get('return_result') == 'verified' and
                                not self.session.get('pending_return')):
                            close_on_verified=True
                            self.session['close_after_verified_return']=False
                            atomic_json(self.data/'session.json',self.session)
                        if not self.session.get('pending_return'):
                            saves = self.save_plan()
                            if (self.session.get('prepared_volume') and
                                    (self.last_device != identity or
                                     self.session['prepared_volume'] != identity or
                                     saves['actions'] or saves['conflicts'])):
                                self.session.pop('prepared_volume', None)
                                atomic_json(self.data/'session.json', self.session)
                            # When EVERY save is identical, a no-copy review
                            # can be prepared automatically. Never auto-copy
                            # a changed save or an absent title.
                            if not saves['actions'] and not saves['conflicts']:
                                if self.prepare_identical_saves_without_copy():
                                    # A PSP with only retro emulators should
                                    # not fail PPSSPP prewarming; the RetroArch
                                    # runtime is validated per selected ROM.
                                    if any(g['availability']=='ready' for g in self.games()):
                                        try:
                                            self.prepare_runtime()
                                        except (OSError, ValueError, subprocess.SubprocessError) as exc:
                                            self.runtime_error=str(exc)
                        # A connection is a READ-ONLY audit, never implicit
                        # writes to game/save files. Invalidation changes only
                        # metadata on the PC. User approves save sync in UI.
                        open_on_attach = bool(
                            self.on_connected and self.last_device != identity
                            and not self.session.get('pending_return')
                            and not self.session.get('exit_requested')
                            and not close_on_verified
                        )
                        if self.last_device != identity:
                            self.events.record('sony_storage_seen', evidence='pc_observed',
                                               volume_serial=identity, mode='usb_storage')
                        self.last_device = identity
                    elif not root:
                        if self.last_device is not None:
                            self.events.record('sony_storage_absent', evidence='pc_observed',
                                               mode='storage_not_visible')
                        self.last_device = None
                    # Full game + SAVEDATA auditing is deliberately expensive
                    # over PSP USB. Do it on new storage attachment, on state
                    # changes and occasionally for freshness, NOT every 3s.
                    # Mutating API requests publish their own fresh snapshot.
                    now = time.monotonic()
                    same_storage = bool(
                        root and self.last_storage_scan_identity == identity
                        and self.snapshot.get('selected_device') == str(root)
                        and self.snapshot.get('scanning') is False
                        and self.snapshot.get('session', {}).get('running') == running
                    )
                    if not (same_storage and not self.error and
                            now-self.last_storage_scan_at < self.storage_scan_interval):
                        self.error = None
                        self.snapshot = {**self.state(),'scanning':False}
                        self.last_storage_scan_identity = identity if root else ''
                        self.last_storage_scan_at = time.monotonic()
                    if root and not running:
                        self.start_xmb_media_prefetch()
                    # A PSP-first session enumerates PDK1 HID, not the
                    # original Sony USBSTOR. Open the selector once on the
                    # rising edge of that verified controller, never a game.
                    hid_now = bool(
                        self.snapshot.get('controller_status',{}).get('connected')
                    ) if root is None else False
                    if (self.on_connected and hid_now and
                            not self.last_hid_present and not running and
                            not self.session.get('exit_requested')):
                        open_on_attach = True
                        self.events.record('sony_hid_seen',evidence='pc_observed',
                                           mode='hid_manual')
                    self.last_hid_present = hid_now
                except (OSError, ValueError) as e:
                    self.error = str(e)
                    self.snapshot = {**self.snapshot,'error':self.error,'scanning':False,
                                     'session':{**self.session,'running':self.running()}}
            if open_on_attach and self.on_connected:
                # Browser opening is best-effort after the read-only audit,
                # outside Dock's lock. It never activates the USB controller.
                try:
                    self.on_connected()
                except (OSError, ValueError):
                    pass
            if close_on_verified and self.on_verified_exit:
                try:
                    self.on_verified_exit()
                except (OSError,ValueError):
                    # User can still close XMB manually. No save retries.
                    pass
            self.stop.wait(3)

def make_demo(data):
    psp = Path(data) / 'sample-psp'
    (psp / 'PSP/SAVEDATA/DEMO0001').mkdir(parents=True, exist_ok=True)
    (psp / 'PSP/SAVEDATA/DEMO0001/DATA.BIN').write_bytes(b'Synthetic save; not a PSP save.')
    (psp / 'ISO').mkdir(exist_ok=True)
    for name in ('Aurora Circuit', 'Lunar Expedition', 'Pocket Orchestra'):
        file = psp / 'ISO' / (name + '.iso')
        if not file.exists():
            file.write_bytes(('DEMO ONLY - NOT AN ISO - ' + name).encode())


def default_data_path(localappdata, *, demo=False):
    """Reuse the single known PC library instead of silently creating a new one.

    Earlier versions ran inside the Codex AppContainer and stored their live
    data under Packages/OpenAI.Codex_*/LocalCache/Local/PSP-Dock/live.
    Launching a normal .cmd outside that container must not give the user a
    misleading blank library or silently replace a known configuration.
    This resolver only inspects the PC; it never searches removable disks.
    """
    local = Path(localappdata)
    if demo:
        return local / 'PSP-Dock/demo'
    from setup_startup import candidate_live_directories
    choices = candidate_live_directories(local)
    if len(choices) == 1:
        return choices[0]
    if len(choices) > 1:
        raise ValueError(
            'Hay varias bibliotecas LIVE de PSP Dock existentes. '
            'Inicia con --data RUTA_LIVE para elegir una sin mezclarlas.'
        )
    return local / 'PSP-Dock/live'

def acquire_instance_lock(data):
    """OS releases this lock after crashes; distinct ports cannot bypass it."""
    handle = (Path(data) / 'instance.lock').open('a+b')
    try:
        handle.seek(0)
        if handle.read(1) == b'':
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        raise ValueError('PSP Dock ya está abierto con esta carpeta de datos.')
    return handle

def recover_session(data):
    data = Path(data)
    data.mkdir(parents=True,exist_ok=True)
    lock = acquire_instance_lock(data)
    try:
        if os.name != 'nt':
            raise ValueError('La comprobación de procesos de recuperación requiere Windows.')
        result = subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',
            '$ErrorActionPreference="Stop"; @(Get-CimInstance Win32_Process -Filter "Name=\'PPSSPPWindows64.exe\' OR Name=\'PPSSPPWindows.exe\'").Count'],
            capture_output=True,text=True,timeout=30,creationflags=0x08000000)
        if result.returncode != 0 or result.stdout.strip() != '0':
            raise ValueError('No se puede recuperar mientras PPSSPP siga abierto o no se pueda comprobar su cierre.')
        session_file=data/'session.json'
        session=read_json(session_file,{'running':False,'pending_return':False})
        session['running']=False
        atomic_json(session_file,session)
        print('Sesión desbloqueada. La devolución sigue pendiente hasta conectar la Memory Stick original. Los registros de copia interrumpida siguen protegidos.')
    finally:
        lock.close()

def launch_xmb_window(url, *, app_window=False):
    """Open the Windows XMB as a standalone fullscreen app, not another tab.

    Only the fixed localhost URL generated by the bound HTTP service is
    allowed. An installed Chrome or Edge uses --app (no browser chrome).
    This never touches a device, driver, browser profile or game save.
    """
    if not re.fullmatch(r'http://127[.]0[.]0[.]1:[0-9]{1,5}/?',url):
        raise ValueError('La ventana XMB solo admite la URL local del Dock.')
    if not app_window:
        return webbrowser.open(url)
    from xmb_window import maximize_existing_xmb, maximize_when_ready
    # Preserve current selection/menu state in an already opened XMB,
    # instead of spawning another Chrome app window every time the user
    # double-clicks the desktop shortcut.
    if maximize_existing_xmb():
        return True
    candidates = [
        Path(os.environ.get('PROGRAMFILES', 'C:/Program Files')) /
        'Google/Chrome/Application/chrome.exe',
        Path(os.environ.get('PROGRAMFILES(X86)', 'C:/Program Files (x86)')) /
        'Microsoft/Edge/Application/msedge.exe',
        Path(os.environ.get('LOCALAPPDATA', str(ROOT))) /
        'Google/Chrome/Application/chrome.exe',
    ]
    browser = next((p for p in candidates if p.is_file()), None)
    if browser is None:
        return webbrowser.open(url)
    proc = subprocess.Popen([str(browser), '--new-window', '--app=' + url,
                             '--start-maximized'], cwd=browser.parent)
    # Chrome may reuse its main profile and ignore start flags. A unique,
    # titled XMB app is then safely maximized without touching any other
    # window or issuing keystrokes to the user's desktop.
    threading.Thread(target=maximize_when_ready,daemon=True,
                     name='PSPDockXMBWindowMaximize').start()
    return proc


def service_fingerprint(data):
    """Stable host-local identifier, not an authentication token."""
    return hashlib.sha256(str(Path(data).resolve()).encode('utf-8')).hexdigest()


def serve(dock, port, open_browser, open_on_connect=False, app_window=False):
    instance_lock = acquire_instance_lock(dock.data)
    token = secrets.token_urlsafe(32)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass
        def reply(self, value, status=200):
            payload = json.dumps(value, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header('Content-Type','application/json; charset=utf-8')
            self.send_header('Content-Length',str(len(payload)))
            self.send_header('Cache-Control','no-store')
            self.end_headers()
            self.wfile.write(payload)
        def valid_host(self):
            return self.headers.get('Host') == f'127.0.0.1:{self.server.server_port}'
        def do_GET(self):
            if not self.valid_host():
                return self.reply({'error':'Host no permitido'},403)
            try:
                # Serve immutable snapshots while the USB worker reads files.
                # Neither the launcher nor token requests wait for USB hashing.
                if self.path == '/api/token':
                    return self.reply({'token':token})
                if self.path == '/api/health':
                    return self.reply({
                        'product':'psp-dock', 'demo':dock.demo,
                        'data_fingerprint':service_fingerprint(dock.data),
                        'port':self.server.server_port,
                    })
                if self.path == '/api/state':
                    return self.reply(dock.snapshot)
                if self.path == '/api/controller-input':
                    # Safe read-only WinMM frame; no USB commands and no
                    # expensive Dock.lock while rendering the XMB.
                    return self.reply(dock.psp_launcher_input())
                if self.path == '/api/media-status':
                    return self.reply(dock.xmb_media_progress)
                if self.path == '/api/copy-queue':
                    return self.reply(dock.copy_queue)
                if self.path.startswith('/api/media/'):
                    parts=self.path.split('/')
                    if len(parts)!=5 or parts[1:3]!=['api','media']:
                        return self.reply({'error':'Recurso desconocido'},404)
                    game_id,kind=parts[3],parts[4]
                    data=dock.xmb_media(game_id,kind)
                    if data is None:
                        return self.reply({'error':'Recurso XMB no disponible'},404)
                    self.send_response(200)
                    self.send_header('Content-Type',
                                     'audio/mpeg' if kind=='sound' else 'image/png')
                    self.send_header('Content-Length',str(len(data)))
                    self.send_header('Cache-Control','private, max-age=120')
                    self.send_header('X-Content-Type-Options','nosniff')
                    self.end_headers()
                    self.wfile.write(data)
                    return
                if self.path == '/api/transfer-status':
                    # Independent progress lock: the copying POST holds
                    # Dock.lock for the entire verified USB→PC transfer.
                    return self.reply(dock.transfer_progress())
                if self.path == '/api/settings':
                    return self.reply(dock.config)
                if self.path == '/api/graphics' or self.path.startswith('/api/graphics/'):
                    requested = (self.path[len('/api/graphics/'):]
                                 if self.path.startswith('/api/graphics/') else None)
                    return self.reply(dock.graphics_view(requested,include_gpu=True))
                if self.path.startswith('/api/icon/'):
                    game_id = self.path[len('/api/icon/'):]
                    with dock.lock:
                        icon = dock.icon(game_id)
                    if icon is None:
                        return self.reply({'error': 'Juego sin ICON0.PNG'},404)
                    self.send_response(200)
                    self.send_header('Content-Type','image/png')
                    self.send_header('Content-Length', str(len(icon)))
                    # The public URL includes only a relative-path game ID;
                    # another PSP can reuse it for completely different art.
                    # Local server cache is scoped to the verified serial,
                    # but the browser must never persist that old response.
                    self.send_header('Cache-Control','no-store')
                    self.send_header('X-Content-Type-Options','nosniff')
                    self.end_headers()
                    self.wfile.write(icon)
                    return
                files = {'/':'index.html','/app.js':'app.js','/style.css':'style.css'}
                name = files.get(self.path)
                if not name:
                    return self.reply({'error':'No encontrado'},404)
                content = (ROOT/'web'/name).read_bytes()
                self.send_response(200)
                self.send_header('Content-Type',{'html':'text/html; charset=utf-8','js':'text/javascript; charset=utf-8','css':'text/css; charset=utf-8'}[name.split('.')[-1]])
                self.send_header('Content-Length',str(len(content)))
                self.send_header('Content-Security-Policy',"default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
                self.end_headers()
                self.wfile.write(content)
            except (OSError, ValueError) as e:
                self.reply({'error':str(e)},409)
        def do_POST(self):
            origin = f'http://127.0.0.1:{self.server.server_port}'
            if not self.valid_host() or self.headers.get('Origin') != origin or self.headers.get('X-Dock-Token') != token:
                return self.reply({'error':'Petición no autorizada'},403)
            try:
                length = int(self.headers.get('Content-Length',0))
                if not 0 < length <= 16384:
                    raise ValueError('Petición demasiado grande o vacía.')
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise ValueError('Petición inválida.')
                if self.path == '/api/cancel-transfer':
                    # Do NOT take dock.lock: the transfer must be able to
                    # observe the cancel flag while it owns that lock.
                    return self.reply(dock.cancel_transfer())
                if self.path == '/api/choose-storage-root':
                    from storage_layout import choose_pc_storage_parent
                    chosen=choose_pc_storage_parent()
                    return self.reply({'path':chosen,'cancelled':chosen is None})
                if self.path == '/api/copy-policy':
                    # A click to SKIP missing games only changes one PC JSON
                    # preference. Never block the browser for a new USB game
                    # inventory or accidentally copy games when skipping.
                    with dock.lock:
                        dock.choose_copy_policy(body.get('choice'))
                        dock.snapshot={**dock.snapshot,
                                       'copy_policy':dock.copy_policy()}
                    return self.reply({'ok':True})
                with dock.lock:
                    previous_pending_return = bool(dock.session.get('pending_return'))
                    save_result = None
                    if self.path == '/api/transfer':
                        dock.transfer(body['ids'])
                    elif self.path == '/api/retro-transfer':
                        dock.transfer_retro(body['ids'])
                    elif self.path == '/api/queue-game-copy':
                        dock.request_game_copy_from_hid(body.get('id'))
                    elif self.path == '/api/cancel-game-copy':
                        dock.cancel_game_copy_queue()
                    elif self.path == '/api/sync':
                        save_result = dock.sync()
                    elif self.path == '/api/resolve':
                        save_result = dock.sync(body)
                    elif self.path == '/api/launch':
                        dock.launch(body['id'])
                    elif self.path == '/api/retro-launch':
                        dock.launch_retro(body['id'])
                    elif self.path == '/api/retro-save-sync':
                        dock.sync_retro_save(body.get('id'),body.get('choice'),
                                             body.get('revision'))
                    elif self.path == '/api/exit-dock':
                        dock.request_exit()
                    elif self.path == '/api/graphics':
                        dock.set_graphics_view(body)
                    elif self.path == '/api/prepare-ppsspp':
                        try:
                            dock.prepare_runtime()
                        except (OSError, ValueError, subprocess.SubprocessError) as e:
                            dock.runtime_error = str(e)
                            raise
                    elif self.path == '/api/settings':
                        dock.settings(body)
                    elif self.path == '/api/storage-root':
                        dock.relocate_storage(body.get('path'))
                    elif self.path == '/api/install-texture-pack':
                        dock.install_curated_texture_pack(body.get('id'),
                                                          body.get('offer_id'))
                    elif self.path == '/api/select-device':
                        if body['root'] not in [d['root'] for d in dock.devices()]:
                            raise ValueError('Unidad no disponible.')
                        dock.settings({'psp_root':body['root']})
                    else:
                        return self.reply({'error':'No encontrado'},404)
                    if (save_result is not None and not previous_pending_return and
                            not dock.demo and not save_result['actions'] and
                            not save_result['conflicts'] and
                            dock.session.get('prepared_volume')):
                        # Background-free, deterministic PC-only preparation
                        # after consent. A runtime error must not turn an
                        # already committed save sync into an HTTP failure
                        # (client could otherwise repeat a write).
                        # Retro-only PSPs never require a PPSSPP runtime.
                        if any(g['availability']=='ready' for g in dock.games()):
                            try:
                                dock.prepare_runtime()
                            except (OSError, ValueError, subprocess.SubprocessError) as e:
                                dock.runtime_error = str(e)
                    try:
                        dock.snapshot = {**dock.state(),'scanning':False}
                    except (OSError,ValueError) as e:
                        # The mutation already committed (possibly launched a
                        # process). A failed USB refresh must not report failure
                        # and invite the client to repeat the mutation.
                        dock.snapshot = {**dock.snapshot,'error':str(e),
                                         'session':{**dock.session,'running':dock.running()}}
                    return self.reply({'ok':True})
            except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as e:
                if self.path in ('/api/transfer','/api/retro-transfer'):
                    progress = dock.transfer_progress()
                    # A rejected request can fail before copy progress is
                    # initialized (invalid game, no space, missing PSP).
                    # Never leave a stale prior 'done' status visible as
                    # success for that newly rejected request.
                    if not progress['active'] and progress['phase'] not in (
                            'cancelled', 'failed'):
                        dock._update_transfer(active=False, phase='failed',
                                              error=str(e))
                # A transfer is deliberately NOT all-or-nothing: a previous
                # game may have been fully verified and committed before
                # cancellation or a later-game failure. Refresh the public
                # snapshot BEFORE returning 409, after Dock.lock has been
                # released. The UI can then immediately show the committed
                # games as ready rather than waiting for the 3s watch tick.
                # Never wait on Dock.lock when cancelling: its cancellation
                # flag must be delivered while the transfer still owns it.
                if self.path in ('/api/transfer', '/api/retro-transfer',
                                 '/api/prepare-ppsspp',
                                 '/api/sync', '/api/resolve'):
                    with dock.lock:
                        try:
                            dock.snapshot = {**dock.state(), 'scanning': False}
                        except (OSError, ValueError) as refresh_error:
                            dock.snapshot = {**dock.snapshot,
                                             'error': str(refresh_error),
                                             'session': {**dock.session,
                                                         'running': dock.running()}}
                self.reply({'error':str(e)},409)
    server = ThreadingHTTPServer(('127.0.0.1',port),Handler)
    def close_when_saved():
        # Called only after a verified ORIGINAL Sony volume and successful
        # return. UI gets a moment to display "Sincronización completa".
        def finish():
            time.sleep(2.0)
            try:
                from xmb_window import close_existing_xmb
                close_existing_xmb()
            finally:
                server.shutdown()
        threading.Thread(target=finish,daemon=True,
                         name='PSPDockVerifiedExit').start()
    dock.on_verified_exit=close_when_saved
    url = f'http://127.0.0.1:{server.server_port}'
    metadata = {'product':'psp-dock', 'port':server.server_port,
                'pid':os.getpid(), 'demo':dock.demo,
                'data_fingerprint':service_fingerprint(dock.data)}
    try:
        atomic_json(dock.data/'server.json', metadata)
    except (OSError, ValueError):
        server.server_close()
        instance_lock.close()
        raise
    print('PSP Dock: ' + url, flush=True)
    if open_on_connect:
        dock.on_connected = lambda: launch_xmb_window(url,app_window=app_window)
    threading.Thread(target=dock.watch, daemon=True).start()
    if open_browser:
        launch_xmb_window(url,app_window=app_window)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        dock.stop.set()
        server.server_close()
        try:
            record=read_json(dock.data/'server.json',{})
            if record == metadata:
                (dock.data/'server.json').unlink(missing_ok=True)
        except (OSError, ValueError, TypeError):
            pass
        instance_lock.close()

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='PSP Dock · prototipo local para Windows')
    parser.add_argument('--demo',action='store_true')
    parser.add_argument('--no-browser',action='store_true')
    parser.add_argument('--app-window',action='store_true',
                        help='Abre la interfaz XMB en una ventana fullscreen sin barra de navegador')
    parser.add_argument('--open-on-connect',action='store_true',
                        help='Abre el XMB al detectar una nueva PSP USBSTOR; requiere servicio ya iniciado')
    parser.add_argument('--recover-session',action='store_true',help='Con Dock y PPSSPP cerrados, desbloquea una sesión interrumpida sin borrar la devolución pendiente')
    parser.add_argument('--port',type=int,default=0,help='0 elige automáticamente un puerto libre')
    parser.add_argument('--data',type=Path)
    args = parser.parse_args()
    try:
        data = args.data or default_data_path(
            os.environ.get('LOCALAPPDATA',str(ROOT)), demo=args.demo
        )
    except ValueError as exc:
        parser.error(str(exc))
    if args.recover_session:
        recover_session(data)
    elif args.demo:
        make_demo(data)
        serve(Dock(data,args.demo),args.port,not args.no_browser,args.open_on_connect,
              args.app_window)
    else:
        serve(Dock(data,args.demo),args.port,not args.no_browser,args.open_on_connect,
              args.app_window)
