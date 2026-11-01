"""Read-only client for the experimental PSP HID + WinUSB file controller.

There is no WRITE/SAVE/DELETE command. Receiving bytes into a new PC file
is permitted only after checking the device's EOF SHA-256, current session,
and (for the test probe) the independent build manifest.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import threading
import uuid

from psp_bulk_protocol import Frame, Op, MAX_PAYLOAD
from psp_winusb_probe import candidate_paths
from psp_winusb_transport import WinUSBTransport, WinUSBError

PROBE_PATH = "PSP/GAME/PSPDockCompositeTest/PROBE.DAT"
PROBE_SIZE = 150003
PROBE_SHA256 = "e2d2ed8fba5bff4fc1e3e2cf9fd4508a6e3fd8f2d8429d44196c6b7ec8ba001e"


def validate_remote_path(value: str, *, directory: bool = False) -> str:
    if (not isinstance(value, str) or not 1 <= len(value) <= 256 or
            any(ord(c) < 32 or ord(c) > 126 or c in ':\\"<>|?*' for c in value)):
        raise ValueError("Ruta PSP fuera del protocolo de solo lectura.")
    parts = value.split("/")
    if (not 1 <= len(parts) <= 12 or
            any(not p or p.startswith(".") or p.endswith((" ", ".")) for p in parts)):
        raise ValueError("Ruta PSP relativa no permitida.")
    if directory and value in ("ISO", "PSP/GAME", "PSP/SAVEDATA"):
        return value
    if parts[0] == "ISO" and len(parts) == 2 and not directory:
        return value
    if parts[:2] in (["PSP", "GAME"], ["PSP", "SAVEDATA"]):
        if len(parts) >= (3 if directory else 4):
            return value
    raise ValueError("Solo se permite leer juegos y guardados de la PSP.")


def _new_local_destination(path: Path) -> Path:
    original = Path(path)
    if not original.is_absolute():
        raise ValueError("El destino del PC debe ser una ruta absoluta.")
    for candidate in (original, *original.parents):
        if candidate.is_symlink() or getattr(candidate, "is_junction", lambda: False)():
            raise ValueError("El destino no puede contener enlaces de carpetas.")
    result = original.resolve()
    if result.exists():
        raise FileExistsError("No se sobrescribe un archivo existente del PC.")
    if os.name == "nt":
        import ctypes as C
        from ctypes import wintypes as W
        kernel = C.WinDLL("kernel32", use_last_error=True)
        kernel.GetDriveTypeW.argtypes = [W.LPCWSTR]
        kernel.GetDriveTypeW.restype = W.UINT
        if kernel.GetDriveTypeW(result.anchor) != 3:
            raise ValueError("El destino debe ser un disco fijo del PC, no la PSP.")
    return result


class CompositeReader:
    def __init__(self, transport: WinUSBTransport):
        self.transport = transport
        self.session = transport.firmware.session
        self.seq = 0
        self.failed = False
        self._lock = threading.RLock()
        if transport.firmware.commands != 0:
            raise WinUSBError("La sesión ya se utilizó: reinicia solo el cliente de prueba PSP.")
        ack = self._exchange(Op.HELLO, b"PDB2-READONLY")
        data = self._json(ack, Op.HELLO_ACK)
        if (data.get("protocol") != "PDB2-stream-v1" or
                data.get("read_only") is not True or
                data.get("max_chunk") != MAX_PAYLOAD or
                data.get("capabilities") != 7 or data.get("volume_serial") is not None):
            self.failed = True
            raise WinUSBError("Handshake desconocido: no aceptar acceso a archivos.")

    def _exchange(self, operation: Op, payload: bytes, offset: int = 0) -> Frame:
        if self.failed or self.seq >= 0xFFFFFFFE:
            raise WinUSBError("La sesión de lectura está interrumpida.")
        self.seq += 1
        try:
            reply = self.transport.exchange(Frame(operation, self.session, self.seq, payload, offset))
            if reply.op == Op.ERROR:
                raise WinUSBError("La PSP rechazó la lectura: " +
                                  reply.payload[:160].decode("ascii", "replace"))
            if reply.offset != offset:
                raise WinUSBError("Offset de respuesta distinto al solicitado.")
            return reply
        except (OSError, ValueError):
            self.failed = True
            raise

    @contextmanager
    def _operation(self):
        """Semantic errors/cancel also invalidate the stream, not just USB errors."""
        with self._lock:
            if self.failed:
                raise WinUSBError("La sesión requiere volver a conectar el cliente de prueba.")
            try:
                yield
            except BaseException:
                # The firmware may already have consumed the request even
                # when metadata validation or a progress callback fails.
                self.failed = True
                raise

    @staticmethod
    def _json(reply: Frame, operation: Op) -> dict:
        if reply.op != operation or len(reply.payload) > MAX_PAYLOAD:
            raise WinUSBError("Tipo de respuesta de archivos incorrecto.")
        try:
            data = json.loads(reply.payload)
        except (ValueError, UnicodeError) as exc:
            raise WinUSBError("Metadatos PSP ilegibles.") from exc
        if not isinstance(data, dict):
            raise WinUSBError("Objeto de metadatos esperado.")
        return data

    def list_directory(self, relative: str) -> list[dict]:
        validate_remote_path(relative, directory=True)
        with self._operation():
            offset = 0
            result = []
            names = set()
            for _ in range(2048):
                reply = self._exchange(Op.LIST, relative.encode("ascii"), offset)
                data = self._json(reply, Op.LIST_DATA)
                entries = data.get("entries")
                next_offset = data.get("next")
                if not isinstance(entries, list) or len(entries) > 64:
                    raise WinUSBError("Página de directorio PSP no válida.")
                for item in entries:
                    if (not isinstance(item, dict) or not isinstance(item.get("name"), str)
                            or type(item.get("directory")) is not bool
                            or type(item.get("size")) is not int
                            or not 0 <= item["size"] <= 0xFFFFFFFF):
                        raise WinUSBError("Entrada de directorio PSP no válida.")
                    name = item["name"]
                    if (not 1 <= len(name) <= 255 or name.startswith(".")
                            or name.endswith((" ", ".")) or
                            any(ord(c) < 32 or ord(c) > 126 or c in '/\\:"<>|?*' for c in name)
                            or name.casefold() in names):
                        raise WinUSBError("Nombre PSP inseguro o duplicado.")
                    names.add(name.casefold())
                    result.append(dict(item))
                if len(result) > 100000:
                    raise WinUSBError("Directorio demasiado grande.")
                if next_offset is None:
                    self.transport.check_session()
                    return result
                if type(next_offset) is not int or not offset < next_offset <= 100000:
                    raise WinUSBError("La paginación PSP no progresa.")
                offset = next_offset
            raise WinUSBError("Demasiadas páginas de directorio PSP.")

    def copy_to_pc(self, relative: str, destination: Path, *, expected_sha256: str | None = None,
                   expected_size: int | None = None, progress=None) -> dict:
        validate_remote_path(relative)
        if expected_sha256 is not None and not re.fullmatch('[0-9a-f]{64}', expected_sha256):
            raise ValueError("SHA-256 independiente no válido.")
        if expected_size is not None and (type(expected_size) is not int or expected_size < 0):
            raise ValueError("Tamaño esperado no válido.")
        target = _new_local_destination(destination)
        with self._operation():
            meta = self._json(self._exchange(Op.STAT, relative.encode('ascii')), Op.META)
            size = meta.get('size')
            if (type(size) is not int or not 0 <= size <= 0xFFFFFFFF or
                    meta.get('read_only') is not True or meta.get('hash_at_eof') is not True or
                    expected_size is not None and size != expected_size):
                raise WinUSBError("Tamaño o modo de archivo diferente al esperado.")
            target.parent.mkdir(parents=True, exist_ok=True)
            stage = target.with_name('.pspdock-composite-' + uuid.uuid4().hex + '.part')
            current = 0
            digest = hashlib.sha256()
            remote_digest = None
            try:
                with stage.open('xb') as out:
                    while current < size:
                        wanted = min(MAX_PAYLOAD, size - current)
                        command = struct.pack('<I', wanted) + relative.encode('ascii')
                        reply = self._exchange(Op.READ, command, current)
                        if reply.op != Op.DATA or not 0 < len(reply.payload) <= wanted:
                            raise WinUSBError("Bloque de archivo incompleto.")
                        out.write(reply.payload)
                        digest.update(reply.payload)
                        current += len(reply.payload)
                        if progress is not None:
                            progress(current, size)
                    # A separate EOF response carries the PSP's independent
                    # hash over the SAME streamed bytes, not a fictional proof.
                    eof = self._exchange(Op.READ, struct.pack('<I', MAX_PAYLOAD) +
                                         relative.encode('ascii'), current)
                    if eof.op != Op.EOF or len(eof.payload) != 32:
                        raise WinUSBError("Falta el SHA-256 final de la PSP.")
                    remote_digest = eof.payload.hex()
                    if digest.hexdigest() != remote_digest:
                        raise WinUSBError("No coincide el SHA-256 enviado por la PSP.")
                    if expected_sha256 is not None and remote_digest != expected_sha256:
                        raise WinUSBError("El archivo no coincide con el manifest independiente.")
                    out.flush()
                    os.fsync(out.fileno())
                self.transport.check_session()
                with stage.open('rb') as local:
                    # Python 3.10 on WSL has no hashlib.file_digest. Use the
                    # same bounded re-read on Windows and Linux test hosts.
                    stored = hashlib.sha256()
                    while block := local.read(1024 * 1024):
                        stored.update(block)
                    if stored.hexdigest() != remote_digest:
                        raise WinUSBError("El archivo escrito en el PC no coincide.")
                # Atomic no-overwrite publication; originals on PSP are untouched.
                os.link(stage, target)
            except (OSError, ValueError):
                self.failed = True
                raise
            finally:
                stage.unlink(missing_ok=True)
            return {'path': str(target), 'bytes': current, 'sha256': remote_digest,
                    'source': relative, 'psp_writes': False,
                    'usb_backend': 'winusb' if getattr(self.transport.backend, 'hardware', False) else 'test',
                    'fat_volume_identity_verified': False}


def main() -> int:
    parser = argparse.ArgumentParser(description='PSP Dock composite read-only test client')
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--status', action='store_true', help='Enumerate only, no USB transactions')
    group.add_argument('--read-probe', type=Path, metavar='NEW_PC_FILE',
                       help='Read ONLY the bundled synthetic test file, verify SHA256')
    args = parser.parse_args()
    try:
        paths = candidate_paths()
        if not args.read_probe:
            print(json.dumps({'candidate_interfaces': paths, 'driver_install_attempted': False,
                              'hardware_transfer_verified': False}, ensure_ascii=False, indent=2))
            return 0
        if len(paths) != 1:
            raise WinUSBError('Se requiere exactamente una interfaz PSP Dock Composite TEST.')
        with WinUSBTransport(paths[0]) as transport:
            reader = CompositeReader(transport)
            result = reader.copy_to_pc(PROBE_PATH, args.read_probe,
                                       expected_sha256=PROBE_SHA256, expected_size=PROBE_SIZE)
            print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError) as exc:
        print('Prueba interrumpida:', str(exc))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
