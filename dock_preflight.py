"""Read-only connection assessment used by PSP Dock's XMB-style launcher.

This is *presentation planning*. It does not start HID, submit USB eject,
sync saves, copy games, or manufacture a PSP firmware/host ownership ACK.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any


@dataclass(frozen=True)
class StoragePreflight:
    phase: str
    connected: bool
    missing_games: int
    different_games: int
    ready_games: int
    save_actions: int
    save_conflicts: int
    saves_prepared: bool
    can_choose_games_on_pc: bool
    can_start_hid: bool
    reason: str
    next_action: str

    def snapshot(self) -> dict[str, Any]:
        return asdict(self)


def assess_storage(
    *, connected: bool, demo: bool, games: list[dict],
    saves: dict, session: dict, recovering: bool,
    current_volume_serial: str = "",
    hid_connected: bool = False,
) -> StoragePreflight:
    """Deterministic facts for the UI, never proof of a USB role transition."""
    if not isinstance(games, list) or not isinstance(saves, dict) or not isinstance(session, dict):
        raise ValueError("Estado de PSP Dock incompleto.")
    counts = {
        "missing": sum(g.get("availability") == "missing" for g in games),
        "different": sum(g.get("availability") == "different" for g in games),
        "ready": sum(g.get("availability") == "ready" for g in games),
        "saves": len(saves.get("actions") or []),
        "conflicts": len(saves.get("conflicts") or []),
    }
    phase = "no_device"
    next_action = "connect_psp"
    reason = "Conecta la PSP para comparar los juegos y guardados."
    if demo:
        phase, next_action, reason = (
            "demo", "inspect_demo", "Demostración: los juegos no son ejecutables."
        )
    elif recovering:
        phase, next_action, reason = (
            "recovery", "recover_session", "Existe una sesión interrumpida; no escribir."
        )
    elif session.get("running"):
        phase, next_action, reason = (
            "playing", "wait_game_close", "PPSSPP está abierto. Espera a que termine."
        )
    elif connected and (session.get("pending_return") or session.get("exit_requested")):
        phase, next_action, reason = (
            "return_review", "sync_return", "Revisa las partidas antes de desconectar."
        )
    elif session.get("exit_requested"):
        phase, next_action, reason = (
            "return_wait_storage", "restore_storage_manually",
            "Salida solicitada. El retorno HID→almacenamiento todavía es manual; no se han enviado órdenes USB."
        )
    elif session.get("pending_return") and hid_connected:
        phase, next_action, reason = (
            "hid_xmb", "launch_or_exit",
            "El mando HID sigue disponible. Puedes jugar otro título antes de salir del Dock."
        )
    elif session.get("pending_return"):
        phase, next_action, reason = (
            "return_wait_storage", "restore_storage_manually",
            "Las partidas del PC siguen pendientes de devolver. Vuelve al USB nativo con la misma Memory Stick."
        )
    elif connected:
        if counts["missing"]:
            phase, next_action, reason = (
                "copy_offer", "choose_missing_games",
                "Hay juegos instalados en la PSP que aún no están en el PC."
            )
        elif counts["different"]:
            phase, next_action, reason = (
                "version_review", "inspect_different_versions",
                "Hay versiones distintas: nunca se sobrescriben automáticamente."
            )
        elif counts["conflicts"]:
            phase, next_action, reason = (
                "save_conflicts", "resolve_save_conflicts",
                "Las versiones de algunas partidas necesitan una decisión."
            )
        elif counts["saves"]:
            phase, next_action, reason = (
                "save_sync_offer", "approve_save_sync",
                "Hay partidas nuevas o modificadas. Sincroniza antes de jugar."
            )
        elif counts["ready"]:
            if current_volume_serial and session.get("prepared_volume") == current_volume_serial:
                phase, next_action, reason = (
                    "storage_ready", "prepare_hid",
                    "Partidas preparadas. La transición automática aún requiere el bridge kernel."
                )
            else:
                phase, next_action, reason = (
                    "save_prepare_offer", "approve_save_sync",
                    "Confirma la preparación de partidas, incluso si no hay cambios que copiar."
                )
        else:
            phase, next_action, reason = (
                "no_ready_games", "choose_games",
                "No hay juegos copiados al PC para el modo Dock."
            )
    elif hid_connected and current_volume_serial and session.get("prepared_volume") == current_volume_serial:
        phase, next_action, reason = (
            "hid_xmb", "launch_or_exit",
            "Mando PDK1 reconocido en modo manual. Puedes seleccionar un juego local."
        )
    elif games:
        phase, next_action, reason = (
            "offline_library", "connect_psp",
            "Esta es una biblioteca recordada. Conecta la PSP para sincronizarla."
        )
    return StoragePreflight(
        phase=phase, connected=bool(connected) and not demo,
        missing_games=counts["missing"], different_games=counts["different"],
        ready_games=counts["ready"], save_actions=counts["saves"],
        save_conflicts=counts["conflicts"],
        # The remembered verified volume may already be absent because the
        # user has switched manually to the HID-only homebrew. This means
        # SAVES were prepared for that volume, NOT that the HID is bound
        # to the same physical PSP or that a role bridge exists.
        saves_prepared=bool(
            not demo and current_volume_serial and
            session.get("prepared_volume") == current_volume_serial
        ),
        can_choose_games_on_pc=not demo and not recovering and bool(connected),
        can_start_hid=False,  # Hard fail closed: live ACK bridge not installed.
        reason=reason, next_action=next_action,
    )
