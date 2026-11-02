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
import subprocess
import threading
import time

from bridge_hid import _WindowsHid, HIDP_CAPS, BridgeHidError, VID
from psp_usb import PspStorage, identify, _volume_serial

PID = 0x01CD
REVISION = 0x0302  # Firmware version, never inferred from Sony bcdDevice.
RETIRED_REVISIONS = frozenset({0x0300, 0x0301})
USB_REVISIONS = frozenset({0x0100, 0x0302})
MAGIC, VERSION = b'PDK2', 3
STATUS, SET_PHASE, HOST_BEGIN, HOST_PROGRESS = 1, 2, 3, 4
STORAGE, CONTROLLER, COPYING, EXIT_SYNC, SAFE, ERROR = 1, 2, 3, 4, 5, 255
CONNECTED, PENDING, SONY_STORAGE, EXIT_REQUESTED, HOST_CONFIRMED = 1, 2, 4, 8, 16
FRAME = struct.Struct('<4sBBBB16sIi')
EXTENSION = struct.Struct('<4sHHIBBHQQ')
EXT_MAGIC, CAPABILITIES, BUILD_ID = b'NAT3', 0x0007, 0x03020001
REPORT_BYTES = 65
WAITING, INVENTORY, CHOOSING, SYNC_IN, COPY_GAME, CONTROL_ACTIVITY, SYNC_OUT, SAFE_ACTIVITY = range(8)
UNKNOWN, BYTES, FILES = 0, 1, 2
PHASE_ACTIVITIES = {STORAGE: (INVENTORY, CHOOSING, SYNC_IN),
                    CONTROLLER: (CONTROL_ACTIVITY,), COPYING: (COPY_GAME,),
                    EXIT_SYNC: (SYNC_OUT,), SAFE: (SAFE_ACTIVITY,)}
HID_PATH = re.compile(r'^\\\\\?\\hid#vid_054c&pid_01cd(?:&|#)', re.I)
USB_PARENT = re.compile(r'^USB\\VID_054C&PID_01CD\\[^\\]+$', re.I)


class NativeDockError(ValueError):
    def __init__(self, diagnostics, *, message='No se pudo verificar la conexión con PSP Dock.',
                 hint='Revisa la conexión y vuelve a abrir PSP Dock en la consola.',
                 connection_phase='blocked'):
        super().__init__(diagnostics)
        self.message, self.hint = message, hint
        self.connection_phase = connection_phase


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
    host_confirmed: bool = False
    protocol_version: int = VERSION
    firmware_revision: int = REVISION
    capabilities: int = CAPABILITIES
    build_id: int = BUILD_ID
    activity: int = WAITING
    unit: int = UNKNOWN
    completed: int = 0
    total: int = 0


def _progress_valid(phase, activity, unit, completed, total, *, command=False):
    if type(activity) is not int or activity not in PHASE_ACTIVITIES.get(phase, ()):
        raise NativeDockError('Actividad de progreso incompatible con la fase confirmada.')
    if any(type(value) is not int for value in (unit, completed, total)):
        raise NativeDockError('Contadores de progreso inválidos.')
    if unit == UNKNOWN:
        if completed or total:
            raise NativeDockError('El progreso sin total conocido no admite porcentajes.')
    elif unit not in (BYTES, FILES) or not 0 <= completed <= total <= 0xffffffffffffffff or total == 0:
        raise NativeDockError('El progreso medido no tiene un total real válido.')


def _validate_status_identity(status):
    if (status.protocol_version, status.firmware_revision, status.capabilities, status.build_id) != (
            VERSION, REVISION, CAPABILITIES, BUILD_ID):
        raise NativeDockError('Protocolo, firmware, capacidades o compilación nativa no verificados.',
                              message='Esta versión de PSP Dock necesita actualizarse.',
                              hint='Instala la versión de PSP Dock incluida con esta app de Windows.')


