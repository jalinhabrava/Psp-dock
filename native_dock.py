"""Native Sony storage + PSP Dock HID, with a session-bound phase protocol.

PDK2 changes the product phase, not FAT ownership. Sony USBSTOR remains the
only PSP filesystem owner while the cable is connected. A write is allowed
only after binding a live PDK2 device to the exact Sony disk and volume via
their common physical USB parent. The legacy PDK1 guards are not weakened.
"""
from __future__ import annotations

from contextlib import contextmanager, nullcontext
from dataclasses import asdict, dataclass
import ctypes as C
from ctypes import wintypes as W
import os
from pathlib import Path
import re
import secrets
import binascii
import struct
import subprocess
import threading
import time

from bridge_hid import _WindowsHid, HIDP_CAPS, BridgeHidError, VID
from psp_usb import PspStorage, identify, _volume_serial

PID = 0x01CD
REVISION = 0x0307  # Firmware version, never inferred from Sony bcdDevice.
FIRMWARE_BUILDS = {0x0303: 0x03030001, 0x0304: 0x03040001,
                   0x0305: 0x03050001, 0x0306: 0x03060001, 0x0307: 0x03070001}
FIRMWARE_CAPABILITIES = {0x0303: 0x0007, 0x0304: 0x0007, 0x0305: 0x000F, 0x0306: 0x000F, 0x0307: 0x000F}
CAP_HOST_SHUTDOWN = 8
RETIRED_REVISIONS = frozenset({0x0300, 0x0301, 0x0302})
USB_REVISIONS = frozenset({0x0100, 0x0303, 0x0304, 0x0305, 0x0306, 0x0307})
MAGIC, VERSION = b'PDK2', 3
STATUS, SET_PHASE, HOST_BEGIN, HOST_PROGRESS = 1, 2, 3, 4
HOST_SHUTDOWN = 5
STORAGE, CONTROLLER, COPYING, EXIT_SYNC, SAFE, ERROR = 1, 2, 3, 4, 5, 255
CONNECTED, PENDING, SONY_STORAGE, EXIT_REQUESTED, HOST_CONFIRMED = 1, 2, 4, 8, 16
SHUTDOWN_REQUESTED = 32
FRAME = struct.Struct('<4sBBBB16sIi')
EXTENSION = struct.Struct('<4sHHIBBHQQ')
EXT_MAGIC, CAPABILITIES, BUILD_ID = b'NAT3', FIRMWARE_CAPABILITIES[REVISION], FIRMWARE_BUILDS[REVISION]
CORE_REPORT_BYTES = 65  # Windows report ID byte + unchanged 64-byte PDK2 core.
REPORT_BYTES = CORE_REPORT_BYTES
FIRMWARE_REPORT_BYTES = {revision: CORE_REPORT_BYTES for revision in FIRMWARE_BUILDS}
FRAGMENT_REVISIONS = frozenset((0x0306, 0x0307))
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
                 connection_phase='blocked', details=None):
        super().__init__(diagnostics)
        self.message, self.hint = message, hint
        self.connection_phase = connection_phase
        self.details = dict(details or {})


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
    shutdown_requested: bool = False
    transport_word: int = 0


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


def _validate_identity(protocol_version, firmware_revision, capabilities, build_id):
    identity = (protocol_version, firmware_revision, capabilities, build_id)
    if (any(type(value) is not int for value in identity) or
            protocol_version != VERSION or capabilities != FIRMWARE_CAPABILITIES.get(firmware_revision) or
            FIRMWARE_BUILDS.get(firmware_revision) != build_id):
        raise NativeDockError('Protocolo, firmware, capacidades o compilación nativa no verificados.',
                              message='Esta versión de PSP Dock necesita actualizarse.',
                              hint='Instala la versión de PSP Dock incluida con esta app de Windows.')
    return identity


def _validate_status_identity(status):
    return _validate_identity(status.protocol_version, status.firmware_revision,
                              status.capabilities, status.build_id)


