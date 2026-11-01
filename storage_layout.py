"""Safe PC-only relocation of PSP Dock's large game/saves/texture/media data.

Settings and session identity remain in AppData, but the user can put
synced ISOs, private PPSSPP memstick/SAVEDATA/TEXTURES, downloaded texture
packs and original XMB media on any other fixed Windows disk.

Never move a mounted PSP volume, never overwrite an existing directory,
never delete the originals after migration, and verify every copied
file's SHA-256 before changing Dock's configuration.
"""
from __future__ import annotations

import ctypes
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import uuid


class StorageLocationError(ValueError):
    pass


CONTENT_DIRS = ('artwork','xmb-assets','profile-backups','backups','hd-iso-candidates')


def choose_pc_storage_parent(*, run=subprocess.run)->str | None:
    """Show the native Windows folder picker after an explicit UI click.

    The dialog returns a parent directory, and PSP Dock proposes a NEW
    child folder within it. No filesystem mutation is performed here.
    """
    if os.name != 'nt':
        raise StorageLocationError('El selector nativo requiere Windows.')
    command=(
        'Add-Type -AssemblyName System.Windows.Forms; '
        '$dialog=New-Object System.Windows.Forms.FolderBrowserDialog; '
        '$dialog.Description="Carpeta principal para los juegos y partidas PSP Dock"; '
        '$dialog.ShowNewFolderButton=$true; '
        '$dialog.RootFolder=[System.Environment+SpecialFolder]::MyComputer; '
        'if($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) '
        '{ Write-Output ("PSPDOCK-FOLDER:"+$dialog.SelectedPath) }; '
        '$dialog.Dispose()'
    )
    try:
        result=run(
            ['powershell.exe','-NoProfile','-STA','-Command',command],
            capture_output=True,text=True,timeout=150,
            creationflags=0x08000000,
        )
    except (OSError,subprocess.SubprocessError) as exc:
        raise StorageLocationError('No se pudo abrir el selector de Windows.') from exc
    if result.returncode!=0:
        raise StorageLocationError('El selector de carpetas falló en Windows.')
    values=[line[len('PSPDOCK-FOLDER:'):].strip()
            for line in result.stdout.splitlines()
            if line.startswith('PSPDOCK-FOLDER:')]
    if not values:
        return None
    if len(values)!=1:
        raise StorageLocationError('Windows devolvió más de una carpeta.')
    chosen=fixed_local_folder(Path(values[0]))
    proposed=chosen/'PSP Dock'
    if proposed.exists():
        raise StorageLocationError(
            'Ya hay una carpeta PSP Dock en este directorio. Elige otra ubicación nueva.')
    return str(proposed)


def fixed_local_folder(path: Path) -> Path:
    p=Path(path)
    if not p.is_absolute() or not str(p).strip() or str(p).startswith('\\\\'):
        raise StorageLocationError('Selecciona una ruta absoluta en un disco local.')
    p=p.resolve()
    if str(p)==p.anchor or len(p.parts)<2:
        raise StorageLocationError('Selecciona una carpeta, no la raíz del disco.')
    if os.name=='nt':
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.GetDriveTypeW.argtypes=[ctypes.c_wchar_p]
        kernel.GetDriveTypeW.restype=ctypes.c_uint
        # DRIVE_FIXED=3; no removable USB, network, CD or unknown devices.
        if kernel.GetDriveTypeW(p.anchor)!=3:
            raise StorageLocationError('Se requiere un disco fijo local, no la PSP.')
    for parent in (p, *p.parents):
        if parent.exists() and parent.is_symlink():
            raise StorageLocationError('No se admiten carpetas enlazadas.')
    return p


def file_manifest(sources: dict[str,Path]) -> dict:
    entries={}
    for subdir, source in sources.items():
        if not source.exists():
            continue
        if not source.is_dir() or source.is_symlink():
            raise StorageLocationError('Origen inválido para migración: '+subdir)
        for f in source.rglob('*'):
            if f.is_symlink():
                raise StorageLocationError('Los enlaces requieren revisión manual.')
            if not f.is_file():
                continue
            rel=f.relative_to(source)
            if len(rel.parts)>25 or len(entries)>150000:
                raise StorageLocationError('Demasiados elementos para una migración segura.')
            entries[(Path(subdir)/rel).as_posix()]=f
    return entries


def migrate_pc_data(*, target:Path, library:Path, memstick:Path,
                    old_data:Path) -> dict:
    """Copy into a NEW empty root; leave source untouched for rollback."""
    root=fixed_local_folder(target)
    old_data=Path(old_data).resolve()
    library=Path(library).resolve()
    memstick=Path(memstick).resolve()
    if (root == old_data or root.is_relative_to(old_data) or
            root.is_relative_to(library) or root.is_relative_to(memstick)
            or old_data.is_relative_to(root) or library.is_relative_to(root)
            or memstick.is_relative_to(root)):
        raise StorageLocationError('La nueva carpeta no puede solaparse con los datos actuales.')
    if root.exists():
        raise StorageLocationError('El destino debe ser una carpeta nueva, todavía inexistente.')
    sources={'library':library,'memstick':memstick}
    for name in CONTENT_DIRS:
        sources[name]=old_data/name
    files=file_manifest(sources)
    total=sum(f.stat().st_size for f in files.values())
    root.parent.mkdir(parents=True,exist_ok=True)
    if shutil.disk_usage(root.parent).free < total+64*1024*1024:
        raise StorageLocationError('Espacio insuficiente para copiar y verificar.')
    stage=root.parent/('.pspdock-move-'+uuid.uuid4().hex)
    stage.mkdir(exist_ok=False)
    try:
        for rel,src in files.items():
            dest=stage/Path(rel)
            dest.parent.mkdir(parents=True,exist_ok=True)
            before=hashlib.sha256()
            after=hashlib.sha256()
            size=0
            # Hash source and destination in the same copy operation;
            # fail instead of treating an interrupted copy as complete.
            with src.open('rb') as inp, dest.open('xb') as out:
                while True:
                    chunk=inp.read(1024*1024)
                    if not chunk:
                        break
                    size+=len(chunk)
                    before.update(chunk)
                    out.write(chunk)
                out.flush()
                os.fsync(out.fileno())
            if size!=src.stat().st_size:
                raise StorageLocationError('Archivo de origen cambiado: '+rel)
            with dest.open('rb') as check:
                while True:
                    chunk=check.read(1024*1024)
                    if not chunk:
                        break
                    after.update(chunk)
            if before.digest()!=after.digest():
                raise StorageLocationError('El destino no coincide: '+rel)
        if root.exists():
            raise StorageLocationError('El destino apareció durante la migración.')
        os.rename(stage,root)
    except Exception:
        # Preserve the incomplete stage rather than deleting data that may
        # be the sole copy following an interruption or user-initiated move.
        raise
    return {'path':str(root),'files':len(files),'bytes':total,
            'library':str(root/'library'),
            'memstick':str(root/'memstick'),
            'old_files_preserved':True}