def decode_status(report: bytes) -> NativeStatus:
    if not isinstance(report, bytes) or len(report) != REPORT_BYTES or report[0] != 0:
        raise NativeDockError('Informe PDK2 incompleto o con otro identificador.')
    magic, version, op, phase, flags, session, seq, result = FRAME.unpack(report[1:33])
    if (magic, version, op) != (MAGIC, VERSION, STATUS):
        raise NativeDockError('El dispositivo no ofrece el protocolo PDK2 esperado.')
    if phase not in (0, STORAGE, CONTROLLER, COPYING, EXIT_SYNC, SAFE, ERROR):
        raise NativeDockError('Fase PDK2 desconocida.')
    if flags & ~31 or not any(session):
        raise NativeDockError('Sesión o indicadores PDK2 inválidos.')
    pending = bool(flags & PENDING)
    if ((pending and (seq == 0 or result != 1)) or
            (not pending and seq == 0 and result != 0) or
            (not pending and seq > 0 and result not in (2,) and result >= 0)):
        raise NativeDockError('Confirmación PDK2 incoherente.')
    ext_magic, firmware, capabilities, build, activity, unit, reserved, done, total = EXTENSION.unpack(report[33:])
    if ext_magic != EXT_MAGIC or reserved:
        raise NativeDockError('Extensión de identidad nativa inválida.')
    status = NativeStatus(session.hex(), phase, seq, result, bool(flags & CONNECTED),
                          pending, bool(flags & SONY_STORAGE), bool(flags & EXIT_REQUESTED),
                          bool(flags & HOST_CONFIRMED), version, firmware, capabilities, build,
                          activity, unit, done, total)
    _validate_status_identity(status)
    if status.host_confirmed:
        if phase != ERROR:
            _progress_valid(phase, activity, unit, done, total)
    elif (activity, unit, done, total) != (WAITING, UNKNOWN, 0, 0):
        raise NativeDockError('La PSP publica progreso antes de confirmar Windows.')
    return status


def encode_command(session: str, sequence: int, phase: int, op: int, *,
                   activity=WAITING, unit=UNKNOWN, completed=0, total=0) -> bytes:
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
    if type(op) is not int or op not in (SET_PHASE, HOST_BEGIN, HOST_PROGRESS):
        raise NativeDockError('Operación de PSP Dock desconocida.')
    if op == HOST_BEGIN and phase != STORAGE:
        raise NativeDockError('El inicio de sesión requiere almacenamiento de entrada.')
    if op == HOST_PROGRESS:
        _progress_valid(phase, activity, unit, completed, total, command=True)
    elif (activity, unit, completed, total) != (WAITING, UNKNOWN, 0, 0):
        raise NativeDockError('La orden no admite progreso adjunto.')
    return (b'\0' + FRAME.pack(MAGIC, VERSION, op, phase, 0, nonce, sequence, 0) +
            EXTENSION.pack(EXT_MAGIC, REVISION, CAPABILITIES, BUILD_ID,
                           activity, unit, 0, completed, total))


def encode_phase(session: str, sequence: int, phase: int) -> bytes:
    return encode_command(session, sequence, phase, SET_PHASE)


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
            raise NativeDockError('La ruta no pertenece al dispositivo nativo verificado.')
        handle = self.kernel.CreateFileW(path, 0xC0000000 if write else 0,
                                          3, None, 3, 0, None)
        if handle == C.c_void_p(-1).value:
            raise NativeDockError(str(self._failure('Abrir Dock PDK2')))
        try:
            attrs = self.attributes(handle)
            if (attrs.get('vendor_id'), attrs.get('product_id')) != (VID, PID):
                raise NativeDockError('Identidad USB nativa no verificada.')
            if attrs.get('revision') not in USB_REVISIONS:
                raise NativeDockError('bcdDevice USB no verificado: ' + hex(attrs.get('revision', 0)))
            data = C.c_void_p()
            if not self.hid.HidD_GetPreparsedData(handle, C.byref(data)):
                raise NativeDockError('No se puede verificar el descriptor PDK2.')
            try:
                caps = HIDP_CAPS()
                if (self.hid.HidP_GetCaps(data, C.byref(caps)) != 0x00110000 or
                    (caps.UsagePage, caps.Usage, caps.FeatureReportByteLength,
                     caps.InputReportByteLength, caps.OutputReportByteLength) != (1, 4, REPORT_BYTES, 9, 5)):
                    raise NativeDockError('Descriptor del mando nativo distinto del esperado.',
                                          message='Esta versión de PSP Dock necesita actualizarse.',
                                          hint='Instala la versión de PSP Dock incluida con esta app de Windows.')
            finally:
                self.hid.HidD_FreePreparsedData(data)
            yield handle
        finally:
            self.kernel.CloseHandle(handle)

    def get(self, handle) -> NativeStatus:
        buf = C.create_string_buffer(REPORT_BYTES)
        if not self.hid.HidD_GetFeature(handle, buf, len(buf)):
            raise NativeDockError(str(self._failure('Leer fase PDK2')))
        return decode_status(buf.raw)

    def set(self, handle, report: bytes) -> None:
        if len(report) != REPORT_BYTES or report[0] != 0 or report[1:5] != MAGIC:
            raise NativeDockError('Orden PDK2 inválida.')
        buf = C.create_string_buffer(report, len(report))
        if not self.hid.HidD_SetFeature(handle, buf, len(buf)):
            raise NativeDockError('Entrega de fase incierta; se debe comprobar el estado antes de reintentar.')


