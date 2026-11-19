"""Optional public GitHub texture-pack source discovery for PSP Dock.

Only when the user explicitly selects "Buscar más packs en Internet".
Fetch the public EmuCoreA PPSSPP catalog from its fixed GitHub path.
Remote/new records are SOURCE LINKS ONLY; no new automatic downloads
are authorized just because an untrusted JSON advertises a SHA256.
Automatic install uses only the app's separately pinned approved list
and the exact archive SHA256 verified by the downloader.
"""
from __future__ import annotations

from io import BytesIO
import json
import re
import threading
import time
import urllib.request

from psp_dock.emulation.psp_texture_packs import (
    DISC_ID, TexturePackError, SafeRedirects, sources_for_disc,
    validated_url, source_catalog,
)


REMOTE_INDEX = (
    "https://raw.githubusercontent.com/"
    "sashkinbro/EmuCoreA-Textures/main/textures.json"
)
MAX_INDEX_BYTES=512*1024
TTL_SECONDS=900
_guard=threading.Lock()
_last_attempt=0.0
_cached=None


def read_remote_index(*, opener=None)->list[dict]:
    """Public source discovery only, bounded, no filesystem writes."""
    requester=opener or urllib.request.build_opener(SafeRedirects()).open
    req=urllib.request.Request(
        REMOTE_INDEX,headers={
            'Accept':'application/json',
            'User-Agent':'PSPDock/0.1 (texture-source-discovery)',
        }
    )
    try:
        with requester(req,timeout=10) as response:
            # Never follow a redirect from this fixed repository path
            # to a different potentially compromised catalog.
            if response.geturl()!=REMOTE_INDEX:
                raise TexturePackError('La fuente de catálogo ha cambiado de dirección.')
            raw=response.read(MAX_INDEX_BYTES+1)
    except (OSError,TimeoutError) as exc:
        raise TexturePackError('No se pudo consultar el catálogo HD público.') from exc
    if len(raw)>MAX_INDEX_BYTES:
        raise TexturePackError('Catálogo público demasiado grande.')
    try:
        data=json.loads(raw)
    except (UnicodeDecodeError,ValueError) as exc:
        raise TexturePackError('Catálogo HD público no es JSON válido.') from exc
    if (not isinstance(data,dict) or data.get('schemaVersion')!=1 or
            data.get('platform')!='ppsspp' or
            not isinstance(data.get('entries'),list) or
            len(data['entries'])>250):
        raise TexturePackError('Formato del catálogo PPSSPP desconocido.')
    result=[]
    seen=set()
    for entry in data['entries']:
        if not isinstance(entry,dict):
            continue
        ident=entry.get('id')
        source=entry.get('sourceUrl')
        serials=entry.get('serials')
        if (not isinstance(ident,str) or
                not re.fullmatch('[a-z0-9-]{3,100}',ident) or
                ident in seen or not isinstance(source,str) or
                not isinstance(serials,list)):
            continue
        try:
            validated_url(source)
        except TexturePackError:
            continue
        if not source.startswith('https://github.com/'):
            continue
        ids=[]
        for serial in serials[:12]:
            if isinstance(serial,str):
                rawid=serial.replace('-','')
                if DISC_ID.fullmatch(rawid):
                    ids.append(rawid)
        if not ids:
            continue
        title=entry.get('name')
        authors=entry.get('authors') or []
        if not isinstance(title,str) or not 1<=len(title)<=180:
            continue
        owner=authors[0] if isinstance(authors,list) and authors else 'Comunidad'
        if not isinstance(owner,str) or not 1<=len(owner)<=120:
            owner='Comunidad'
        seen.add(ident)
        result.append({
            'id':ident,'title':title,'publisher':owner,
            'source_url':source,'disc_ids':ids,
        })
    return result


def sources_online(disc_id:str, *,fetcher=None, clock=time.monotonic)->dict:
    """Merge pinned installable sources with new source-only remote records."""
    if not isinstance(disc_id,str) or not DISC_ID.fullmatch(disc_id):
        return {'disc_id':disc_id,'sources':[],'source':'invalid'}
    static=sources_for_disc(disc_id)
    fetcher=fetcher or read_remote_index
    global _last_attempt,_cached
    with _guard:
        if _cached is not None and clock()-_last_attempt<TTL_SECONDS:
            found=_cached
        else:
            found=fetcher()
            _cached=found
            _last_attempt=clock()
    approved_ids={row['id'] for row in static}
    additional=[]
    for row in found:
        if disc_id in row['disc_ids'] and row['id'] not in approved_ids:
            additional.append({
                'id':row['id'],'title':row['title'],
                'publisher':row['publisher'],'source_url':row['source_url'],
                'size_bytes':None,'downloadable':False,
                'distribution':'link-only',
                'license_notice':'Hallazgo público no revisado; consulta al autor.',
            })
    return {'disc_id':disc_id,
            'sources':static+additional,
            'source':'pinned+public_git_index',
            'unverified_new':len(additional)}
