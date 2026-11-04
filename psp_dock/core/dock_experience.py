"""PSP Dock: product-level Switch-like experience, with no hardware effects.

Pure state machine, intentionally separate from ARK, Windows PnP and the
existing experimental usb_handoff.Handoff coordinator. Emits *intent*
objects, not commands to hardware. No one may claim a hardware switch by
passing a stale BOOT.TXT, hiding a volume, or selecting a menu option.

User journey:
  attach -> inventory -> ask transfer (if missing) -> consent/copy or skip ->
  inspect/resolve saves -> verify storage -> role transition -> HID launcher ->
  game -> XMB launcher -> user exits -> restore storage -> verify return/saves ->
  safe to disconnect.

Input choices are made on Windows *before* HID is active (mouse/keyboard).
There is never a need to toggle back to storage just to answer "copy now?".
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any

from psp_dock.devices.usb_handoff import RoleBridgeProof


class Stage(str, Enum):
    WAITING = "waiting"
    AUDITING = "auditing"
    DECIDE_GAMES = "decide_games"
    COPYING_GAMES = "copying_games"
    REVIEW_SAVES = "review_saves"
    SYNCING_SAVES = "syncing_saves"
    STORAGE_READY = "storage_ready"
    HOST_LOCKING = "host_locking"
    HOST_LOCKED = "host_locked"
    PSP_RELEASING = "psp_releasing"
    WAIT_HID = "wait_hid"
    XMB = "xmb"
    GAME = "game"
    RETURNING = "returning"
    WAIT_STORAGE_RETURN = "wait_storage_return"
    SYNC_BACK = "sync_back"
    SAFE_TO_UNPLUG = "safe_to_unplug"
    HALTED = "halted"


class ExperienceError(ValueError):
    pass


@dataclass(frozen=True)
class Intent:
    name: str
    args: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Experience:
    stage: Stage = Stage.WAITING
    volume_serial: str = ""
    usb_parent: str = ""
    inventory_tag: str = ""
    missing_games: int = 0
    ready_games: int = 0
    save_actions: int = 0
    save_conflicts: int = 0
    transfer_choice: str = ""
    hid_session: str = ""
    selected_game: str = ""
    revision: int = 0
    error: str = ""

    def snapshot(self) -> dict:
        return {**self.__dict__, "stage": self.stage.value}

    @classmethod
    def from_snapshot(cls, data: dict) -> "Experience":
        if not isinstance(data, dict) or set(data) != set(cls.__dataclass_fields__):
            raise ExperienceError("Estado PSP Dock desconocido o incompleto.")
        try:
            stage = Stage(data["stage"])
            result = cls(**{**data, "stage": stage})
        except (ValueError, TypeError) as exc:
            raise ExperienceError("Fase PSP Dock inválida.") from exc
        for number in ("missing_games", "ready_games", "save_actions", "save_conflicts", "revision"):
            if type(getattr(result, number)) is not int or getattr(result, number) < 0:
                raise ExperienceError("Contador de sesión inválido.")
        if stage not in (Stage.WAITING, Stage.HALTED) and (
            not result.volume_serial or not result.usb_parent
        ):
            raise ExperienceError("Estado sin identidad de PSP vinculada.")
        if stage in (Stage.XMB, Stage.GAME, Stage.RETURNING,
                     Stage.WAIT_STORAGE_RETURN, Stage.SYNC_BACK, Stage.SAFE_TO_UNPLUG):
            token = result.hid_session
            if (len(token) != 32 or
                any(ch not in "0123456789abcdef" for ch in token)):
                raise ExperienceError("Sesión HID mal formada.")
        return result

    def event(self, action: str, **values: Any) -> tuple["Experience", tuple[Intent, ...]]:
        """Apply an observed event, yielding effects for an external adapter."""
        def required(key: str, message: str):
            if values.get(key) is not True:
                raise ExperienceError(message)

        def to(stage: Stage, *intents: Intent, **changes: Any):
            return replace(
                self, stage=stage, revision=self.revision + 1, **changes
            ), tuple(intents)

        if action == "halt":
            return to(Stage.HALTED, error=str(values.get("reason") or "Sesión suspendida"))
        if self.stage == Stage.HALTED:
            raise ExperienceError("La sesión está suspendida, requiere recuperación.")

        if self.stage == Stage.WAITING and action == "storage_arrived":
            serial, parent = values.get("volume_serial"), values.get("usb_parent")
            if not isinstance(serial, str) or not serial.isdecimal():
                raise ExperienceError("Serial del volumen PSP inválido.")
            if not isinstance(parent, str) or not parent.upper().startswith(
                "USB\\VID_054C&PID_02D2\\"
            ):
                raise ExperienceError("La USB Sony PSP no está verificada.")
            required("sony_psp_verified", "El almacenamiento no corresponde a una PSP Sony.")
            required("single_volume", "Hay múltiples candidatos a PSP.")
            return to(Stage.AUDITING, Intent("inventory_read_only"),
                      volume_serial=serial, usb_parent=parent,
                      inventory_tag="", hid_session="", selected_game="",
                      error="")

        if self.stage == Stage.AUDITING and action == "inventory_complete":
            tag = values.get("inventory_tag")
            counts = ("missing_games", "ready_games", "save_actions", "save_conflicts")
            if not isinstance(tag, str) or len(tag) != 64 or any(
                c not in "0123456789abcdef" for c in tag
            ) or any(type(values.get(k)) is not int or values[k] < 0 for k in counts):
                raise ExperienceError("Inventario incompleto o sin integridad.")
            stage = (Stage.DECIDE_GAMES if values["missing_games"]
                     else Stage.REVIEW_SAVES)
            command = ("show_game_copy_choice" if stage == Stage.DECIDE_GAMES
                       else "show_save_review")
            return to(stage, Intent(command),
                      inventory_tag=tag, transfer_choice="",
                      **{k: values[k] for k in counts})

        if self.stage == Stage.DECIDE_GAMES and action == "user_copy_choice":
            required("same_inventory", "El inventario cambió; volver a comparar.")
            choice = values.get("choice")
            if choice not in ("copy", "skip"):
                raise ExperienceError("Elige copiar o continuar sin copiar.")
            if choice == "copy":
                return to(Stage.COPYING_GAMES, Intent("verified_copy_missing_games"),
                          transfer_choice=choice)
            return to(Stage.REVIEW_SAVES, Intent("show_save_review"),
                      transfer_choice=choice)

        if self.stage == Stage.COPYING_GAMES and action == "copy_complete":
            required("copies_verified", "La transferencia de juegos no se verificó.")
            required("same_identity", "La PSP cambió durante la transferencia.")
            required("no_journals", "Existen copias de juegos interrumpidas.")
            return to(Stage.REVIEW_SAVES, Intent("show_save_review"),
                      ready_games=self.ready_games + self.missing_games,
                      missing_games=0)

        if self.stage == Stage.REVIEW_SAVES and action == "save_review_complete":
            required("same_identity", "La PSP no coincide con el inventario.")
            if values.get("conflicts_left") != 0:
                raise ExperienceError("Los conflictos de partidas requieren una elección.")
            if self.save_actions > 0:
                required("user_approved_sync", "Las partidas requieren autorización para copiarlas.")
                return to(Stage.SYNCING_SAVES, Intent("sync_verified_saves_with_backups"),
                          save_conflicts=0)
            return to(Stage.STORAGE_READY, Intent("show_storage_ready"),
                      save_conflicts=0)

        if self.stage == Stage.SYNCING_SAVES and action == "save_sync_complete":
            required("copies_verified", "Guardados sin verificación.")
            required("no_conflicts", "Hay conflictos de partidas.")
            required("no_journals", "Hay operaciones interrumpidas.")
            required("same_identity", "La PSP cambió durante la copia.")
            return to(Stage.STORAGE_READY, Intent("show_storage_ready"),
                      save_actions=0, save_conflicts=0)

        if self.stage == Stage.STORAGE_READY and action == "start_dock":
            if self.ready_games <= 0:
                raise ExperienceError("Ningún juego copiado al PC para iniciar el Dock.")
            proof = values.get("role_bridge_proof")
            if not isinstance(proof, RoleBridgeProof) or not proof.qualifies(
                self.volume_serial, self.usb_parent
            ):
                raise ExperienceError(
                    "Falta bridge kernel REAL y su ACK de USB/FAT. "
                    "No iniciar HID ni expulsar la unidad."
                )
            required("same_identity", "Identidad PSP desconocida en la transición.")
            required("no_pending_io", "Copias o archivos en la PSP aún abiertos.")
            required("emulator_closed", "PPSSPP sigue abierto.")
            return to(Stage.HOST_LOCKING, Intent("lock_host_volume_no_eject"))

        if self.stage == Stage.HOST_LOCKING and action == "host_lock_acquired":
            required("exclusive_lock_verified", "Windows no obtuvo acceso exclusivo a FAT.")
            required("cached_writes_flushed", "Windows no confirmó flush completo.")
            return to(Stage.HOST_LOCKED, Intent("request_firmware_prepare"))

        if self.stage == Stage.HOST_LOCKED and action == "psp_prepare_ack":
            required("live_session_ack", "Falta ACK vivo del firmware PSP.")
            required("fat_ownership_safe", "No hay exclusión mutua real de FAT.")
            required("host_volume_still_locked", "Windows perdió el lock de volumen.")
            return to(Stage.PSP_RELEASING, Intent("request_psp_storage_stop_once"))

        if self.stage == Stage.PSP_RELEASING and action == "storage_stopped":
            required("firmware_stop_ack", "El firmware no confirmó USBSTOR parado.")
            required("host_volume_still_locked", "Windows perdió la exclusividad.")
            return to(Stage.WAIT_HID, Intent("wait_psp_hid_same_usb"))

        if self.stage == Stage.WAIT_HID and action == "hid_attached":
            token = values.get("session")
            if (not isinstance(token, str) or len(token) != 32 or
                any(c not in "0123456789abcdef" for c in token) or
                not any(bytes.fromhex(token))):
                raise ExperienceError("Sesión HID mal formada.")
            for k, message in (
                ("descriptor_verified", "HID desconocido."),
                ("same_physical_usb", "No es la PSP del almacenamiento."),
                ("storage_not_mounted", "Storage y HID visibles simultáneamente."),
                ("live_firmware_ack", "La PSP no confirmó activación HID."),
            ):
                required(k, message)
            return to(Stage.XMB, Intent("show_controller_xmb"),
                      hid_session=token)

        if self.stage == Stage.XMB and action == "launch_game":
            game_id = values.get("game_id")
            if not isinstance(game_id, str) or not game_id:
                raise ExperienceError("Juego no seleccionado.")
            required("game_local_verified", "El juego no está disponible en el PC.")
            required("same_hid_session", "La PSP ha cambiado de sesión HID.")
            return to(Stage.GAME, Intent("launch_isolated_ppsspp", {"game_id": game_id}),
                      selected_game=game_id)

        if self.stage == Stage.GAME and action == "game_closed":
            required("ppsspp_closed", "PPSSPP está en ejecución.")
            required("save_files_idle", "El juego mantiene partidas abiertas.")
            return to(Stage.XMB, Intent("show_controller_xmb"), selected_game="")

        if self.stage == Stage.XMB and action == "exit_dock":
            required("same_hid_session", "Sesión HID distinta.")
            required("ppsspp_closed", "Cierra primero el juego.")
            return to(Stage.RETURNING, Intent("request_hid_return_once",
                                              {"session": self.hid_session}))

        if self.stage == Stage.RETURNING and action == "return_issued_once":
            required("attempt_recorded", "La orden HID pudo duplicarse.")
            return to(Stage.WAIT_STORAGE_RETURN, Intent("wait_original_storage"))

        if self.stage == Stage.WAIT_STORAGE_RETURN and action == "original_storage_returned":
            if (values.get("volume_serial") != self.volume_serial or
                values.get("usb_parent") != self.usb_parent):
                raise ExperienceError("Otra PSP u otra Memory Stick; no sincronizar.")
            required("single_volume", "Varias unidades candidatas.")
            required("ppsspp_closed", "El juego sigue ejecutándose.")
            return to(Stage.SYNC_BACK, Intent("inspect_and_sync_return_saves"))

        if self.stage == Stage.SYNC_BACK and action == "return_sync_verified":
            for key, message in (
                ("verified_backups", "No hay backups verificables del retorno."),
                ("no_conflicts", "Conflictos de guardado pendientes."),
                ("no_journals", "Copias interrumpidas."),
                ("same_identity", "El almacenamiento PSP cambió."),
            ):
                required(key, message)
            return to(Stage.SAFE_TO_UNPLUG, Intent("announce_safe_disconnect"))

        if self.stage == Stage.SAFE_TO_UNPLUG and action == "cable_removed":
            required("host_storage_released", "Windows no ha liberado el almacenamiento.")
            return Experience(revision=self.revision + 1), tuple()

        raise ExperienceError(
            f"Evento {action!r} no permitido durante {self.stage.value}."
        )
