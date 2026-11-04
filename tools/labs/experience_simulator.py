"""Deterministic, memory-only rehearsal of :mod:`dock_experience`.

THIS MODULE PRODUCES **SYNTHETIC** OBSERVATIONS ONLY. No PSP, USB device,
Windows volume, PPSSPP process, game file, or save is accessed. Every successful
role transition is a *model exercise*, NEVER evidence that a PSP role bridge,
FAT lock, firmware ACK, or HID connection works on physical hardware.

This is deliberately separate from the live Dock adapters. In particular,
there is no public proof factory: a qualifying-but-FICTIONAL RoleBridgeProof
exists only as a temporary local variable while the simulation sends the
``start_dock`` event. Never use this module to authorize physical operations.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Literal

from psp_dock.core.dock_experience import Experience, ExperienceError, Stage
SYNTHETIC_ONLY = "SYNTHETIC_ONLY_NO_HARDWARE_EVIDENCE"
MOCK_SERIAL = "2958455453"
MOCK_SONY_USB = "USB\\VID_054C&PID_02D2\\SIMULATION_ONLY"
MOCK_HID_SESSION = "df05364a139d42b69ec02180d40876ba"


@dataclass(frozen=True)
class MockIdentity:
    volume_serial: str = MOCK_SERIAL
    usb_parent: str = MOCK_SONY_USB


@dataclass(frozen=True)
class MockInventory:
    """Names and simulated differences; contains no game or savedata bytes."""

    local_games: tuple[str, ...] = ("DARIUSBURST", "RIDGE_RACER")
    missing_games: tuple[str, ...] = ("LUMINES", "PATAPON")
    save_updates: tuple[str, ...] = ("SAVE_DARIUS", "SAVE_RIDGE")
    conflicting_saves: tuple[str, ...] = ("SAVE_RIDGE",)

    def __post_init__(self) -> None:
        groups = (self.local_games, self.missing_games,
                  self.save_updates, self.conflicting_saves)
        for names in groups:
            if (not isinstance(names, tuple)
                    or any(not isinstance(name, str) or not name for name in names)
                    or len(names) != len(set(names))):
                raise ValueError("Inventario sintético inválido o duplicado.")
        if set(self.local_games) & set(self.missing_games):
            raise ValueError("Un juego no puede estar presente y ausente a la vez.")
        if not set(self.conflicting_saves) <= set(self.save_updates):
            raise ValueError("Los conflictos deben pertenecer a las partidas pendientes.")


@dataclass(frozen=True)
class SimulatedStep:
    action: str
    before: str
    after: str
    intents: tuple[str, ...]
    revision: int
    evidence: str = SYNTHETIC_ONLY


class MockPSPDockSession:
    """Send synthetic observations through the real, pure Experience FSM.

    Only explicit calls progress the simulation. Intent objects are recorded
    as names, never dispatched to the OS or firmware. No automatic transfers,
    save resolutions, role changes, returns, or disconnections occur.
    """

    def __init__(self, inventory: MockInventory | None = None) -> None:
        self.inventory = inventory if inventory is not None else MockInventory()
        self.state = Experience()
        self.bound_identity: MockIdentity | None = None
        self.steps: list[SimulatedStep] = []
        self.local_games = set(self.inventory.local_games)
        self.mock_verified_game_copies: set[str] = set()
        self.conflict_resolutions: dict[str, Literal["psp", "pc"]] = {}
        self.initial_saves_synced = False
        self.initial_sync_approved = False
        self.return_sync_approved = False
        self.pending_pc_saves = False

    def _event(self, action: str, **synthetic_values: object) -> tuple[str, ...]:
        before = self.state
        # Experience is pure: a rejected event leaves state and log unchanged.
        after, intents = before.event(action, **synthetic_values)
        names = tuple(intent.name for intent in intents)
        self.state = after
        self.steps.append(SimulatedStep(
            action, before.stage.value, after.stage.value, names, after.revision,
        ))
        return names

    def attach_storage(
        self, identity: MockIdentity = MockIdentity(), *,
        sony_verified: bool = True, single_volume: bool = True,
    ) -> tuple[str, ...]:
        intents = self._event(
            "storage_arrived", volume_serial=identity.volume_serial,
            usb_parent=identity.usb_parent, sony_psp_verified=sony_verified,
            single_volume=single_volume,
        )
        if self.bound_identity is not None and identity != self.bound_identity:
            # Different fake Memory Stick: old save comparisons do not apply.
            self.initial_saves_synced = False
        # A consent decision belongs to exactly one attachment/return cycle.
        self.initial_sync_approved = False
        self.return_sync_approved = False
        self.conflict_resolutions.clear()
        self.bound_identity = identity
        return intents

    def audit_inventory(self) -> tuple[str, ...]:
        # A hash of fixture names, not an actual on-disk integrity check.
        digest = hashlib.sha256(repr(self.inventory).encode("utf-8")).hexdigest()
        saves = () if self.initial_saves_synced else self.inventory.save_updates
        conflicts = () if self.initial_saves_synced else self.inventory.conflicting_saves
        return self._event(
            "inventory_complete", inventory_tag=digest,
            missing_games=len(set(self.inventory.missing_games) - self.local_games),
            ready_games=len(self.local_games),
            save_actions=len(saves), save_conflicts=len(conflicts),
        )

    def choose_games(self, choice: Literal["copy", "skip"], *,
                     same_inventory: bool = True) -> tuple[str, ...]:
        return self._event("user_copy_choice", choice=choice,
                           same_inventory=same_inventory)

    def confirm_game_copies(self, *, verified: bool, same_identity: bool = True,
                            no_journals: bool = True) -> tuple[str, ...]:
        names = self._event(
            "copy_complete", copies_verified=verified,
            same_identity=same_identity, no_journals=no_journals,
        )
        # Mock ledger changes only after FSM accepts the verified-copy event.
        new_games = set(self.inventory.missing_games) - self.local_games
        self.local_games.update(new_games)
        self.mock_verified_game_copies.update(new_games)
        return names

    def resolve_save_conflict(self, save_id: str,
                              keep: Literal["psp", "pc"]) -> None:
        if self.state.stage != Stage.REVIEW_SAVES:
            raise ExperienceError("La revisión de partidas no está activa.")
        if save_id not in self.inventory.conflicting_saves or self.initial_saves_synced:
            raise ExperienceError("Conflicto de partida desconocido.")
        if keep not in ("psp", "pc"):
            raise ExperienceError("Elige expresamente PSP o PC.")
        self.conflict_resolutions[save_id] = keep

    def review_saves(self, *, approve_sync: bool = False,
                     same_identity: bool = True) -> tuple[str, ...]:
        outstanding = (set(self.inventory.conflicting_saves)
                       - set(self.conflict_resolutions))
        if self.initial_saves_synced:
            outstanding = set()
        names = self._event(
            "save_review_complete", same_identity=same_identity,
            conflicts_left=len(outstanding), user_approved_sync=approve_sync,
        )
        self.initial_sync_approved = (self.state.stage == Stage.SYNCING_SAVES)
        return names

    def confirm_initial_save_copies(self, *, verified: bool,
                                    no_conflicts: bool = True,
                                    no_journals: bool = True,
                                    same_identity: bool = True) -> tuple[str, ...]:
        if not self.initial_sync_approved:
            raise ExperienceError("Falta consentimiento para sincronizar partidas.")
        names = self._event(
            "save_sync_complete", copies_verified=verified,
            no_conflicts=no_conflicts, no_journals=no_journals,
            same_identity=same_identity,
        )
        self.initial_saves_synced = True
        return names

    def request_dock(
        self, *, synthetic_role_bridge_present: bool,
        synthetic_bridge_live_ack: bool,
        same_identity: bool = True, no_pending_io: bool = True,
        emulator_closed: bool = True,
    ) -> tuple[str, ...]:
        # This TEMPORARY fixture has the shape that the FSM requires. Its
        # positive attributes are FICTIONAL; they do not attest to hardware.
        # Never expose the object as a return value or in a checkpoint/log.
        from psp_dock.devices.usb_handoff import ROLE_CAPABILITIES as _capabilities
        from psp_dock.devices.usb_handoff import RoleBridgeProof as _MockTemporaryProofType

        mock_proof = _MockTemporaryProofType(
            source=("live_psp_role_bridge" if synthetic_role_bridge_present
                    else "SYNTHETIC_ONLY_UNQUALIFIED"),
            volume_serial=self.state.volume_serial,
            usb_parent=self.state.usb_parent,
            protocol_version=1,
            capabilities=_capabilities if synthetic_role_bridge_present
            else frozenset(),
            hardware_roundtrip_verified=synthetic_role_bridge_present,
            live_ack_verified=synthetic_bridge_live_ack,
        )
        return self._event(
            "start_dock", role_bridge_proof=mock_proof,
            same_identity=same_identity, no_pending_io=no_pending_io,
            emulator_closed=emulator_closed,
        )

    def lock_host_volume(self, *, exclusive: bool, flushed: bool) -> tuple[str, ...]:
        return self._event(
            "host_lock_acquired", exclusive_lock_verified=exclusive,
            cached_writes_flushed=flushed,
        )

    def firmware_prepare_ack(self, *, live_ack: bool, fat_safe: bool,
                             host_still_locked: bool) -> tuple[str, ...]:
        return self._event(
            "psp_prepare_ack", live_session_ack=live_ack,
            fat_ownership_safe=fat_safe, host_volume_still_locked=host_still_locked,
        )

    def stop_mock_storage(self, *, firmware_ack: bool,
                          host_still_locked: bool) -> tuple[str, ...]:
        return self._event(
            "storage_stopped", firmware_stop_ack=firmware_ack,
            host_volume_still_locked=host_still_locked,
        )

    def attach_mock_hid(
        self, *, session: str = MOCK_HID_SESSION,
        descriptor_verified: bool, same_usb: bool,
        storage_unmounted: bool, firmware_ack: bool,
    ) -> tuple[str, ...]:
        return self._event(
            "hid_attached", session=session,
            descriptor_verified=descriptor_verified,
            same_physical_usb=same_usb, storage_not_mounted=storage_unmounted,
            live_firmware_ack=firmware_ack,
        )

    def launch_game(self, game_id: str, *, same_hid_session: bool = True,
                    ) -> tuple[str, ...]:
        names = self._event(
            "launch_game", game_id=game_id,
            game_local_verified=(game_id in self.local_games),
            same_hid_session=same_hid_session,
        )
        # Conservatively track PC saves even if interrupted during gameplay.
        self.pending_pc_saves = True
        return names

    def close_game(self, *, ppsspp_closed: bool,
                   save_files_idle: bool) -> tuple[str, ...]:
        return self._event(
            "game_closed", ppsspp_closed=ppsspp_closed,
            save_files_idle=save_files_idle,
        )

    def exit_dock(self, *, same_hid_session: bool,
                  ppsspp_closed: bool) -> tuple[str, ...]:
        return self._event(
            "exit_dock", same_hid_session=same_hid_session,
            ppsspp_closed=ppsspp_closed,
        )

    def record_single_return_request(self, *, recorded: bool) -> tuple[str, ...]:
        return self._event("return_issued_once", attempt_recorded=recorded)

    def original_storage_returned(
        self, identity: MockIdentity = MockIdentity(), *,
        single_volume: bool = True, ppsspp_closed: bool = True,
    ) -> tuple[str, ...]:
        return self._event(
            "original_storage_returned", volume_serial=identity.volume_serial,
            usb_parent=identity.usb_parent, single_volume=single_volume,
            ppsspp_closed=ppsspp_closed,
        )

    def approve_return_save_sync(self) -> None:
        if self.state.stage != Stage.SYNC_BACK:
            raise ExperienceError("El retorno de partidas aún no está disponible.")
        self.return_sync_approved = True

    def confirm_return_save_copies(
        self, *, verified_backups: bool,
        no_conflicts: bool = True, no_journals: bool = True,
        same_identity: bool = True,
    ) -> tuple[str, ...]:
        if not self.return_sync_approved:
            raise ExperienceError("La sincronización de retorno requiere aprobación.")
        names = self._event(
            "return_sync_verified", verified_backups=verified_backups,
            no_conflicts=no_conflicts, no_journals=no_journals,
            same_identity=same_identity,
        )
        self.pending_pc_saves = False
        return names

    def unplug(self, *, host_storage_released: bool) -> tuple[str, ...]:
        return self._event("cable_removed",
                           host_storage_released=host_storage_released)

    def interrupt(self, reason: str = "Synthetic USB disconnect") -> tuple[str, ...]:
        return self._event("halt", reason=reason)

    def restart_after_interruption(self, identity: MockIdentity, *,
                                   sony_verified: bool = True,
                                   single_volume: bool = True) -> tuple[str, ...]:
        """Manual re-attach/re-audit; never resumes old USB/HID permissions."""
        if self.state.stage != Stage.HALTED or identity != self.bound_identity:
            raise ExperienceError("Recuperación requiere la PSP y tarjeta originales.")
        if not sony_verified or not single_volume:
            raise ExperienceError("Recuperación requiere identidad Sony y volumen único.")
        old = self.state
        self.state = Experience(revision=old.revision + 1)
        self.steps.append(SimulatedStep(
            "manual_recovery_restart", old.stage.value, Stage.WAITING.value,
            (), self.state.revision,
        ))
        self.initial_sync_approved = False
        self.return_sync_approved = False
        self.conflict_resolutions.clear()
        # Previously unsynced PC saves and verified mock PC games survive.
        # A completely NEW inventory/consent/role handoff is required.
        return self.attach_storage(identity, sony_verified=sony_verified,
                                   single_volume=single_volume)

    def checkpoint(self) -> dict:
        """JSON-safe model checkpoint. Never contains a proof or USB handle."""
        return {
            "mode": SYNTHETIC_ONLY,
            "state": self.state.snapshot(),
            "bound_identity": (asdict(self.bound_identity)
                               if self.bound_identity else None),
            "inventory": {key: list(value)
                          for key, value in asdict(self.inventory).items()},
            "local_games": sorted(self.local_games),
            "mock_verified_game_copies": sorted(self.mock_verified_game_copies),
            "conflict_resolutions": dict(self.conflict_resolutions),
            "initial_saves_synced": self.initial_saves_synced,
            "initial_sync_approved": self.initial_sync_approved,
            "return_sync_approved": self.return_sync_approved,
            "pending_pc_saves": self.pending_pc_saves,
            "steps": [{**asdict(step), "intents": list(step.intents)}
                      for step in self.steps],
        }

    @classmethod
    def from_checkpoint(cls, saved: dict) -> "MockPSPDockSession":
        """Restore simulation bookkeeping, without resuming a halted session."""
        keys = {
            "mode", "state", "bound_identity", "inventory", "local_games",
            "mock_verified_game_copies", "conflict_resolutions",
            "initial_saves_synced", "initial_sync_approved",
            "return_sync_approved", "pending_pc_saves", "steps",
        }
        if not isinstance(saved, dict) or set(saved) != keys or saved["mode"] != SYNTHETIC_ONLY:
            raise ExperienceError("Checkpoint no pertenece al simulador.")
        try:
            if set(saved["inventory"]) != set(MockInventory.__dataclass_fields__):
                raise ValueError("El inventario está incompleto")
            inventory = MockInventory(**{
                key: tuple(value) for key, value in saved["inventory"].items()
            })
            sim = cls(inventory)
            sim.state = Experience.from_snapshot(saved["state"])
            identity = saved["bound_identity"]
            sim.bound_identity = MockIdentity(**identity) if identity is not None else None
            sim.local_games = set(saved["local_games"])
            sim.mock_verified_game_copies = set(saved["mock_verified_game_copies"])
            sim.conflict_resolutions = dict(saved["conflict_resolutions"])
            for key in ("initial_saves_synced", "initial_sync_approved",
                        "return_sync_approved", "pending_pc_saves"):
                if type(saved[key]) is not bool:
                    raise ValueError("Indicador inválido")
                setattr(sim, key, saved[key])
            sim.steps = [SimulatedStep(
                action=step["action"], before=step["before"],
                after=step["after"], intents=tuple(step["intents"]),
                revision=step["revision"], evidence=step["evidence"],
            ) for step in saved["steps"]]
            if (not sim.local_games <= (set(inventory.local_games)
                                        | set(inventory.missing_games))
                    or not sim.mock_verified_game_copies <= sim.local_games
                    or not set(sim.conflict_resolutions) <= set(inventory.conflicting_saves)
                    or any(value not in ("pc", "psp")
                           for value in sim.conflict_resolutions.values())
                    or any(step.evidence != SYNTHETIC_ONLY for step in sim.steps)
                    or ((sim.state.volume_serial or sim.state.usb_parent)
                        and sim.bound_identity != MockIdentity(
                            sim.state.volume_serial, sim.state.usb_parent))):
                raise ValueError("Contenido sintético inválido")
        except (TypeError, ValueError, KeyError, AttributeError) as exc:
            raise ExperienceError("Checkpoint del simulador incompleto o corrupto.") from exc
        return sim


def run_mock_journey(choice: Literal["copy", "skip"] = "copy") -> MockPSPDockSession:
    """Demonstrate two games, explicit consent, full return and safe unplug."""
    sim = MockPSPDockSession()
    sim.attach_storage()
    sim.audit_inventory()
    sim.choose_games(choice)
    if choice == "copy":
        sim.confirm_game_copies(verified=True)
    sim.resolve_save_conflict("SAVE_RIDGE", "psp")
    sim.review_saves(approve_sync=True)
    sim.confirm_initial_save_copies(verified=True)
    sim.request_dock(synthetic_role_bridge_present=True,
                     synthetic_bridge_live_ack=True)
    sim.lock_host_volume(exclusive=True, flushed=True)
    sim.firmware_prepare_ack(live_ack=True, fat_safe=True, host_still_locked=True)
    sim.stop_mock_storage(firmware_ack=True, host_still_locked=True)
    sim.attach_mock_hid(descriptor_verified=True, same_usb=True,
                        storage_unmounted=True, firmware_ack=True)
    for game in ("DARIUSBURST", "LUMINES" if choice == "copy" else "RIDGE_RACER"):
        sim.launch_game(game)
        sim.close_game(ppsspp_closed=True, save_files_idle=True)
    sim.exit_dock(same_hid_session=True, ppsspp_closed=True)
    sim.record_single_return_request(recorded=True)
    sim.original_storage_returned()
    sim.approve_return_save_sync()
    sim.confirm_return_save_copies(verified_backups=True)
    sim.unplug(host_storage_released=True)
    return sim


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Simulación PSP Dock sin USB ni archivos")
    parser.add_argument("--choice", choices=("copy", "skip"), default="copy")
    args = parser.parse_args()
    print(json.dumps(run_mock_journey(args.choice).checkpoint(),
                     ensure_ascii=False, indent=2))
