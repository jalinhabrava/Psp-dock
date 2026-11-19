"""Read bounded UMD PARAM.SFO / EBOOT.PBP metadata without extracting games.

This is read-only and deliberately narrow: only the original SFO metadata
needed to identify a PSP game, revision and compatible texture pack.
The game is never executed; no ROMs, saves or PSP firmware are modified.
"""
from __future__ import annotations

from pathlib import Path
import re
import struct

from psp_dock.media.psp_artwork import (
    SECTOR, _CSOReader, _InvalidCSO, _directory_record,
    _read_directory,
)


MAX_SFO_BYTES = 128 * 1024
MAX_SFO_ENTRIES = 128
MAX_TEXT_BYTES = 8192
GAME_ID_RE = re.compile(r"^[A-Z]{4}[0-9]{5}$")
ALLOWED = frozenset({
    "DISC_ID", "DISC_VERSION", "TITLE", "CATEGORY",
    "PSP_SYSTEM_VER", "REGION", "PARENTAL_LEVEL",
    "SAVEDATA_TITLE", "SAVEDATA_DIRECTORY",
})


def parse_sfo(raw: bytes) -> dict:
    """Parse Sony SFO v1 with strict offset/length validation."""
    if (not isinstance(raw, bytes) or len(raw) < 20 or
            len(raw) > MAX_SFO_BYTES or raw[:4] != b"\0PSF"):
        return {}
    _, version, key_base, data_base, count = struct.unpack_from("<4sIIII", raw)
    if (version not in (0x00000100, 0x00000101) or count > MAX_SFO_ENTRIES or
            20 + count * 16 > len(raw) or
            not 20 + count * 16 <= key_base < len(raw) or
            not key_base <= data_base <= len(raw)):
        return {}
    result = {}
    for index in range(count):
        offset = 20 + index * 16
        key_relative, kind, size, total, value_relative = struct.unpack_from(
            "<HHIII", raw, offset
        )
        if size > total or total > MAX_SFO_BYTES or size > MAX_TEXT_BYTES:
            continue
        key_start = key_base + key_relative
        end_key = raw.find(b"\0",key_start,min(data_base,key_start+64))
        if key_start < key_base or key_start >= data_base or end_key < 0:
            continue
        try:
            key = raw[key_start:end_key].decode("ascii", "strict")
        except UnicodeDecodeError:
            continue
        if key not in ALLOWED or key in result:
            continue
        start = data_base + value_relative
        if start < data_base or start + size > len(raw):
            continue
        content = raw[start:start+size]
        if kind in (0x0004, 0x0204):
            value = content.split(b"\0",1)[0].decode("utf-8","replace").strip()
            if value and len(value) <= 256:
                result[key] = value
        elif kind == 0x0404 and size == 4:
            result[key] = struct.unpack("<I",content)[0]
    if "DISC_ID" in result:
        result["DISC_ID"] = result["DISC_ID"].upper()
        if not GAME_ID_RE.fullmatch(result["DISC_ID"]):
            result.pop("DISC_ID")
    return result


def pbp_param_sfo(path: Path) -> bytes | None:
    with Path(path).open("rb") as file:
        header = file.read(40)
        if len(header) != 40 or header[:4] != b"\0PBP":
            return None
        offsets = struct.unpack_from("<8I",header,8)
        total = file.seek(0,2)
        if offsets[0] < 40 or offsets[-1] > total or (
                sorted(offsets) != list(offsets)):
            return None
        length = offsets[1]-offsets[0]
        if not 20 <= length <= MAX_SFO_BYTES:
            return None
        file.seek(offsets[0])
        result = file.read(length)
        return result if len(result) == length else None


def _iso_stream_sfo(stream) -> bytes | None:
    size = stream.seek(0,2)
    if size < 17*SECTOR:
        return None
    stream.seek(16*SECTOR)
    pvd = stream.read(SECTOR)
    if len(pvd)!=SECTOR or pvd[:7]!=b"\x01CD001\x01":
        return None
    root = _directory_record(pvd[156:])
    if not root or not root["directory"]:
        return None
    game = next((r for r in _read_directory(stream,root,size)
                 if r["name"] == "PSP_GAME" and r["directory"]),None)
    if game is None:
        return None
    file = next((r for r in _read_directory(stream,game,size)
                 if r["name"] == "PARAM.SFO" and not r["directory"]),None)
    if (file is None or not 20 <= file["size"] <= MAX_SFO_BYTES or
            file["extent"]*SECTOR+file["size"]>size):
        return None
    stream.seek(file["extent"]*SECTOR)
    return stream.read(file["size"])


def read_metadata(path: Path) -> dict:
    """Unsupported/corrupt ROMs return empty metadata (no private guesses)."""
    path=Path(path)
    suffix=path.suffix.lower()
    try:
        if suffix == ".pbp":
            raw=pbp_param_sfo(path)
        elif suffix == ".iso":
            with path.open("rb") as stream:
                raw=_iso_stream_sfo(stream)
        elif suffix == ".cso":
            with _CSOReader(path) as stream:
                raw=_iso_stream_sfo(stream)
        else:
            return {}
        return parse_sfo(raw) if raw else {}
    except (_InvalidCSO, OSError, ValueError, struct.error):
        return {}
