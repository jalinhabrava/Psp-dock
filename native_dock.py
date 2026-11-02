"""Native Sony storage + PSP Dock HID, with a session-bound phase protocol.

PDK2 changes the product phase, not FAT ownership. Sony USBSTOR remains the
only PSP filesystem owner while the cable is connected. A write is allowed
only after binding a live PDK2 device to the exact Sony disk and volume via
their common physical USB parent. The legacy PDK1 guards are not weakened.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass
import ctypes as C
from ctypes import wintypes as W
import os
from pathlib import Path
import re
import struct
import threading
import time

from bridge_hid import _WindowsHid, HIDP_CAPS, BridgeHidError, VID
from psp_usb import PspStorage, identify, _volume_serial

PID = 0x01CD
REVISION = 0x0300
MAGIC, VERSION = b'PDK2', 2
STATUS, SET_PHASE = 1, 2
STORAGE, CONTROLLER, COPYING, EXIT_SYNC, SAFE, ERROR = 1, 2, 3, 4, 5, 255
CONNECTED, PENDING, SONY_STORAGE, EXIT_REQUESTED = 1, 2, 4, 8
FRAME = struct.Struct('<4sBBBB16sIi')
HID_PATH = re.compile(r'^\\\\\?\\hid#vid_054c&pid_01cd(?:&|#)', re.I)
USB_PARENT = re.compile(r'^USB\\VID_054C&PID_01CD\\[^\\]+$', re.I)


class NativeDockError(ValueError):
    pass


@dataclass(frozen=True)
class NativeStatus:
    session: str
    phase: int
    sequence: int
    result: int
    connected: bool
    pending: bool
    sony_storage: bool
    exit_requested: bool


def decode_status(report: bytes) -> NativeStatus:
    if not isinstance(report, bytes) or len(report) != 33 or report[0] != 0:
        raise NativeDockError('Informe PDK2 incompleto o con otro identificador.')
    magic, version, op, phase, flags, session, seq, result = FRAME.unpack(report[1:])
    if (magic, version, op) != (MAGIC, VERSION, STATUS):
        raise NativeDockError('El dispositivo no ofrece el protocolo PDK2 esperado.')
    if phase not in (0, STORAGE, CONTROLLER, COPYING, EXIT_SYNC, SAFE, ERROR):
        raise NativeDockError('Fase PDK2 desconocida.')
    if flags & ~15 or not any(session):
        raise NativeDockError('Sesión o indicadores PDK2 inválidos.')
    pending = bool(flags & PENDING)
    if ((pending and (seq == 0 or result != 1)) or
            (not pending and seq == 0 and result != 0) or
            (not pending and seq > 0 and result not in (2,) and result >= 0)):
        raise NativeDockError('Confirmación PDK2 incoherente.')
    return NativeStatus(session.hex(), phase, seq, result, bool(flags & CONNECTED),
                        pending, bool(flags & SONY_STORAGE), bool(flags & EXIT_REQUESTED))


def encode_phase(session: str, sequence: int, phase: int) -> bytes:
    try:
        nonce = bytes.fromhex(session)
    except (ValueError, TypeError) as exc:
        raise NativeDockError('Sesión PDK2 inválida.') from exc
    if len(nonce) != 16 or not any(nonce):
        raise NativeDockError('Sesión PDK2 vacía o de otra longitud.')
    if type(sequence) is not int or not 1 <= sequence <= 0xFFFFFFFF:
        raise NativeDockError('Secuencia PDK2 fuera de rango.')
    if type(phase) is not int or phase not in (STORAGE, CONTROLLER, COPYING, EXIT_SYNC, SAFE):
        raise NativeDockError('No se puede solicitar esta fase del Dock.')
    return b'\0' + FRAME.pack(MAGIC, VERSION, SET_PHASE, phase, 0, nonce, sequence, 0)


class NativeHid(_WindowsHid):
    """Reuse Win32 enumeration, with an independent PDK2 descriptor gate."""

    def __init__(self):
        # The base constructor only declares API signatures. Its PDK1 get,
        # set and write-open methods are deliberately not used here.
        super().__init__()
        self.expected_product, self.expected_path = PID, HID_PATH

    @contextmanager
    def open(self, path: str, write: bool = False):
        if not isinstance(path, str) or not HID_PATH.match(path):
            raise NativeDockError('La ruta no pertenece al Dock nativo 0300.')
        handle = self.kernel.CreateFileW(path, 0xC0000000 if write else 0,
                                          3, None, 3, 0, None)
        if handle == C.c_void_p(-1).value:
            raise NativeDockError(str(self._failure('Abrir Dock PDK2')))
        try:
            attrs = self.attributes(handle)
            if attrs['revision'] != REVISION:
                raise NativeDockError('Revisión nativa no verificada: ' + hex(attrs['revision']))
            data = C.c_void_p()
            if not self.hid.HidD_GetPreparsedData(handle, C.byref(data)):
                raise NativeDockError('No se puede verificar el descriptor PDK2.')
            try:
                caps = HIDP_CAPS()
                if (self.hid.HidP_GetCaps(data, C.byref(caps)) != 0x00110000 or
                    (caps.UsagePage, caps.Usage, caps.FeatureReportByteLength,
                     caps.InputReportByteLength, caps.OutputReportByteLength) != (1, 4, 33, 9, 5)):
                    raise NativeDockError('Descriptor del mando nativo distinto del esperado.')
            finally:
                self.hid.HidD_FreePreparsedData(data)
            yield handle
        finally:
            self.kernel.CloseHandle(handle)

    def get(self, handle) -> NativeStatus:
        buf = C.create_string_buffer(33)
        if not self.hid.HidD_GetFeature(handle, buf, len(buf)):
            raise NativeDockError(str(self._failure('Leer fase PDK2')))
        return decode_status(buf.raw)

    def set(self, handle, report: bytes) -> None:
        if len(report) != 33 or report[0] != 0 or report[1:5] != MAGIC:
            raise NativeDockError('Orden PDK2 inválida.')
        buf = C.create_string_buffer(report, len(report))
        if not self.hid.HidD_SetFeature(handle, buf, len(buf)):
            raise NativeDockError('Entrega de fase incierta; se debe comprobar el estado antes de reintentar.')


def probe_native(*, api_factory=NativeHid) -> dict | None:
    if os.environ.get('PSP_DOCK_TEST_NO_REAL_USB') == '1' and api_factory is NativeHid:
        return None
    if os.name != 'nt' and api_factory is NativeHid:
        return None
    api = api_factory()
    paths = api.paths()
    if not paths:
        return None
    if len(paths) != 1:
        raise NativeDockError('Hay más de un Dock nativo; no se elige una consola automáticamente.')
    try:
        with api.open(paths[0]) as handle:
            attrs, status = api.attributes(handle), api.get(handle)
    except (BridgeHidError, OSError) as exc:
        raise NativeDockError(str(exc)) from exc
    if (attrs.get('vendor_id'), attrs.get('product_id'), attrs.get('revision')) != (VID, PID, REVISION):
        raise NativeDockError('Identidad USB nativa no verificada.')
    if not status.connected or not status.sony_storage or status.phase in (0, ERROR) or status.result < 0:
        raise NativeDockError('El controlador nativo todavía no confirma almacenamiento Sony y conexión.')
    return {'path': paths[0], **attrs, 'status': asdict(status)}


def request_phase(path: str, session: str, phase: int, *, api_factory=NativeHid,
                  timeout: float = 2.0, clock=time.monotonic, pause=time.sleep) -> dict:
    """One SET_FEATURE, then observe the same handle/session until its ACK.

    A timeout is uncertainty, never permission to replay the request. A
    subsequent explicit action first reads the actual phase/sequence again.
    """
    encode_phase(session, 1, phase)  # validate caller values before opening HID
    api = api_factory()
    with api.open(path, write=True) as handle:
        before = api.get(handle)
        if before.session != session or not before.connected or not before.sony_storage:
            raise NativeDockError('La sesión de la PSP cambió antes de la orden.')
        if before.pending or before.result < 0 or before.phase == ERROR:
            raise NativeDockError('La PSP tiene una orden pendiente o un error sin resolver.')
        if before.phase == phase:
            return {'accepted': True, 'already_applied': True, 'phase': phase,
                    'session': session, 'sequence': before.sequence}
        sequence = before.sequence + 1
        report = encode_phase(session, sequence, phase)
        api.set(handle, report)
        deadline = clock() + max(0.1, min(float(timeout), 5.0))
        while clock() < deadline:
            status = api.get(handle)
            if status.session != session or not status.connected or not status.sony_storage:
                raise NativeDockError('La conexión cambió durante la confirmación de fase.')
            if status.sequence > sequence or status.result < 0:
                raise NativeDockError('La PSP devolvió una confirmación de otra orden o un error.')
            if status.sequence == sequence and not status.pending and status.result == 2:
                if status.phase != phase:
                    raise NativeDockError('La PSP confirmó una fase distinta.')
                return {'accepted': True, 'already_applied': False, 'phase': phase,
                        'session': session, 'sequence': sequence}
            pause(0.025)
    raise NativeDockError('La PSP no confirmó el cambio de fase; no se repite la orden automáticamente.')


def hid_instance(path: str) -> str:
    if not isinstance(path, str) or not HID_PATH.match(path):
        raise NativeDockError('Ruta HID nativa inválida.')
    fields = path[4:].split('#')
    if len(fields) != 4 or not fields[3].startswith('{'):
        raise NativeDockError('Ruta HID sin instancia PnP inequívoca.')
    return '\\'.join(fields[:3]).upper()


def composite_parent(instance: str) -> tuple[int, str]:
    """Walk past MI_00/MI_01 to the physical composite parent, never a hub."""
    if os.name != 'nt':
        raise NativeDockError('La asociación USB requiere Windows.')
    cfg = C.WinDLL('cfgmgr32', use_last_error=True)
    cfg.CM_Locate_DevNodeW.argtypes = [C.POINTER(W.DWORD), W.LPWSTR, W.ULONG]
    cfg.CM_Locate_DevNodeW.restype = W.DWORD
    cfg.CM_Get_Parent.argtypes = [C.POINTER(W.DWORD), W.DWORD, W.ULONG]
    cfg.CM_Get_Parent.restype = W.DWORD
    cfg.CM_Get_Device_IDW.argtypes = [W.DWORD, W.LPWSTR, W.ULONG, W.ULONG]
    cfg.CM_Get_Device_IDW.restype = W.DWORD
    current = W.DWORD()
    if cfg.CM_Locate_DevNodeW(C.byref(current), str(instance), 0):
        raise NativeDockError('La instancia USB ya no está presente.')
    for _ in range(12):
        name = C.create_unicode_buffer(1024)
        if cfg.CM_Get_Device_IDW(current, name, len(name), 0):
            raise NativeDockError('No se puede comprobar el padre del dispositivo.')
        if USB_PARENT.fullmatch(name.value):
            return int(current.value), name.value.upper()
        parent = W.DWORD()
        if cfg.CM_Get_Parent(C.byref(parent), current, 0):
            break
        current = parent
    raise NativeDockError('No hay un padre físico USB 054C:01CD verificable.')


def eject_bound_parent(identity: PspStorage, parent: tuple[int, str]) -> dict:
    """Request normal removal of exactly the already-bound composite node."""
    again, _ = identify(identity.drive)
    current = composite_parent(again.disk_id)
    if again != identity or current != parent:
        raise NativeDockError('La identidad cambió antes de finalizar; no se expulsa.')
    cfg = C.WinDLL('cfgmgr32', use_last_error=True)
    cfg.CM_Request_Device_EjectW.argtypes = [W.DWORD, C.POINTER(W.ULONG), W.LPWSTR,
                                            W.ULONG, W.ULONG]
    cfg.CM_Request_Device_EjectW.restype = W.DWORD
    veto, name = W.ULONG(), C.create_unicode_buffer(1024)
    code = int(cfg.CM_Request_Device_EjectW(parent[0], C.byref(veto), name, len(name), 0))
    return {'host_eject_accepted': code == 0, 'configret': code,
            'veto_type': int(veto.value), 'veto_name': name.value,
            'volume_serial': identity.volume_serial, 'usb_parent': parent[1]}


class NativeDockTransport:
    capabilities = {'automatic_transition': True, 'controller_during_storage': True,
                    'concurrent_copy_navigation': False, 'real_memory_stick': True}

    def __init__(self, *, probe=probe_native, identify_storage=identify,
                 parent_of=composite_parent, serial_of=_volume_serial,
                 change_phase=request_phase, eject=eject_bound_parent):
        self._probe, self._identify, self._parent = probe, identify_storage, parent_of
        self._serial, self._change_phase, self._eject = serial_of, change_phase, eject
        self._link = None
        self._storage = None
        self._parent_node = None
        self._commands = threading.RLock()
        self._finished = None

    @property
    def current(self):
        return dict(self._link) if self._link else None

    def _bind(self, device, roots, *, fresh=False):
        status = device['status']
        old = self._link
        if (not fresh and old and old['session_id'] == status['session'] and
                old['path'] == device['path'] and self._storage and
                any(str(d.get('root','')).rstrip('\\/').casefold() ==
                    self._storage.drive.rstrip('\\/').casefold() for d in roots) and
                self._serial(self._storage.drive) == old['volume_serial']):
            storage, parent = self._storage, self._parent_node
        else:
            hid_parent = self._parent(hid_instance(device['path']))
            matches = []
            for row in roots:
                drive = str(row.get('root', ''))
                try:
                    storage, _ = self._identify(drive)
                    parent = self._parent(storage.disk_id)
                except (ValueError, OSError, RuntimeError):
                    continue
                if parent[1] == hid_parent[1]:
                    matches.append((storage, parent))
            if len(matches) != 1:
                raise NativeDockError('Esperando una única Memory Stick Sony del mismo Dock USB.')
            storage, parent = matches[0]
        observation = {
            'session_id': status['session'], 'volume_serial': storage.volume_serial,
            'storage_mounted': True,
            'storage_ready': status['sony_storage'] and not status['pending'] and status['phase'] in (STORAGE, COPYING, EXIT_SYNC),
            'controller_ready': status['phase'] == CONTROLLER and not status['pending'],
            'safe_ready': status['phase'] == SAFE and not status['pending'],
            'controller_available': status['phase'] in (STORAGE, CONTROLLER) and not status['pending'],
            'phase': status['phase'], 'path': device['path'], 'product_id': device['product_id'],
            'revision': device['revision'], 'usb_parent': parent[1], 'root': storage.drive,
            'exit_requested': status['exit_requested'],
        }
        self._storage, self._parent_node, self._link = storage, parent, observation
        return dict(observation)

    def observe(self, devices: list[dict]) -> dict | None:
        try:
            device = self._probe()
            if device is None:
                self._link = None
                return None
            return self._bind(device, devices)
        except Exception:
            self._link = None
            raise

    def _fresh(self, root=None):
        old, storage = self._link, self._storage
        if old is None or storage is None:
            raise NativeDockError('No hay una sesión nativa verificada.')
        if root is not None and str(root).rstrip('\\/').casefold() != storage.drive.rstrip('\\/').casefold():
            raise NativeDockError('La operación apunta a otra Memory Stick.')
        device = self._probe()
        if (device is None or device['path'] != old['path'] or
                device['status']['session'] != old['session_id']):
            self._link = None
            raise NativeDockError('La sesión de la PSP cambió; operación cancelada.')
        result = self._bind(device, [{'root': storage.drive}], fresh=True)
        if result['volume_serial'] != old['volume_serial'] or result['usb_parent'] != old['usb_parent']:
            self._link = None
            raise NativeDockError('La Memory Stick o el padre USB cambiaron.')
        return result

    def assert_storage(self, root) -> PspStorage:
        current = self._fresh(root)
        if not current['storage_ready'] or current['phase'] not in (STORAGE, COPYING, EXIT_SYNC):
            raise NativeDockError('La escritura requiere una fase de sincronización confirmada.')
        return self._storage

    def assert_controller(self, expected_session=None) -> dict:
        current = self._fresh()
        if not current['controller_ready'] or (expected_session is not None and current['session_id'] != expected_session):
            raise NativeDockError('La PSP no está en la misma fase de mando confirmada.')
        return current

    def set_phase(self, phase: int) -> dict:
        with self._commands:
            current = self._fresh()
            result = self._change_phase(current['path'], current['session_id'], phase)
            # Read back over a new handle and bind the disk again; a successful
            # Win32 SET_FEATURE by itself never authorizes a write or launch.
            after = self._fresh()
            if not result.get('accepted') or after['phase'] != phase:
                raise NativeDockError('No se ha confirmado la fase solicitada.')
            return result

    def request_role(self, context: dict) -> dict:
        current = self._link
        identity = context.get('identity', {})
        if (not current or identity.get('session_id') != current['session_id'] or
                identity.get('volume_serial') != current['volume_serial']):
            raise NativeDockError('La orden no pertenece a esta sesión del Dock.')
        role, purpose = context.get('role'), context.get('purpose')
        if role == 'controller':
            phase = CONTROLLER
        elif role == 'storage':
            phase = EXIT_SYNC if purpose == 'exit' else COPYING if purpose == 'game_copy' else STORAGE
        else:
            raise NativeDockError('Papel del Dock desconocido.')
        return self.set_phase(phase)

    def controller_status(self) -> dict:
        current = self._link
        if not current:
            return {'connected': False, 'can_play': False, 'mode': 'await_native',
                    'synthetic_storage': False, 'reason': 'Esperando PSP Dock.'}
        return {'connected': current['controller_available'],
                'can_play': current['controller_ready'], 'mode': 'native_dock',
                'synthetic_storage': False, 'phase': current['phase'],
                'path': current['path'], 'session': current['session_id'],
                'product_id': PID, 'revision': current['revision'],
                'reason': 'PSP Dock conectado.' if current['controller_ready'] else 'Sincronizando PSP.'}

    def finish_session(self) -> dict:
        with self._commands:
            current = self._fresh()
            if current['phase'] not in (EXIT_SYNC, SAFE):
                raise NativeDockError('La salida requiere completar la sincronización de vuelta.')
            if current['phase'] != SAFE:
                self.set_phase(SAFE)
            storage, parent = self._storage, self._parent_node
            result = self._eject(storage, parent)
            if not result.get('host_eject_accepted'):
                raise NativeDockError('Windows mantiene la PSP en uso: ' + str(result.get('veto_name') or result.get('configret')))
            self._finished = {'verified': True, **result}
            self._link = None
            return dict(self._finished)
