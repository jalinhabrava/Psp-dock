"""Bounded, read-only extraction of the PSP's own XMB preview media.

ISO/CSO: PSP_GAME/{ICON0.PNG,PIC0.PNG,PIC1.PNG,SND0.AT3}
PBP:      fixed PARAM.SFO/ICON0/ICON1/PIC0/PIC1/SND0 sections.

Nothing is downloaded from the internet and nothing is written to the PSP.
SND0.AT3 can be converted to MP3 for Chromium using an existing FFmpeg
installation, storing the result exclusively in the PC's cache.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import uuid

from psp_dock.media.psp_artwork import (
    MAX_PNG_BYTES, SECTOR, _CSOReader, _InvalidCSO,
    _directory_record, _read_directory, normalized_png,
)

RESOURCE_NAMES = {
    "icon": "ICON0.PNG",
    "overlay": "PIC0.PNG",
    "background": "PIC1.PNG",
    "sound": "SND0.AT3",
}
PBP_SECTION = {"icon":1, "overlay":3, "background":4, "sound":5}
MAX_SOUND_BYTES = 3*1024*1024
MEDIA_LIMITS = {
    "icon": MAX_PNG_BYTES,
    "overlay": MAX_PNG_BYTES,
    "background": MAX_PNG_BYTES,
    "sound": MAX_SOUND_BYTES,
}


def valid_at3(data: bytes) -> bool:
    """PSP SND0 usually carries ATRAC3 in a RIFF/WAVE container."""
    if not isinstance(data, bytes) or not 44 <= len(data) <= MAX_SOUND_BYTES:
        return False
    if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        return False
    size = struct.unpack_from("<I", data, 4)[0]
    if size < 36 or size+8 > len(data) or len(data)-(size+8) > 16:
        return False
    # For ordinary PCM WAV or an executable masquerading as RIFF, reject.
    position, fmt, samples = 12, False, False
    while position+8 <= size+8:
        kind = data[position:position+4]
        length = struct.unpack_from("<I",data,position+4)[0]
        start = position+8
        if start+length > size+8:
            return False
        if kind == b"fmt " and length >= 16:
            code = struct.unpack_from("<H",data,start)[0]
            # ATRAC3 0x270, ATRAC3plus 0xFFFE (WAVEFORMATEXTENSIBLE)
            fmt = code in (0x0270,0xFFFE)
        elif kind == b"data" and length > 0:
            samples = True
        position = start+length+(length & 1)
    return fmt and samples


def _clean(kind: str, data: bytes | None) -> bytes | None:
    if kind not in RESOURCE_NAMES or not data or len(data)>MEDIA_LIMITS[kind]:
        return None
    return data if kind=="sound" and valid_at3(data) else (
        normalized_png(data) if kind!="sound" else None
    )


def _pbp_resource(path: Path, kind: str) -> bytes | None:
    with path.open("rb") as f:
        header=f.read(40)
        if len(header)!=40 or header[:4]!=b"\x00PBP":
            return None
        offsets=struct.unpack_from("<8I",header,8)
        total=f.seek(0,2)
        if offsets[0]<40 or sorted(offsets)!=list(offsets) or offsets[-1]>total:
            return None
        index=PBP_SECTION[kind]
        start=offsets[index]
        end=offsets[index+1]
        if not 44 <= end-start <= MEDIA_LIMITS[kind]:
            return None
        f.seek(start)
        return _clean(kind,f.read(end-start))


def _iso_resource(stream, kind: str) -> bytes | None:
    size=stream.seek(0,2)
    if size<17*SECTOR:
        return None
    stream.seek(16*SECTOR)
    pvd=stream.read(SECTOR)
    if len(pvd)!=SECTOR or pvd[:7]!=b"\x01CD001\x01":
        return None
    root=_directory_record(pvd[156:])
    if not root or not root["directory"]:
        return None
    game=next((r for r in _read_directory(stream,root,size)
               if r["directory"] and r["name"]=="PSP_GAME"),None)
    if not game:
        return None
    asset=next((r for r in _read_directory(stream,game,size)
                if r["name"]==RESOURCE_NAMES[kind] and not r["directory"]),None)
    if not asset or not 44 <= asset["size"] <= MEDIA_LIMITS[kind]:
        return None
    if asset["extent"] <= 0 or asset["extent"]*SECTOR+asset["size"] > size:
        return None
    stream.seek(asset["extent"]*SECTOR)
    return _clean(kind,stream.read(asset["size"]))


def read_resource(path: Path, kind: str) -> bytes | None:
    """Read one bounded resource. Malformed/nonexistent media yields None."""
    if kind not in RESOURCE_NAMES:
        raise ValueError("Recurso XMB desconocido.")
    path=Path(path)
    try:
        if path.suffix.lower()==".pbp":
            return _pbp_resource(path,kind)
        if path.suffix.lower()==".iso":
            with path.open("rb") as stream:
                return _iso_resource(stream,kind)
        if path.suffix.lower()==".cso":
            with _CSOReader(path) as stream:
                return _iso_resource(stream,kind)
    except (OSError,ValueError,struct.error,_InvalidCSO):
        pass
    return None


def media_key(*, volume: str, game: dict) -> str:
    """Tie a cache entry to original volume and versioned game fingerprint."""
    expected=("id","relative","format","size","modified_ns")
    if not volume or not isinstance(game,dict) or any(k not in game for k in expected):
        raise ValueError("No existe identidad de catálogo PSP verificable.")
    selected={key:game.get(key) for key in expected}
    selected["signature"]=game.get("signature")
    selected["volume_serial"]=volume
    return hashlib.sha256(json.dumps(selected,sort_keys=True).encode()).hexdigest()


def convert_at3_to_mp3(source: bytes, *, ffmpeg: str | None = None,
                       timeout: int = 20) -> bytes | None:
    """Convert user's own local XMB sound to browser-compatible MP3.

    FFmpeg is invoked without shell or network access, with bounded input,
    output, playback length and runtime. The untrusted source stays in a
    temporary private directory; failure never blocks a game launching.
    """
    if not valid_at3(source):
        return None
    executable = ffmpeg or shutil.which("ffmpeg")
    if not executable:
        return None
    if timeout<1 or timeout>60:
        raise ValueError("Tiempo de conversión inválido.")
    with tempfile.TemporaryDirectory(prefix="pspdock-at3-") as tmp:
        inp=Path(tmp)/"sound.at3"
        out=Path(tmp)/"sound.mp3"
        inp.write_bytes(source)
        try:
            result=subprocess.run(
                [str(executable),"-hide_banner","-loglevel","error",
                 "-nostdin","-i",str(inp),"-vn","-t","60",
                 "-ac","2","-b:a","112k","-f","mp3",str(out)],
                stdin=subprocess.DEVNULL,capture_output=True,
                timeout=timeout,check=False,
            )
            if result.returncode or not out.is_file() or out.stat().st_size>2*1024*1024:
                return None
            result_bytes=out.read_bytes()
            return result_bytes if result_bytes.startswith(b"ID3") or (
                len(result_bytes)>4 and result_bytes[:1]==b"\xff"
            ) else None
        except (OSError,subprocess.SubprocessError):
            return None


class XMBAssetCache:
    """PC-only media store; generation names cannot expose arbitrary paths."""

    def __init__(self, root: Path):
        self.root=Path(root)

    def path(self, key: str, kind: str) -> Path:
        if (len(key)!=64 or any(c not in "0123456789abcdef" for c in key) or
                kind not in RESOURCE_NAMES):
            raise ValueError("Identificador de caché XMB inválido.")
        return self.root/(key+"-"+kind+(".mp3" if kind=="sound" else ".png"))

    def cached(self, key: str, kind: str) -> bytes | None:
        path=self.path(key,kind)
        if not path.is_file() or path.is_symlink():
            return None
        value=path.read_bytes()
        if kind=="sound":
            if 0<len(value)<=2*1024*1024 and value[:3]==b"ID3":
                return value
        elif _clean(kind,value) is not None:
            return value
        return None

    def ensure(self, key: str, kind: str, source: Path | None) -> bytes | None:
        cache=self.cached(key,kind)
        if cache:
            return cache
        if source is None or not source.is_file():
            return None
        raw=read_resource(source,kind)
        if not raw:
            return None
        value=convert_at3_to_mp3(raw) if kind=="sound" else raw
        if not value:
            return None
        self.root.mkdir(parents=True,exist_ok=True)
        destination=self.path(key,kind)
        stage=self.root/(".xmb-"+uuid.uuid4().hex+".tmp")
        try:
            with stage.open("xb") as writer:
                writer.write(value)
                writer.flush()
                os.fsync(writer.fileno())
            if not destination.exists():
                os.replace(stage,destination)
        finally:
            stage.unlink(missing_ok=True)
        return value
