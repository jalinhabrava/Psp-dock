"""Opt-in, PC-only hybrid HD texture generation for PPSSPP.

PPSSPP itself discovers and hashes textures while a game runs. We process only
complete PNGs in its TEXTURES/<DISC_ID>/new directory and publish replacements
under the same hashes. Separate ISO/CSO inventory is deliberately *unmapped*:
file bytes alone cannot reproduce a PPSSPP runtime texture hash reliably.
No writes to an attached PSP, user texture packs, saves, or game images.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time
import uuid

from psp_artwork import SECTOR, _CSOReader, _directory_record, _read_directory, valid_png

DISC_ID_RE = re.compile(r"[A-Z]{4}[0-9]{5}\Z")
DUMP_RE = re.compile(r"[0-9a-f]{24}(?:_[0-9]{1,2})?\.png\Z")
OWNED_HEADER = "# PSP Dock generated texture pack (PC only)"
INI_CONTENT = (
    OWNED_HEADER + "\n"
    "[options]\nversion = 1\nhash = xxh64\nignoreAddress = true\n\n"
    "[hashes]\n"
)
MAX_INPUT_BYTES = 24 * 1024 * 1024
MAX_OUTPUT_PIXELS = 16_777_216
MAX_SCAN_ASSET = 8 * 1024 * 1024
MAX_SCAN_ITEMS = 5000
MAX_EXTRACT_BYTES = 128 * 1024 * 1024
ENGINES = ("lanczos", "realesrgan")


class TextureGenerationError(ValueError):
    pass


def verified_disc_id(disc_id: str) -> str:
    if not isinstance(disc_id, str) or not DISC_ID_RE.fullmatch(disc_id):
        raise TextureGenerationError("DISC_ID del juego no válido.")
    return disc_id


def safe_pc_path(path: Path) -> Path:
    """Reject links/junctions throughout the path; PC storage is verified by Dock."""
    path = Path(path).absolute()
    for segment in (path, *path.parents):
        if segment.is_symlink() or getattr(segment, "is_junction", lambda: False)():
            raise TextureGenerationError("No se admiten enlaces en la carpeta de texturas.")
    return path


def pack_folder(memstick: Path, disc_id: str) -> Path:
    verified_disc_id(disc_id)
    return safe_pc_path(Path(memstick) / "PSP" / "TEXTURES" / disc_id)


def generator_owned(folder: Path) -> bool:
    ini = safe_pc_path(folder / "textures.ini")
    if not ini.is_file() or ini.stat().st_size > 8192:
        return False
    return ini.read_bytes().startswith(OWNED_HEADER.encode("ascii") + b"\n")


def prepare_pack(memstick: Path, disc_id: str) -> Path:
    """Never merge into an existing third-party pack, including a ZIP pack."""
    root = safe_pc_path(memstick)
    if not root.is_dir():
        raise TextureGenerationError("La Memory Stick privada del PC no existe.")
    folder = pack_folder(root, disc_id)
    if (folder / "textures.zip").exists():
        raise TextureGenerationError("Hay un pack comunitario ZIP: no se mezclará.")
    if (folder / "textures.ini").exists():
        if not generator_owned(folder):
            raise TextureGenerationError("textures.ini pertenece a otro pack; no se sobrescribirá.")
        return folder
    if folder.exists() and any(p.name != "new" for p in folder.iterdir()):
        raise TextureGenerationError("La carpeta ya contiene texturas ajenas al generador.")
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / "textures.ini").open("x", encoding="ascii", newline="\n") as out:
        out.write(INI_CONTENT)
        out.flush()
        os.fsync(out.fileno())
    return folder


def installed_status(memstick: Path, disc_id: str) -> dict:
    folder = pack_folder(memstick, disc_id)
    if (folder / "textures.zip").is_file():
        kind = "community"
    elif generator_owned(folder):
        kind = "generated"
    elif (folder / "textures.ini").is_file():
        kind = "external"
    else:
        kind = "none"
    if kind != "generated":
        return {"kind": kind, "generated": 0, "dumped": 0}
    return {
        "kind": kind,
        "generated": sum(1 for p in folder.glob("*.png") if DUMP_RE.fullmatch(p.name)),
        "dumped": sum(1 for p in (folder / "new").glob("*.png")
                      if DUMP_RE.fullmatch(p.name)) if (folder / "new").is_dir() else 0,
    }


def _pillow():
    try:
        from PIL import Image
        return Image
    except ImportError as exc:
        raise TextureGenerationError(
            "Para generar PNG en segundo plano instala Pillow en el Python del Dock."
        ) from exc


def _image_ok(path: Path, *, expected_scale: int | None = None,
              original_size: tuple[int, int] | None = None) -> tuple[int, int]:
    Image = _pillow()
    with Image.open(path) as image:
        width, height = image.size
        if (not 1 <= width <= 4096 or not 1 <= height <= 4096
                or width * height > MAX_OUTPUT_PIXELS):
            raise TextureGenerationError("Imagen de dimensiones no admitidas.")
        if image.format != "PNG":
            raise TextureGenerationError("PPSSPP debe guardar texturas PNG.")
        image.verify()
    if expected_scale is not None and (
            original_size is None or (width, height) !=
            (original_size[0] * expected_scale, original_size[1] * expected_scale)):
        raise TextureGenerationError("El escalador devolvió dimensiones inesperadas.")
    return width, height


@dataclass(frozen=True)
class TextureSettings:
    scale: int = 2
    engine: str = "lanczos"
    realesrgan_exe: str = ""

    def __post_init__(self):
        if (type(self.scale) is not int or self.scale not in (2, 4)
                or self.engine not in ENGINES):
            raise TextureGenerationError("Configuración del generador HD inválida.")
        if self.engine == "realesrgan":
            executable = safe_pc_path(Path(self.realesrgan_exe))
            if (executable.name.lower() != "realesrgan-ncnn-vulkan.exe"
                    or not executable.is_file()):
                raise TextureGenerationError(
                    "Real-ESRGAN no está instalado en una ruta local válida."
                )


def generate_one(memstick: Path, disc_id: str, source: Path,
                 settings: TextureSettings, *, stable_seconds: float = 2,
                 runner=subprocess.run) -> str:
    """Return generated/skipped/deferred, never overwrite a published texture."""
    folder = pack_folder(memstick, disc_id)
    if not generator_owned(folder) or (folder / "textures.zip").exists():
        raise TextureGenerationError("El pack generado ya no es el propietario.")
    source = safe_pc_path(source)
    if source.parent != safe_pc_path(folder / "new") or not DUMP_RE.fullmatch(source.name):
        raise TextureGenerationError("El archivo no corresponde al volcado de PPSSPP.")
    target = folder / source.name
    if target.exists():
        return "skipped"
    if not source.is_file() or source.stat().st_size > MAX_INPUT_BYTES:
        return "skipped"
    old = source.stat()
    if time.time() - old.st_mtime < stable_seconds:
        return "deferred"
    if shutil.disk_usage(folder).free < 96 * 1024 * 1024:
        raise TextureGenerationError("No hay espacio suficiente para texturas HD.")
    w, h = _image_ok(source)
    if w * h * settings.scale**2 > MAX_OUTPUT_PIXELS:
        return "skipped"
    tmp = folder / (".pspdock-" + uuid.uuid4().hex + ".png")
    try:
        if settings.engine == "lanczos":
            Image = _pillow()
            with Image.open(source) as image:
                output = image.resize(
                    (w * settings.scale, h * settings.scale), Image.Resampling.LANCZOS
                )
                output.save(tmp, format="PNG")
        else:
            exe = safe_pc_path(Path(settings.realesrgan_exe))
            if not exe.is_file() or exe.name.lower() != "realesrgan-ncnn-vulkan.exe":
                raise TextureGenerationError("Se retiró el escalador de IA configurado.")
            try:
                result = runner(
                    [str(exe), "-i", str(source), "-o", str(tmp),
                     "-s", str(settings.scale), "-f", "png",
                     "-n", "realesrgan-x4plus"],
                    cwd=str(exe.parent), capture_output=True, timeout=180,
                    creationflags=(0x08000000 | 0x00004000) if os.name == "nt" else 0,
                )
            except (OSError, subprocess.SubprocessError) as exc:
                raise TextureGenerationError("Real-ESRGAN falló o agotó el tiempo.") from exc
            if result.returncode != 0:
                raise TextureGenerationError("Real-ESRGAN no pudo procesar la textura.")
        _image_ok(tmp, expected_scale=settings.scale, original_size=(w, h))
        current = source.stat()
        if current.st_size != old.st_size or current.st_mtime_ns != old.st_mtime_ns:
            return "deferred"
        # A hard link fails if the output appeared concurrently; os.replace
        # would silently destroy an existing community or manually edited file.
        try:
            os.link(tmp, target)
        except FileExistsError:
            return "skipped"
        return "generated"
    finally:
        tmp.unlink(missing_ok=True)


def process_dump_batch(memstick: Path, disc_id: str,
                       settings: TextureSettings, *, limit: int = 2,
                       stable_seconds: float = 2,
                       ignore_unchanged: set | None = None) -> dict:
    folder = pack_folder(memstick, disc_id)
    if not generator_owned(folder):
        raise TextureGenerationError("Activa primero el generador HD de este juego.")
    raw = safe_pc_path(folder / "new")
    counts = {"generated": 0, "skipped": 0, "deferred": 0, "errors": 0}
    if not raw.is_dir():
        return counts
    for item in sorted(raw.iterdir(), key=lambda p: p.name):
        if sum(counts.values()) >= limit:
            break
        if not DUMP_RE.fullmatch(item.name):
            continue
        # Finished captures must not monopolize the first page of the queue
        # forever, otherwise no new file past the first two is ever upgraded.
        if (folder / item.name).is_file():
            continue
        try:
            revision = (item.name, item.stat().st_size, item.stat().st_mtime_ns)
        except OSError:
            counts["errors"] += 1
            continue
        if ignore_unchanged is not None and revision in ignore_unchanged:
            continue
        if item.is_symlink() or getattr(item, "is_junction", lambda: False)():
            counts["errors"] += 1
            if ignore_unchanged is not None:
                ignore_unchanged.add(revision)
            continue
        try:
            result = generate_one(memstick, disc_id, item, settings,
                                  stable_seconds=stable_seconds)
            counts[result] += 1
            if result == "skipped" and ignore_unchanged is not None:
                ignore_unchanged.add(revision)
        except (OSError, TextureGenerationError):
            counts["errors"] += 1
            if ignore_unchanged is not None:
                ignore_unchanged.add(revision)
    return counts


def discover_iso_assets(path: Path, cache: Path, *, disc_id: str,
                        max_items: int = MAX_SCAN_ITEMS) -> dict:
    """Read-only format-based inventory of files in ISO9660/CSO.

    Does not invent a PPSSPP hash. PNG/JPEG bytes remain on the private PC
    cache for later matching. Proprietary compression is detected using
    headers, never inferred from game names nor decoded speculatively.
    """
    from psp_texture_engine import identify
    verified_disc_id(disc_id)
    media = safe_pc_path(path)
    cache = safe_pc_path(cache) / disc_id
    if not media.is_file() or media.suffix.lower() not in (".iso", ".cso"):
        raise TextureGenerationError("La exploración requiere una ISO o CSO local.")
    if cache.exists() and any(cache.iterdir()):
        # Do not overwrite a previous scan or mix two game revisions.
        raise TextureGenerationError("Ya hay un inventario previo. Se conserva sin sobrescribir.")
    if type(max_items) is not int or not 1 <= max_items <= MAX_SCAN_ITEMS:
        raise TextureGenerationError("Límite de inventario no admitido.")
    cache.mkdir(parents=True, exist_ok=True)
    result = {"disc_id": disc_id, "source": "local_iso9660",
              "files_visited": 0, "candidates": [], "stored": 0,
              "engine_version": 1,
              "recognized_unsupported": 0,
              "format_profiles": {},
              "unmapped": True, "limited": False,
              "note": "Recursos sin hash PPSSPP; no son reemplazos listos para usar."}
    stream = _CSOReader(media) if media.suffix.lower() == ".cso" else media.open("rb")
    with stream:
        size = stream.seek(0, 2)
        stream.seek(16 * SECTOR)
        pvd = stream.read(SECTOR)
        if len(pvd) != SECTOR or pvd[:7] != b"\x01CD001\x01":
            raise TextureGenerationError("No es un volumen ISO9660 PSP reconocido.")
        root = _directory_record(pvd[156:190])
        if root is None or not root["directory"]:
            raise TextureGenerationError("Directorio raíz de ISO dañado.")
        queue = [("", root, 0)]
        dirs = 0
        while queue and result["files_visited"] < max_items and dirs < 1024:
            prefix, folder, depth = queue.pop(0)
            dirs += 1
            entries = _read_directory(stream, folder, size)
            for entry in entries:
                if result["files_visited"] >= max_items:
                    result["limited"] = True
                    break
                name = entry["name"]
                if name in ("", ".", "..", "\x00", "\x01") or "/" in name:
                    continue
                rel = prefix + "/" + name if prefix else name
                if entry["directory"]:
                    if depth < 10:
                        queue.append((rel, entry, depth + 1))
                    continue
                result["files_visited"] += 1
                extension = Path(name).suffix.lower()
                length = entry["size"]
                offset = entry["extent"] * SECTOR
                if not 0 < length <= 64*1024*1024 or offset <= 0 or offset + length > size:
                    continue
                stream.seek(offset)
                preview = stream.read(min(16,length))
                detection = identify(preview,extension=extension)
                if detection.family in ("dzip_unknown","marmalade_dzip"):
                    if detection.status != "recognized_unsupported":
                        result["limited"] = True
                        continue
                    result["recognized_unsupported"] += 1
                    result["format_profiles"][detection.family] = (
                        result["format_profiles"].get(detection.family,0)+1)
                    result["candidates"].append({
                        "path":rel, "format":detection.family,
                        "source_extension":extension,
                        "packed_bytes":length,
                        "unpacked_bytes_declared":detection.declared_size,
                        "variant_prefix_unverified":detection.variant_prefix,
                        "codec_status":detection.status,
                        "mapped":False, "decoded":False,
                    })
                    continue
                if extension not in (".png", ".jpg", ".jpeg", ".gim", ".tm2"):
                    continue
                if length > MAX_SCAN_ASSET:
                    continue
                if extension in (".gim", ".tm2"):
                    result["candidates"].append({
                        "path": rel, "format": extension.lstrip("."),
                        "mapped": False, "decoded": False,
                    })
                    continue
                if result["stored"] + length > MAX_EXTRACT_BYTES:
                    result["limited"] = True
                    continue
                stream.seek(offset)
                payload = stream.read(length)
                if len(payload) != length:
                    continue
                # Keep a signature check even for file extensions; unknown
                # archive items are not promoted as valid images.
                if extension == ".png":
                    valid = valid_png(payload)
                else:
                    valid = payload.startswith(b"\xff\xd8\xff") and payload.rstrip(b"\x00").endswith(b"\xff\xd9")
                if not valid:
                    continue
                sha = hashlib.sha256(payload).hexdigest()
                target = cache / (sha + extension)
                if not target.exists():
                    with target.open("xb") as dest:
                        dest.write(payload)
                result["stored"] += length
                result["candidates"].append({
                    "path": rel, "format": extension.lstrip("."),
                    "mapped": False, "decoded": True, "sha256": sha,
                })
    result["limited"] |= result["files_visited"] >= max_items or bool(queue)
    manifest = cache / "index.json"
    with manifest.open("x", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    return result


class TextureWorker:
    """One throttled daemon worker, never launches games or touches USB."""

    def __init__(self, memstick: Path):
        self.memstick = Path(memstick)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._disc = None
        self._until_idle = False
        self._snapshot = {"active": False, "generated": 0,
                          "errors": 0, "last_error": "", "disc_id": None}

    def status(self) -> dict:
        with self._lock:
            return dict(self._snapshot)

    def stop(self):
        self._stop.set()

    def start(self, disc_id: str, settings: TextureSettings, *,
              until_idle: bool = False):
        verified_disc_id(disc_id)
        with self._lock:
            if self._thread and self._thread.is_alive():
                if self._disc == disc_id and not self._stop.is_set():
                    self._until_idle = self._until_idle and until_idle
                    return
                raise TextureGenerationError("Hay otro generador HD terminando.")
            self._stop = threading.Event()
            self._disc = disc_id
            self._until_idle = until_idle
            self._snapshot = {"active": True, "generated": 0,
                              "errors": 0, "last_error": "", "disc_id": disc_id}
            self._thread = threading.Thread(
                target=self._run, args=(disc_id, settings, self._stop),
                daemon=True, name="PSPDockHDTextures")
            self._thread.start()

    def _run(self, disc_id, settings, stop):
        try:
            # Below-normal OS thread priority where available; leave PPSSPP
            # frame presentation and controller input as the priority.
            if os.name == "nt":
                import ctypes
                kernel = ctypes.WinDLL("kernel32")
                kernel.SetThreadPriority(kernel.GetCurrentThread(), -1)
            quiet_cycles = 0
            ignored_files = set()
            while not stop.is_set():
                try:
                    progress = process_dump_batch(
                        self.memstick, disc_id, settings, limit=2,
                        ignore_unchanged=ignored_files)
                    with self._lock:
                        self._snapshot["generated"] += progress["generated"]
                        self._snapshot["errors"] += progress["errors"]
                        if progress["errors"]:
                            self._snapshot["last_error"] = "Algunas imágenes no pudieron procesarse."
                        quiet_cycles = (0 if progress["generated"] or progress["deferred"]
                                        else quiet_cycles + 1)
                        if self._until_idle and quiet_cycles >= 2:
                            break
                except (OSError, TextureGenerationError) as exc:
                    with self._lock:
                        self._snapshot["errors"] += 1
                        self._snapshot["last_error"] = str(exc)[:200]
                    # An invalidated pack must never trigger repeated writes.
                    break
                stop.wait(2.0)
        finally:
            with self._lock:
                self._snapshot["active"] = False
