"""Read-only Dariusburst (ULJM05558) asset forensics and PPSSPP dump comparison.

The actual game uses a proprietary DZIP envelope for .TX2/.XXI/.BXN.
Only the magic and declared uncompressed size are confirmed at this stage.
Never treat bytes beyond its 8-byte header as decoded imagery or infer a
PPSSPP hash from packed asset bytes. Reports contain metadata, not game data.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import struct

from psp_artwork import SECTOR, _CSOReader, _directory_record, _read_directory
from psp_hd_textures import DUMP_RE, safe_pc_path
from psp_game_metadata import read_metadata
from psp_texture_engine import identify

GAME_DISC_ID = "ULJM05558"
VALID_ASSET_TYPES = frozenset((".tx2", ".xxi", ".bxn"))
MAX_UNPACKED = 64 * 1024 * 1024
MAX_SINGLE_FILE = 64 * 1024 * 1024
MAX_TOTAL_FILES = 3000
MAX_SHA_BYTES = 320 * 1024 * 1024
MAX_RESULT_BYTES = 4 * 1024 * 1024
CAPTURE_RE = re.compile(r"([0-9a-f]{24})(?:_([0-9]{1,2}))?\.png\Z")


class DariusburstREError(ValueError):
    pass


@dataclass(frozen=True)
class DzipHeader:
    unpacked_size: int

    @classmethod
    def inspect(cls, payload: bytes, file_size: int) -> "DzipHeader":
        if (type(file_size) is not int or not 8 <= file_size <= MAX_SINGLE_FILE
                or len(payload) < 8 or payload[:4] != b"DZIP"):
            raise DariusburstREError("Cabecera DZIP inválida.")
        n = struct.unpack_from("<I", payload, 4)[0]
        if not 1 <= n <= MAX_UNPACKED:
            raise DariusburstREError("DZIP declara un tamaño descomprimido peligroso.")
        return cls(n)


def resource_group(path: str) -> str:
    pieces = path.upper().split("/")
    return next((group for group in ("IMAGES", "PARTICLES", "SCENES")
                 if group in pieces), "OTHER")


def tx2_size_hypotheses(unpacked_size: int) -> list[dict]:
    """Only size arithmetic, NOT a decoded TX2 format interpretation."""
    results = []
    for overhead, meaning in ((0x28, "cabecera de 40 bytes hipotética"),
                              (0x428, "cabecera+paleta de 1064 bytes hipotéticas")):
        texels = unpacked_size - overhead
        if not 64 <= texels <= 1024 * 1024:
            continue
        matches = [
            [w, texels // w] for w in (16, 32, 64, 128, 256, 512, 1024)
            if texels % w == 0 and texels // w in
            (16, 32, 64, 128, 256, 512, 1024)
        ]
        if matches:
            results.append({"overhead": overhead, "interpretation": meaning,
                            "bytes_per_pixel_hypothesis": 1,
                            "possible_dimensions": matches})
    return results


def _iso_entries(media: Path):
    """Bounded ISO9660 + CSO v1 enumerator using PSP Dock's reader."""
    stream = _CSOReader(media) if media.suffix.lower() == ".cso" else media.open("rb")
    with stream:
        volume_size = stream.seek(0, 2)
        if volume_size < 17 * SECTOR:
            raise DariusburstREError("ISO/CSO demasiado pequeña.")
        stream.seek(16 * SECTOR)
        descriptor = stream.read(SECTOR)
        if descriptor[:7] != b"\x01CD001\x01":
            raise DariusburstREError("Volumen ISO9660 no reconocido.")
        root = _directory_record(descriptor[156:])
        if not root or not root["directory"]:
            raise DariusburstREError("Directorio raíz corrupto.")
        queue = [("", root, 0)]
        visited, visited_directories = 0, 0
        while queue:
            prefix, folder, depth = queue.pop(0)
            visited_directories += 1
            if visited_directories > 1024:
                raise DariusburstREError("Demasiados directorios para un análisis seguro.")
            for node in _read_directory(stream, folder, volume_size):
                name = node["name"]
                if name in ("", ".", "..", "\x00", "\x01"):
                    continue
                if ("/" in name or "\\" in name or len(name) > 220
                        or not name.isprintable()):
                    raise DariusburstREError("Nombre de archivo ISO no reconocido.")
                relative = f"{prefix}/{name}" if prefix else name
                if node["directory"]:
                    if depth >= 16:
                        raise DariusburstREError("ISO supera la profundidad permitida.")
                    queue.append((relative, node, depth + 1))
                else:
                    visited += 1
                    if visited > MAX_TOTAL_FILES:
                        raise DariusburstREError("Demasiados archivos en el volumen.")
                    offset = node["extent"] * SECTOR
                    length = node["size"]
                    if offset < 0 or length < 0 or offset + length > volume_size:
                        raise DariusburstREError("Entrada fuera de los límites del volumen.")
                    yield stream, relative, offset, length


