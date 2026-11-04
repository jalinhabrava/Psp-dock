"""Install ONLY recognized PSP homebrew equivalents in the user's PC folder.

Portable RetroArch comes from its official, pinned stable release. Libretro
cores are downloaded separately and only for the supported PSP emulator
families currently inventoried. Installation never reads PSP ROMs, copies
BIOS, executes downloaded payloads, elevates, or changes global RetroArch.

The installer's selected `storage_root` is authoritative:
    <storage_root>/emulators/retroarch/retroarch.exe

No .exe installer exists yet: its future onboarding step should persist
storage_root and retro_install_policy='auto'; existing users can opt in from
XMB settings without losing their selected content storage location.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import urllib.parse
import urllib.request
import uuid
import zipfile

from psp_dock.emulation.psp_retro import SYSTEMS
from psp_dock.core.storage_layout import fixed_local_folder


STABLE_VERSION = '1.22.2'
STABLE_URL = f'https://buildbot.libretro.com/stable/{STABLE_VERSION}/windows/x86_64/RetroArch.7z'
# Independent package manifests agree on this SHA-256 (Chocolatey and Scoop).
# Never execute an unpinned RetroArch executable from a mutable URL.
STABLE_SHA256 = 'b2139b1d0f9d4526dc6b5ce23cbb3efdc766096fa6f2c3df016818b486ac6372'
CORE_BASE = 'https://buildbot.libretro.com/nightly/windows/x86_64/latest/'
ARCHIVE_LIMIT = 256 * 1024 * 1024
CORE_ZIP_LIMIT = 160 * 1024 * 1024
CORE_DLL_LIMIT = 260 * 1024 * 1024
MAX_EMULATORS = 64
# Core licenses are independent from RetroArch's GPLv3 license. The business
# product must not auto-distribute noncommercial cores (FBNeo, old MAME,
# Snes9x, Genesis Plus GX, PicoDrive) without copyright-holder permission.
# GPL alternatives for SNES/MD/SMS/GG are in psp_retro.SYSTEMS.
NONCOMMERCIAL_CORES = frozenset({'fbneo_libretro.dll','mame2003_plus_libretro.dll',
    'genesis_plus_gx_libretro.dll','picodrive_libretro.dll','snes9x_libretro.dll'})
# Positive audit, not "everything except known non-commercial": newly added
# cores must get a fresh SPDX review before becoming auto-downloadable.
AUTO_APPROVED_CORES = frozenset({
    'mgba_libretro.dll','gambatte_libretro.dll','bsnes_libretro.dll',
    'mesen_libretro.dll','blastem_libretro.dll','gearsystem_libretro.dll',
    'mupen64plus_next_libretro.dll','mednafen_pce_fast_libretro.dll',
    'stella_libretro.dll','race_libretro.dll',
})


class RetroInstallError(ValueError):
    pass


def managed_retroarch(storage_root: Path) -> Path:
    root=fixed_local_folder(Path(storage_root))
    return root/'emulators'/'retroarch'/'retroarch.exe'


def required_cores(emulators: list[dict]) -> list[str]:
    """Allowlisted family names, not arbitrary PSP paths, URLs or filenames."""
    if not isinstance(emulators,list) or len(emulators)>MAX_EMULATORS:
        raise RetroInstallError('Inventario PSP excesivo o inválido.')
    cores=set()
    for emulator in emulators:
        if not isinstance(emulator,dict) or not isinstance(emulator.get('systems'),list):
            raise RetroInstallError('Emulador PSP sin plataformas verificadas.')
        for system in emulator['systems']:
            if not isinstance(system,str) or system not in SYSTEMS:
                raise RetroInstallError('Plataforma de emulación desconocida.')
            cores.add(SYSTEMS[system][1])
    return sorted(cores)


def install_plan(storage_root: Path, emulators: list[dict], configured: str='') -> dict:
    exe=managed_retroarch(storage_root)
    requested=required_cores(emulators)
    restricted=[name for name in requested if name not in AUTO_APPROVED_CORES]
    authorized=[name for name in requested if name in AUTO_APPROVED_CORES]
    external=bool(configured and Path(configured).absolute()!=exe.absolute())
    chosen=Path(configured) if external else exe
    present=chosen.is_file() and not chosen.is_symlink()
    missing=[name for name in authorized if not (chosen.parent/'cores'/name).is_file()]
    return {
        'managed_path':str(exe),'requested_cores':requested,
        'auto_cores':authorized,'requires_license_review':restricted,
        'missing_cores':missing,
        'retroarch_installed':present,
        'external':external,
        'needed':bool(authorized and (not present or missing) and not external),
        'status':('external' if external else 'not_detected' if not requested else
                  'license_review' if not authorized else
                  'ready' if present and not missing else 'needs_install'),
        'source': 'Libretro/RetroArch (buildbot.libretro.com)',
    }


class _OnlyOfficialRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        _check_url(newurl)
        return super().redirect_request(req,fp,code,msg,headers,newurl)


def _check_url(url: str) -> None:
    p=urllib.parse.urlsplit(url)
    if (p.scheme!='https' or p.hostname!='buildbot.libretro.com' or
            p.username is not None or p.password is not None or
            p.port not in (None,443) or p.query or p.fragment):
        raise RetroInstallError('La descarga no proviene del servidor oficial Libretro HTTPS.')


def _download(url: str, target: Path, maximum: int, cb=None, *, opener=None) -> str:
    _check_url(url)
    if target.exists():
        raise RetroInstallError('Ya existe un archivo temporal de descarga.')
    handler=opener or urllib.request.build_opener(_OnlyOfficialRedirects).open
    digest=hashlib.sha256()
    received=0
    try:
        with handler(url,timeout=40) as response, target.open('xb') as out:
            _check_url(response.geturl())
            header=response.headers.get('Content-Length')
            if header and (not header.isdigit() or int(header)>maximum):
                raise RetroInstallError('El paquete supera el tamaño máximo permitido.')
            while True:
                chunk=response.read(min(1024*1024,maximum+1-received))
                if not chunk: break
                received+=len(chunk)
                if received>maximum:
                    raise RetroInstallError('Descarga demasiado grande; se ha detenido.')
                digest.update(chunk)
                out.write(chunk)
                if cb:
                    cb(received,int(header) if header else 0)
            out.flush()
            os.fsync(out.fileno())
        if received<128:
            raise RetroInstallError('Descarga incompleta o vacía.')
        if header and received != int(header):
            raise RetroInstallError('Descarga truncada: el tamaño no coincide.')
        return digest.hexdigest()
    except Exception:
        target.unlink(missing_ok=True)
        raise


def _safe_archive_member(value: str) -> bool:
    value=value.strip()
    if not value or '\\' in value or ':' in value or '\x00' in value:
        return False
    path=PurePosixPath(value)
    return (not path.is_absolute() and '..' not in path.parts and
            all(part not in ('','.') for part in path.parts if part!='.'))


def _extract_pinned_retroarch(archive:Path, output:Path) -> Path:
    """7-Zip is already supported by Windows bsdtar; no third-party exe."""
    if os.name!='nt':
        raise RetroInstallError('La distribución RetroArch preparada requiere Windows.')
    try:
        listing=subprocess.run(['tar.exe','-tf',str(archive)],capture_output=True,
                               text=True,timeout=160,check=True)
    except (OSError,subprocess.SubprocessError) as exc:
        raise RetroInstallError('Windows bsdtar no puede inspeccionar RetroArch.7z.') from exc
    members=listing.stdout.splitlines()
    if not 2 <= len(members) <= 75000 or any(not _safe_archive_member(x) for x in members):
        raise RetroInstallError('El paquete RetroArch contiene rutas no permitidas.')
    try:
        subprocess.run(['tar.exe','-xf',str(archive),'-C',str(output)],
                       capture_output=True,text=True,timeout=240,check=True)
    except (OSError,subprocess.SubprocessError) as exc:
        raise RetroInstallError('No se pudo extraer RetroArch.7z de forma local.') from exc
    top=output/'RetroArch-Win64'
    if not (top/'retroarch.exe').is_file():
        raise RetroInstallError('Falta retroarch.exe en la distribución oficial extraída.')
    # Even pinned archives must not leave reparse points inside a managed app.
    if any(p.is_symlink() or (hasattr(p,'is_junction') and p.is_junction())
           for p in output.rglob('*')):
        raise RetroInstallError('El paquete RetroArch contiene enlaces no permitidos.')
    return top


def _extract_core(archive:Path, core:str, destination:Path) -> str:
    """Read exactly the requested DLL, no filename-derived archive extract."""
    if core not in AUTO_APPROVED_CORES or core not in {pair[1] for pair in SYSTEMS.values()}:
        raise RetroInstallError('Núcleo Libretro no permitido.')
    try:
        with zipfile.ZipFile(archive) as source:
            infos=source.infolist()
            if (len(infos)!=1 or infos[0].is_dir() or infos[0].filename!=core or
                    infos[0].file_size>CORE_DLL_LIMIT or infos[0].file_size<128 or
                    infos[0].flag_bits & 1):
                raise RetroInstallError('El ZIP oficial no contiene exactamente el núcleo esperado.')
            h=hashlib.sha256()
            with source.open(infos[0],'r') as inp, destination.open('xb') as out:
                size=0
                while True:
                    chunk=inp.read(1024*1024)
                    if not chunk:break
                    size+=len(chunk)
                    if size>CORE_DLL_LIMIT:
                        raise RetroInstallError('El núcleo supera el tamaño seguro.')
                    h.update(chunk)
                    out.write(chunk)
                out.flush()
                os.fsync(out.fileno())
            return h.hexdigest()
    except (zipfile.BadZipFile,EOFError) as exc:
        raise RetroInstallError('El archivo del núcleo Libretro está dañado.') from exc


def install_required(storage_root:Path, emulators:list[dict],
                     *, configured:str='', progress=None,
                     download=None, extract=None) -> dict:
    """Synchronous worker body; caller uses a background thread on Windows.

    Existing user's external RetroArch is never modified. Installed cores are
    immutable until an explicitly versioned future update is requested.
    """
    storage=fixed_local_folder(Path(storage_root))
    plan=install_plan(storage,emulators,configured)
    if plan['external']:
        raise RetroInstallError('RetroArch está configurado fuera del almacén del Dock: no se modifica.')
    if not plan['auto_cores']:
        raise RetroInstallError('Sin núcleos descargables automáticamente: comprueba las licencias.')
    if not plan['needed']:
        return {**plan,'installed_cores':[],'downloaded_bytes':0}
    root=storage/'emulators'
    if root.exists() and (root.is_symlink() or (hasattr(root,'is_junction') and root.is_junction())):
        raise RetroInstallError('La carpeta de emuladores no puede ser un enlace.')
    root.mkdir(parents=True,exist_ok=True)
    exe=managed_retroarch(storage)
    folder=exe.parent
    if folder.exists() and (folder.is_symlink() or (hasattr(folder,'is_junction') and folder.is_junction())):
        raise RetroInstallError('RetroArch no puede estar en un directorio enlazado.')
    # Include extraction and archive temporary space, not just download size.
    if shutil.disk_usage(root).free < 1400*1024*1024:
        raise RetroInstallError('Se requieren al menos 1,4 GB libres para instalar RetroArch.')
    folder_is_new=not folder.exists()
    if not folder_is_new and not exe.is_file():
        raise RetroInstallError('La carpeta RetroArch existente está incompleta. Revisar manualmente.')
    if not folder_is_new and exe.is_symlink():
        raise RetroInstallError('No se ejecutan enlaces a RetroArch.')
    stage=root/('.retro-install-'+uuid.uuid4().hex)
    stage.mkdir(exist_ok=False)
    emit=progress or (lambda **kwargs:None)
    transfer=download or _download
    unpack=extract or _extract_pinned_retroarch
    manifest={'product':'psp-dock','version':STABLE_VERSION,'source':STABLE_URL,
              'release_sha256':STABLE_SHA256,'cores':{}}
    bytes_total=0
    installed=[]
    try:
        if folder_is_new:
            emit(phase='retroarch_download',item='RetroArch '+STABLE_VERSION)
            archive=stage/'RetroArch.7z'
            hash_value=transfer(STABLE_URL,archive,ARCHIVE_LIMIT,
                                lambda done,total:emit(phase='retroarch_download',
                                                       item='RetroArch',bytes_done=done,
                                                       bytes_total=total))
            bytes_total+=archive.stat().st_size
            if hash_value!=STABLE_SHA256:
                raise RetroInstallError('El SHA-256 del ejecutable RetroArch no coincide con la versión fijada.')
            emit(phase='retroarch_extract',item='RetroArch portable')
            extract_dir=stage/'unpacked'
            extract_dir.mkdir()
            payload=unpack(archive,extract_dir)
            if not (payload/'retroarch.exe').is_file():
                raise RetroInstallError('Archivo retroarch.exe no encontrado después de extraer.')
            if folder.exists():
                raise RetroInstallError('El destino RetroArch apareció durante la instalación.')
            os.rename(payload,folder)
            emit(phase='retroarch_ready',item='RetroArch')
        (folder/'cores').mkdir(exist_ok=True)
        for core in plan['auto_cores']:
            target=folder/'cores'/core
            if target.exists():
                if not target.is_file() or target.is_symlink():
                    raise RetroInstallError('Un núcleo existente no es un archivo seguro: '+core)
                continue
            emit(phase='core_download',item=core)
            package=stage/(core+'.zip')
            url=CORE_BASE+core+'.zip'
            package_sha=transfer(url,package,CORE_ZIP_LIMIT,
                                 lambda done,total:emit(phase='core_download',item=core,
                                                        bytes_done=done,bytes_total=total))
            bytes_total+=package.stat().st_size
            emit(phase='core_verify',item=core)
            temp=folder/'cores'/('.dock-core-'+uuid.uuid4().hex+'.dll')
            try:
                core_sha= _extract_core(package,core,temp)
                if target.exists():
                    raise RetroInstallError('El núcleo apareció mientras se instalaba: '+core)
                os.rename(temp,target)
            finally:
                temp.unlink(missing_ok=True)
            installed.append(core)
            manifest['cores'][core]={'sha256':core_sha,'zip_sha256':package_sha,'url':url}
            package.unlink(missing_ok=True)
        # Files are verified independently and no core/config/ROM is replaced.
        emit(phase='ready',item='Núcleos instalados')
        path=folder/'psp-dock-install-manifest.json'
        if path.exists():
            old=json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(old,dict) or old.get('product')!='psp-dock':
                raise RetroInstallError('El manifiesto existente no pertenece a PSP Dock.')
            old_cores=old.get('cores',{})
            if isinstance(old_cores,dict):
                manifest['cores']={**old_cores,**manifest['cores']}
        temp=folder/('.dock-manifest-'+uuid.uuid4().hex+'.json')
        try:
            with temp.open('x',encoding='utf-8') as f:
                json.dump(manifest,f,ensure_ascii=False,indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp,path)
        finally:
            temp.unlink(missing_ok=True)
        return {**install_plan(storage,emulators,str(exe)),
                'installed_cores':installed,'downloaded_bytes':bytes_total}
    finally:
        # Staging directory is created by this call and never contains PSP
        # saves or user files. If RetroArch was successfully installed before
        # a network error, preserve it for safe idempotent retry.
        shutil.rmtree(stage,ignore_errors=True)
