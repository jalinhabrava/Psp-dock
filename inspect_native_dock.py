"""One read-only observation of native PSP Dock, including unknown revisions.

This diagnostic never authorizes storage writes or phase commands. It keeps
NativeHid/probe_native unchanged: an observed revision is not a supported
revision. Win32 opens have desired access zero; the only HID transaction is
GET_FEATURE for a 33-byte descriptor. No background process is installed.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import ctypes as C
from ctypes import wintypes as W
from dataclasses import asdict
import datetime
import json
import os
from pathlib import Path
import subprocess

from bridge_hid import _WindowsHid, HIDP_CAPS, BridgeHidError
from native_dock import (PID, REVISION, RETIRED_REVISIONS, HID_PATH, USB_PARENT,
                         decode_status, hid_instance)
from psp_usb import identify


class InspectionError(ValueError):
    pass


class NativeReadOnlyInspector(_WindowsHid):
    """Inspect via base Win32 APIs without invoking the production open gate."""

    def __init__(self):
        super().__init__()
        self.expected_product, self.expected_path = PID, HID_PATH

    @contextmanager
    def open(self, path: str, write: bool = False):
        if write:
            raise InspectionError('El inspector no permite abrir para escritura.')
        if not isinstance(path, str) or not HID_PATH.match(path):
            raise InspectionError('La ruta no es HID 054C:01CD.')
        # Access 0 is sufficient for attributes, caps and HidD_GetFeature.
        # Do not use NativeHid.open, whose strict revision guard remains intact.
        handle = self.kernel.CreateFileW(path, 0, 3, None, 3, 0, None)
        if handle == C.c_void_p(-1).value:
            raise self._failure('Abrir inspector HID de sólo lectura')
        try:
            yield handle
        finally:
            self.kernel.CloseHandle(handle)

    def set(self, handle, report: bytes) -> None:
        raise InspectionError('El inspector nunca envía órdenes HID.')

    def inspect(self, path: str) -> dict:
        result = {'path': path, 'writes_authorized': False, 'can_change_phase': False,
                  'supported_revision': False, 'descriptor_matches': False,
                  'retired_revision': False, 'revision_status': 'unknown',
                  'pdk2_valid': False, 'errors': []}
        with self.open(path) as handle:
            attrs = self.attributes(handle)
            result.update(attrs)
            result['revision_hex'] = f"{attrs['revision']:04X}"
            result['supported_revision'] = attrs['revision'] == REVISION
            result['retired_revision'] = attrs['revision'] in RETIRED_REVISIONS
            result['revision_status'] = ('supported' if result['supported_revision'] else
                                         'retired' if result['retired_revision'] else 'unknown')
            data = C.c_void_p()
            if not self.hid.HidD_GetPreparsedData(handle, C.byref(data)):
                result['errors'].append(str(self._failure('Leer descriptor HID observado')))
                return result
            try:
                caps = HIDP_CAPS()
                if self.hid.HidP_GetCaps(data, C.byref(caps)) != 0x00110000:
                    result['errors'].append('HidP_GetCaps rechazó el descriptor observado.')
                    return result
                result['caps'] = {
                    'usage_page': int(caps.UsagePage), 'usage': int(caps.Usage),
                    'input_bytes': int(caps.InputReportByteLength),
                    'output_bytes': int(caps.OutputReportByteLength),
                    'feature_bytes': int(caps.FeatureReportByteLength),
                }
            finally:
                self.hid.HidD_FreePreparsedData(data)
            result['descriptor_matches'] = result['caps'] == {
                'usage_page': 1, 'usage': 4, 'input_bytes': 9,
                'output_bytes': 5, 'feature_bytes': 33,
            }
            if result['caps']['feature_bytes'] == 33:
                buffer = C.create_string_buffer(33)
                if self.hid.HidD_GetFeature(handle, buffer, len(buffer)):
                    result['feature_hex'] = buffer.raw.hex()
                    try:
                        result['status'] = asdict(decode_status(buffer.raw))
                        result['pdk2_valid'] = True
                    except ValueError as exc:
                        result['errors'].append(str(exc))
                else:
                    result['errors'].append(str(self._failure('Leer estado PDK2')))
            else:
                result['errors'].append('No se consulta PDK2: el informe Feature no mide 33 bytes.')
        return result


def windows_ancestry(instance: str) -> list[dict]:
    """Read PnP ancestry; never request removal, rebinding or state changes."""
    if os.name != 'nt':
        raise InspectionError('La ascendencia PnP requiere Windows.')
    cfg = C.WinDLL('cfgmgr32', use_last_error=True)
    for name, args in (
        ('CM_Locate_DevNodeW', [C.POINTER(W.DWORD), W.LPWSTR, W.ULONG]),
        ('CM_Get_Parent', [C.POINTER(W.DWORD), W.DWORD, W.ULONG]),
        ('CM_Get_Device_IDW', [W.DWORD, W.LPWSTR, W.ULONG, W.ULONG]),
    ):
        fn = getattr(cfg, name)
        fn.argtypes, fn.restype = args, W.DWORD
    current = W.DWORD()
    if cfg.CM_Locate_DevNodeW(C.byref(current), instance, 0):
        raise InspectionError('La instancia PnP ya no está presente.')
    chain, seen = [], set()
    for _ in range(16):
        if current.value in seen:
            raise InspectionError('Ciclo inesperado en la ascendencia PnP.')
        seen.add(current.value)
        name = C.create_unicode_buffer(1024)
        if cfg.CM_Get_Device_IDW(current, name, len(name), 0):
            raise InspectionError('No se puede leer la ascendencia PnP.')
        chain.append({'devinst': int(current.value), 'instance': name.value.upper()})
        parent = W.DWORD()
        if cfg.CM_Get_Parent(C.byref(parent), current, 0):
            return chain
        current = parent
    raise InspectionError('La ascendencia PnP supera el límite de observación.')


def _physical_parent(chain: list[dict]) -> dict | None:
    matches = [node for node in chain if USB_PARENT.fullmatch(node['instance'])]
    return dict(matches[0]) if len(matches) == 1 else None


def inspect_native(*, api_factory=NativeReadOnlyInspector, storage_roots=None,
                   identify_storage=identify, ancestry=windows_ancestry) -> dict:
    """A snapshot of observed HID + storage, with explicit nonauthorization."""
    result = {
        'schema': 'pspdock-native-observation-v1',
        'observed_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'read_only': True, 'writes_authorized': False, 'can_change_phase': False,
        'required_revision_hex': f'{REVISION:04X}', 'devices': [],
        'retired_revision_hex': [f'{revision:04X}' for revision in sorted(RETIRED_REVISIONS)],
        'storage_candidates': [], 'errors': [],
    }
    if api_factory is NativeReadOnlyInspector:
        if os.environ.get('PSP_DOCK_TEST_NO_REAL_USB') == '1':
            result['errors'].append('Observación USB real deshabilitada durante las pruebas.')
            return result
        if os.name != 'nt':
            result['errors'].append('La observación HID nativa requiere Windows.')
            return result
    try:
        api = api_factory()
        for path in api.paths():
            row = {'path': path, 'writes_authorized': False, 'can_change_phase': False,
                   'supported_revision': False, 'errors': []}
            try:
                row.update(api.inspect(path))
            except (ValueError, OSError, BridgeHidError) as exc:
                row['errors'].append(str(exc))
            try:
                row['pnp_instance'] = hid_instance(path)
                row['ancestry'] = ancestry(row['pnp_instance'])
                row['physical_usb_parent'] = _physical_parent(row['ancestry'])
            except (ValueError, OSError, BridgeHidError) as exc:
                row['errors'].append(str(exc))
            # Reassert these invariants even for injected diagnostic backends.
            row['writes_authorized'] = row['can_change_phase'] = False
            result['devices'].append(row)
    except (ValueError, OSError, BridgeHidError) as exc:
        result['errors'].append(str(exc))
    if storage_roots is None:
        from dock import windows_devices
        try:
            storage_roots = [device['root'] for device in windows_devices()]
        except (ValueError, OSError) as exc:
            result['errors'].append(str(exc))
            storage_roots = []
    for root in dict.fromkeys(storage_roots):
        row = {'drive': str(root), 'writes_authorized': False, 'binding_exact': False,
               'matches_hid_paths': [], 'errors': []}
        try:
            identity, _ = identify_storage(str(root))
            row['identity'] = asdict(identity)
            row['ancestry'] = ancestry(identity.disk_id)
            parent = _physical_parent(row['ancestry'])
            row['physical_usb_parent'] = parent
            if parent:
                row['matches_hid_paths'] = [device['path'] for device in result['devices']
                    if device.get('physical_usb_parent') == parent]
                row['binding_exact'] = len(row['matches_hid_paths']) == 1
        except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as exc:
            row['errors'].append(str(exc))
        result['storage_candidates'].append(row)
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='Observar PSP Dock nativo sin escribir ni cambiar de fase')
    parser.add_argument('--drive', action='append', help='Limitar candidatos a esta raíz PSP, por ejemplo G:\\')
    parser.add_argument('--output', type=Path, help='Guardar JSON nuevo en un disco fijo del PC')
    args = parser.parse_args(argv)
    try:
        destination = None
        if args.output is not None:
            from psp_composite_client import _new_local_destination
            destination = _new_local_destination(args.output)
        result = inspect_native(storage_roots=args.drive)
        content = json.dumps(result, ensure_ascii=False, indent=2) + '\n'
        if destination is not None:
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open('x', encoding='utf-8') as output:
                output.write(content)
                output.flush()
                os.fsync(output.fileno())
        print(content, end='')
        return 0 if result['devices'] else 2
    except (ValueError, OSError) as exc:
        print(json.dumps({'error': str(exc), 'writes_authorized': False}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