def decode_status(report: bytes) -> NativeStatus:
    if (not isinstance(report, bytes) or len(report) not in (CORE_REPORT_BYTES, REPORT_BYTES)
            or report[0] != 0):
        raise NativeDockError('Informe PDK2 incompleto o con otro identificador.')
    magic, version, op, phase, flags, session, seq, result = FRAME.unpack(report[1:33])
    if (magic, version, op) != (MAGIC, VERSION, STATUS):
        raise NativeDockError('El dispositivo no ofrece el protocolo PDK2 esperado.')
    if phase not in (0, STORAGE, CONTROLLER, COPYING, EXIT_SYNC, SAFE, ERROR):
        raise NativeDockError('Fase PDK2 desconocida.')
    if flags & ~63 or not any(session):
        raise NativeDockError('Sesión o indicadores PDK2 inválidos.')
    pending = bool(flags & PENDING)
    if ((pending and (seq == 0 or result != 1)) or
            (not pending and seq == 0 and result != 0) or
            (not pending and seq > 0 and result not in (2,) and result >= 0)):
        raise NativeDockError('Confirmación PDK2 incoherente.')
    ext_magic, firmware, capabilities, build, activity, unit, reserved, done, total = EXTENSION.unpack(report[33:65])
    if ext_magic != EXT_MAGIC or (reserved and firmware not in FRAGMENT_REVISIONS):
        raise NativeDockError('Extensión de identidad nativa inválida.')
    if firmware in FRAGMENT_REVISIONS and (reserved & 15) > 9:
        raise NativeDockError('Diagnóstico de transporte nativo inválido.')
    status = NativeStatus(session.hex(), phase, seq, result, bool(flags & CONNECTED),
                          pending, bool(flags & SONY_STORAGE), bool(flags & EXIT_REQUESTED),
                          bool(flags & HOST_CONFIRMED), version, firmware, capabilities, build,
                          activity, unit, done, total, bool(flags & SHUTDOWN_REQUESTED), reserved)
    _validate_status_identity(status)
    if len(report) != FIRMWARE_REPORT_BYTES[firmware]:
        raise NativeDockError('El tamaño USB no corresponde al firmware verificado.')
    if status.shutdown_requested and (not status.capabilities & CAP_HOST_SHUTDOWN or
            status.pending or status.phase != SAFE or status.sequence == 0 or status.result != 2):
        raise NativeDockError('Confirmación terminal de PSP Dock incoherente.')
    if status.host_confirmed:
        if phase != ERROR:
            _progress_valid(phase, activity, unit, done, total)
    elif (activity, unit, done, total) != (WAITING, UNKNOWN, 0, 0):
        raise NativeDockError('La PSP publica progreso antes de confirmar Windows.')
    return status


def encode_command(session: str, sequence: int, phase: int, op: int, *,
                   activity=WAITING, unit=UNKNOWN, completed=0, total=0,
                   firmware_revision=REVISION, build_id=BUILD_ID) -> bytes:
    capabilities = FIRMWARE_CAPABILITIES.get(firmware_revision)
    _validate_identity(VERSION, firmware_revision, capabilities, build_id)
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
    if type(op) is not int or op not in (SET_PHASE, HOST_BEGIN, HOST_PROGRESS, HOST_SHUTDOWN):
        raise NativeDockError('Operación de PSP Dock desconocida.')
    if op == HOST_BEGIN and phase != STORAGE:
        raise NativeDockError('El inicio de sesión requiere almacenamiento de entrada.')
    if op == HOST_SHUTDOWN and (phase != SAFE or not capabilities & CAP_HOST_SHUTDOWN):
        raise NativeDockError('Esta identidad no permite el cierre confirmado con cable conectado.')
    if op == HOST_PROGRESS:
        _progress_valid(phase, activity, unit, completed, total, command=True)
    elif (activity, unit, completed, total) != (WAITING, UNKNOWN, 0, 0):
        raise NativeDockError('La orden no admite progreso adjunto.')
    core = (b'\0' + FRAME.pack(MAGIC, VERSION, op, phase, 0, nonce, sequence, 0) +
            EXTENSION.pack(EXT_MAGIC, firmware_revision, capabilities, build_id,
                           activity, unit, 0, completed, total))
    return core


