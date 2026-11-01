"""Pure, fail-closed handoff coordinator for the future PSP Dock.

NO hardware I/O, filesystem access, USB calls, or Windows process control.
The existing Windows launcher remains unchanged. This models the contract
between its already tested library/save operations and a *future* verified
PSP VSH plugin. Never mistake an OS safe-eject for a PSP firmware ACK.

The caller must independently verify every fact before supplying an event.
The returned action names are INTENTS, not executed operations.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict, field
from enum import Enum
from typing import Any


class Phase(str, Enum):
    WAITING_PSP = "waiting_psp"
    STORAGE_READY = "storage_ready"
    PREPARED = "prepared"
    EJECT_REQUESTED = "eject_requested"
    STORAGE_RELEASED = "storage_released"
    HID_READY = "hid_ready"
    PLAYING = "playing"
    RETURN_REQUESTED = "return_requested"
    STORAGE_RETURN_PENDING = "storage_return_pending"
    SYNC_BACK = "sync_back"
    SAFE_TO_UNPLUG = "safe_to_unplug"
    HALTED = "halted"


class HandoffError(ValueError):
    """Invalid or unverified event: no state transition and no side effects."""


ROLE_CAPABILITIES = frozenset({
    "exclusive_fat",
    "release_storage",
    "activate_hid",
    "restore_storage",
    "live_role_ack",
})


@dataclass(frozen=True)
class RoleBridgeProof:
    """Facts provided by a FUTURE, live hardware adapter, not by BOOT.TXT.

    A valid VSH observer marker proves only a successful state query. It is
    NOT proof that the kernel can release FAT, switch roles or return saves.
    This is a trust-boundary contract; callers must obtain and verify these
    facts directly, and never construct them from user input or stale files.
    """

    source: str
    volume_serial: str
    usb_parent: str
    protocol_version: int
    capabilities: frozenset[str]
    hardware_roundtrip_verified: bool
    live_ack_verified: bool

    def qualifies(self, serial: str, parent: str) -> bool:
        return (
            self.source == "live_psp_role_bridge"
            and self.volume_serial == serial
            and self.usb_parent == parent
            and type(self.protocol_version) is int
            and self.protocol_version >= 1
            and isinstance(self.capabilities, frozenset)
            and ROLE_CAPABILITIES <= self.capabilities
            and self.hardware_roundtrip_verified is True
            and self.live_ack_verified is True
        )


@dataclass(frozen=True)
class Command:
    """An intent to perform via a separate authorized and verified adapter."""

    name: str
    args: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Handoff:
    phase: Phase = Phase.WAITING_PSP
    volume_serial: str = ""
    usb_parent: str = ""
    hid_session: str = ""
    selected_game: str = ""
    fault: str = ""
    revision: int = 0

    def snapshot(self) -> dict:
        """A durable JSON-compatible state, never credentials or game contents."""
        obj = asdict(self)
        obj["phase"] = self.phase.value
        return obj

    @classmethod
    def from_snapshot(cls, obj: dict) -> "Handoff":
        """Unknown fields, missing identity, or impossible states fail closed."""
        if set(obj) != set(cls.__dataclass_fields__):
            raise HandoffError("Formato de sesión PSP Dock desconocido.")
        try:
            state = cls(**{**obj, "phase": Phase(obj["phase"])})
        except (ValueError, TypeError) as exc:
            raise HandoffError("Estado de sesión no reconocido.") from exc
        if type(state.revision) is not int or state.revision < 0:
            raise HandoffError("Revisión persistente de sesión inválida.")
        if (state.phase not in (Phase.WAITING_PSP, Phase.HALTED) and
                (not state.volume_serial or not state.usb_parent)):
            raise HandoffError("Sesión sin identidad PSP vinculada.")
        if state.phase in (Phase.HID_READY, Phase.PLAYING,
                           Phase.RETURN_REQUESTED, Phase.STORAGE_RETURN_PENDING,
                           Phase.SYNC_BACK, Phase.SAFE_TO_UNPLUG) and (
                               not isinstance(state.hid_session, str) or
                               len(state.hid_session) != 32 or
                               any(c not in "0123456789abcdef" for c in state.hid_session)):
            raise HandoffError("Sesión sin conexión HID vinculada.")
        return state

    def event(self, kind: str, **data: Any) -> tuple["Handoff", tuple[Command, ...]]:
        """Pure transition. A failure raises; caller preserves previous snapshot."""
        from dataclasses import replace

        def need(key: str, value: Any) -> None:
            if not data.get(key):
                raise HandoffError(value)

        def transition(phase: Phase, *cmds: Command, **changes: Any):
            return replace(self, phase=phase, revision=self.revision + 1, **changes), tuple(cmds)

        if kind == "abort":
            return transition(Phase.HALTED, fault=str(data.get("reason") or "Sesión suspendida."))

        if self.phase == Phase.HALTED:
            raise HandoffError("Sesión bloqueada: la recuperación necesita verificación externa.")

        if kind == "storage_present" and self.phase == Phase.WAITING_PSP:
            serial, usb = data.get("volume_serial"), data.get("usb_parent")
            if not isinstance(serial, str) or not serial.isdecimal():
                raise HandoffError("Número de volumen ausente o inválido.")
            if not isinstance(usb, str) or not usb.upper().startswith("USB\\VID_054C&PID_"):
                raise HandoffError("Dispositivo USB Sony PSP no verificado.")
            need("mounted_single_volume", "No hay una sola Memory Stick comprobada.")
            return transition(Phase.STORAGE_READY,
                              Command("inventory_and_plan_saves"),
                              volume_serial=serial, usb_parent=usb,
                              hid_session="", selected_game="", fault="")

        if kind == "prepared" and self.phase == Phase.STORAGE_READY:
            need("games_present", "Faltan juegos en la biblioteca local.")
            need("saves_verified", "Los guardados no están verificados.")
            need("no_save_conflicts", "Hay conflictos de partidas sin resolver.")
            need("no_pending_copy", "Sigue habiendo transferencias abiertas.")
            return transition(Phase.PREPARED, Command("show_dock_ready"))

        if kind == "dock_requested" and self.phase == Phase.PREPARED:
            proof = data.get("role_bridge_proof")
            if not isinstance(proof, RoleBridgeProof) or not proof.qualifies(
                self.volume_serial, self.usb_parent
            ):
                raise HandoffError(
                    "Observador VSH/BOOT.TXT no autoriza USB: falta prueba del "
                    "gestor kernel con exclusividad FAT, HID y retorno en hardware."
                )
            need("no_pending_copy", "La Memory Stick tiene transferencias pendientes.")
            need("emulator_closed", "PPSSPP está abierto; no expulsar USB.")
            need("same_storage_identity", "La PSP montada ya no es la sincronizada.")
            return transition(Phase.EJECT_REQUESTED, Command("request_safe_eject"))

        if kind == "host_eject_ok" and self.phase == Phase.EJECT_REQUESTED:
            need("os_confirmed", "Windows no confirmó expulsión segura.")
            need("same_storage_identity", "No es el dispositivo que se preparó.")
            # The host has only released its handles. No firmware ACK exists yet.
            return transition(Phase.STORAGE_RELEASED, Command("await_verified_hid"))

        if kind == "hid_present" and self.phase == Phase.STORAGE_RELEASED:
            token = data.get("session")
            if (not isinstance(token, str) or len(token) != 32 or
                    any(c not in "0123456789abcdef" for c in token)):
                raise HandoffError("Sesión HID no reconocida.")
            need("descriptor_verified", "Descriptor PSP Dock HID sin verificar.")
            need("storage_absent", "Storage y HID podrían compartir un FAT activo.")
            need("same_physical_usb", "Dispositivo HID de otra consola o sesión.")
            return transition(Phase.HID_READY,
                              Command("show_xmb_library"),
                              hid_session=token)

        if kind == "play_requested" and self.phase == Phase.HID_READY:
            title = data.get("game_id")
            if not isinstance(title, str) or not title:
                raise HandoffError("Juego no seleccionado.")
            need("game_copied_verified", "El juego todavía no está copiado o verificado.")
            need("same_hid_session", "La sesión HID ha cambiado.")
            return transition(Phase.PLAYING,
                              Command("launch_ppsspp", {"game_id": title}),
                              selected_game=title)

        if kind == "emulator_exited" and self.phase == Phase.PLAYING:
            need("process_gone", "PPSSPP aún está ejecutándose.")
            need("local_saves_idle", "Los archivos de guardados del PC siguen abiertos.")
            return transition(Phase.RETURN_REQUESTED,
                              Command("request_hid_return_to_storage",
                                      {"session": self.hid_session}))

        if kind == "hid_return_sent" and self.phase == Phase.RETURN_REQUESTED:
            need("transport_attempted_once", "No hay intento único de retorno HID.")
            # Delivery is uncertain even when host reports error. Never retry
            # blindly; wait for the correct original USB storage to return.
            return transition(Phase.STORAGE_RETURN_PENDING,
                              Command("await_original_storage"))

        if kind == "storage_returned" and self.phase == Phase.STORAGE_RETURN_PENDING:
            if (data.get("volume_serial") != self.volume_serial or
                    data.get("usb_parent") != self.usb_parent):
                raise HandoffError("La Memory Stick no es la del inicio: prohibido sincronizar.")
            need("mounted_single_volume", "Varios volúmenes PSP, no se puede seleccionar.")
            need("ppsspp_closed", "PPSSPP todavía tiene el juego abierto.")
            return transition(Phase.SYNC_BACK,
                              Command("verified_sync_with_backups"))

        if kind == "sync_return_finished" and self.phase == Phase.SYNC_BACK:
            need("copies_verified", "Las copias de vuelta no pasaron verificación.")
            need("no_conflicts", "Hay conflicto de guardados pendiente.")
            need("no_copy_journals", "Existe una transferencia interrumpida.")
            return transition(Phase.SAFE_TO_UNPLUG,
                              Command("notify_safe_to_disconnect"))

        if kind == "psp_disconnected" and self.phase == Phase.SAFE_TO_UNPLUG:
            need("os_storage_released", "Windows todavía tiene montado el volumen.")
            return Handoff(revision=self.revision + 1), tuple()

        raise HandoffError(f"Evento {kind!r} no permitido en fase {self.phase.value!r}.")
