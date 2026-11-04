"""Fail-closed contract for a future PSP USBSTOR <-> HID role adapter.

Pure coordination types only: this module performs NO OS, USB, firmware,
filesystem, process, PnP, volume-lock, or emulator I/O. In particular it does
not provide DeviceIoControl/FSCTL, eject, SCSI, or a USB command transport.

Windows observations, even of HID and USB storage with matching identifiers,
ARE NOT firmware acknowledgements. A real ``RoleBridgeProof`` can only be
supplied by a FUTURE, independently implemented and audited live attestor.
There is intentionally no implementation or factory for that proof here.

The single-use return ledger and polling gate are *memory-only contracts*;
they never send a request or stop an actual Windows polling thread. The future
driver must persist a return-attempt marker BEFORE issuing its one USB command,
and must make the polling thread explicitly confirm it is quiescent.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from typing import TYPE_CHECKING, Any, Mapping, Protocol, runtime_checkable

if TYPE_CHECKING:
    from psp_dock.devices.usb_handoff import RoleBridgeProof


OBSERVATION_ONLY = "WINDOWS_OBSERVATION_ONLY_NO_FIRMWARE_ACK"
INTENT_ONLY = "LOCAL_INTENT_ONLY_NOT_DISPATCHED"
_SONY_STORAGE_PREFIX = "USB\\VID_054C&PID_02D2\\"
_HID_SESSION_FORMAT = re.compile(r"[0-9a-f]{32}\Z")
_SERIAL_FORMAT = re.compile(r"[0-9]+\Z")
_RETURN_RESERVATION_KEY = object()


class RoleContractError(ValueError):
    """Invalid or insufficient evidence; no external side effects occurred."""


class UnavailableRoleError(RoleContractError):
    """The physical role bridge is not available or not certified."""


class IdentityMismatchError(RoleContractError):
    """The observed volume, USB parent or HID session does not match."""


class PollingContractError(RoleContractError):
    """The caller has not demonstrated that the volume poller is quiescent."""


class ReturnAttemptError(RoleContractError):
    """A return request may already have been sent: never retry blindly."""


@dataclass(frozen=True)
class StorageIdentity:
    """An expected Sony volume identity, not proof of actual device ownership."""

    volume_serial: str
    usb_parent: str

    def __post_init__(self) -> None:
        if (not isinstance(self.volume_serial, str)
                or _SERIAL_FORMAT.fullmatch(self.volume_serial) is None):
            raise IdentityMismatchError("El serial de volumen debe ser decimal ASCII.")
        if (not isinstance(self.usb_parent, str)
                or self.usb_parent.strip() != self.usb_parent
                or not self.usb_parent.upper().startswith(_SONY_STORAGE_PREFIX)
                or len(self.usb_parent) <= len(_SONY_STORAGE_PREFIX)):
            raise IdentityMismatchError("Se requiere el USB padre Sony PSP exacto.")
        # Windows PnP IDs are case-insensitive. Match the *whole* parent.
        object.__setattr__(self, "usb_parent", self.usb_parent.upper())


@dataclass(frozen=True)
class SessionExpectation:
    """Correlatable identifiers only, NEVER a verified same-device assertion."""

    storage: StorageIdentity
    hid_session: str

    def __post_init__(self) -> None:
        if not isinstance(self.storage, StorageIdentity):
            raise IdentityMismatchError("Identidad de almacenamiento inválida.")
        if (not isinstance(self.hid_session, str)
                or _HID_SESSION_FORMAT.fullmatch(self.hid_session) is None
                or not any(bytes.fromhex(self.hid_session))):
            raise IdentityMismatchError("La sesión HID debe ser única y hexadecimal.")

    def matches_storage(self, identity: StorageIdentity) -> bool:
        return type(identity) is StorageIdentity and identity == self.storage

    def matches_hid(self, session: str) -> bool:
        return type(session) is str and session == self.hid_session


class ObservedRole(str, Enum):
    UNAVAILABLE = "UNAVAILABLE"
    STORAGE_VISIBLE = "STORAGE_VISIBLE"
    HID_VISIBLE_UNBOUND = "HID_VISIBLE_UNBOUND"
    DUAL_PRESENT_UNSAFE = "DUAL_PRESENT_UNSAFE"
    WINDOWS_PREPARED_REMOVAL = "WINDOWS_PREPARED_REMOVAL"
    NO_PSP_INTERFACE_OBSERVED = "NO_PSP_INTERFACE_OBSERVED"


@dataclass(frozen=True)
class HostUsbStatus:
    """Host-side status. Its trust level can NEVER be raised by input flags.

    The classification mirrors ``usb_transport_observer.classify_observation``
    without importing its OS-facing dependencies or running a probe.
    """

    state: ObservedRole
    storage: StorageIdentity | None = None
    hid_candidates: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.state, ObservedRole):
            raise RoleContractError("Estado USB observado desconocido.")
        if type(self.hid_candidates) is not int or self.hid_candidates < 0:
            raise RoleContractError("Contador HID observado inválido.")
        if self.storage is not None and not isinstance(self.storage, StorageIdentity):
            raise RoleContractError("Identidad Sony observada inválida.")
        has_storage = self.storage is not None
        has_hid = self.hid_candidates > 0
        expected = {
            ObservedRole.UNAVAILABLE: (False, False),
            ObservedRole.STORAGE_VISIBLE: (True, False),
            ObservedRole.HID_VISIBLE_UNBOUND: (False, True),
            ObservedRole.DUAL_PRESENT_UNSAFE: (True, True),
            ObservedRole.WINDOWS_PREPARED_REMOVAL: (False, False),
            ObservedRole.NO_PSP_INTERFACE_OBSERVED: (False, False),
        }[self.state]
        if (has_storage, has_hid) != expected:
            raise RoleContractError("Presencia USB/HID incoherente con estado observado.")

    @property
    def evidence_kind(self) -> str:
        return OBSERVATION_ONLY

    @property
    def live_firmware_ack(self) -> bool:
        return False

    @property
    def exclusive_fat_confirmed(self) -> bool:
        return False

    @property
    def role_transition_confirmed(self) -> bool:
        return False

    @classmethod
    def from_host_report(cls, report: Mapping[str, Any]) -> "HostUsbStatus":
        """Accept only the observational part of a Windows diagnostic.

        Reject reports attempting to promote a Windows snapshot into a live
        ACK, even if the report's sender set privileged booleans to True.
        """
        if not isinstance(report, Mapping):
            raise RoleContractError("Diagnóstico Windows inválido.")
        for field in ("role_transition_confirmed", "exclusive_fat_confirmed",
                      "live_firmware_ack", "hardware_roundtrip_verified"):
            if field in report and report[field] is not False:
                raise RoleContractError(
                    "Un diagnóstico Windows no puede certificar FAT ni ACK firmware."
                )
        try:
            state = ObservedRole(report["state"])
        except (KeyError, TypeError, ValueError) as exc:
            raise RoleContractError("Estado Windows no reconocido.") from exc
        serial = report.get("volume_serial")
        parent = report.get("usb_parent")
        if (serial is None) != (parent is None):
            raise IdentityMismatchError("El serial y padre USB deben aparecer juntos.")
        identity = (StorageIdentity(serial, parent)
                    if serial is not None else None)
        count = report.get("hid_candidates", 0)
        return cls(state=state, storage=identity, hid_candidates=count)


class ReturnRecordState(str, Enum):
    UNKNOWN = "unknown"                  # Fail closed after lost journal.
    NOT_ATTEMPTED = "not_attempted"       # External durable record required.
    ATTEMPT_RECORDED = "attempt_recorded"  # May have been delivered, do not retry.


@dataclass(frozen=True, init=False)
class ReturnAttemptIntent:
    """Local ledger reservation only; does not send USB or prove an ACK.

    The ordinary constructor rejects arbitrary caller-created reservations.
    This private key is a misuse guard, NOT authentication or a security key.
    """

    session: SessionExpectation

    def __init__(self, session: SessionExpectation, *, _key: object = None) -> None:
        if _key is not _RETURN_RESERVATION_KEY or not isinstance(session, SessionExpectation):
            raise ReturnAttemptError("El retorno solo puede reservarlo el ledger.")
        object.__setattr__(self, "session", session)

    @property
    def evidence_kind(self) -> str:
        return INTENT_ONLY


class OneShotReturnLedger:
    """One-shot reservation. Future caller must persist the state externally.

    Default UNKNOWN blocks return attempts, including after a crash. The
    external, audited caller must supply NOT_ATTEMPTED *from its durable
    journal* and persist ATTEMPT_RECORDED before actual transport dispatch.
    This class cannot guarantee durable journaling or delivery by itself.
    """

    def __init__(self, session: SessionExpectation, *,
                 durable_state: ReturnRecordState = ReturnRecordState.UNKNOWN) -> None:
        if not isinstance(session, SessionExpectation):
            raise IdentityMismatchError("Sesión de retorno inválida.")
        if not isinstance(durable_state, ReturnRecordState):
            raise ReturnAttemptError("Estado persistente de retorno inválido.")
        self.session = session
        self._state = durable_state

    @property
    def state(self) -> ReturnRecordState:
        return self._state

    def reserve_before_dispatch(self, hid_session: str) -> ReturnAttemptIntent:
        if not self.session.matches_hid(hid_session):
            raise IdentityMismatchError("Sesión HID distinta: retorno rechazado.")
        if self._state != ReturnRecordState.NOT_ATTEMPTED:
            raise ReturnAttemptError("Retorno ya intentado o estado desconocido.")
        self._state = ReturnRecordState.ATTEMPT_RECORDED
        return ReturnAttemptIntent(self.session, _key=_RETURN_RESERVATION_KEY)


class PollingMode(str, Enum):
    ACTIVE = "active"
    PAUSE_REQUESTED = "pause_requested"
    PAUSED = "paused"
    RETURN_WATCH_ONLY = "return_watch_only"
    ORIGINAL_STORAGE_OBSERVED = "original_storage_observed"
    HALTED = "halted"


@dataclass(frozen=True)
class PollingTicket:
    """A tracked in-flight poll. NOT evidence of an OS volume lock."""

    serial_number: int
    expected_storage: StorageIdentity
    purpose: str


@dataclass(frozen=True)
class PollingPauseRequest:
    """Signal to stop the real polling loop; not confirmation it has stopped."""

    sequence: int
    expected_storage: StorageIdentity


class VolumePollingContract:
    """Two-phase pause + identity-limited return watcher for a future loop.

    No Windows enumeration happens here. It is unsafe to start the role
    transition at PAUSE_REQUESTED: the real loop must first stop, drain
    pending probes, and acknowledge the matching request. In HID mode, normal
    volume enumeration remains disabled. A scoped original-device watcher can
    be enabled only after reserving the one-shot return intent.
    """

    def __init__(self, expected_storage: StorageIdentity) -> None:
        if not isinstance(expected_storage, StorageIdentity):
            raise IdentityMismatchError("Falta identidad del volumen esperado.")
        self.expected_storage = expected_storage
        self._mode = PollingMode.ACTIVE
        self._sequence = 0
        self._in_flight: dict[int, PollingTicket] = {}
        self._pause_request: PollingPauseRequest | None = None
        self._original_seen = False
        self._hid_candidate: SessionExpectation | None = None

    @property
    def mode(self) -> PollingMode:
        return self._mode

    @property
    def normal_poll_allowed(self) -> bool:
        return self._mode == PollingMode.ACTIVE

    @property
    def return_watch_allowed(self) -> bool:
        return self._mode == PollingMode.RETURN_WATCH_ONLY

    @property
    def polls_in_flight(self) -> int:
        return len(self._in_flight)

    def _ticket(self, purpose: str) -> PollingTicket:
        self._sequence += 1
        ticket = PollingTicket(self._sequence, self.expected_storage, purpose)
        self._in_flight[ticket.serial_number] = ticket
        return ticket

    def begin_volume_poll(self) -> PollingTicket:
        if not self.normal_poll_allowed:
            raise PollingContractError("Sondeo de volúmenes pausado.")
        return self._ticket("normal")

    def finish_poll(self, ticket: PollingTicket) -> None:
        if (not isinstance(ticket, PollingTicket)
                or self._in_flight.get(ticket.serial_number) is not ticket):
            raise PollingContractError("Sondeo ajeno, duplicado o desconocido.")
        del self._in_flight[ticket.serial_number]

    def request_pause(self) -> PollingPauseRequest:
        if self._mode != PollingMode.ACTIVE:
            raise PollingContractError("Pausa ya solicitada o sesión bloqueada.")
        self._mode = PollingMode.PAUSE_REQUESTED
        self._sequence += 1
        self._pause_request = PollingPauseRequest(
            self._sequence, self.expected_storage,
        )
        # New polls are immediately blocked; old ones still must drain.
        return self._pause_request

    def acknowledge_pause(self, request: PollingPauseRequest, *,
                          poller_stopped: bool) -> None:
        if self._mode != PollingMode.PAUSE_REQUESTED or request is not self._pause_request:
            raise PollingContractError("ACK de pausa ausente o de otra generación.")
        if poller_stopped is not True or self._in_flight:
            raise PollingContractError("El sondeador sigue activo o tiene trabajo pendiente.")
        self._mode = PollingMode.PAUSED

    def require_paused(self) -> None:
        if self._mode != PollingMode.PAUSED or self._in_flight:
            raise PollingContractError("Se requiere pausa confirmada y sin sondeos vivos.")

    def bind_observed_hid_candidate(self, session: SessionExpectation) -> None:
        """Correlate a candidate after pause; does NOT certify firmware ACK."""
        self.require_paused()
        if (not isinstance(session, SessionExpectation)
                or session.storage != self.expected_storage):
            raise IdentityMismatchError("Sesión HID candidata de otra PSP/Memory Stick.")
        if self._hid_candidate is not None:
            raise PollingContractError("La sesión HID candidata ya fue vinculada.")
        self._hid_candidate = session

    def begin_return_watch(self, attempt: ReturnAttemptIntent) -> None:
        self.require_paused()
        if (not isinstance(attempt, ReturnAttemptIntent)
                or attempt.session.storage != self.expected_storage):
            raise IdentityMismatchError("Retorno dirigido a otra PSP/Memory Stick.")
        if self._hid_candidate is None or attempt.session != self._hid_candidate:
            raise IdentityMismatchError("Retorno de sesión HID no vinculada.")
        self._original_seen = False
        self._mode = PollingMode.RETURN_WATCH_ONLY

    def begin_original_storage_probe(self) -> PollingTicket:
        if not self.return_watch_allowed:
            raise PollingContractError("El retorno todavía no puede observarse.")
        return self._ticket("return_original_only")

    def record_original_storage_probe(
        self, ticket: PollingTicket, observed: StorageIdentity | None,
    ) -> bool:
        if (self._mode != PollingMode.RETURN_WATCH_ONLY
                or self._in_flight.get(getattr(ticket, "serial_number", None)) is not ticket
                or ticket.purpose != "return_original_only"):
            raise PollingContractError("Sondeo de retorno inválido.")
        self.finish_poll(ticket)
        if observed is not None:
            if not isinstance(observed, StorageIdentity) or observed != self.expected_storage:
                self.halt()
                raise IdentityMismatchError("Volvió otro serial o USB padre; no sincronizar.")
            self._original_seen = True
        if self._original_seen and not self._in_flight:
            self._mode = PollingMode.ORIGINAL_STORAGE_OBSERVED
        return self._original_seen

    def resume_after_verified_return(
        self, returned: StorageIdentity, *, saves_synced: bool,
        no_pending_io: bool,
    ) -> None:
        if (self._mode != PollingMode.ORIGINAL_STORAGE_OBSERVED
                or self._in_flight):
            raise PollingContractError("Retorno original aún no confirmado.")
        if returned != self.expected_storage:
            raise IdentityMismatchError("No es la Memory Stick original.")
        if saves_synced is not True or no_pending_io is not True:
            raise PollingContractError("Guardados/transferencias pendientes.")
        self._mode = PollingMode.ACTIVE
        self._pause_request = None

    def halt(self) -> None:
        """Remain paused after errors; never silently start the poller."""
        self._mode = PollingMode.HALTED


@runtime_checkable
class UsbRoleAdapter(Protocol):
    """Port future independently audited physical adapters must implement.

    All live verification must happen inside a future hardware attestor that
    validates fresh challenge/response, exact identity, capabilities and FAT
    ownership. A status snapshot cannot implement ``verified_live_proof``.
    """

    def observe(self) -> HostUsbStatus:
        ...

    def verified_live_proof(self, expected: StorageIdentity) -> RoleBridgeProof | None:
        ...

    def request_prepare(self, expected: StorageIdentity, *,
                        polling: VolumePollingContract) -> None:
        ...

    def request_storage_stop(self, expected: StorageIdentity, *,
                             polling: VolumePollingContract) -> None:
        ...

    def request_return_once(self, attempt: ReturnAttemptIntent, *,
                            polling: VolumePollingContract) -> None:
        ...


class UnavailableRoleAdapter:
    """Default adapter. Permanently unavailable and incapable of I/O/ACK.

    Its methods do not accept reported-true booleans or fabricated attestation
    objects. The exact safe result is always no proof and no operations.
    """

    def observe(self) -> HostUsbStatus:
        return HostUsbStatus(ObservedRole.UNAVAILABLE)

    def verified_live_proof(self, expected: StorageIdentity) -> None:
        if not isinstance(expected, StorageIdentity):
            raise IdentityMismatchError("Identidad de PSP requerida.")
        return None

    def request_prepare(self, expected: StorageIdentity, *,
                        polling: VolumePollingContract) -> None:
        raise UnavailableRoleError("No existe atestador vivo: preparación PSP prohibida.")

    def request_storage_stop(self, expected: StorageIdentity, *,
                             polling: VolumePollingContract) -> None:
        raise UnavailableRoleError("Sin ACK de firmware no puede detenerse USBSTOR.")

    def request_return_once(self, attempt: ReturnAttemptIntent, *,
                            polling: VolumePollingContract) -> None:
        raise UnavailableRoleError("No existe transporte seguro de retorno HID.")