def probe_native(*, api_factory=NativeHid) -> dict | None:
    if os.environ.get('PSP_DOCK_TEST_NO_REAL_USB') == '1' and api_factory is NativeHid:
        return None
    if os.name != 'nt' and api_factory is NativeHid:
        return None
    try:
        api = api_factory()
        paths = api.paths()
        if not paths:
            return None
        if len(paths) != 1:
            raise NativeDockError('Hay más de un Dock nativo; no se elige una consola automáticamente.',
                                  message='Hay más de una PSP conectada.', hint='Deja conectada sólo la PSP que quieras utilizar.')
        with api.open(paths[0]) as handle:
            attrs, status = api.attributes(handle), api.get(handle)
    except NativeDockError:
        raise
    except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
        raise NativeDockError(str(exc)) from exc
    if ((attrs.get('vendor_id'), attrs.get('product_id')) != (VID, PID) or
            attrs.get('revision') not in USB_REVISIONS):
        raise NativeDockError('Identidad USB nativa no verificada.')
    _validate_status_identity(status)
    if not status.connected or not status.sony_storage or status.phase in (0, ERROR) or status.result < 0:
        raise NativeDockError('El controlador nativo todavía no confirma almacenamiento Sony y conexión.')
    if not status.host_confirmed and status.phase != STORAGE:
        raise NativeDockError('El firmware ofrece una fase activa sin haber confirmado Windows.')
    return {'path': paths[0], **attrs, 'usb_revision': attrs['revision'],
            'protocol_version': status.protocol_version, 'firmware_revision': status.firmware_revision,
            'status': asdict(status)}


def request_command(path: str, session: str, phase: int, op: int, *,
                    activity=WAITING, unit=UNKNOWN, completed=0, total=0,
                    api_factory=NativeHid, timeout: float = 2.0,
                    clock=time.monotonic, pause=time.sleep) -> dict:
    """One SET_FEATURE, then observe the same handle/session until its ACK.

    A timeout is uncertainty, never permission to replay the request. A
    subsequent explicit action first reads the actual phase/sequence again.
    """
    fields = {'activity': activity, 'unit': unit, 'completed': completed, 'total': total}
    encode_command(session, 1, phase, op, **fields)  # Validate before opening HID.
    api = api_factory()
    with api.open(path, write=True) as handle:
        before = api.get(handle)
        _validate_status_identity(before)
        if before.session != session or not before.connected or not before.sony_storage:
            raise NativeDockError('La sesión de la PSP cambió antes de la orden.')
        if before.pending or before.result < 0 or before.phase == ERROR:
            raise NativeDockError('La PSP tiene una orden pendiente o un error sin resolver.')
        if op != HOST_BEGIN and not before.host_confirmed:
            raise NativeDockError('Windows todavía no ha confirmado esta sesión de PSP Dock.')
        if op == HOST_BEGIN and before.phase != STORAGE:
            raise NativeDockError('La entrada no está en almacenamiento; no se reclama otra sesión.')
        if op == HOST_PROGRESS and before.phase != phase:
            raise NativeDockError('El progreso pertenece a otra fase de la PSP.')
        already = ((op == SET_PHASE and before.phase == phase) or
                   (op == HOST_BEGIN and before.host_confirmed) or
                   (op == HOST_PROGRESS and (before.activity, before.unit, before.completed, before.total) ==
                    (activity, unit, completed, total)))
        if already:
            return {'accepted': True, 'already_applied': True, 'phase': phase,
                    'session': session, 'sequence': before.sequence, 'operation': op,
                    'exit_requested': before.exit_requested}
        sequence = before.sequence + 1
        report = encode_command(session, sequence, phase, op, **fields)
        api.set(handle, report)
        deadline = clock() + max(0.1, min(float(timeout), 5.0))
        while clock() < deadline:
            status = api.get(handle)
            _validate_status_identity(status)
            if status.session != session or not status.connected or not status.sony_storage:
                raise NativeDockError('La conexión cambió durante la confirmación de fase.')
            if status.sequence > sequence or status.result < 0:
                raise NativeDockError('La PSP devolvió una confirmación de otra orden o un error.')
            if status.sequence == sequence and not status.pending and status.result == 2:
                if status.phase != phase or not status.host_confirmed:
                    raise NativeDockError('La PSP no confirmó la misma fase y sesión de Windows.')
                if op == HOST_PROGRESS and (status.activity, status.unit, status.completed, status.total) != (
                        activity, unit, completed, total):
                    raise NativeDockError('La PSP confirmó otro progreso.')
                return {'accepted': True, 'already_applied': False, 'phase': phase,
                        'session': session, 'sequence': sequence, 'operation': op,
                        'exit_requested': status.exit_requested}
            pause(0.025)
    raise NativeDockError('La PSP no confirmó el cambio de fase; no se repite la orden automáticamente.')