def inventory_dariusburst(media: Path, *, max_hash_bytes: int = MAX_SHA_BYTES) -> dict:
    """Read local ISO/CSO, output metadata-only provenance of compressed files."""
    media = safe_pc_path(media)
    if (not media.is_file() or media.suffix.lower() not in (".iso", ".cso")
            or type(max_hash_bytes) is not int
            or not 0 <= max_hash_bytes <= MAX_SHA_BYTES):
        raise DariusburstREError("La fuente debe ser una ISO/CSO local verificable.")
    metadata = read_metadata(media)
    if metadata.get("DISC_ID") != GAME_DISC_ID:
        raise DariusburstREError("No es Dariusburst ULJM05558 verificado.")
    result = {
        "schema": 1, "game": "Dariusburst", "disc_id": GAME_DISC_ID,
        "disc_version": metadata.get("DISC_VERSION"),
        "method": "iso9660_dzip_envelope_only",
        "replacement_hashes_derived": False,
        "decompression_verified": False,
        "file_count": 0, "dzip_count": 0, "total_declared_unpacked_bytes": 0,
        "extension_counts": {}, "group_counts": {},
        "records": [], "errors": [], "source_bytes": media.stat().st_size,
        "decoder_ready": False, "signature_variants": {},
    }
    source_digest = hashlib.sha256()
    with media.open("rb") as stream:
        for piece in iter(lambda: stream.read(1024 * 1024), b""):
            source_digest.update(piece)
    result["source_sha256"] = source_digest.hexdigest()
    extension_counts = Counter()
    group_counts = Counter()
    signature_variants = defaultdict(Counter)
    remaining = max_hash_bytes
    for stream, relative, offset, size in _iso_entries(media):
        result["file_count"] += 1
        extension = Path(relative).suffix.lower()
        extension_counts[extension] += 1
        group = resource_group(relative)
        group_counts[group] += 1
        if extension not in VALID_ASSET_TYPES:
            continue
        stream.seek(offset)
        header = stream.read(min(12, size))
        detected = identify(header, extension=extension)
        record = {
            "path": relative, "extension": extension,
            "group": group, "packed_bytes": size,
            "iso_offset": offset, "has_dzip": False,
            "decoded": False, "ppsspp_hash": None,
            "status": "opaque",
            "format_family": detected.family,
            "decoder_status": detected.status,
            "unverified_variant_prefix": detected.variant_prefix,
        }
        if header[:4] == b"DZIP":
            try:
                envelope = DzipHeader.inspect(header, size)
                record["has_dzip"] = True
                record["unpacked_bytes"] = envelope.unpacked_size
                result["dzip_count"] += 1
                result["total_declared_unpacked_bytes"] += envelope.unpacked_size
                signature_variants[extension][detected.variant_prefix] += 1
                if extension == ".tx2":
                    record["size_hypotheses"] = tx2_size_hypotheses(envelope.unpacked_size)
            except DariusburstREError as exc:
                result["errors"].append({"path": relative, "reason": str(exc)})
                record["status"] = "bad_dzip_header"
        else:
            result["errors"].append({"path": relative, "reason": "Cabecera DZIP ausente"})
            record["status"] = "unknown_header"
        if record["has_dzip"] and size <= remaining:
            digest = hashlib.sha256()
            stream.seek(offset)
            left = size
            while left:
                block = stream.read(min(1024 * 1024, left))
                if not block:
                    raise DariusburstREError("Recurso truncado durante la lectura.")
                digest.update(block)
                left -= len(block)
            record["packed_sha256"] = digest.hexdigest()
            remaining -= size
        result["records"].append(record)
    result["extension_counts"] = dict(sorted(extension_counts.items()))
    result["group_counts"] = dict(sorted(group_counts.items()))
    result["signature_variants"] = {
        extension: dict(sorted(counts.items()))
        for extension, counts in sorted(signature_variants.items())
    }
    result["hashed_bytes"] = max_hash_bytes - remaining
    return result


def safe_report_write(path: Path, report: dict) -> None:
    """Publish only metadata to a new local file; never replace existing data."""
    output = safe_pc_path(path)
    if output.suffix != ".json" or output.is_file():
        raise DariusburstREError("Selecciona un informe JSON nuevo.")
    serialized = (json.dumps(report, ensure_ascii=False, indent=2,
                             sort_keys=True) + "\n").encode("utf-8")
    if len(serialized) > MAX_RESULT_BYTES:
        raise DariusburstREError("Informe demasiado grande.")
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("xb") as f:
        f.write(serialized)
        f.flush()
        import os
        os.fsync(f.fileno())


