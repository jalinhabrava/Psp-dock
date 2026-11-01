"""Safe downloader/installer for approved PPSSPP HD texture ZIP packs.

Only exact DISC_ID-matched, individually curated offers with SHA256,
size and publisher may be installed. No automatic downloading of ROMs,
executables or unverified forum attachments. The ZIP stays unchanged
and lives ONLY in the PC's private PPSSPP PSP/TEXTURES/<DISC_ID>.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from io import BytesIO
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import urllib.parse
import urllib.request
import uuid
import zipfile


DISC_ID = re.compile(r'[A-Z]{4}[0-9]{5}\Z')
SHA256 = re.compile(r'[0-9a-f]{64}\Z')
ALLOWED_HOSTS = frozenset((
    'github.com','objects.githubusercontent.com',
    'release-assets.githubusercontent.com','raw.githubusercontent.com',
))
MAX_ZIP_BYTES = 512*1024*1024
MAX_UNPACKED_BYTES = 2*1024*1024*1024
MAX_ENTRIES = 30000
TEXTURE_TYPES = ('.png','.ktx2','.dds','.jpg','.jpeg','.bmp','.zim')


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
                elif raw.casefold() in ('license','license.txt','readme.txt','readme.md'):
                    if info.file_size>2*1024*1024:
                        raise TexturePackError('Documento del pack demasiado grande.')
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
                         private_memstick:Path, opener=None)->dict:
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
            (target/'textures.zip').exists()):
        raise TexturePackError('Ya existe un pack de texturas; no sobrescribir.')
    if shutil.disk_usage(memstick).free<offer.size_bytes+64*1024*1024:
        raise TexturePackError('No hay espacio en el disco del PC.')
    target.mkdir(parents=True,exist_ok=True)
    temp=target/('.download-'+uuid.uuid4().hex+'.part')
    opener=opener or urllib.request.build_opener(SafeRedirects()).open
    sha=hashlib.sha256()
    count=0
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
            dest.flush()
            os.fsync(dest.fileno())
        if count!=offer.size_bytes or sha.hexdigest()!=offer.sha256:
            raise TexturePackError('SHA-256 o tamaño no coinciden con la fuente verificada.')
        stats=inspect_zip(temp)
        try:
            # Hard-link publishing cannot overwrite if another request
            # has already installed the same game's textures.zip.
            os.link(temp,target/'textures.zip')
        except FileExistsError as exc:
            raise TexturePackError('El paquete ya está instalado; no sobrescribir.') from exc
        return {'status':'installed','disc_id':matching_disc_id,
                'size_bytes':count,**stats,'source':offer.source_url}
    finally:
        temp.unlink(missing_ok=True)


# Offers are deliberately curated, not guessed from matching game titles.
# Sources can be found on the official PPSSPP forum; packages are added
# here only after a reviewer verifies author permission, exact DISC_ID,
# fixed download URL, SHA256, size and ZIP contents. No approved archive
# has been verified for ULJM05558 (Dariusburst) yet.
CURATED_OFFERS:tuple[TextureOffer,...]=()


def offers_for_disc(disc_id:str)->list[dict]:
    if not isinstance(disc_id,str) or not DISC_ID.fullmatch(disc_id):
        return []
    return [dict(id=o.id,title=o.title,publisher=o.publisher,
                 source_url=o.source_url,permission=o.permission,
                 size_bytes=o.size_bytes)
            for o in CURATED_OFFERS if o.disc_id==disc_id]
