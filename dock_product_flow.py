"""Executable orchestration for one manually opened PSP Dock session.

Hardware observations and file operations belong to the injected adapter.
This module never derives USB ownership from a UI request, a directory name,
or successful delivery of a role command. ``ready`` requires an observed
controller, and every write callback requires observed storage for the bound
session. GET handlers may call ``snapshot`` without doing any I/O.

The caller normally calls ``observe`` then ``advance`` from Dock's existing
worker. HTTP actions only record intent: ``choose``, ``request_copy``,
``request_exit`` and ``retry``. The worker executes at most one callback per
``advance``. Callbacks must independently recheck physical identity around
their actual I/O and retain the existing per-file backup/hash/journal checks.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import threading
import time
from typing import Callable


class ProductFlowError(ValueError):
    pass


class ProductFlowCancelled(ProductFlowError):
    """A checkpoint between verified operations observed an entry cancellation."""


@dataclass(frozen=True)
class FlowCallbacks:
    """Each callback accepts a context dict and returns a result dict.

    inventory: fingerprint (SHA-256), counts, missing_game_ids.
    sync_initial: context includes choice and the offered inventory.
    copy_game: context includes one game_id and the offered inventory.
    sync_return: context AND result must say direction="to_psp".
    request_role: context includes role (storage/controller) and purpose;
                  result accepted=True only means command delivery.

    All contexts also contain identity={session_id, volume_serial} and a
    durable operation_id. None of these parameters come directly from UI.
    """

    inventory: Callable[[dict], dict]
    sync_initial: Callable[[dict], dict]
    copy_game: Callable[[dict], dict]
    sync_return: Callable[[dict], dict]
    request_role: Callable[[dict], dict]
    finalize_exit: Callable[[dict], dict] | None = None


SCHEMA = "pspdock-product-flow-v1"
CHOICES = frozenset(("all", "saves", "games", "none"))
COUNTS = ("psp_games", "pc_games", "missing_games", "save_actions", "save_conflicts")
PHASES = frozenset((
    "waiting_psp", "entering_storage", "inventory", "choosing", "syncing_in",
    "entering_controller", "ready", "waiting_storage_copy", "copying_game",
    "returning_to_controller", "waiting_storage_return", "syncing_out",
    "finalizing", "safe_to_disconnect", "attention",
))
STORAGE_WAIT = frozenset(("entering_storage", "waiting_storage_copy", "waiting_storage_return"))
CONTROLLER_WAIT = frozenset(("entering_controller", "returning_to_controller"))
ROLE_WAIT = STORAGE_WAIT | CONTROLLER_WAIT
STORAGE_WORK = frozenset(("inventory", "choosing", "syncing_in", "copying_game", "syncing_out"))
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_GAME_ID = re.compile(r"[0-9a-f]{24}\Z")
MESSAGES = {
    "waiting_psp": "Conecta tu PSP y abre PSP Dock en la consola.",
    "entering_storage": "Sincronizando PSP · esperando el almacenamiento.",
    "inventory": "Sincronizando PSP · comparando juegos y partidas.",
    "choosing": "¿Qué quieres sincronizar con este ordenador?",
    "syncing_in": "Sincronizando PSP · preparando el contenido elegido.",
    "entering_controller": "Preparando el modo mando.",
    "ready": "PSP lista. Puedes navegar por el Dock.",
    "waiting_storage_copy": "Preparando el almacenamiento para copiar el juego.",
    "copying_game": "Copiando el juego al Dock.",
    "returning_to_controller": "Copia terminada. Preparando el modo mando.",
    "waiting_storage_return": "Sincronizando PSP · preparando la devolución de partidas.",
    "syncing_out": "Sincronizando PSP · devolviendo partidas y estados compatibles.",
    "finalizing": "Finalizando la conexión con la PSP.",
    "safe_to_disconnect": "Sincronización terminada. Ya puedes desconectar la PSP.",
    "attention": "La sesión necesita revisión antes de continuar.",
}


def plan_session_return(entry_psp, entry_pc, current_psp, current_pc):
    """Plan only PC changes made during this session, always toward the PSP.

    The entry copies may deliberately differ when the user chose to use PC
    saves. That is not itself a conflict: a later changed PC save may replace
    the *unchanged* PSP entry version. A changed PSP target requires review.
    Current identical copies need no write. Deletions are never propagated.
    """
    maps = []
    for original in (entry_psp, entry_pc, current_psp, current_pc):
        if not isinstance(original, dict) or len(original) > 16384:
            raise ProductFlowError("Comparación de partidas de sesión inválida.")
        value = dict(original)
        if any(not isinstance(key, str) or not key or len(key) > 1024 or
               (signature is not None and (not isinstance(signature, str) or
                                          not _HEX64.fullmatch(signature)))
               for key, signature in value.items()):
            raise ProductFlowError("La comparación no contiene firmas válidas de partidas.")
        maps.append(value)
    before_psp, before_pc, now_psp, now_pc = maps
    actions, conflicts, ignored = [], [], []
    unchanged = 0
    for slot in sorted(set().union(*(set(value) for value in maps))):
        old_psp, old_pc = before_psp.get(slot), before_pc.get(slot)
        target, source = now_psp.get(slot), now_pc.get(slot)
        if source == target:
            unchanged += 1
            continue
        if source == old_pc:
            if target != old_psp:
                ignored.append(slot)
            unchanged += 1
            continue
        if source is None:
            conflicts.append({"slot": slot, "reason": "La partida del PC desapareció; no se propagan borrados.",
                              "source_hash": source, "target_hash": target,
                              "entry_psp_hash": old_psp, "entry_pc_hash": old_pc})
        elif target != old_psp:
            conflicts.append({"slot": slot, "reason": "La partida de la PSP cambió durante la sesión.",
                              "source_hash": source, "target_hash": target,
                              "entry_psp_hash": old_psp, "entry_pc_hash": old_pc})
        else:
            actions.append({"slot": slot, "direction": "to_psp",
                            "source_hash": source, "target_hash": target})
    return {"actions": actions, "conflicts": conflicts, "unchanged": unchanged,
            "ignored_psp_changes": ignored, "direction": "to_psp"}


def _empty_state():
    return {
        "schema": SCHEMA, "phase": "waiting_psp", "purpose": "initial",
        "identity": {}, "choice": None, "offer_id": "", "inventory": {},
        "game_id": "", "revision": 0, "return_required": False,
        "entry_completed": False, "cancel_requested": False,
        "resume_phase": "", "attention_reason": "", "conflicts": [],
        "recovery_required": False, "role_request_sent": False,
        "operation": {}, "last_result": {},
    }


def _atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    stage = path.with_name(path.name + ".tmp-" + secrets.token_hex(12))
    try:
        with stage.open("x", encoding="utf-8") as output:
            json.dump(value, output, ensure_ascii=False, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(stage, path)
    finally:
        stage.unlink(missing_ok=True)


def _observation(value):
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ProductFlowError("Observación de conexión inválida.")
    for key in ("session_id", "volume_serial"):
        if (not isinstance(value.get(key), str) or not value[key]
                or len(value[key]) > 256 or any(ord(c) < 32 for c in value[key])):
            raise ProductFlowError("La conexión no tiene una identidad de sesión y tarjeta válida.")
    for key in ("storage_ready", "controller_ready"):
        if type(value.get(key)) is not bool:
            raise ProductFlowError("Falta la comprobación del modo de la PSP.")
    if type(value.get("safe_ready", False)) is not bool:
        raise ProductFlowError("Estado de finalización USB inválido.")
    if type(value.get("exit_requested", False)) is not bool:
        raise ProductFlowError("Solicitud de salida de la PSP inválida.")
    if type(value.get("shutdown_pending", False)) is not bool:
        raise ProductFlowError("Estado de cierre nativo inválido.")
    if type(value.get("shutdown_cancel", False)) is not bool:
        raise ProductFlowError("Motivo de cierre nativo inválido.")
    if value.get("shutdown_pending") and (value["storage_ready"] or value["controller_ready"] or value.get("safe_ready")):
        raise ProductFlowError("El cierre pendiente no permite archivos ni mando.")
    phase = value.get("phase")
    if phase is not None and (type(phase) is not int or
            phase not in (1, 2, 3, 4, 5) and not (phase == 255 and value.get('shutdown_pending'))):
        raise ProductFlowError("Fase del transporte PSP inválida.")
    if not value["storage_ready"] and not value["controller_ready"] and not value.get("safe_ready", False) and not value.get("shutdown_pending", False):
        return None
    return {**{key: value[key] for key in
              ("session_id", "volume_serial", "storage_ready", "controller_ready")},
            "safe_ready": value.get("safe_ready", False), "phase": phase,
            "exit_requested": value.get("exit_requested", False),
            "shutdown_pending": value.get("shutdown_pending", False),
            "shutdown_cancel": value.get("shutdown_cancel", False)}


def _inventory(value):
    if not isinstance(value, dict) or not _HEX64.fullmatch(str(value.get("fingerprint", ""))):
        raise ProductFlowError("El inventario no tiene una firma comprobada.")
    counts = value.get("counts")
    if not isinstance(counts, dict) or any(
        type(counts.get(key)) is not int or not 0 <= counts[key] <= 1_000_000
        for key in COUNTS
    ):
        raise ProductFlowError("El inventario no incluye los recuentos necesarios.")
    ids = value.get("missing_game_ids")
    if (not isinstance(ids, list) or len(ids) > 2048 or
            any(not isinstance(i, str) or not _GAME_ID.fullmatch(i) for i in ids)
            or len(set(ids)) != len(ids) or counts["missing_games"] != len(ids)):
        raise ProductFlowError("La selección de juegos ausentes no coincide con el inventario.")
    # Only bounded facts needed by the coordinator enter its durable journal.
    return {"fingerprint": value["fingerprint"],
            "counts": {key: counts[key] for key in COUNTS},
            "missing_game_ids": list(ids)}


class ProductFlow:
    def __init__(self, journal_path, callbacks: FlowCallbacks, *, enabled=True,
                 capabilities=None, role_timeout=45.0, clock=time.monotonic):
        if type(enabled) is not bool or not isinstance(callbacks, FlowCallbacks):
            raise ProductFlowError("Configuración del flujo de producto inválida.")
        if not 0 < role_timeout <= 600:
            raise ProductFlowError("Tiempo de espera USB inválido.")
        self.path = Path(journal_path)
        self.callbacks = callbacks
        self.enabled = enabled
        caps = capabilities or {}
        self.capabilities = {
            "controller_during_storage": caps.get("controller_during_storage") is True,
            "automatic_transition": caps.get("automatic_transition") is True,
        }
        self._clock = clock
        self._timeout = float(role_timeout)
        self._lock = threading.RLock()
        self._busy = False
        self._game_running = False
        self._observed = None
        self._wait_since = self._clock()
        self._state = _empty_state()
        self._startup_recovery = False
        if self.path.exists():
            try:
                if self.path.stat().st_size > 512 * 1024:
                    raise ValueError("journal too large")
                value = json.loads(self.path.read_text(encoding="utf-8"))
                # Older journals have no early-cancellation fields. Preserve
                # their recovery requirement and infer only already-reached
                # controller sessions; this never renews permission to write.
                if isinstance(value, dict) and set(value) == set(_empty_state()) - {"entry_completed", "cancel_requested"}:
                    value.update(entry_completed=bool(value.get("return_required") or
                                 value.get("phase") == "ready" or value.get("purpose") in ("exit", "game_copy")),
                                 cancel_requested=False)
                self._validate_journal(value)
                self._state = value
            except (ValueError, TypeError, KeyError):
                self._state.update(phase="attention", recovery_required=True,
                                   attention_reason="El registro de sesión no es válido; revisa la recuperación.")
            else:
                # A process restart is never renewed permission to perform I/O.
                if value["phase"] == "safe_to_disconnect":
                    # A completed previous session does not strand the next
                    # manual launch on its old completion screen.
                    self._state = _empty_state()
                    self._state["revision"] = value["revision"]
                elif value["phase"] != "waiting_psp":
                    self._state.update(
                        phase="attention", recovery_required=True,
                        resume_phase=(value["resume_phase"] if value["phase"] == "attention"
                                      else value["phase"]),
                        attention_reason="La sesión anterior se interrumpió. Revisa los datos antes de reintentar.",
                    )
                    if value["return_required"] and value["purpose"] == "initial":
                        self._state.update(purpose="exit", resume_phase="waiting_storage_return")
                    elif value["cancel_requested"] and not value["entry_completed"]:
                        self._state.update(purpose="cancel", resume_phase="finalizing")
            # Presentation is separate from recovery authority. A new manual
            # launch welcomes the user and shows retained work as a notice;
            # the durable stop and explicit Retry guards remain unchanged.
            self._startup_recovery = self._state['phase'] == 'attention'

    @staticmethod
    def _validate_journal(value):
        if (not isinstance(value, dict) or set(value) != set(_empty_state()) or
                value["schema"] != SCHEMA or value["phase"] not in PHASES or
                value["purpose"] not in ("initial", "game_copy", "exit", "cancel") or
                value["choice"] not in CHOICES | {None} or
                type(value["revision"]) is not int or value["revision"] < 0 or
                type(value["return_required"]) is not bool or
                type(value["entry_completed"]) is not bool or
                type(value["cancel_requested"]) is not bool or
                type(value["recovery_required"]) is not bool or
                type(value["role_request_sent"]) is not bool or
                not isinstance(value["operation"], dict) or
                not isinstance(value["last_result"], dict) or
                not isinstance(value["conflicts"], list)):
            raise ValueError("invalid product journal")
        if value["inventory"]:
            _inventory(value["inventory"])
        if value["identity"]:
            ident = value["identity"]
            if set(ident) != {"session_id", "volume_serial"}:
                raise ValueError("invalid journal identity")
            _observation({**ident, "storage_ready": True, "controller_ready": False})
        if value["offer_id"] and not _HEX64.fullmatch(value["offer_id"]):
            raise ValueError("invalid journal offer")
        if value["game_id"] and not _GAME_ID.fullmatch(value["game_id"]):
            raise ValueError("invalid journal game")
        if value["resume_phase"] and value["resume_phase"] not in PHASES:
            raise ValueError("invalid journal resume phase")
        if (value["phase"] != "waiting_psp" and not value["identity"]):
            raise ValueError("journal without identity")

    def _commit(self, **changes):
        old = self._state
        updated = {**old, **deepcopy(changes), "revision": old["revision"] + 1}
        _atomic_json(self.path, updated)
        self._state = updated
        if updated["phase"] != old["phase"] and updated["phase"] in ROLE_WAIT:
            self._wait_since = self._clock()

    def _phase(self, phase, **changes):
        self._commit(phase=phase, role_request_sent=False, **changes)
        self._startup_recovery = False

    def _same(self, *, storage=False, controller=False, allow_new_session=False):
        seen, expected = self._observed, self._state["identity"]
        return bool(seen and expected and
                    seen["volume_serial"] == expected["volume_serial"] and
                    (allow_new_session or seen["session_id"] == expected["session_id"]) and
                    (not storage or seen["storage_ready"]) and
                    (not controller or seen["controller_ready"]))

    def _storage_for(self, purpose):
        # Legacy/manual adapters expose physical roles only. A native
        # adapter additionally proves the logical phase, so COPY/EXIT_SYNC
        # cannot be inferred just because Sony storage remains mounted.
        expected = {"initial": 1, "game_copy": 3, "exit": 4, "cancel": 1}[purpose]
        return bool(self._same(storage=True) and self._observed.get("phase") in (None, expected))

    def _attention(self, reason, *, conflicts=None, resume_phase=None, recovery_required=None):
        resume_phase = resume_phase or self._state["phase"]
        changes = {}
        if recovery_required is not None:
            changes['recovery_required'] = recovery_required
        if (self._state["return_required"] and self._state["purpose"] == "initial"
                and resume_phase == "ready"):
            # A reconnect after playing must retain the session comparison.
            # Renewed consent will finish the interrupted return, not capture
            # a new entry baseline that hides changes made on the PC.
            changes.update(purpose="exit")
            resume_phase = "waiting_storage_return"
        self._phase("attention", attention_reason=str(reason)[:1000],
                    resume_phase=resume_phase,
                    conflicts=deepcopy(conflicts or []), **changes)

    def _new_offer(self, inventory):
        value = {"identity": self._state["identity"], "inventory": inventory["fingerprint"],
                 "nonce": secrets.token_hex(16)}
        return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()

    def observe(self, observation):
        """Supply independently checked facts, never parameters from HTTP.

        session_id is the adapter's binding for this flow, not a claim that
        legacy manual HID has a kernel role ACK. A changed binding requires
        fresh review. Intermediate disappearance during a role change is
        expected and does not fabricate success or restart a command.
        """
        seen = _observation(observation)
        with self._lock:
            self._observed = seen
            if not self.enabled:
                return
            phase = self._state["phase"]
            if phase == "attention":
                if (seen and self._state["identity"] and
                        seen["volume_serial"] == self._state["identity"]["volume_serial"] and
                        seen["session_id"] != self._state["identity"]["session_id"] and
                        not self._state["recovery_required"]):
                    # A reconnect creates a fresh device nonce. It is the
                    # original card, but only an explicit Retry may bind the
                    # new session and authorize another callback.
                    self._commit(recovery_required=True)
                return  # New hardware observations are not renewed consent.
            if phase == "safe_to_disconnect":
                if seen is None:
                    self._commit(**{k: v for k, v in _empty_state().items()
                                    if k not in ("revision", "schema")})
                return
            if phase == "waiting_psp":
                if seen is not None:
                    identity = {key: seen[key] for key in ("session_id", "volume_serial")}
                    if seen.get('shutdown_pending'):
                        cancelling = seen.get('shutdown_cancel') is True
                        self._phase('attention',
                                    attention_reason='Queda un cierre de conexión pendiente. Reintentar comprobará sólo ese cierre.',
                                    resume_phase='finalizing', identity=identity,
                                    purpose='cancel' if cancelling else 'exit',
                                    cancel_requested=cancelling, entry_completed=not cancelling,
                                    recovery_required=True, offer_id=secrets.token_hex(32))
                        return
                    initial = ("inventory" if seen["storage_ready"] and seen.get("phase") in (None, 1)
                               else "entering_storage")
                    self._phase(initial, identity=identity, purpose="initial", choice=None,
                                inventory={}, game_id="", offer_id=secrets.token_hex(32),
                                attention_reason="", conflicts=[], recovery_required=False,
                                operation={}, last_result={}, return_required=False,
                                entry_completed=False, cancel_requested=False)
                    if seen.get("exit_requested"):
                        self.request_cancel()
                return
            if seen is not None and not self._same():
                self._attention("La conexión corresponde a otra sesión o Memory Stick.",
                                recovery_required=(seen["volume_serial"] == self._state["identity"].get("volume_serial")))
                return
            if seen and seen.get("exit_requested") and self.can_cancel_entry():
                self.request_cancel()
                return
            if phase in ROLE_WAIT:
                if phase in STORAGE_WAIT and self._storage_for(self._state["purpose"]):
                    target = {"entering_storage": "inventory", "waiting_storage_copy": "copying_game",
                              "waiting_storage_return": "syncing_out"}[phase]
                    self._phase(target)
                elif phase in CONTROLLER_WAIT and self._same(controller=True):
                    self._phase("ready", purpose="initial", game_id="", attention_reason="",
                                conflicts=[], recovery_required=False, entry_completed=True)
                return
            if phase == "ready" and not self._same(controller=True):
                if seen is None and not self._state["return_required"] and not self._game_running:
                    self._commit(**{k: v for k, v in _empty_state().items()
                                    if k not in ("revision", "schema")})
                else:
                    self._attention("El mando ya no está disponible; conserva la sesión para devolver las partidas.")
                return
            if phase == "finalizing" and seen is None and not self._busy:
                self._attention("La PSP desapareció antes de confirmar el cierre seguro.")
                return
            if phase in STORAGE_WORK and not self._storage_for(self._state["purpose"]):
                if phase in ("inventory", "choosing") and not self._busy:
                    self._commit(**{k: v for k, v in _empty_state().items()
                                    if k not in ("revision", "schema")})
                else:
                    self._attention("El almacenamiento dejó de estar disponible durante la operación.")

    def _check_action(self):
        if not self.enabled:
            raise ProductFlowError("El flujo de producto no está disponible para esta conexión.")
        if self._busy or self._game_running:
            raise ProductFlowError("Espera a que termine la operación o el juego en curso.")

    def _check_offer(self, offer_id):
        if (not isinstance(offer_id, str) or not self._state["offer_id"] or
                not secrets.compare_digest(offer_id, self._state["offer_id"])):
            raise ProductFlowError("La selección pertenece a otra comparación. Actualiza la pantalla.")

    def choose(self, choice, offer_id):
        with self._lock:
            self._check_action()
            self._check_offer(offer_id)
            if (not isinstance(choice, str) or choice not in CHOICES or
                    self._state["phase"] != "choosing" or not self._storage_for("initial")):
                raise ProductFlowError("No hay una elección de sincronización disponible para esta PSP.")
            # NONE does not call sync_initial, even if files differ.
            self._phase("entering_controller" if choice == "none" else "syncing_in",
                        choice=choice, purpose="initial", attention_reason="", conflicts=[])

    def request_copy(self, game_id):
        with self._lock:
            self._check_action()
            if self._state["phase"] != "ready" or not self._same(controller=True):
                raise ProductFlowError("La copia puntual requiere una sesión de mando preparada.")
            if game_id not in self._state["inventory"].get("missing_game_ids", []):
                raise ProductFlowError("El juego no pertenece a los títulos pendientes de esta PSP.")
            self._phase("waiting_storage_copy", purpose="game_copy", game_id=game_id,
                        attention_reason="", conflicts=[])

    def request_exit(self):
        with self._lock:
            self._check_action()
            if self._state["phase"] != "ready" or not self._same(controller=True):
                raise ProductFlowError("La salida requiere una sesión de Dock preparada.")
            self._phase("waiting_storage_return", purpose="exit", game_id="",
                        attention_reason="", conflicts=[])

    def can_cancel_entry(self):
        with self._lock:
            return bool(self.enabled and not self._state["entry_completed"] and
                        not self._state["return_required"] and not self._game_running and
                        not self._state["recovery_required"] and
                        self._state["phase"] not in ("waiting_psp", "safe_to_disconnect"))

    def request_cancel(self):
        """Cancel entry without importing/exporting anything new.

        A callback already admitted may finish its current verified file.
        Its next checkpoint or successful return then goes straight to the
        eject callback. Errors retain attention/recovery and never imply that
        an incomplete copy became verified merely because exit was requested.
        """
        with self._lock:
            if not self.can_cancel_entry() or not self._same():
                raise ProductFlowError("No hay una entrada de esta PSP que se pueda cancelar.")
            if self._state["cancel_requested"]:
                return
            if (self._state["phase"] == "attention" and
                    self._state["operation"].get("status") == "started" and
                    self._state["operation"].get("name") != "inventory"):
                self._commit(cancel_requested=True, purpose="cancel", resume_phase="finalizing")
            elif self._busy:
                self._commit(cancel_requested=True)
            else:
                self._phase("finalizing", purpose="cancel", cancel_requested=True,
                            game_id="", attention_reason="", conflicts=[])

    def set_game_running(self, running):
        if type(running) is not bool:
            raise ProductFlowError("Estado del juego inválido.")
        with self._lock:
            if running and (self._busy or self._state["phase"] != "ready" or
                            not self._same(controller=True)):
                raise ProductFlowError("La sesión de mando no está preparada para jugar.")
            if running and not self._state["return_required"]:
                self._commit(return_required=True)
            self._game_running = running
            # Closing a game deliberately performs NO role transition.

    def _can_retry(self):
        if self._state["phase"] != "attention" or self._busy or self._game_running:
            return False
        if self._observed and self._observed.get("shutdown_pending"):
            # A released medium cannot be scanned to renew a binding.
            # Only the exact retained nonce may finish this close.
            return (self._state["resume_phase"] == "finalizing" and self._same())
        if self._state["recovery_required"]:
            finalizing = (self._state["resume_phase"] == "finalizing" and
                          self._observed and self._observed.get("safe_ready"))
            return self._same(storage=not finalizing, allow_new_session=True)
        return self._same()

    def retry(self, offer_id):
        """Explicit renewed consent; merely refreshing never retries I/O.

        An interrupted initial sync returns to a fresh inventory and choice.
        Copy/exit retain the exact authorized purpose; their callbacks must
        independently verify any incomplete per-file journals before writing.
        After process restart the original card must be seen as storage first.
        """
        with self._lock:
            self._check_action()
            self._check_offer(offer_id)
            if not self._can_retry():
                raise ProductFlowError("Conecta la Memory Stick original y revisa la recuperación antes de reintentar.")
            identity = {key: self._observed[key] for key in ("session_id", "volume_serial")}
            purpose = self._state["purpose"]
            target = {"initial": "entering_storage", "game_copy": "waiting_storage_copy",
                      "exit": "waiting_storage_return", "cancel": "finalizing"}[purpose]
            if purpose in ("exit", "cancel") and self._state["resume_phase"] == "finalizing":
                target = "finalizing"
            changes = {"identity": identity, "recovery_required": False,
                       "attention_reason": "", "conflicts": [], "operation": {},
                       "resume_phase": "", "offer_id": secrets.token_hex(32)}
            if purpose == "initial":
                changes.update(choice=None, inventory={})
            self._phase(target, **changes)

    def halt(self, reason):
        """Stop after an integration failure; never grant renewed consent."""
        with self._lock:
            self._attention(reason)

    def _context(self, operation_id):
        state = self._state
        return {"identity": deepcopy(state["identity"]), "operation_id": operation_id,
                "purpose": state["purpose"], "choice": state["choice"],
                "inventory": deepcopy(state["inventory"]), "game_id": state["game_id"]}

    def advance(self):
        """Execute at most one callback. Results never substitute for USB observations."""
        with self._lock:
            if not self.enabled or self._busy or self._game_running:
                return False
            phase = self._state["phase"]
            if phase in ROLE_WAIT:
                if not self.capabilities["automatic_transition"]:
                    return False  # Manual adapter: wait for a real observed role.
                if self._clock() - self._wait_since >= self._timeout:
                    self._attention("La PSP no confirmó el cambio de modo. Revisa la conexión antes de reintentar.")
                    return False
                if self._state["role_request_sent"]:
                    return False
                if not self._same():
                    return False
                name = "request_role"
            else:
                name = {"inventory": "inventory", "syncing_in": "sync_initial",
                        "copying_game": "copy_game", "syncing_out": "sync_return",
                        "finalizing": "finalize_exit"}.get(phase)
                if (name is None or not self._same() or
                        name != "finalize_exit" and not self._storage_for(self._state["purpose"])):
                    return False
            operation_id = secrets.token_hex(16)
            context = self._context(operation_id)
            if name == "request_role":
                context["role"] = "storage" if phase in STORAGE_WAIT else "controller"
            if name == "sync_return":
                context["direction"] = "to_psp"
            operation = {"id": operation_id, "name": name, "status": "started", "phase": phase}
            # This durable intent MUST succeed before the adapter gets a call.
            self._commit(operation=operation)
            self._busy = True
        try:
            callback = getattr(self.callbacks, name)
            if callback is None:
                raise ProductFlowError("Falta el adaptador de cierre seguro del Dock.")
            result = callback(deepcopy(context))
            if not isinstance(result, dict):
                raise ProductFlowError("La operación no devolvió un resultado verificable.")
            if name == "inventory":
                result = _inventory(result)
            elif name == "request_role":
                if result.get("accepted") is not True:
                    raise ProductFlowError(result.get("reason") or "La solicitud de cambio de modo no fue aceptada.")
            else:
                if type(result.get("verified")) is not bool:
                    raise ProductFlowError("La operación no indicó si sus datos se verificaron.")
                conflicts = result.get("conflicts", [])
                if not isinstance(conflicts, list) or len(conflicts) > 2048:
                    raise ProductFlowError("Resultado de conflictos inválido.")
                if name == "sync_return" and result.get("direction") != "to_psp":
                    raise ProductFlowError("La salida sólo permite devolver guardados y estados a la PSP.")
                if name == "sync_return" and any(
                    direction != "to_psp" for direction in result.get("directions", [])
                ):
                    raise ProductFlowError("La devolución contiene una dirección no autorizada.")
                if name == "sync_initial" and result.get("inventory") is not None:
                    result = {**result, "inventory": _inventory(result["inventory"])}
                if name == "finalize_exit" and (result.get("verified") is not True or
                                                result.get("host_eject_accepted") is not True):
                    raise ProductFlowError("Windows no confirmó la expulsión segura de esta PSP.")
        except ProductFlowCancelled as exc:
            with self._lock:
                self._busy = False
                if (self._state["operation"].get("id") == operation_id and
                        self._state["cancel_requested"] and not self._state["entry_completed"]):
                    try:
                        self._phase("finalizing", purpose="cancel", conflicts=[], attention_reason="",
                                    operation={**operation, "status": "cancelled_at_checkpoint"},
                                    last_result={"entry_cancelled": True})
                    except OSError:
                        self._state.update(phase="attention", purpose="cancel", resume_phase="finalizing",
                                           recovery_required=True, attention_reason="No se pudo registrar el cierre. Revisa la recuperación.")
                else:
                    self._attention(str(exc), resume_phase=phase)
            return True
        except Exception as exc:
            # Includes adapter errors, but not KeyboardInterrupt/SystemExit.
            with self._lock:
                self._busy = False
                if self._state["phase"] == phase and self._state["operation"].get("id") == operation_id:
                    try:
                        if self._state["cancel_requested"]:
                            self._commit(purpose="cancel")
                        self._attention(str(exc), resume_phase=phase)
                    except OSError:
                        self._state.update(phase="attention", resume_phase=phase,
                                           recovery_required=True,
                                           attention_reason="No se pudo guardar el resultado. Revisa la recuperación.")
            return True
        with self._lock:
            self._busy = False
            if self._state["phase"] != phase or self._state["operation"].get("id") != operation_id:
                return True  # Connection changed during the call; never overwrite that stop.
            completed = {**operation, "status": "completed"}
            try:
                if self._state["cancel_requested"] and name != "finalize_exit":
                    self._phase("finalizing", purpose="cancel", operation=completed,
                                attention_reason="", conflicts=[], last_result={"entry_cancelled": True})
                elif name == "inventory":
                    self._phase("choosing", inventory=result, offer_id=self._new_offer(result),
                                operation=completed, last_result={"inventory_verified": True})
                elif name == "request_role":
                    self._commit(role_request_sent=True, operation=completed,
                                 last_result={"command_delivered": True, "role": context["role"]})
                elif not result["verified"] or result.get("conflicts"):
                    self._commit(operation=completed, last_result={"verified": False})
                    self._attention(result.get("reason") or "Hay datos pendientes de revisión.",
                                    conflicts=result.get("conflicts"), resume_phase=phase)
                elif name == "sync_initial":
                    refreshed = result.get("inventory", self._state["inventory"])
                    self._phase("entering_controller", inventory=refreshed, operation=completed,
                                last_result={"verified": True, "choice": context["choice"]})
                elif name == "copy_game":
                    inventory = deepcopy(self._state["inventory"])
                    inventory["missing_game_ids"].remove(context["game_id"])
                    inventory["counts"]["missing_games"] -= 1
                    self._phase("returning_to_controller", inventory=inventory, operation=completed,
                                last_result={"verified": True, "game_id": context["game_id"]})
                elif name == "sync_return":
                    self._phase("finalizing", operation=completed,
                                last_result={"verified": True, "direction": "to_psp"})
                else:
                    self._phase("safe_to_disconnect", return_required=False, operation=completed,
                                last_result={"verified": True, "host_eject_accepted": True})
            except OSError:
                # A copy may already be committed. The old durable STARTED
                # record and this in-memory stop forbid automatic repetition.
                self._state.update(phase="attention", resume_phase=phase, recovery_required=True,
                                   attention_reason="La operación terminó pero no se pudo guardar el resultado. Revisa la recuperación.")
            return True

    def snapshot(self):
        """A cheap copy for the UI; no discovery, file access or callbacks."""
        with self._lock:
            state = self._state
            phase = state["phase"]
            message = MESSAGES[phase]
            if phase in ROLE_WAIT and not self.capabilities["automatic_transition"]:
                message = ("Activa el almacenamiento USB de la misma PSP para continuar."
                           if phase in STORAGE_WAIT else
                           "Activa PSP Dock en modo mando para continuar.")
            if state["purpose"] == "cancel" and phase == "safe_to_disconnect":
                message = "Conexión cerrada. Ya puedes desconectar la PSP."
            return {
                "enabled": self.enabled, "phase": phase, "purpose": state["purpose"],
                "startup_recovery": self._startup_recovery and phase == 'attention',
                "resume_phase": state["resume_phase"],
                "entry_completed": state["entry_completed"], "cancel_requested": state["cancel_requested"],
                "blocking": phase != "ready", "message": message,
                "can_choose": (phase == "choosing" and not self._busy and self._same(storage=True)),
                "offer_id": state["offer_id"], "choice": state["choice"],
                "use_pc_saves": state["choice"] in ("games", "none"),
                "counts": deepcopy(state["inventory"].get("counts", {key: 0 for key in COUNTS})),
                "capabilities": dict(self.capabilities),
                "attention_reason": state["attention_reason"],
                "can_review_saves": bool(phase == "attention" and state["conflicts"]
                                         and self._same(storage=True) and not self._busy),
                "can_retry": self._can_retry(),
                "can_cancel": bool(self.can_cancel_entry() and self._same() and
                                   not self._busy and not state["cancel_requested"] and
                                   phase != "finalizing" and
                                   not (phase == "attention" and
                                        state["operation"].get("status") == "started" and
                                        state["operation"].get("name") != "inventory")),
                "can_close_app": phase == "attention" and not self._busy and not self._game_running,
                "can_close": phase == "safe_to_disconnect" and not self._busy,
                "operation_active": self._busy, "game_running": self._game_running,
                "recovery_required": state["recovery_required"],
                "revision": state["revision"], "last_result": deepcopy(state["last_result"]),
            }