def encode_phase(session: str, sequence: int, phase: int) -> bytes:
    return encode_command(session, sequence, phase, SET_PHASE)


class NativeHid(_WindowsHid):
    """Reuse Win32 enumeration, with an independent PDK2 descriptor gate."""

    def __init__(self):
        # The base constructor only declares API signatures. Its PDK1 get,
        # set and write-open methods are deliberately not used here.
        super().__init__()
        self.expected_product, self.expected_path = PID, HID_PATH
        self._report_lengths = {}
        self.hid.HidD_SetOutputReport.argtypes = [W.HANDLE, C.c_void_p, W.ULONG]
        self.hid.HidD_SetOutputReport.restype = W.BOOLEAN

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
                    caps.FeatureReportByteLength not in (CORE_REPORT_BYTES, REPORT_BYTES) or
                    (caps.UsagePage, caps.Usage,
                     caps.InputReportByteLength, caps.OutputReportByteLength) != (1, 4, 9, 5)):
                    raise NativeDockError('Descriptor del mando nativo distinto del esperado.',
                                          message='Esta versión de PSP Dock necesita actualizarse.',
                                          hint='Instala la versión de PSP Dock incluida con esta app de Windows.')
                if not hasattr(self, '_report_lengths'):
                    self._report_lengths = {}
                self._report_lengths[handle] = int(caps.FeatureReportByteLength)
            finally:
                self.hid.HidD_FreePreparsedData(data)
            yield handle
        finally:
            getattr(self, '_report_lengths', {}).pop(handle, None)
            self.kernel.CloseHandle(handle)

    def get(self, handle) -> NativeStatus:
        length = getattr(self, '_report_lengths', {}).get(handle)
        if length not in (CORE_REPORT_BYTES, REPORT_BYTES):
            raise NativeDockError('El handle HID no conserva un descriptor de tamaño verificado.')
        buf = C.create_string_buffer(length)
        if not self.hid.HidD_GetFeature(handle, buf, len(buf)):
            raise NativeDockError(str(self._failure('Leer fase PDK2')))
        return decode_status(buf.raw)

    def set(self, handle, report: bytes) -> None:
        if (not isinstance(report, bytes) or len(report) not in (CORE_REPORT_BYTES, REPORT_BYTES) or
                len(report) != getattr(self, '_report_lengths', {}).get(handle) or
                report[0] != 0 or report[1:5] != MAGIC):
            raise NativeDockError('Orden PDK2 inválida.')
        extension = EXTENSION.unpack(report[33:65])
        _validate_identity(report[5], extension[1], extension[2], extension[3])
        if len(report) != FIRMWARE_REPORT_BYTES[extension[1]]:
            raise NativeDockError('La orden no usa el transporte exacto de esta versión.')
        _, _, operation, phase, flags, session, sequence, result = FRAME.unpack(report[1:33])
        if (extension[0] != EXT_MAGIC or extension[6] or flags or result or
                report != encode_command(session.hex(), sequence, phase, operation,
                    activity=extension[4], unit=extension[5], completed=extension[7], total=extension[8],
                    firmware_revision=extension[1], build_id=extension[3])):
            raise NativeDockError('La orden no conserva el formato completo del protocolo.')
        if extension[1] in FRAGMENT_REVISIONS:
            token = secrets.randbelow(256)
            payload = report[1:]
            for index in range(32):
                chunk = bytes((0, 0xc0 | index, token)) + payload[index*2:index*2+2]
                buf = C.create_string_buffer(chunk, 5)
                if not self.hid.HidD_SetOutputReport(handle, buf, 5):
                    raise NativeDockError(
                        f'Entrega de orden incompleta en fragmento {index + 1}/32; '
                        'se comprobará su estado antes de cualquier reintento.')
            checksum = binascii.crc_hqx(payload, 0xffff)
            commit = bytes((0, 0xe0, token, checksum & 255, checksum >> 8))
            buf = C.create_string_buffer(commit, 5)
            if not self.hid.HidD_SetOutputReport(handle, buf, 5):
                raise NativeDockError('Confirmación de entrega incierta; se comprobará el estado de la orden.')
            return
        buf = C.create_string_buffer(report, len(report))
        if not self.hid.HidD_SetFeature(handle, buf, len(buf)):
            raise NativeDockError('Entrega de fase incierta; se debe comprobar el estado antes de reintentar.')

    def transport_diagnostic(self, handle, status: NativeStatus) -> dict:
        """Read RAM diagnostics in 0306's two previously reserved status bytes.

        Windows exposes GetFeature, not GetOutputReport. Keeping this on the
        already working Feature GET avoids changing the controller input or
        any descriptor length. Command reserved bytes must still be zero.
        """
        _validate_status_identity(status)
        if status.firmware_revision not in FRAGMENT_REVISIONS:
            return {}
        current = self.get(handle)
        if (current.session != status.session or
                _validate_status_identity(current) != _validate_status_identity(status)):
            raise NativeDockError('La identidad cambió al leer el diagnóstico USB.')
        word = current.transport_word
        drop, received = word & 15, (word >> 4) & 127
        result = (word >> 11) & 31
        if result & 16:
            result -= 32
        value = None if result == -16 else result
        return {'available': True, 'drop_reason': drop, 'received_bytes': received,
                'completion_result': 0 if drop == 6 else value,
                'parser_result': value if drop == 6 else None}


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
                    clock=time.monotonic, pause=time.sleep, diagnostics=None) -> dict:
    """One SET_FEATURE, then observe the same handle/session until its ACK.

    A timeout is uncertainty, never permission to replay the request. A
    subsequent explicit action first reads the actual phase/sequence again.
    """
    if op == HOST_SHUTDOWN:
        raise NativeDockError('El cierre terminal requiere primero la expulsión verificada del medio.')
    fields = {'activity': activity, 'unit': unit, 'completed': completed, 'total': total}
    encode_command(session, 1, phase, op, **fields)  # Validate before opening HID.
    api = api_factory()
    with api.open(path, write=True) as handle:
        with diagnostics.measure('usb.command.get_initial') if diagnostics else nullcontext():
            before = api.get(handle)
        identity = _validate_status_identity(before)
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
        # Both admitted firmware builds share PDK2/v3. Echo the exact build
        # verified on this handle; the preferred package is not its identity.
        report = encode_command(session, sequence, phase, op, **fields,
                                firmware_revision=before.firmware_revision,
                                build_id=before.build_id)
        # Keep diagnostic fsync outside the measured Win32 call. The outer
        # command span is durable; this active subspan is readable in memory.
        with diagnostics.measure('usb.command.set_feature') if diagnostics else nullcontext():
            api.set(handle, report)
        last_observed = before
        # This is the ACK observation budget, not a deadline for the
        # synchronous Win32 SET_FEATURE above. Keep those durations separate.
        deadline = clock() + max(0.1, min(float(timeout), 5.0))
        with diagnostics.measure('usb.command.ack_wait') if diagnostics else nullcontext():
            while clock() < deadline:
                # Fast GETs only update in-memory aggregates. A blocked call
                # remains visible as active; a failed/slow one is persisted.
                with diagnostics.measure('usb.command.get_poll') if diagnostics else nullcontext():
                    status = api.get(handle)
                last_observed = status
                if _validate_status_identity(status) != identity:
                    raise NativeDockError('La identidad del firmware cambió durante la confirmación de fase.')
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
            operation = {HOST_BEGIN: 'HOST_BEGIN', SET_PHASE: 'SET_PHASE', HOST_PROGRESS: 'HOST_PROGRESS'}[op]
            action = {HOST_BEGIN: 'el inicio de la conexión', SET_PHASE: 'el cambio de modo',
                      HOST_PROGRESS: 'la actualización del progreso'}[op]
            error_text = (
                f'La PSP no confirmó {action}. {operation}: secuencia enviada {sequence}; '
                f'último estado: secuencia {last_observed.sequence}, resultado {last_observed.result}, '
                f'pendiente {int(last_observed.pending)}, fase {last_observed.phase}, '
                f'Windows confirmado {int(last_observed.host_confirmed)}. '
                'No se repite automáticamente la orden.')
            transport = {}
            if hasattr(api, 'transport_diagnostic'):
                try:
                    transport = api.transport_diagnostic(handle, last_observed)
                except (OSError, RuntimeError, ValueError) as exc:
                    transport = {'available': False, 'error': str(exc)}
            if transport.get('available') is True:
                error_text += (f" USB: recibidos {transport['received_bytes']}, "
                    f"resultado {transport['completion_result']}, descarte {transport['drop_reason']}, "
                    f"intérprete {transport['parser_result']}.")
            raise NativeDockError(error_text,
                message=f'La PSP no confirmó {action}.',
                hint='Pulsa Reintentar para comprobar la conexión de nuevo, o cierra PSP Dock.',
                details={'operation': operation, 'sequence': sequence, 'requested_phase': phase,
                         'before': asdict(before), 'last_observed': asdict(last_observed),
                         'transport': transport})


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
                 progress=request_progress, eject=eject_bound_parent,
                 diagnostics=None, shutdown_path=None, shutdown=None):
        from native_shutdown import NativeShutdown
        self._probe, self._identify, self._parent = probe, identify_storage, parent_of
        self._serial, self._change_phase, self._eject = serial_of, change_phase, eject
        self._begin, self._progress = begin, progress
        self.diagnostics = diagnostics
        self._link = None
        self._storage = None
        self._parent_node = None
        self._commands = threading.RLock()
        self._finished = None
        self._shutdown = shutdown if shutdown is not None else NativeShutdown(shutdown_path)
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

    @property
    def shutdown_pending(self):
        # Available before the first USB tick, including crash recovery.
        return self._shutdown.pending

    def retire_completed_shutdown(self):
        self._shutdown.retire_completed()

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
            with self.diagnostics.measure('usb.bind_hid_parent') if self.diagnostics else nullcontext():
                hid_parent = self._parent(hid_instance(device['path']))
            matches, failures = [], []
            for row in roots:
                drive = str(row.get('root', ''))
                try:
                    with self.diagnostics.measure('usb.identify_storage') if self.diagnostics else nullcontext():
                        storage, _ = self._identify(drive)
                    with self.diagnostics.measure('usb.bind_storage_parent') if self.diagnostics else nullcontext():
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
            'sequence': status['sequence'], 'result': status['result'],
            'pending': status['pending'], 'shutdown_requested': status.get('shutdown_requested', False),
            'activity': status['activity'], 'unit': status['unit'],
            'completed': status['completed'], 'total': status['total'],
            'usb_parent': parent[1], 'root': storage.drive,
            'exit_requested': status['exit_requested'],
        }
        self._storage, self._parent_node, self._link = storage, parent, observation
        return dict(observation)

    def observe(self, devices: list[dict]) -> dict | None:
        try:
            if self._shutdown.pending:
                closing = self._shutdown.observation(probe=self._probe, parent_of=self._parent)
                if closing is not None:
                    self._link = closing
                    self._connection = self._connection_value('closing', 'Terminando la conexión con la PSP.')
                    return dict(closing)
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
        if self._shutdown.pending:
            raise NativeDockError('La tarjeta está en proceso de salida; sólo se permite terminar el cierre.')
        old, storage = self._link, self._storage
        if old is None or storage is None:
            raise NativeDockError('No hay una sesión nativa verificada.')
        if root is not None and str(root).rstrip('\\/').casefold() != storage.drive.rstrip('\\/').casefold():
            raise NativeDockError('La operación apunta a otra Memory Stick.')
        with self.diagnostics.measure('usb.fresh_probe') if self.diagnostics else nullcontext():
            device = self._probe()
        if (device is None or device['path'] != old['path'] or
                device['status']['session'] != old['session_id']):
            self._link = None
            raise NativeDockError('La sesión de la PSP cambió; operación cancelada.')
        with self.diagnostics.measure('usb.fresh_bind') if self.diagnostics else nullcontext():
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
            with self.diagnostics.measure('usb.begin_recheck_before') if self.diagnostics else nullcontext():
                current = self._fresh()
            if current['phase'] != STORAGE:
                raise NativeDockError('La conexión no está preparada para iniciar una sesión nueva.')
            with self.diagnostics.measure('usb.begin_command', important=True) if self.diagnostics else nullcontext():
                options = {'diagnostics':self.diagnostics} if self._begin is request_begin else {}
                result = self._begin(current['path'], current['session_id'], **options)
            with self.diagnostics.measure('usb.begin_recheck_after') if self.diagnostics else nullcontext():
                after = self._fresh()
            if not result.get('accepted') or not after['host_confirmed']:
                raise NativeDockError('La PSP no ha confirmado la conexión con Windows.')
            return result

    def set_progress(self, context, activity, *, unit=UNKNOWN, completed=0, total=0):
        """Display-only command; it cannot grant file or controller permission."""
        with self._commands:
            if self._shutdown.pending:
                raise NativeDockError('El cierre terminal no admite más órdenes de progreso.')
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
                'capabilities': current['capabilities'], 'build_id': current['build_id'],
                'reason': ('Esperando la confirmación de Windows.' if not current['host_confirmed'] else
                           'PSP Dock conectado.' if current['controller_ready'] else
                           'Elige qué quieres sincronizar.' if current['activity'] == CHOOSING else 'Sincronizando PSP.')}

    def finish_session(self, *, cancel=False) -> dict:
        with self._commands:
            if self._shutdown.pending:
                try:
                    self._finished = self._shutdown.finish(parent_of=self._parent)
                finally:
                    self._link = self._shutdown.current
                return dict(self._finished)
            current = self._fresh()
            unclaimed = cancel and not current['host_confirmed']
            if unclaimed:
                # A failed HOST_BEGIN must not be needed to cancel itself.
                # No Dock file work was admitted without the claim. The exact
                # volume/parent is still rebound above and normal Windows PnP
                # removal flushes it or returns its veto; USB is never forced.
                if current['phase'] != STORAGE or not current['storage_ready']:
                    raise NativeDockError('La PSP tiene una orden pendiente; espera a comprobar su estado antes de cancelar.')
            elif cancel and current['phase'] in (STORAGE, CONTROLLER):
                # Cancelling entry is an eject path, never a save callback.
                self.set_phase(EXIT_SYNC)
                current = self._link
            if not unclaimed and current['phase'] not in (EXIT_SYNC, SAFE):
                raise NativeDockError('La salida requiere completar la sincronización de vuelta.')
            if not unclaimed and current['phase'] != SAFE:
                self.set_phase(SAFE)
                current = self._link
            storage, parent = self._storage, self._parent_node
            if current['capabilities'] & CAP_HOST_SHUTDOWN:
                self._shutdown.prepare(current, storage, cancel=cancel)
                try:
                    self._finished = self._shutdown.finish(parent_of=self._parent)
                finally:
                    self._link = self._shutdown.current
                return dict(self._finished)
            result = self._eject(storage, parent)
            if not result.get('host_eject_accepted'):
                raise NativeDockError('Windows mantiene la PSP en uso: ' + str(result.get('veto_name') or result.get('configret')))
            self._finished = {'verified': True, **result}
            self._link = None
            return dict(self._finished)