def request_phase(path: str, session: str, phase: int, **options) -> dict:
    return request_command(path, session, phase, SET_PHASE, **options)


def request_begin(path: str, session: str, **options) -> dict:
    return request_command(path, session, STORAGE, HOST_BEGIN, **options)


def request_progress(path: str, session: str, phase: int, activity: int, *,
                     unit=UNKNOWN, completed=0, total=0, **options) -> dict:
    return request_command(path, session, phase, HOST_PROGRESS, activity=activity,
                           unit=unit, completed=completed, total=total, **options)


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
                 change_phase=request_phase, begin=request_begin,
                 progress=request_progress, eject=eject_bound_parent):
        self._probe, self._identify, self._parent = probe, identify_storage, parent_of
        self._serial, self._change_phase, self._eject = serial_of, change_phase, eject
        self._begin, self._progress = begin, progress
        self._link = None
        self._storage = None
        self._parent_node = None
        self._commands = threading.RLock()
        self._finished = None
        self._connection = self._connection_value('waiting', 'Conecta tu PSP y abre PSP Dock en la consola.')

    @staticmethod
    def _connection_value(phase, message, *, hint='', diagnostics='', retryable=False):
        return {'phase': phase, 'message': message, 'hint': hint,
                'diagnostics': diagnostics, 'retryable': retryable}

    def connection_status(self):
        """Cached presentation facts only; never enumerate or claim USB here."""
        return dict(self._connection)

    def connection_error(self, error):
        self._connection = self._connection_value(
            getattr(error, 'connection_phase', 'blocked'),
            getattr(error, 'message', 'No se pudo comprobar la conexión con PSP Dock.'),
            hint=getattr(error, 'hint', 'Revisa la conexión; el Dock volverá a comprobarla.'),
            diagnostics=str(error)[:2000], retryable=True)

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
            matches, failures = [], []
            for row in roots:
                drive = str(row.get('root', ''))
                try:
                    storage, _ = self._identify(drive)
                    parent = self._parent(storage.disk_id)
                except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as exc:
                    failures.append(f'{drive}: {exc}')
                    continue
                if parent[1] == hid_parent[1]:
                    matches.append((storage, parent))
                else:
                    failures.append(f'{drive}: el padre del disco no coincide con el mando.')
            if len(matches) != 1:
                detail = ('No hay una única Memory Stick Sony vinculada al mismo padre USB.' +
                          (' ' + ' | '.join(failures) if failures else ' Windows no ha publicado una unidad verificable.'))
                raise NativeDockError(detail, connection_phase='waiting_storage',
                                      message='PSP encontrada. Esperando su Memory Stick.',
                                      hint='Mantén la PSP conectada mientras Windows prepara el almacenamiento.')
            storage, parent = matches[0]
        observation = {
            'session_id': status['session'], 'volume_serial': storage.volume_serial,
            'storage_mounted': True,
            'storage_ready': status['sony_storage'] and not status['pending'] and status['phase'] in (STORAGE, COPYING, EXIT_SYNC),
            'controller_ready': status['phase'] == CONTROLLER and not status['pending'] and status['host_confirmed'],
            'safe_ready': status['phase'] == SAFE and not status['pending'],
            'controller_available': (status['phase'] == CONTROLLER or
                                     status['phase'] == STORAGE and status['activity'] == CHOOSING)
                                    and not status['pending'] and status['host_confirmed'],
            'phase': status['phase'], 'path': device['path'], 'product_id': device['product_id'],
            'revision': device['revision'], 'usb_revision': device['revision'],
            'protocol_version': status['protocol_version'], 'firmware_revision': status['firmware_revision'],
            'capabilities': status['capabilities'], 'build_id': status['build_id'],
            'host_confirmed': status['host_confirmed'],
            'activity': status['activity'], 'unit': status['unit'],
            'completed': status['completed'], 'total': status['total'],
            'usb_parent': parent[1], 'root': storage.drive,
            'exit_requested': status['exit_requested'],
        }
        self._storage, self._parent_node, self._link = storage, parent, observation
        return dict(observation)

    def observe(self, devices: list[dict]) -> dict | None:
        try:
            self._connection = self._connection_value('identifying', 'Buscando PSP Dock.')
            device = self._probe()
            if device is None:
                self._link = None
                self._connection = self._connection_value('waiting', 'Conecta tu PSP y abre PSP Dock en la consola.')
                return None
            self._connection = self._connection_value('waiting_storage', 'PSP encontrada. Comprobando su Memory Stick.')
            result = self._bind(device, devices)
            self._connection = self._connection_value('verified', 'PSP Dock conectado.')
            return result
        except Exception as exc:
            self._link = None
            self.connection_error(exc)
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
        if (not current['host_confirmed'] or not current['storage_ready'] or
                current['phase'] not in (STORAGE, COPYING, EXIT_SYNC)):
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
            if not current['host_confirmed']:
                raise NativeDockError('Windows no ha confirmado esta sesión antes del cambio de fase.')
            result = self._change_phase(current['path'], current['session_id'], phase)
            # Read back over a new handle and bind the disk again; a successful
            # Win32 SET_FEATURE by itself never authorizes a write or launch.
            after = self._fresh()
            if not result.get('accepted') or after['phase'] != phase or not after['host_confirmed']:
                raise NativeDockError('No se ha confirmado la fase solicitada.')
            return result

    def _assert_context(self, context):
        current, identity = self._link, context.get('identity', {})
        if (not current or identity.get('session_id') != current['session_id'] or
                identity.get('volume_serial') != current['volume_serial']):
            raise NativeDockError('La orden no pertenece a esta sesión del Dock.')
        return current

    def begin_session(self, context):
        """Explicit claim, called only after the worker journals its intent."""
        with self._commands:
            self._assert_context(context)
            current = self._fresh()
            if current['phase'] != STORAGE:
                raise NativeDockError('La conexión no está preparada para iniciar una sesión nueva.')
            result = self._begin(current['path'], current['session_id'])
            after = self._fresh()
            if not result.get('accepted') or not after['host_confirmed']:
                raise NativeDockError('La PSP no ha confirmado la conexión con Windows.')
            return result

    def set_progress(self, context, activity, *, unit=UNKNOWN, completed=0, total=0):
        """Display-only command; it cannot grant file or controller permission."""
        with self._commands:
            current = self._assert_context(context)
            if not current['host_confirmed']:
                raise NativeDockError('El progreso requiere una sesión ya confirmada.')
            # The wire method rechecks nonce/phase and ACK on the same HID.
            # No disk rescan is needed for display metadata; actual file
            # operations still use assert_storage and revalidate the disk.
            result = self._progress(current['path'], current['session_id'], current['phase'], activity,
                                    unit=unit, completed=completed, total=total)
            if result.get('accepted') is True:
                self._link = {**self._link, 'activity': activity, 'unit': unit,
                              'completed': completed, 'total': total,
                              'exit_requested': current['exit_requested'] or result.get('exit_requested') is True,
                              'controller_available': current['host_confirmed'] and (
                                  current['phase'] == CONTROLLER or
                                  current['phase'] == STORAGE and activity == CHOOSING)}
            return result

    def request_role(self, context: dict) -> dict:
        self._assert_context(context)
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
                'usb_revision': current['usb_revision'], 'protocol_version': current['protocol_version'],
                'firmware_revision': current['firmware_revision'], 'host_confirmed': current['host_confirmed'],
                'reason': ('Esperando la confirmación de Windows.' if not current['host_confirmed'] else
                           'PSP Dock conectado.' if current['controller_ready'] else
                           'Elige qué quieres sincronizar.' if current['activity'] == CHOOSING else 'Sincronizando PSP.')}

    def finish_session(self, *, cancel=False) -> dict:
        with self._commands:
            current = self._fresh()
            if cancel and current['phase'] in (STORAGE, CONTROLLER):
                # Cancelling entry is an eject path, never a save callback.
                self.set_phase(EXIT_SYNC)
                current = self._link
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
