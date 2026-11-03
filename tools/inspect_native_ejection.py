"""Read-only PnP tree and removal relations of one exactly bound native PSP.

No eject, disable, lock, dismount, USB command, or file-write API is called.
Run from the checkout on Windows while native PSP Dock is connected:
    python tools/inspect_native_ejection.py --drive G:\\
The optional --device selects an exact enumerated HID path or HID instance ID.
Without it the production probe requires a single unambiguous native PSP.

Microsoft documentation for the read-only Configuration Manager APIs:
https://learn.microsoft.com/en-us/windows/win32/api/cfgmgr32/nf-cfgmgr32-cm_get_device_id_listw
https://learn.microsoft.com/en-us/windows/win32/api/cfgmgr32/nf-cfgmgr32-cm_get_device_id_list_sizew
https://learn.microsoft.com/en-us/windows/win32/api/cfgmgr32/nf-cfgmgr32-cm_locate_devnodew
https://learn.microsoft.com/en-us/windows-hardware/drivers/kernel/irp-mn-query-device-relations

Relations do not prove that a storage-only eject would preserve HID. PnP also
removes children, which need not appear in EjectionRelations/RemovalRelations.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import ctypes as C
from ctypes import wintypes as W
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

# The script lives in tools; import only the existing read-only identity code.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from native_dock import NativeHid, composite_parent, hid_instance, probe_native
from psp_usb import identify


CR_SUCCESS, CR_NO_SUCH_DEVNODE, CR_BUFFER_SMALL = 0, 0x0D, 0x1A
# The SDK spells this EJECTRELATIONS (the conceptual page says Ejection).
RELATIONS = {'EjectionRelations':0x00000004,
             'RemovalRelations':0x00000008, 'BusRelations':0x00000020}
MAX_NODES, MAX_DEPTH, MAX_CHARS = 128, 16, 1024 * 1024


class InspectionError(ValueError):
    pass


def require_live_windows():
    if os.name != 'nt':
        raise InspectionError('Esta consulta PnP requiere Windows.')
    if os.environ.get('PSP_DOCK_TEST_NO_REAL_USB') == '1':
        raise InspectionError('Consulta USB real deshabilitada por PSP_DOCK_TEST_NO_REAL_USB=1.')


class ReadOnlyNativeHid(NativeHid):
    def __init__(self, device=None):
        super().__init__()
        self.selected_device = device

    def paths(self):
        paths = super().paths()
        if self.selected_device is None:
            return paths
        selected = self.selected_device.casefold()
        matches = [path for path in paths
                   if selected in (path.casefold(), hid_instance(path).casefold())]
        if len(matches) != 1:
            raise InspectionError('--device no identifica una única interfaz HID nativa presente.')
        return matches

    @contextmanager
    def open(self, path, write=False):
        if write:
            raise InspectionError('El inspector no abre HID para escritura.')
        with super().open(path, write=False) as handle:
            yield handle

    def set(self, handle, report):
        raise InspectionError('El inspector no envía órdenes HID.')


class PnpReader:
    """Only read APIs; NORMAL lookup never cancels a pending removal."""
    def __init__(self):
        require_live_windows()
        self.cfg = C.WinDLL('cfgmgr32', use_last_error=True)
        signatures = {
            'CM_Locate_DevNodeW':[C.POINTER(W.DWORD), W.LPWSTR, W.ULONG],
            'CM_Get_Child':[C.POINTER(W.DWORD), W.DWORD, W.ULONG],
            'CM_Get_Sibling':[C.POINTER(W.DWORD), W.DWORD, W.ULONG],
            'CM_Get_Device_ID_Size':[C.POINTER(W.ULONG), W.DWORD, W.ULONG],
            'CM_Get_Device_IDW':[W.DWORD, W.LPWSTR, W.ULONG, W.ULONG],
            'CM_Get_Device_ID_List_SizeW':[C.POINTER(W.ULONG), W.LPCWSTR, W.ULONG],
            'CM_Get_Device_ID_ListW':[W.LPCWSTR, W.LPWSTR, W.ULONG, W.ULONG],
        }
        for name, arguments in signatures.items():
            function = getattr(self.cfg, name)
            function.argtypes, function.restype = arguments, W.ULONG

    @staticmethod
    def check(code, operation):
        if code != CR_SUCCESS:
            raise InspectionError(f'{operation}: CONFIGRET=0x{code:08X}')

    def locate(self, instance):
        node = W.DWORD()
        # CM_LOCATE_DEVNODE_NORMAL=0: no phantom or CANCELREMOVE lookup.
        self.check(int(self.cfg.CM_Locate_DevNodeW(C.byref(node), instance, 0)),
                   'CM_Locate_DevNodeW')
        return int(node.value)

    def instance(self, node):
        for _ in range(4):
            size = W.ULONG()
            self.check(int(self.cfg.CM_Get_Device_ID_Size(C.byref(size), node, 0)),
                       'CM_Get_Device_ID_Size')
            if not 0 < size.value < MAX_CHARS:
                raise InspectionError('Tamaño de instancia PnP fuera de límite.')
            # Device_ID_Size excludes the NUL; List_Size below includes it.
            buffer = C.create_unicode_buffer(size.value + 1)
            code = int(self.cfg.CM_Get_Device_IDW(node, buffer, len(buffer), 0))
            if code == CR_BUFFER_SMALL:
                continue
            self.check(code, 'CM_Get_Device_IDW')
            return buffer.value.upper()
        raise InspectionError('La instancia PnP cambia durante la lectura.')

    def children(self, node):
        child = W.DWORD()
        code = int(self.cfg.CM_Get_Child(C.byref(child), node, 0))
        if code == CR_NO_SUCH_DEVNODE:
            return []
        self.check(code, 'CM_Get_Child')
        result, seen = [], set()
        while True:
            value = int(child.value)
            if value in seen or len(result) >= MAX_NODES:
                raise InspectionError('Ciclo o exceso de hijos en el árbol PnP.')
            seen.add(value)
            result.append(value)
            sibling = W.DWORD()
            code = int(self.cfg.CM_Get_Sibling(C.byref(sibling), child.value, 0))
            if code == CR_NO_SUCH_DEVNODE:
                return result
            self.check(code, 'CM_Get_Sibling')
            child = sibling

    def relations(self, instance, flag):
        # Do not add FILTER_PRESENT: it would hide non-present relations.
        # Preserve query errors distinctly from a successful empty MULTI_SZ.
        operation = 'CM_Get_Device_ID_List_SizeW'
        for _ in range(4):
            size = W.ULONG()
            operation = 'CM_Get_Device_ID_List_SizeW'
            code = int(self.cfg.CM_Get_Device_ID_List_SizeW(C.byref(size), instance, flag))
            if code == CR_BUFFER_SMALL:
                continue
            if code != CR_SUCCESS:
                break
            if not 0 < size.value <= MAX_CHARS:
                return {'configret':code, 'instances':None, 'error':'Tamaño MULTI_SZ inválido.'}
            buffer = C.create_unicode_buffer(size.value)
            operation = 'CM_Get_Device_ID_ListW'
            code = int(self.cfg.CM_Get_Device_ID_ListW(instance, buffer, len(buffer), flag))
            if code == CR_BUFFER_SMALL:
                continue
            if code != CR_SUCCESS:
                break
            # buffer.value would silently omit all but the first relation.
            raw = buffer[:]
            if not raw.endswith('\0'):
                return {'configret':code, 'instances':None, 'error':'MULTI_SZ sin terminador.'}
            content = raw.rstrip('\0')
            if content and not raw.endswith('\0\0'):
                return {'configret':code, 'instances':None, 'error':'MULTI_SZ sin doble terminador.'}
            instances = content.split('\0') if content else []
            if any(not item for item in instances):
                return {'configret':code, 'instances':None, 'error':'MULTI_SZ con cadena intermedia vacía.'}
            return {'configret':code, 'instances':[item.upper() for item in instances]}
        return {'configret':code, 'configret_hex':f'0x{code:08X}',
                'instances':None, 'error':operation}

    def tree(self, root):
        pending, nodes, seen = [(root, None, 0)], {}, set()
        while pending:
            node, parent, depth = pending.pop()
            if node in seen or len(nodes) >= MAX_NODES or depth >= MAX_DEPTH:
                raise InspectionError('Árbol PnP cíclico o fuera del límite de consulta.')
            seen.add(node)
            instance = self.instance(node)
            children = self.children(node)
            nodes[instance] = {'devinst':node, 'parent':parent, 'roles':[],
                              'relations':{name:self.relations(instance, flag)
                                           for name, flag in RELATIONS.items()}}
            pending.extend((child, instance, depth + 1) for child in reversed(children))
        return nodes


def drive_root(value):
    if not isinstance(value, str) or re.fullmatch(r'[A-Za-z]:[\\/]?', value) is None:
        raise InspectionError('--drive debe ser una raíz de unidad, por ejemplo G:\\')
    return value[:2].upper() + '\\'


def removable_roots():
    kernel = C.WinDLL('kernel32', use_last_error=True)
    kernel.GetLogicalDrives.argtypes, kernel.GetLogicalDrives.restype = [], W.DWORD
    kernel.GetDriveTypeW.argtypes, kernel.GetDriveTypeW.restype = [W.LPCWSTR], W.UINT
    mask = int(kernel.GetLogicalDrives())
    if not mask:
        raise InspectionError('GetLogicalDrives no devolvió unidades consultables.')
    return [chr(65 + bit) + ':\\' for bit in range(26)
            if mask & (1 << bit) and kernel.GetDriveTypeW(chr(65 + bit) + ':\\') == 2]


def status_view(device):
    status = device['status']
    return {key:status[key] for key in ('phase','sequence','result','pending','host_confirmed',
                                      'protocol_version','firmware_revision','capabilities','build_id')}


def inspect(*, drive=None, device=None):
    require_live_windows()
    started = time.perf_counter()
    reader = PnpReader()
    api_factory = lambda:ReadOnlyNativeHid(device)
    before = probe_native(api_factory=api_factory)
    if before is None:
        raise InspectionError('No hay una PSP Dock nativa inequívoca; USB del XMB no es esta identidad.')
    hid = hid_instance(before['path'])
    parent = composite_parent(hid)
    matches, rejected = [], []
    for root in [drive_root(drive)] if drive is not None else removable_roots():
        try:
            storage, _ = identify(root)
            storage_parent = composite_parent(storage.disk_id)
            interface_parent = composite_parent(storage.usb_device_id)
            if storage_parent != parent or interface_parent != parent:
                raise InspectionError('El disco o su interfaz no pertenecen al mismo padre HID.')
            matches.append(storage)
        except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as error:
            rejected.append({'drive':root, 'error':str(error)[:500]})
    if len(matches) != 1:
        raise InspectionError('No hay una única Memory Stick Sony vinculada a esta PSP: ' +
                              json.dumps(rejected, ensure_ascii=False, separators=(',', ':')))
    storage = matches[0]
    nodes = reader.tree(parent[0])
    roles = {'composite_parent':parent[1], 'hid':hid,
             'disk':storage.disk_id.upper(), 'storage_interface':storage.usb_device_id.upper()}
    for role, instance in roles.items():
        if instance not in nodes:
            raise InspectionError(f'El nodo {role} falta en el subárbol de la PSP vinculada.')
        nodes[instance]['roles'].append(role)
    # Reject removal/rebinding during the snapshot. A phase change alone is
    # permissible; this inspector sends no command and reports both statuses.
    after_storage, _ = identify(storage.drive)
    after = probe_native(api_factory=api_factory)
    if (after_storage != storage or after is None or after['path'] != before['path'] or
            after['status']['session'] != before['status']['session'] or
            status_view(after)['firmware_revision'] != status_view(before)['firmware_revision'] or
            composite_parent(hid) != parent or composite_parent(storage.disk_id) != parent):
        raise InspectionError('La identidad de la PSP cambió durante la consulta; repite la observación.')
    for instance, row in nodes.items():
        if reader.locate(instance) != row['devinst'] or reader.instance(row['devinst']) != instance:
            raise InspectionError('Cambió el árbol PnP durante la consulta.')
    complete = all(relation['instances'] is not None
                   for row in nodes.values() for relation in row['relations'].values())
    return {
        'schema':'pspdock-native-ejection-observation-v1',
        'observed_at':datetime.now(timezone.utc).isoformat(),
        'read_only':True, 'ok':complete, 'binding_verified':True,
        'eject_authorized':False, 'storage_only_eject_supported':None,
        'binding':{'drive':storage.drive, 'volume_serial':storage.volume_serial,
                   'hid_path':before['path'], **roles,
                   'session_fingerprint':hashlib.sha256(before['status']['session'].encode()).hexdigest()[:16]},
        'status_before':status_view(before), 'status_after':status_view(after),
        'relation_flags':{name:f'0x{flag:08X}' for name, flag in RELATIONS.items()},
        'nodes':nodes, 'rejected_storage_candidates':rejected,
        'elapsed_ms':round((time.perf_counter() - started) * 1000, 3),
        'errors':[] if complete else ['Hay consultas de relaciones fallidas; sus listas son null, no vacías.'],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description='Consultar árbol y relaciones de una PSP nativa sin expulsar ni escribir.')
    parser.add_argument('--drive', help='Memory Stick exacta; por ejemplo G:\\ (si se omite, exige coincidencia única).')
    parser.add_argument('--device', help='Ruta HID o ID de instancia HID nativa exactos, ya enumerados en Windows.')
    parser.add_argument('--pretty', action='store_true', help='Sangrar el JSON; por defecto es una sola línea.')
    args = parser.parse_args(argv)
    try:
        result = inspect(drive=args.drive, device=args.device)
    except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as error:
        result = {'schema':'pspdock-native-ejection-observation-v1', 'read_only':True,
                  'ok':False, 'binding_verified':False, 'eject_authorized':False,
                  'errors':[str(error)], 'error_type':type(error).__name__}
    print(json.dumps(result, ensure_ascii=True, indent=2 if args.pretty else None,
                     separators=None if args.pretty else (',', ':')))
    return 0 if result['ok'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
