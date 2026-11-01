"""Conservative, opt-in SRAM continuity across PSP homebrew and RetroArch.

Only raw battery-backed saves with known extension, unambiguous ROM name and
plausible size are eligible. Savestates and unknown emulator formats NEVER
participate. Plans are read-only and conflict decisions require a fresh
snapshot token and an explicit user choice. All writes keep a PC SHA backup
and a durable journal; an interrupted operation fails closed.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import uuid
from collections import Counter

from retro_runtime import private_save_paths


MAX_SRAM = 128 * 1024
MAX_SLOTS = 2048
# Raw cartridge SRAM formats with a viable source-format mapping. This is NOT
# a claim about emulator-specific .state files or savegame database formats.
RAW_PROFILES = {
    ('gpSP Kai','gba'): ('.sav', 'gba'),
    ('TempGBA4PSP','gba'): ('.sav', 'gba'),
    ('Snes9xTYL','snes'): ('.srm', 'snes'),
    ('Snes9x Euphoria','snes'): ('.srm', 'snes'),
}
SAVE_DIRS = frozenset({'save','saves','saveram','sram'})
GBA_SIZES = frozenset({512, 8*1024, 32*1024, 64*1024, 128*1024})
SNES_SIZES = frozenset(2**i for i in range(6,18))  # 64..131072 bytes


def _safe(root: Path, path: Path) -> Path:
    root, path = Path(root).absolute(), Path(path).absolute()
    if not path.is_relative_to(root):
        raise ValueError('Guardado retro fuera de la carpeta permitida.')
    cursor = path
    while True:
        if cursor.is_symlink() or (hasattr(cursor,'is_junction') and cursor.is_junction()):
            raise ValueError('No se aceptan enlaces ni junctions para SRAM.')
        if cursor == root:
            break
        cursor = cursor.parent
    return path


def _sha(path: Path) -> str | None:
    if not path.is_file():
        return None
    size = path.stat().st_size
    if size < 1 or size > MAX_SRAM:
        return 'invalid-size'
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda:f.read(64*1024),b''):
            h.update(block)
    return h.hexdigest()


def _plausible(system: str, path: Path | None) -> bool:
    if path is None or not path.exists():
        return True
    if not path.is_file() or path.is_symlink():
        return False
    size = path.stat().st_size
    return size in (GBA_SIZES if system == 'gba' else SNES_SIZES)


def _save_index(root: Path, emulator: dict, roms: list[dict]) -> dict | None:
    """One bounded enumeration PER emulator, not per ROM over slow PSP FAT."""
    base = _safe(root,root/Path(emulator['relative']).parent)
    parents = {_safe(root,root/item['relative']).parent for item in roms}
    indexed = {}
    count = 0
    for folder, dirs, files in os.walk(base,followlinks=False):
        here = _safe(root,Path(folder))
        depth = len(here.relative_to(base).parts)
        if depth >= 4:
            dirs[:] = []
        else:
            dirs[:] = [name for name in dirs if not (here/name).is_symlink() and
                       not (hasattr(here/name,'is_junction') and (here/name).is_junction())]
        count += len(files)+len(dirs)
        if count > MAX_SLOTS:
            return None
        eligible = (here in parents or any(part.casefold() in SAVE_DIRS
                    for part in here.relative_to(base).parts))
        if not eligible:
            continue
        for name in files:
            file = here/name
            suffix = file.suffix.casefold()
            if suffix in ('.sav','.srm'):
                indexed.setdefault((file.stem.casefold(),suffix),[]).append(_safe(root,file))
    return indexed


def _source(root: Path, emulator: dict, rom: dict, suffix: str,
            indexed:dict | None) -> tuple[Path | None,str]:
    """Match ONE PSP SRAM by exact ROM stem; do not scan arbitrary files.

    Only SAVES/SAVE/SRAM folders within the PSP homebrew, or ROM sidecars,
    are considered. Missing source gets a conservative *suggested* new slot
    in SAVES; creating it requires a distinct explicit approval.
    """
    base = _safe(root,root/Path(emulator['relative']).parent)
    rom_file = _safe(root,root/rom['relative'])
    if not base.is_dir() or not rom_file.is_relative_to(base):
        return None,'invalid_root'
    stem = rom_file.stem.casefold()
    if indexed is None:
        return None,'too_many_files'
    matches = indexed.get((stem,suffix),[])
    if len(matches)>1:
        return None,'ambiguous'
    if len(matches)==1:
        return matches[0],'existing'
    return _safe(root,base/'SAVES'/(rom_file.stem+suffix)),'suggested'


def _baseline_key(volume: str, rom_id: str) -> str:
    return hashlib.sha256((volume+'\n'+rom_id).encode('utf-8')).hexdigest()


def _revision(volume: str, rom_id: str, psp_file: Path | None,
              psp_sha: str | None, pc_sha: str | None, baseline: str | None) -> str:
    value = [volume,rom_id,str(psp_file) if psp_file else '',psp_sha,pc_sha,baseline]
    return hashlib.sha256(json.dumps(value).encode('utf-8')).hexdigest()


def plan_retro_saves(*, root: Path, storage_root: Path, volume: str,
                     roms: list[dict], emulators: list[dict],
                     baselines: dict) -> list[dict]:
    """Small, bounded read-only plan, suitable for XMB status presentation."""
    root = Path(root).absolute()
    index = {e['id']:e for e in emulators}
    by_emulator = {}
    for rom in roms:
        by_emulator.setdefault(rom['emulator_id'],[]).append(rom)
    locations = {key:_save_index(root,emulator,group)
                 for key,group in by_emulator.items()
                 if (emulator:=index.get(key)) and
                 any((emulator['name'],rom['system']) in RAW_PROFILES for rom in group)}
    repeated = {key:Counter(Path(rom['relative']).stem.casefold()
                       for rom in group) for key,group in by_emulator.items()}
    rows = []
    for rom in roms:
        emulator = index.get(rom.get('emulator_id'))
        key = (emulator.get('name') if emulator else None,rom.get('system'))
        profile = RAW_PROFILES.get(key)
        row = {'rom_id':rom['id'],'title':rom['title'],
               'system':rom['system'],'emulator':key[0],
               'status':'unsupported','reason':'El formato SRAM de este emulador PSP no está verificado.',
               'source_relative':None,'suggested_path':False,'revision':None}
        if not profile or rom.get('format')=='ZIP':
            if rom.get('format')=='ZIP':
                row['reason']='La SRAM de ROMs ZIP necesita confirmar el nombre interno.'
            rows.append(row)
            continue
        if repeated.get(rom['emulator_id'],{}).get(Path(rom['relative']).stem.casefold(),0)>1:
            row['status']='blocked'
            row['reason']='Dos ROMs del mismo emulador comparten nombre de SRAM; no se adivina cuál es cuál.'
            rows.append(row)
            continue
        suffix, kind = profile
        source, origin = _source(root,emulator,rom,suffix,locations[rom['emulator_id']])
        if source is None:
            row['status'] = 'blocked'
            row['reason'] = 'Varias SRAM con el mismo nombre o ruta PSP no reconocida.'
            rows.append(row)
            continue
        row['source_relative'] = source.relative_to(root).as_posix()
        row['suggested_path'] = origin=='suggested'
        save, _ = private_save_paths(storage_root,volume,rom['id'])
        _safe(storage_root,save)
        if not _plausible(kind,source) or not _plausible(kind,save):
            row['status']='blocked'
            row['reason']='SRAM fuera de los tamaños seguros; podría tener cabecera o compresión.'
            rows.append(row)
            continue
        psp_sha, pc_sha = _sha(source),_sha(save)
        baseline = baselines.get(_baseline_key(volume,rom['id']))
        if not isinstance(baseline,str) or len(baseline)!=64:
            baseline=None
        if psp_sha is None and pc_sha is None:
            status='none'
        elif psp_sha == pc_sha:
            status='identical'
        elif psp_sha is None:
            status='pc_to_psp'
        elif pc_sha is None:
            status='psp_to_pc'
        elif baseline and baseline == psp_sha:
            status='pc_to_psp'
        elif baseline and baseline == pc_sha:
            status='psp_to_pc'
        else:
            status='conflict'
        row.update(status=status,reason='',
                   revision=_revision(volume,rom['id'],source,psp_sha,pc_sha,baseline),
                   psp_present=psp_sha is not None,pc_present=pc_sha is not None,
                   psp_signature=psp_sha,pc_signature=pc_sha)
        rows.append(row)
    return rows


def copy_sram_verified(*, source:Path, target:Path, source_root:Path,
                       target_root:Path, storage_root:Path,
                       backup_dir:Path, journal_dir:Path,
                       expected_source:str, expected_target:str | None,
                       system:str) -> str:
    """One user-approved, journaled copy; backup target on PC before mutation.

    A crash after creating the durable journal blocks any new changes until
    the user inspects journal+backup. The PSP never receives PC savestates.
    """
    source = _safe(source_root,source)
    target = _safe(target_root,target)
    backup_dir = _safe(storage_root,backup_dir)
    journal_dir = _safe(storage_root,journal_dir)
    if not _plausible(system,source) or not _plausible(system,target):
        raise ValueError('SRAM incompatible o dañada: no se sincroniza.')
    if _sha(source) != expected_source or _sha(target) != expected_target:
        raise ValueError('La SRAM cambió antes de copiar; vuelve a compararla.')
    if list(journal_dir.glob('*.json')):
        raise ValueError('Hay una sincronización retro interrumpida. Revisa su journal y backup.')
    target.parent.mkdir(parents=True,exist_ok=True)
    backup_dir.mkdir(parents=True,exist_ok=True)
    journal_dir.mkdir(parents=True,exist_ok=True)
    if list(journal_dir.glob('*.json')):
        raise ValueError('Hay un journal retro de otra operación. Revisa antes de modificar guardados.')
    token = uuid.uuid4().hex
    stage = _safe(target_root,target.with_name('.dock-retro-stage-'+token))
    backup = _safe(backup_dir,backup_dir/(token+'.bak'))
    journal = _safe(journal_dir,journal_dir/(token+'.json'))
    try:
        with source.open('rb') as read, stage.open('xb') as out:
            while True:
                data=read.read(64*1024)
                if not data: break
                out.write(data)
            out.flush()
            os.fsync(out.fileno())
        if _sha(stage)!=expected_source or _sha(source)!=expected_source:
            raise ValueError('La fuente cambió durante la copia de SRAM.')
        if expected_target is not None:
            shutil.copy2(target,backup)
            if _sha(backup)!=expected_target:
                raise ValueError('No se pudo respaldar la SRAM anterior.')
        if _sha(target)!=expected_target:
            raise ValueError('La SRAM de destino cambió durante el respaldo.')
        payload={'source':str(source),'target':str(target),'backup':str(backup) if expected_target else None,
                 'stage':str(stage),'expected_source':expected_source,
                 'expected_target':expected_target}
        with journal.open('x',encoding='utf-8') as record:
            json.dump(payload,record)
            record.flush()
            os.fsync(record.fileno())
        if _sha(source)!=expected_source or _sha(target)!=expected_target:
            raise ValueError('Los archivos cambiaron; revisar el journal antes de repetir.')
        os.replace(stage,target)
        if _sha(target)!=expected_source:
            raise ValueError('No se pudo verificar la nueva SRAM. Conserva el backup.')
        journal.unlink()
        return expected_source
    finally:
        # Never delete journal after publication on failure; its presence
        # deliberately blocks retries until manual inspection/recovery.
        if not journal.exists() and stage.exists():
            stage.unlink()
