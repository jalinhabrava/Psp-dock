"""Safe downloader/installer for approved PPSSPP HD texture ZIP packs.

Only exact DISC_ID-matched, individually curated offers with SHA256,
size and publisher may be installed. No automatic downloading of ROMs,
executables or unverified forum attachments. The ZIP stays unchanged
and lives ONLY in the PC's private PPSSPP PSP/TEXTURES/<DISC_ID>.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import urllib.parse
import urllib.request
import uuid
import zipfile
from functools import lru_cache


DISC_ID = re.compile(r'[A-Z]{4}[0-9]{5}\Z')
SHA256 = re.compile(r'[0-9a-f]{64}\Z')
ALLOWED_HOSTS = frozenset((
    'github.com','objects.githubusercontent.com',
    'release-assets.githubusercontent.com','raw.githubusercontent.com',
))
MAX_ZIP_BYTES = 512*1024*1024
MAX_UNPACKED_BYTES = 2*1024*1024*1024
MAX_ENTRIES = 30000
TEXTURE_TYPES = ('.png','.ktx2','.dds','.jpg','.jpeg','.bmp','.zim','.zimg')
SOURCE_INDEX = Path(__file__).resolve().parents[2] / 'texture_catalog_sources.json'
SOURCE_BLOB_SHA = '69da82d63d40630e18bd4b5d01c482e19e74c3db'


class TexturePackError(ValueError):
    pass


@dataclass(frozen=True)
class TextureOffer:
    id: str
    disc_id: str
    title: str
    publisher: str
    source_url: str
    archive_url: str
    sha256: str
    size_bytes: int
    permission: str

    def validate(self):
        if (not re.fullmatch('[a-z0-9-]{3,64}',self.id)
                or not DISC_ID.fullmatch(self.disc_id)
                or not SHA256.fullmatch(self.sha256)
                or not 1 <= self.size_bytes <= MAX_ZIP_BYTES
                or not self.title or not self.publisher or not self.permission):
            raise TexturePackError('Oferta de texturas incompleta o inválida.')
        validated_url(self.archive_url)
        validated_url(self.source_url)


def validated_url(url:str)->str:
    if not isinstance(url,str) or len(url)>2048:
        raise TexturePackError('URL del paquete inválida.')
    try:
        parsed=urllib.parse.urlparse(url)
        if (parsed.scheme!='https' or parsed.username or parsed.password
                or parsed.port not in (None,443) or parsed.hostname not in ALLOWED_HOSTS
                or parsed.fragment):
            raise TexturePackError('Solo fuentes HTTPS GitHub verificadas.')
    except ValueError as exc:
        raise TexturePackError('URL HTTPS inválida.') from exc
    return url


class SafeRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validated_url(newurl)
        return super().redirect_request(req,fp,code,msg,headers,newurl)


def inspect_zip(path:Path)->dict:
    """Reject executable payloads, path traversal, symlinks and ZIP bombs."""
    path=Path(path)
    if not path.is_file() or path.stat().st_size > MAX_ZIP_BYTES:
        raise TexturePackError('El paquete ZIP es demasiado grande.')
    try:
        with zipfile.ZipFile(path) as archive:
            entries=archive.infolist()
            if not 1<=len(entries)<=MAX_ENTRIES:
                raise TexturePackError('Número de archivos del pack no admitido.')
            has_ini=False
            textures=0
            total=0
            seen=set()
            for info in entries:
                raw=info.filename
                filename=PurePosixPath(raw)
                if (not raw or '\\' in raw or raw.startswith('/') or
                        ':' in raw or filename.is_absolute() or
                        any(x in ('..','') for x in filename.parts) or
                        len(filename.parts)>16):
                    raise TexturePackError('Ruta de paquete insegura.')
                normalized=raw.rstrip('/').casefold()
                if normalized in seen:
                    raise TexturePackError('Archivo duplicado en el paquete ZIP.')
                seen.add(normalized)
                mode=(info.external_attr >> 16) & 0xffff
                if stat.S_ISLNK(mode):
                    raise TexturePackError('Enlace simbólico dentro del ZIP.')
                if info.flag_bits&0x1:
                    raise TexturePackError('ZIP cifrado no admitido.')
                if info.file_size>128*1024*1024:
                    raise TexturePackError('Una textura supera el límite.')
                total+=info.file_size
                if total>MAX_UNPACKED_BYTES:
                    raise TexturePackError('Tamaño descomprimido desproporcionado.')
                if info.is_dir():
                    continue
                if info.compress_size>0 and info.file_size>info.compress_size*400:
                    raise TexturePackError('Posible bomba ZIP de expansión excesiva.')
                if raw=='textures.ini':
                    has_ini=True
                    if info.file_size>256*1024:
                        raise TexturePackError('textures.ini demasiado grande.')
                    with archive.open(info) as f:
                        ini=f.read(256*1024).decode('utf-8-sig','strict')
                    if '[options]' not in ini.lower():
                        raise TexturePackError('textures.ini no reconocido.')
                elif raw.lower().endswith(TEXTURE_TYPES):
                    textures+=1
                elif (raw.casefold().endswith(('.txt','.md')) or
                      raw.casefold()=='license'):
                    # Community packs may ship safe text mapping/credits
                    # nested beside images (e.g. Text/Key.txt); these
                    # never execute and ZIP contents are never extracted.
                    if info.file_size>2*1024*1024:
                        raise TexturePackError('Documento del pack demasiado grande.')
                elif raw=='Map/Map_Radar_Redraw.pdn' and info.file_size<=1024*1024:
                    # The SHA256-pinned SonofUgly LCS archive includes this
                    # inert Paint.NET source project. It is never extracted
                    # or opened by PSP Dock; PPSSPP ignores nontextures.
                    # Never generalize this exception to executables.
                    pass
                else:
                    # Refuse executables and scripts rather than ever
                    # unpacking or executing them on the customer's PC.
                    raise TexturePackError('El ZIP contiene archivos no reconocidos.')
            if not has_ini or textures<1:
                raise TexturePackError('No es un paquete HD de PPSSPP válido.')
            return {'textures':textures,'files':len(entries),
                    'unpacked_bytes':total}
    except (zipfile.BadZipFile, UnicodeDecodeError) as exc:
        raise TexturePackError('ZIP de texturas inválido.') from exc


def download_and_install(offer:TextureOffer, *, matching_disc_id:str,
                         private_memstick:Path, opener=None,
                         progress=None)->dict:
    """One explicit user-approved pack install; no auto install on discovery."""
    offer.validate()
    if offer.disc_id != matching_disc_id or not DISC_ID.fullmatch(matching_disc_id):
        raise TexturePackError('El paquete no corresponde a esta edición del juego.')
    memstick=Path(private_memstick)
    if memstick.is_symlink() or not memstick.is_dir():
        raise TexturePackError('Memory Stick privada del PC inexistente.')
    target=memstick/'PSP/TEXTURES'/matching_disc_id
    if (target.is_symlink() or
            not target.resolve().is_relative_to(memstick.resolve()) or
            any(getattr(p,'is_junction',lambda:False)() or p.is_symlink()
                for p in (target,target.parent,target.parent.parent)) or
            target.exists() and any(target.iterdir())):
        raise TexturePackError('Ya hay texturas instaladas para este juego; no se mezclarán ni sobrescribirán.')
    if shutil.disk_usage(memstick).free<offer.size_bytes+64*1024*1024:
        raise TexturePackError('No hay espacio en el disco del PC.')
    target.mkdir(parents=True,exist_ok=True)
    temp=target/('.download-'+uuid.uuid4().hex+'.part')
    opener=opener or urllib.request.build_opener(SafeRedirects()).open
    sha=hashlib.sha256()
    count=0
    if progress is not None:
        progress(0,offer.size_bytes,'downloading')
    try:
        request=urllib.request.Request(
            offer.archive_url,headers={'User-Agent':'PSPDock/0.1',
                                       'Accept':'application/zip,application/octet-stream'})
        with opener(request,timeout=45) as response, temp.open('xb') as dest:
            validated_url(response.geturl())
            while True:
                piece=response.read(1024*1024)
                if not piece:
                    break
                count+=len(piece)
                if count>offer.size_bytes or count>MAX_ZIP_BYTES:
                    raise TexturePackError('Descarga mayor de lo autorizado.')
                dest.write(piece)
                sha.update(piece)
                if progress is not None:
                    progress(count,offer.size_bytes,'downloading')
            dest.flush()
            os.fsync(dest.fileno())
        if count!=offer.size_bytes or sha.hexdigest()!=offer.sha256:
            raise TexturePackError('SHA-256 o tamaño no coinciden con la fuente verificada.')
        if progress is not None:
            progress(count,offer.size_bytes,'validating')
        stats=inspect_zip(temp)
        try:
            # Hard-link publishing cannot overwrite if another request
            # has already installed the same game's textures.zip.
            os.link(temp,target/'textures.zip')
        except FileExistsError as exc:
            raise TexturePackError('El paquete ya está instalado; no sobrescribir.') from exc
        if progress is not None:
            progress(count,offer.size_bytes,'installed')
        return {'status':'installed','disc_id':matching_disc_id,
                'size_bytes':count,**stats,'source':offer.source_url}
    finally:
        temp.unlink(missing_ok=True)


@lru_cache(maxsize=1)
def source_catalog()->tuple[dict,...]:
    """Inspect a pinned snapshot of a third-party author-attributed catalog.

    Only factual metadata (game IDs, source URLs, sizes, hashes and attribution)
    is bundled, never the actual texture images or game assets. The snapshot
    was derived from EmuCoreA-Textures' 2026-09-19 SHA-pinned catalog.
    Changes require a reviewed code update, not a silent remote replacement.
    """
    try:
        source=json.loads(SOURCE_INDEX.read_text(encoding='utf-8'))
    except (OSError,UnicodeDecodeError,json.JSONDecodeError) as exc:
        raise TexturePackError('No se puede cargar el catálogo de fuentes HD.') from exc
    if (source.get('schema')!=1 or
            source.get('source_repository')!='sashkinbro/EmuCoreA-Textures' or
            source.get('source_blob_sha')!=SOURCE_BLOB_SHA or
            not isinstance(source.get('entries'),list) or
            not 1<=len(source['entries'])<=250):
        raise TexturePackError('Fuente del catálogo HD no reconocida.')
    result=[]
    identifiers=set()
    for entry in source['entries']:
        if (not isinstance(entry,dict) or
                not re.fullmatch('[a-z0-9-]{3,100}',entry.get('id','')) or
                entry['id'] in identifiers or
                not isinstance(entry.get('disc_ids'),list) or
                not 1<=len(entry['disc_ids'])<=12 or
                any(not isinstance(d,str) or not DISC_ID.fullmatch(d)
                    for d in entry['disc_ids'])):
            raise TexturePackError('Registro de catálogo HD inválido.')
        identifiers.add(entry['id'])
        validated_url(entry['source_url'])
        if entry.get('download_url'):
            validated_url(entry['download_url'])
            if (not SHA256.fullmatch(entry.get('sha256','')) or
                    type(entry.get('size_bytes')) is not int or
                    entry['size_bytes']<1):
                raise TexturePackError('Pack de texturas sin SHA-256 o tamaño.')
        if (entry.get('download_ready') is True and
                (not entry.get('download_url') or
                 entry.get('size_bytes',0)>MAX_ZIP_BYTES or
                 not entry.get('license_notice'))):
            raise TexturePackError('Oferta de instalación sin origen verificable.')
        result.append(entry)
    return tuple(result)


def sources_for_disc(disc_id:str)->list[dict]:
    """Show source information even when direct auto-install is unavailable."""
    if not isinstance(disc_id,str) or not DISC_ID.fullmatch(disc_id):
        return []
    out=[]
    for item in source_catalog():
        if disc_id not in item['disc_ids']:
            continue
        ready=item.get('download_ready') is True
        out.append({
            'id':item['id'],'title':item['title'],
            'game_title':item['game_title'],'publisher':item['publisher'],
            'source_url':item['source_url'],
            'size_bytes':item.get('size_bytes'),
            'downloadable':ready,
            'distribution':item.get('distribution','link-only'),
            'license_notice':item.get('license_notice',''),
        })
    return sorted(out,key=lambda x:(not x['downloadable'],x['title'].casefold()))


def approved_offers()->tuple[TextureOffer,...]:
    """Only reviewed, exact-revision ZIPs may reach the downloader."""
    offers=[]
    for item in source_catalog():
        if item.get('download_ready') is not True:
            continue
        for serial in item['disc_ids']:
            offer=TextureOffer(
                id=item['id'],disc_id=serial,title=item['title'],
                publisher=item['publisher'],source_url=item['source_url'],
                archive_url=item['download_url'],
                sha256=item['sha256'],size_bytes=item['size_bytes'],
                permission=item['license_notice'],
            )
            offer.validate()
            offers.append(offer)
    return tuple(offers)


CURATED_OFFERS=approved_offers()


def offers_for_disc(disc_id:str)->list[dict]:
    if not isinstance(disc_id,str) or not DISC_ID.fullmatch(disc_id):
        return []
    return [
        dict(id=o.id,title=o.title,publisher=o.publisher,
             source_url=o.source_url,permission=o.permission,
             size_bytes=o.size_bytes)
        for o in CURATED_OFFERS if o.disc_id==disc_id
    ]