def capture_inventory(dump_folder: Path, *, limit: int = 30000) -> dict:
    """Read PPSSPP PNG capture names + decoded pixels. No upload or mutation."""
    folder = safe_pc_path(dump_folder)
    if not folder.is_dir():
        return {"count": 0, "entries": [], "invalid": 0}
    try:
        from PIL import Image, ImageOps
    except ImportError as exc:
        raise DariusburstREError("Pillow es necesario para analizar los PNG.") from exc
    if type(limit) is not int or not 1 <= limit <= 30000:
        raise DariusburstREError("Límite de capturas inválido.")
    entries, invalid = [], 0
    for path in sorted(folder.iterdir()):
        if path.suffix.lower() != ".png":
            continue
        if len(entries) >= limit:
            raise DariusburstREError("Demasiadas capturas.")
        parsed = CAPTURE_RE.fullmatch(path.name)
        if not parsed:
            invalid += 1
            continue
        if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
            invalid += 1
            continue
        if not path.is_file() or not 45 <= path.stat().st_size <= 24 * 1024 * 1024:
            invalid += 1
            continue
        try:
            with Image.open(path) as image:
                if (image.format != "PNG" or max(image.size) > 4096
                        or image.width * image.height > 16_777_216):
                    invalid += 1
                    continue
                rgba = image.convert("RGBA")
                digest = hashlib.sha256(rgba.tobytes()).hexdigest()
                dimensions = [image.width, image.height]
        except (OSError, ValueError):
            invalid += 1
            continue
        entries.append({
            "hash": parsed.group(1),
            "mip": int(parsed.group(2) or 0),
            "name": path.name,
            "width": dimensions[0], "height": dimensions[1],
            "pixels_sha256": digest,
        })
    return {"count": len(entries), "entries": entries, "invalid": invalid}


def compare_capture_sessions(before: dict, after: dict) -> dict:
    """Compare real emulator hashes across runs; no false ISO mappings."""
    for snapshot in (before, after):
        if not isinstance(snapshot, dict) or not isinstance(snapshot.get("entries"), list):
            raise DariusburstREError("Inventario de capturas inválido.")
    def keyed(snapshot: dict) -> dict:
        result = {}
        for entry in snapshot["entries"]:
            if (not isinstance(entry, dict)
                    or not isinstance(entry.get("hash"), str)
                    or not re.fullmatch(r"[0-9a-f]{24}", entry["hash"])
                    or not isinstance(entry.get("mip"), int)
                    or not 0 <= entry["mip"] <= 99
                    or not re.fullmatch(r"[0-9a-f]{64}", entry.get("pixels_sha256", ""))):
                raise DariusburstREError("Huella de textura inválida.")
            key = (entry["hash"], entry["mip"])
            if key in result:
                raise DariusburstREError("Hash+mip duplicado en el mismo inventario.")
            result[key] = entry
        return result
    a, b = keyed(before), keyed(after)
    common = a.keys() & b.keys()
    changed = sorted(k[0] for k in common
                     if a[k]["pixels_sha256"] != b[k]["pixels_sha256"])
    return {
        "before": len(a), "after": len(b),
        "repeated_same_pixels": sum(a[k]["pixels_sha256"] == b[k]["pixels_sha256"]
                                    for k in common),
        "same_hash_different_pixels": len(changed),
        "changed_hashes": changed,
        "new_hashes": sorted(k[0] for k in b.keys() - a.keys()),
        "missing_hashes": sorted(k[0] for k in a.keys() - b.keys()),
        "source_mapping_verified": False,
    }


def correlate_decoded_candidates(capture: dict, candidates: list[dict]) -> dict:
    """Exploratory exact-pixel matching, NOT proof of source provenance.

    A manifest flag from any caller is never proof that a decoder reproduced
    the binary source. This function cannot authorize replacement publishing.
    """
    entries = capture.get("entries", [])
    indexed = defaultdict(list)
    for image in entries:
        indexed[(image["pixels_sha256"], image["width"], image["height"])].append(image)
    proposed = []
    unresolved = []
    for item in candidates:
        if (not isinstance(item, dict)
                or not re.fullmatch(r"[0-9a-f]{64}", item.get("pixels_sha256", ""))
                or not isinstance(item.get("source_path"), str)):
            unresolved.append({"reason": "invalid_candidate"})
            continue
        key = (item["pixels_sha256"], item.get("width"), item.get("height"))
        matches = indexed.get(key, [])
        if len(matches) == 1:
            proposed.append({
                "source_path": item["source_path"],
                "ppsspp_hash": matches[0]["hash"],
                "mip": matches[0]["mip"],
                "evidence": "rgba_pixel_equality_only",
                "source_provenance_verified": False,
                "publishing_authorized": False,
            })
        else:
            unresolved.append({"source_path": item["source_path"],
                               "reason": "no_exact_match" if not matches else "ambiguous"})
    return {"pixel_matches_unverified": proposed, "unresolved": unresolved,
            "published_replacements": 0}
