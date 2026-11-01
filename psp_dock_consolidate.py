"""One-time Sony Memory Stick cleanup with TWO verified PC backups.

Only the PSP Dock lab EBOOT folders and observer plugins installed by this
project are in the explicit allowlist. Games, emulator homebrews, ARK,
SAVEDATA and flash remain untouched. Run --preview by default; --apply
requires an exact Sony USB device and volume serial, no live Dock/PPSSPP,
and refuses any unexpected file in a candidate lab folder.

Order of mutations after verification:
  1) critical saves/ARK/plugins PC backup, verified;
  2) additional FULL PC backup of all old PSP Dock lab files, verified;
  3) install ONE new PSP Dock app in an independent folder, verify hashes;
  4) remove just PSP Dock entries from PLUGINS.TXT, verify;
  5) unlink only hashed allowlisted lab files, rmdir their empty dirs;
  6) verify product, plugin configuration, Sony identity and SAVEDATA hashes.
No firmware flash, SCSI, Windows eject, USB role switching, or save writes.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
from types import SimpleNamespace
import uuid

from dock import ROOT, atomic_json, windows_devices
from lab_eject import real_dock_open, ppsspp_open
from psp_backup import (
    DEFAULT_DEST, _reparse, _safe_relative, backup as critical_backup,
    copy_verified, verify_archive,
)
from psp_usb import identify, _volume_serial, _usb_parent


class CleanupError(ValueError):
    pass


LABS = {
    'PSPDockProbe': ('EBOOT.PBP','LICENSE-PSPSDK.txt','README.md'),
    'PSPDockScreenSafe': ('EBOOT.PBP',),
    'PSPDockUsbSteps': ('EBOOT.PBP',),
    'PSPDockUsbGate': ('EBOOT.PBP','usbstate.prx'),
    'PSPDockUsbGate06': ('EBOOT.PBP','usbstate.prx'),
    'PSPDockUsbStatus07': ('EBOOT.PBP','usbstate.prx'),
    'PSPDockBusCycle08': ('EBOOT.PBP','usbstate.prx'),
    'PSPDockK1Check': ('EBOOT.PBP',),
    'PSPDockHidOnly': ('EBOOT.PBP','hidonlycore.prx','bridgehid.prx','LICENSE.txt'),
}
PLUGINS = {
    'PSPDockProbe': ('pspdockvsh.prx','usbstate.prx','BOOT.TXT'),
    'PSPDockKernelK1': ('pspdockk1observer.prx',),
    'PSPDockK1Proof': ('pspdockk1proof.prx','K1PROOF.TXT'),
}
PRODUCT = ('EBOOT.PBP','dockcore.prx','bridgehid.prx',
           'LICENSE-FuSa-GPL-3.0.txt')
OLD_REFERENCES = (
    'ms0:/SEPLUGINS/PSPDockProbe/pspdockvsh.prx',
    'ms0:/SEPLUGINS/PSPDockKernelK1/pspdockk1observer.prx',
    'ms0:/SEPLUGINS/PSPDockK1Proof/pspdockk1proof.prx',
)


def sha256(path: Path) -> str:
    with Path(path).open('rb') as reader:
        return hashlib.file_digest(reader,'sha256').hexdigest()


def same_sony_without_slow_cim(identity) -> bool:
    """Recheck physical Sony parent and FAT serial without repeated 30s CIM.

    Only initial identify() associates G: with the exact Sony PSP physical
    disk. Subsequent checks use Win32 drive class, its FAT volume serial
    and the *live* config-manager USB ancestor for that already bound
    physical disk, all read-only. No PnP eject, media control or writes.
    """
    try:
        import ctypes as C
        from ctypes import wintypes as W
        kernel=C.WinDLL('kernel32',use_last_error=True)
        kernel.GetDriveTypeW.argtypes=[W.LPCWSTR]
        kernel.GetDriveTypeW.restype=W.UINT
        if kernel.GetDriveTypeW(identity.drive)!=2:
            return False
        if _volume_serial(identity.drive)!=identity.volume_serial:
            return False
        if not (Path(identity.drive)/'PSP/GAME').is_dir():
            return False
        _node,usb_parent=_usb_parent(identity.disk_id)
        return usb_parent.upper()==identity.usb_device_id.upper()
    except (OSError,ValueError,RuntimeError):
        return False


def clean_plugin_config(original: bytes) -> bytes:
    try:
        text=original.decode('utf-8-sig')
    except UnicodeDecodeError as exc:
        raise CleanupError('PLUGINS.TXT no usa texto UTF-8 reconocido.') from exc
    new=[]
    for line in text.splitlines(keepends=True):
        stripped=line.strip()
        if 'pspdock' in stripped.casefold():
            if not re.fullmatch(
                r'vsh,\s*(?:ms0:/)?(?:SEPLUGINS/)?'
                r'(?:PSPDockProbe/pspdockvsh\.prx|'
                r'PSPDockKernelK1/pspdockk1observer\.prx|'
                r'PSPDockK1Proof/pspdockk1proof\.prx),\s*(?:on|off)',
                stripped,re.IGNORECASE,
            ):
                raise CleanupError('Entrada PSP Dock desconocida en PLUGINS.TXT: '+stripped)
            continue
        new.append(line)
    return ''.join(new).encode('utf-8')


def _strict_directory(root: Path, relative: str,
                      expected_names: tuple[str, ...]) -> list[Path]:
    target=root.joinpath(*relative.split('/'))
    if not target.is_dir() or _reparse(target):
        raise CleanupError('Falta carpeta original o es insegura: '+relative)
    entries=sorted(target.iterdir(),key=lambda p:p.name.casefold())
    if [p.name for p in entries]!=sorted(expected_names,key=str.casefold):
        raise CleanupError('La carpeta contiene archivos no previstos; no borrar: '+relative+
                           ' -> '+repr([p.name for p in entries]))
    if any(not p.is_file() or _reparse(p) for p in entries):
        raise CleanupError('Archivo irregular o enlace en carpeta: '+relative)
    return entries


def inspect(root: Path, product_src: Path) -> dict:
    """Read-only hard allowlist; abort on ALL unexpected lab contents."""
    root=Path(root)
    config=root/'seplugins/PLUGINS.TXT'
    if not config.is_file() or _reparse(config):
        raise CleanupError('No se encontró PLUGINS.TXT regular original.')
    original=config.read_bytes()
    cleaned=clean_plugin_config(original)
    if cleaned==original:
        raise CleanupError('No hay entradas antiguas de PSP Dock para retirar.')
    if (root/'PSP/GAME/PSPDock').exists():
        raise CleanupError('Ya existe PSP Dock; no sobrescribir.')
    if not (root/'PSP/SAVEDATA').is_dir() or not (root/'PSP/SYSTEM').is_dir():
        raise CleanupError('No se reconoce la PSP y sus guardados originales.')
    candidates=[]
    for name, expected in LABS.items():
        folder='PSP/GAME/'+name
        candidates.extend(_strict_directory(root,folder,expected))
    for name, expected in PLUGINS.items():
        folder='seplugins/'+name
        candidates.extend(_strict_directory(root,folder,expected))
    candidates.append(config)
    sources={}
    for name in PRODUCT:
        src=Path(product_src)/name
        if not src.is_file() or _reparse(src) or src.stat().st_size<=0:
            raise CleanupError('Producto PSP Dock no compilado/verificado: '+name)
        sources[name]={'source':src,'sha256':sha256(src),
                       'size':src.stat().st_size}
    # Physical HID bridge must be byte-for-byte identical to the validated
    # FuSa derivative that previously controlled PPSSPP correctly.
    if sources['bridgehid.prx']['sha256'] != (
        '8f702c70b8464101cc24c5661b0cff7ef327683b0896f094b42837296d2a548e'
    ):
        raise CleanupError('El driver HID difiere del validado físicamente.')
    files={p.relative_to(root).as_posix(): {'size':p.stat().st_size,
                                            'sha256':sha256(p)}
           for p in candidates}
    return {'files':files,'sources':sources,'original_config':original,
            'clean_config':cleaned,
            'legacy_dirs':['PSP/GAME/'+n for n in LABS]+
                          ['seplugins/'+n for n in PLUGINS]}


def backup_owned(root:Path, snapshot:dict, backup_root:Path, identity,
                 critical_report:dict) -> Path:
    """Verified standalone PC archive for all lab folders and plugin config."""
    backup_root=Path(backup_root)
    backup_root.mkdir(parents=True,exist_ok=True)
    expected_size=sum(x['size'] for x in snapshot['files'].values())
    if shutil.disk_usage(backup_root).free < expected_size+8*1024*1024:
        raise CleanupError('No hay espacio libre para respaldar laboratorios.')
    stamp=dt.datetime.now().strftime('%Y%m%d-%H%M%S')
    destination=backup_root/('cleanup-pspdock-'+stamp+'-'+secrets.token_hex(4))
    destination.mkdir(exist_ok=False)
    incomplete=destination/'BACKUP-INCOMPLETE.txt'
    incomplete.write_text('No restaurar hasta completar verificación SHA256.\n')
    record={
        'schema':'pspdock-cleanup-1','status':'incomplete',
        'created_utc':dt.datetime.now(dt.timezone.utc).isoformat(),
        'psp_volume_serial':identity.volume_serial,
        'psp_usb_parent':identity.usb_device_id,
        'critical_backup':critical_report['directory'],
        'files':snapshot['files'],
        'product_sources':{k:{'size':v['size'],'sha256':v['sha256']}
                           for k,v in snapshot['sources'].items()},
    }
    for rel,item in sorted(snapshot['files'].items()):
        # Never follow ".." or an unexpected path from disk/manifests.
        if _safe_relative(rel)!=rel:
            raise CleanupError('Ruta de backup insegura.')
        original=root/Path(rel)
        copied=destination/'data'/Path(rel)
        result=copy_verified(original,copied,item['size'])
        if result['sha256']!=item['sha256']:
            raise CleanupError('Backup no coincide con huella original: '+rel)
    record['status']='verified'
    atomic_json(destination/'manifest.json',record)
    incomplete.unlink()
    verify_cleanup_backup(destination)
    return destination


def verify_cleanup_backup(folder:Path) -> dict:
    folder=Path(folder)
    if (folder/'BACKUP-INCOMPLETE.txt').exists():
        raise CleanupError('Backup de laboratorios incompleto.')
    manifest=json.loads((folder/'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('schema')!='pspdock-cleanup-1' or manifest.get('status')!='verified':
        raise CleanupError('Manifiesto de laboratorio no verificado.')
    total=0
    for rel,item in manifest['files'].items():
        p=folder/'data'/Path(rel)
        if _safe_relative(rel)!=rel or not p.is_file() or _reparse(p):
            raise CleanupError('Falta archivo en backup: '+rel)
        if p.stat().st_size!=item['size'] or sha256(p)!=item['sha256']:
            raise CleanupError('Hash de backup incorrecto: '+rel)
        total+=item['size']
    return {'files':len(manifest['files']),'bytes':total,
            'status':'verified'}


def _same_source(root:Path, snapshot:dict) -> None:
    for rel,expected in snapshot['files'].items():
        source=root/Path(rel)
        if (not source.is_file() or _reparse(source) or
            source.stat().st_size!=expected['size'] or
            sha256(source)!=expected['sha256']):
            raise CleanupError('PSP cambió durante la preparación: '+rel)


def consolidate(root:Path, product_src:Path, backup_root:Path, identity,
                verify_identity) -> dict:
    """Destructive action ONLY after all backups; refuses unsafe mutations."""
    root=Path(root)
    if not verify_identity():
        raise CleanupError('PSP Sony cambió de identidad.')
    snapshot=inspect(root,product_src)
    before_saves={p.relative_to(root).as_posix():sha256(p)
                  for p in (root/'PSP/SAVEDATA').rglob('*') if p.is_file()}
    critical=critical_backup(
        root,backup_root,'critical',identity.volume_serial,
        identity.usb_device_id,verify_identity=verify_identity,
    )
    verify_archive(Path(critical['directory']))
    legacy_archive=backup_owned(root,snapshot,backup_root,identity,critical)
    if not verify_identity():
        raise CleanupError('Sony USB cambió tras respaldo. No escribir.')
    _same_source(root,snapshot)
    installed=root/'PSP/GAME/PSPDock'
    staged=root/'PSP/GAME'/('.pspdock-product-'+uuid.uuid4().hex)
    staged.mkdir(exist_ok=False)
    try:
        for name,item in snapshot['sources'].items():
            destination=staged/name
            with item['source'].open('rb') as inp, destination.open('xb') as out:
                shutil.copyfileobj(inp,out)
                out.flush()
                os.fsync(out.fileno())
            if sha256(destination)!=item['sha256']:
                raise CleanupError('La copia del cliente permanente falló: '+name)
        if not verify_identity():
            raise CleanupError('La PSP Sony cambió al instalar.')
        os.rename(staged,installed)
    finally:
        if staged.exists():
            # Only files WE just created inside the unpredictable stage.
            for p in staged.iterdir():
                if p.name in snapshot['sources'] and p.is_file() and not _reparse(p):
                    p.unlink()
            staged.rmdir()
    # All new product files MUST already be correct before touching plugins.
    for name,item in snapshot['sources'].items():
        if sha256(installed/name)!=item['sha256']:
            raise CleanupError('Instalación no verificable: '+name)
    config=root/'seplugins/PLUGINS.TXT'
    if config.read_bytes()!=snapshot['original_config']:
        raise CleanupError('Configuración PSP cambió; se conserva backup y producto.')
    tmp=config.with_name('.pspdock-clean-'+uuid.uuid4().hex+'.tmp')
    try:
        with tmp.open('xb') as output:
            output.write(snapshot['clean_config'])
            output.flush()
            os.fsync(output.fileno())
        os.replace(tmp,config)
    finally:
        tmp.unlink(missing_ok=True)
    if config.read_bytes()!=snapshot['clean_config']:
        raise CleanupError('No se pudo desactivar observadores VSH/K1.')
    # Plugin config no longer references any code that is about to go away.
    # Remove only exact, previously hashed files; NEVER recursive rmtree.
    deleted=0
    for folder in snapshot['legacy_dirs']:
        directory=root/Path(folder)
        for file in sorted(directory.iterdir()):
            rel=file.relative_to(root).as_posix()
            expected=snapshot['files'].get(rel)
            if (expected is None or not file.is_file() or _reparse(file) or
                file.stat().st_size!=expected['size'] or
                sha256(file)!=expected['sha256']):
                raise CleanupError('Archivo cambiado; detener limpieza: '+rel)
            file.unlink()
            deleted+=1
        directory.rmdir()
    after_saves={p.relative_to(root).as_posix():sha256(p)
                 for p in (root/'PSP/SAVEDATA').rglob('*') if p.is_file()}
    if after_saves!=before_saves:
        raise CleanupError('SAVEDATA difiere tras limpiar; detener y revisar backup.')
    if not verify_identity():
        raise CleanupError('La PSP cambió durante limpieza; no afirmar éxito.')
    for name,item in snapshot['sources'].items():
        if sha256(installed/name)!=item['sha256']:
            raise CleanupError('El producto difiere tras limpiar: '+name)
    result={
        'status':'completed','new_app':'PSP/GAME/PSPDock',
        'product_files':len(snapshot['sources']),
        'removed_lab_folders':len(snapshot['legacy_dirs']),
        'removed_lab_files':deleted,
        'critical_backup':critical['directory'],
        'legacy_backup':str(legacy_archive),
        'savedata_files_verified_unchanged':len(after_saves),
        'plugins_txt_entries_removed':len(snapshot['original_config'].splitlines())-
                                      len(snapshot['clean_config'].splitlines()),
        'firmware_flash_modified':False,
        'roms_modified':False,
        'usb_role_switched':False,
    }
    atomic_json(Path(legacy_archive)/'cleanup-result.json',result)
    return result


def main() -> int:
    cli=argparse.ArgumentParser(description='Reemplazar PSP Dock LAB por cliente sin temporizador')
    cli.add_argument('--apply',action='store_true')
    cli.add_argument('--serial',default=None,
                     help='Exige escribir exactamente el serial Sony PSP leído antes')
    args=cli.parse_args()
    try:
        from types import SimpleNamespace
        devices=windows_devices()
        if len(devices)!=1:
            raise CleanupError('Se necesita exactamente UNA PSP Sony USBSTOR.')
        root=Path(devices[0]['root'])
        identity,_=identify(str(root))
        if (identity.usb_device_id.upper() !=
            'USB\\VID_054C&PID_02D2\\8A67FB08B1B4B5A6'.upper()):
            raise CleanupError('Sony USB parent distinto del PSP verificado.')
        sources=ROOT/'homebrew/psp-dock-app/dist'
        snapshot=inspect(root,sources)
        print(json.dumps({
            'psp':str(root),'serial':identity.volume_serial,
            'labs_to_remove':9,'observer_plugins_to_remove':3,
            'files_to_backup':len(snapshot['files']),
            'bytes_to_backup':sum(e['size'] for e in snapshot['files'].values()),
            'product_bytes':sum(e['size'] for e in snapshot['sources'].values()),
            'new_app':'PSP/GAME/PSPDock','flash_or_savedata_writes':False,
        },ensure_ascii=False,indent=2),flush=True)
        if not args.apply:
            print('VISTA PREVIA: no se escribió nada en la PSP.')
            return 0
        if args.serial != identity.volume_serial:
            raise CleanupError('Para aplicar, especifica --serial igual al Sony leído.')
        if ppsspp_open() or real_dock_open():
            raise CleanupError('Cierra PPSSPP y el servicio PSP Dock LIVE antes de modificar.')
        def still_same():
            return same_sony_without_slow_cim(identity)
        result=consolidate(root,sources,DEFAULT_DEST,identity,still_same)
        print('PSP LIMPIA:',json.dumps(result,ensure_ascii=False,indent=2))
        return 0
    except (CleanupError,OSError,ValueError) as exc:
        print('LIMPIEZA CANCELADA O INCOMPLETA:',exc)
        print('Si existe un backup de limpieza, conservarlo. No reintentar a ciegas.')
        return 1


if __name__=='__main__':
    raise SystemExit(main())
