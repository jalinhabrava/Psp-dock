"""Verified, read-only-to-PSP backup before testing an ARK-5 VSH plugin.

The input PSP is identified independently through Sony USB PnP and volume
serial; the destination is always an independent PC folder. Source files are
opened in READ-ONLY mode and are never modified, renamed or deleted.

Default CRITICAL scope includes saves, plugin configuration and CFW data.
The optional FULL scope captures every readable file (except Windows-created
metadata), but still never performs any write on the PSP.

The backup is considered valid only if each file matches its source hash,
the source and copied file sizes remain stable, and the mounted Sony USB
identity matches at the beginning and after the copy.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import secrets
import shutil

from dock import ROOT, windows_devices
from tools.labs.lab_eject import ppsspp_open, real_dock_open
from psp_dock.devices.psp_usb import identify, PspStorage, UsbTransitionError


class BackupError(ValueError):
    pass


# Keep backups OUTSIDE the dated Codex project outputs directory, which may
# be cleaned up in a later development session.
DEFAULT_DEST = Path.home() / "Documents" / "PSP-Dock-Backups"
CRITICAL = ("PSP/SAVEDATA", "PSP/SYSTEM", "SEPLUGINS")
IGNORED_WINDOWS = {"system volume information", "$recycle.bin"}
RESERVE_BYTES = 64 * 1024 * 1024


def _safe_relative(path: str) -> str:
    """Never follow source paths, Windows shortcuts, reparse points or '..'."""
    norm = PurePosixPath(path.replace("\\", "/"))
    parts = norm.parts
    if not parts or any(part in ("", ".", "..") for part in parts):
        raise BackupError("Ruta relativa del backup no segura.")
    return norm.as_posix()


def _reparse(path: Path) -> bool:
    if path.is_symlink():
        return True
    stat = path.lstat()
    return bool(getattr(stat, "st_file_attributes", 0) & 0x400)


def selection(root: Path, scope: str) -> tuple[list[tuple[str, int]], list[str]]:
    """Enumerate stable source file list, never following reparse points."""
    if scope not in ("critical", "full"):
        raise BackupError("El alcance debe ser critical o full.")
    if not (root / "PSP/GAME").is_dir():
        raise BackupError("El dispositivo no tiene la estructura PSP esperada.")

    missing: list[str] = []
    files: list[tuple[str, int]] = []

    if scope == "full":
        top = [p for p in root.iterdir()
               if p.name.casefold() not in IGNORED_WINDOWS]
    else:
        top = [root / entry for entry in CRITICAL]
        # Some ARK loaders and configuration may live outside SAVEDATA.
        top.extend(p for p in root.glob("ARK*"))
        psp_games = root / "PSP/GAME"
        if psp_games.is_dir():
            top.extend(p for p in psp_games.glob("ARK*"))

    seen: set[str] = set()
    for item in sorted(top, key=lambda p: str(p).casefold()):
        rel_root = _safe_relative(item.relative_to(root).as_posix())
        if not item.exists():
            missing.append(rel_root)
            continue
        if _reparse(item):
            raise BackupError("Enlace o punto de reanálisis en origen: " + rel_root)
        if item.is_file():
            targets = [item]
        elif item.is_dir():
            targets = []
            for directory, dirs, names in os.walk(item, topdown=True, followlinks=False):
                current = Path(directory)
                for dirname in dirs:
                    if _reparse(current / dirname):
                        raise BackupError("Directorio no seguro en la Memory Stick.")
                for name in names:
                    file = current / name
                    if _reparse(file) or not file.is_file():
                        raise BackupError("Archivo no regular de Memory Stick: " + str(file))
                    targets.append(file)
                dirs.sort(key=str.casefold)
        else:
            raise BackupError("Entrada de Memory Stick desconocida: " + rel_root)

        for file in targets:
            rel = _safe_relative(file.relative_to(root).as_posix())
            if rel.casefold() in seen:
                continue
            seen.add(rel.casefold())
            files.append((rel, file.stat().st_size))
    files.sort(key=lambda pair: pair[0].casefold())
    return files, missing


def copy_verified(source: Path, target: Path, expected_size: int) -> dict:
    """Use only reads on PSP. Verify against both copied bytes and fresh source."""
    initial = source.stat()
    if not source.is_file() or _reparse(source) or initial.st_size != expected_size:
        raise BackupError("El archivo PSP cambió antes de copiarlo: " + str(source))
    target.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    with source.open("rb") as reader, target.open("xb") as writer:
        for chunk in iter(lambda: reader.read(1024 * 1024), b""):
            digest.update(chunk)
            writer.write(chunk)
        writer.flush()
        os.fsync(writer.fileno())
    after = source.stat()
    if (initial.st_size, initial.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise BackupError("Archivo PSP modificado durante la copia: " + str(source))
    if target.stat().st_size != expected_size:
        raise BackupError("La copia no tiene el tamaño esperado: " + str(target))
    expected = digest.hexdigest()
    with target.open("rb") as reader:
        copied_hash = hashlib.file_digest(reader, "sha256").hexdigest()
    with source.open("rb") as reader:
        current_hash = hashlib.file_digest(reader, "sha256").hexdigest()
    if copied_hash != expected or current_hash != expected:
        raise BackupError("La verificación SHA-256 del archivo ha fallado: " + str(source))
    return {"size": expected_size, "sha256": expected}


def _distinct_disks(source: Path, dest: Path) -> bool:
    """Refuse any destination located inside the PSP mount."""
    s = os.path.normcase(os.path.abspath(str(source)))
    d = os.path.normcase(os.path.abspath(str(dest)))
    try:
        return os.path.commonpath((s, d)) != s
    except ValueError:
        # Different Windows drives: cannot be the same folder tree.
        return True


def backup(
    source: Path,
    dest_root: Path,
    scope: str,
    volume_serial: str,
    usb_device_id: str,
    verify_identity=None,
    *,
    reserve_bytes: int = RESERVE_BYTES,
) -> dict:
    """Copy selected source files only to a new PC directory, fail closed."""
    if not _distinct_disks(source, dest_root):
        raise BackupError("El destino del backup no puede estar dentro de la PSP.")
    candidates, missing = selection(source, scope)
    size_bytes = sum(size for _, size in candidates)
    if not candidates:
        raise BackupError("No hay archivos para respaldar en la PSP.")
    dest_root.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(dest_root).free < size_bytes + reserve_bytes:
        raise BackupError("No hay espacio libre suficiente en el PC para el backup.")

    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = dest_root / (stamp + "-" + secrets.token_hex(4))
    backup_dir.mkdir(exist_ok=False)
    pending = backup_dir / "BACKUP-INCOMPLETE.txt"
    pending.write_text("BACKUP INCOMPLETO: NO USAR PARA RESTAURAR.\n",
                       encoding="utf-8")
    manifest = {
        "schema": "psp-dock-backup-1",
        "status": "incomplete",
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "scope": scope,
        "volume_serial": volume_serial,
        "usb_device_id": usb_device_id,
        "size_bytes": size_bytes,
        "file_count": len(candidates),
        "missing_optional": missing,
        "files": {},
    }
    try:
        for rel, expected_size in candidates:
            original = source.joinpath(*PurePosixPath(rel).parts)
            target = backup_dir / "data" / Path(*PurePosixPath(rel).parts)
            manifest["files"][rel] = copy_verified(original, target, expected_size)
        if verify_identity is not None and not verify_identity():
            raise BackupError("La identidad USB de la PSP cambió durante el backup.")
        # Detect new/removed files that would invalidate the snapshot.
        after, _ = selection(source, scope)
        if after != candidates:
            raise BackupError("La Memory Stick cambió durante el backup.")
        manifest["status"] = "verified"
        file = backup_dir / "manifest.json"
        with file.open("x", encoding="utf-8") as output:
            json.dump(manifest, output, ensure_ascii=False, indent=2, sort_keys=True)
            output.flush()
            os.fsync(output.fileno())
        pending.unlink()
        return {"directory": str(backup_dir), "manifest": str(file),
                "scope": scope, "file_count": len(candidates),
                "size_bytes": size_bytes, "status": "verified"}
    except Exception:
        # Keep incomplete file and any copied data for manual recovery.
        # NEVER delete or retry a possibly interrupted PSP USB transfer.
        raise


def verify_archive(snapshot: Path) -> dict:
    """Re-verify the PC backup alone, even when the PSP has disconnected."""
    manifest_path = snapshot / "manifest.json"
    if not manifest_path.is_file() or (snapshot / "BACKUP-INCOMPLETE.txt").exists():
        raise BackupError("El backup no está marcado como verificado.")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeError) as exc:
        raise BackupError("Manifiesto del backup inválido.") from exc
    if (manifest.get("schema") != "psp-dock-backup-1" or
            manifest.get("status") != "verified" or
            not isinstance(manifest.get("files"), dict)):
        raise BackupError("Manifiesto no reconocido o incompleto.")
    expected_files = manifest["files"]
    if manifest.get("file_count") != len(expected_files):
        raise BackupError("El número de archivos no coincide con el manifiesto.")
    total = 0
    for rel, entry in expected_files.items():
        if _safe_relative(rel) != rel or not isinstance(entry, dict):
            raise BackupError("Ruta o registro del manifiesto inválido.")
        file = snapshot / "data" / Path(*PurePosixPath(rel).parts)
        if not file.is_file() or _reparse(file):
            raise BackupError("Falta un archivo del respaldo: " + rel)
        length, digest_expected = entry.get("size"), entry.get("sha256")
        if (type(length) is not int or length < 0 or
                not isinstance(digest_expected, str) or
                len(digest_expected) != 64):
            raise BackupError("Metadatos SHA/tamaño no válidos en " + rel)
        with file.open("rb") as input_file:
            if hashlib.file_digest(input_file, "sha256").hexdigest() != digest_expected:
                raise BackupError("Archivo del backup modificado o corrupto: " + rel)
        if file.stat().st_size != length:
            raise BackupError("Tamaño de backup inesperado: " + rel)
        total += length
    if total != manifest.get("size_bytes"):
        raise BackupError("Los bytes totales no coinciden con el manifiesto.")
    # Check for unmanifested data as well; they may be a partial copy.
    data_dir = snapshot / "data"
    actual, _ = selection_for_archive(data_dir)
    if set(actual) != set(expected_files):
        raise BackupError("El respaldo contiene archivos no registrados o ausentes.")
    return {"directory": str(snapshot), "file_count": len(expected_files),
            "size_bytes": total, "status": "verified"}


def selection_for_archive(root: Path) -> tuple[list[str], None]:
    """Enumerate a backup directory without PSP layout constraints."""
    if not root.is_dir():
        raise BackupError("La carpeta de datos del backup no existe.")
    files: list[str] = []
    for folder, dirs, names in os.walk(root, topdown=True, followlinks=False):
        current = Path(folder)
        for name in dirs:
            if _reparse(current / name):
                raise BackupError("Backup con un enlace de directorio no permitido.")
        for name in names:
            file = current / name
            if not file.is_file() or _reparse(file):
                raise BackupError("Backup con un archivo no permitido.")
            files.append(_safe_relative(file.relative_to(root).as_posix()))
    return files, None


def main() -> int:
    parser = argparse.ArgumentParser(description="Copia verificada PSP -> PC")
    parser.add_argument("--scope", choices=("critical", "full"), default="critical")
    parser.add_argument("--run", action="store_true",
                        help="Solicita confirmación y copia solamente AL PC")
    parser.add_argument("--verify-latest", action="store_true",
                        help="Revisa último backup del PC, sin necesidad de PSP conectada")
    args = parser.parse_args()
    try:
        if args.verify_latest:
            # Recovery tool stores its own backup folder under the same PC
            # parent. It is NOT a PSP snapshot. Select only directories with
            # a complete manifest, newest by name, not any arbitrary folder.
            snapshots = sorted(
                (p for p in DEFAULT_DEST.iterdir()
                 if p.is_dir() and p.name[:8].isdigit() and
                 (p / "manifest.json").is_file()),
                key=lambda p: p.name,
                reverse=True
            ) if DEFAULT_DEST.is_dir() else []
            if not snapshots:
                raise BackupError("No hay copias de seguridad en el PC.")
            print("REVALIDACIÓN DEL PC:", json.dumps(verify_archive(snapshots[0]),
                                                    ensure_ascii=False))
            return 0
        if ppsspp_open() or real_dock_open():
            raise BackupError("Cierra PPSSPP y PSP Dock real antes de copiar.")
        devices = windows_devices()
        if len(devices) != 1:
            raise BackupError("Conecta UNA PSP Sony por USB nativo del XMB.")
        root = Path(devices[0]["root"])
        identity, _ = identify(str(root))
        candidates, missing = selection(root, args.scope)
        if not candidates:
            raise BackupError("No hay archivos para copiar.")
        total = sum(size for _, size in candidates)
        if not _distinct_disks(root, DEFAULT_DEST):
            raise BackupError("El destino PC se encuentra sobre la Memory Stick.")
        print("PSP:", identity.model, identity.drive)
        print("Identidad de volumen:", identity.volume_serial)
        print("Tipo:", args.scope, "Archivos:", len(candidates),
              "Bytes:", total)
        print("Destino en PC:", DEFAULT_DEST)
        print("Los archivos de la PSP se ABREN SOLO PARA LECTURA.")
        if missing:
            print("Rutas opcionales ausentes:", len(missing))
        if not args.run:
            print("SOLO VISTA PREVIA: nada ha sido copiado.")
            return 0
        if input("Escribe BACKUP PSP para confirmar copia SOLO AL PC: ").strip() != "BACKUP PSP":
            print("Cancelado, no se copiaron datos.")
            return 0
        if identify(str(root))[0] != identity:
            raise BackupError("La PSP cambió antes de comenzar la copia.")

        def same_device():
            return identify(str(root))[0] == identity

        result = backup(root, DEFAULT_DEST, args.scope,
                        identity.volume_serial, identity.usb_device_id,
                        verify_identity=same_device)
        print("BACKUP VERIFICADO:", json.dumps(result, ensure_ascii=False))
        print("La PSP NO ha recibido escrituras, ni cambios de plugins o firmware.")
        return 0
    except (BackupError, ValueError, OSError, UsbTransitionError) as exc:
        print("BACKUP NO VERIFICADO:", exc)
        print("Si existe BACKUP-INCOMPLETE.txt, conservarlo y NO restaurar desde él.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
