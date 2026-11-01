"""PSP Dock: launch an already-copied PC game with the real PSP as HID.

Experimental *manual* HID -> PPSSPP stage. Not the final automatic dock.

No PSP filesystem writes, USB role switching, host eject, SET_FEATURE or
automatic save return. The PSP remains in the standalone HID-only homebrew
for this test. PPSSPP uses an isolated PC memstick and game copied to PC.

Commands:
  python ppsspp_hid_session.py             # read-only local game preview
  python ppsspp_hid_session.py --prepare   # private PC-only PPSSPP runtime
  python ppsspp_hid_session.py --play      # require live PDK1 and no PSP disk
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import subprocess
import time
from typing import Any

from bridge_hid import probe as hid_probe
from dock import (
    ROOT, acquire_instance_lock, atomic_json, digest, game_inventory,
    inside, prepare_emulator, windows_devices,
)
from lab_eject import ppsspp_open, real_dock_open


class HidPpssppError(ValueError):
    pass


EMULATOR = ROOT / "tools/PPSSPP/PPSSPPWindows64.exe"
LAB_DATA = ROOT / "data/ppsspp-hid-lab"
PRIVATE_MEMSTICK = LAB_DATA / "memstick"
SCALE = 3
BACKEND = "directx11"
MIN_GAME_BYTES = 1024 * 1024
PREPARED_NAME = "prepared.json"


@dataclass(frozen=True)
class LaunchPlan:
    live_data: Path
    library: Path
    game: Path
    relative: str
    size: int
    emulator: Path
    private_memstick: Path
    private_data: Path


def local_live_roots() -> list[Path]:
    """Find existing PC-only Dock state, including old Codex app virtualization.

    Do not search arbitrary disks and never use the PSP USB volume as a
    library. An explicit --live-data overrides discovery if needed.
    """
    local = Path(os.environ.get(
        "LOCALAPPDATA", str(Path.home() / "AppData/Local")
    ))
    return [
        local / "PSP-Dock/live",
        local / "Packages/OpenAI.Codex_2p2nqsd0c76g0/"
                "LocalCache/Local/PSP-Dock/live",
    ]


def find_live_data(explicit: Path | None = None) -> Path:
    if explicit is not None:
        candidate = Path(explicit)
        if not candidate.is_dir():
            raise HidPpssppError("La carpeta live del Dock no existe.")
        return candidate.resolve()
    candidates = []
    for candidate in local_live_roots():
        if candidate.is_dir() and (candidate / "library").is_dir():
            candidates.append(candidate.resolve())
    candidates = list(dict.fromkeys(candidates))
    if len(candidates) != 1:
        raise HidPpssppError(
            "No se encontró una única biblioteca PC existente. "
            "Indica --live-data con la carpeta local del Dock."
        )
    return candidates[0]


def _pc_only_library(live: Path, library: Path, private_data: Path) -> None:
    """Never allow a local-game plan to use the PSP's removable filesystem."""
    if live.is_symlink() or library.is_symlink() or private_data.is_symlink():
        raise HidPpssppError("No se admiten enlaces a rutas de juegos o guardados.")
    if library.is_relative_to(private_data) or private_data.is_relative_to(library):
        raise HidPpssppError("La biblioteca y el sandbox PPSSPP deben estar separados.")
    for candidate in windows_devices():
        drive = Path(candidate["root"]).resolve()
        if library.is_relative_to(drive) or private_data.is_relative_to(drive):
            raise HidPpssppError("La biblioteca o el sandbox están sobre la PSP USB.")
    if len(windows_devices()) > 1:
        raise HidPpssppError("Varias PSP detectadas: no inferir el origen de los juegos.")


def plan_game(
    live_data: Path | None = None,
    game_relative: str | None = None,
    *,
    private_data: Path = LAB_DATA,
    emulator: Path = EMULATOR,
) -> LaunchPlan:
    """Read-only preparation: only games already on the PC are eligible."""
    live = find_live_data(live_data)
    library = inside(live, "library")
    private_data = Path(private_data).absolute().resolve()
    _pc_only_library(live, library, private_data)
    games = [g for g in game_inventory(library) if g["format"] in ("ISO", "CSO")]
    if game_relative:
        chosen = [g for g in games if g["relative"] == game_relative]
        if len(chosen) != 1:
            raise HidPpssppError("El juego solicitado no está copiado en el PC.")
    elif len(games) == 1:
        chosen = games
    elif not games:
        raise HidPpssppError("No hay ISO/CSO locales; no leeremos juegos desde la PSP.")
    else:
        raise HidPpssppError(
            "Hay varios juegos locales; selecciona uno mediante --game RUTA_RELATIVA."
        )
    game = inside(library, chosen[0]["relative"])
    if not game.is_file() or game.stat().st_size < MIN_GAME_BYTES:
        raise HidPpssppError("El archivo local no parece una imagen de juego completa.")
    if game.suffix.lower() == ".iso":
        with game.open("rb") as stream:
            stream.seek(16 * 2048 + 1)
            if stream.read(5) != b"CD001":
                raise HidPpssppError("La ISO local no tiene descriptor ISO9660 esperado.")
    emulator = Path(emulator).absolute().resolve()
    if (emulator.name.lower() not in ("ppssppwindows64.exe", "ppssppwindows.exe")
            or not emulator.is_file()
            or not (emulator.parent / "assets").is_dir()):
        raise HidPpssppError("PPSSPP oficial portable no está completo.")
    return LaunchPlan(live, library, game, chosen[0]["relative"],
                      game.stat().st_size, emulator, private_data / "memstick",
                      private_data)


def validate_hid_only() -> dict[str, Any]:
    """Strict *live* HID gate; never treat a PDK1 snapshot as a FAT ACK."""
    storage = windows_devices()
    if storage:
        raise HidPpssppError(
            "PSP USBSTOR sigue montada. Sal del modo USB nativo antes de jugar."
        )
    report = hid_probe()
    if not report.get("supported") or report.get("errors"):
        raise HidPpssppError("No se ha podido verificar el dispositivo HID PDK1.")
    devices = report.get("devices")
    if not isinstance(devices, list) or len(devices) != 1:
        raise HidPpssppError("Se necesita exactamente un PSP Dock HID PDK1 conectado.")
    status = devices[0].get("status")
    if (not isinstance(status, dict) or
        status.get("connected") is not True or
        status.get("pending") is not False or
        status.get("consumed") is not False or
        status.get("mode") != 2 or status.get("version") != 1 or
        not isinstance(status.get("session"), str) or
        len(status["session"]) != 32):
        raise HidPpssppError("La sesión PDK1 no está conectada o tiene estado incorrecto.")
    try:
        session = bytes.fromhex(status["session"])
    except ValueError as exc:
        raise HidPpssppError("Sesión PDK1 con caracteres no hexadecimales.") from exc
    if len(session) != 16 or not any(session):
        raise HidPpssppError("Sesión PDK1 inválida o vacía.")
    return {"session": status["session"], "path": devices[0]["path"]}


def _prevent_concurrent_usage(plan: LaunchPlan) -> None:
    if real_dock_open():
        raise HidPpssppError("Hay un servicio PSP Dock real que podría sincronizar partidas.")
    if ppsspp_open():
        raise HidPpssppError("PPSSPP ya está abierto: cerrar antes de iniciar la sesión HID.")
    state_file = plan.live_data / "session.json"
    if state_file.is_file():
        try:
            session = json.loads(state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise HidPpssppError("Sesión anterior del Dock ilegible.") from exc
        if (not isinstance(session, dict) or session.get("running")
                or session.get("pending_return")):
            raise HidPpssppError("Hay una sesión anterior pendiente en el Dock real.")
    if list((plan.live_data / "memstick/PSP/SAVEDATA").glob(".dock-journal-*.json")):
        raise HidPpssppError("La sesión anterior tiene un journal de guardados pendiente.")


def prepare_private_runtime(plan: LaunchPlan) -> Path:
    """Prepare PPSSPP using PC-only paths; never touch PSP USB or live saves."""
    _prevent_concurrent_usage(plan)
    exe = prepare_emulator(
        plan.emulator, plan.private_data, plan.private_memstick,
        SCALE, BACKEND,
    )
    # Cache the work done BEFORE the 90-second HID experiment. Otherwise,
    # prepare_emulator() hashes every asset during --play, wasting test time.
    record = {
        "exe": str(exe.resolve()),
        "exe_sha256": digest(exe),
        "source_exe_sha256": digest(plan.emulator),
        "source_exe": str(plan.emulator),
        "memstick": str(plan.private_memstick),
        "scale": SCALE,
        "backend": BACKEND,
    }
    atomic_json(plan.private_data / PREPARED_NAME, record)
    return exe


def verified_prepared_runtime(plan: LaunchPlan) -> Path:
    """Fast PC-only validation; NEVER rehash/copy assets while HID is active."""
    record_file = plan.private_data / PREPARED_NAME
    try:
        record = json.loads(record_file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise HidPpssppError(
            "Prepara PPSSPP en el PC primero (--prepare), antes de abrir HID."
        ) from exc
    if not isinstance(record, dict) or any(record.get(k) != expected for k, expected in (
        ("source_exe", str(plan.emulator)),
        ("memstick", str(plan.private_memstick)),
        ("scale", SCALE),
        ("backend", BACKEND),
    )):
        raise HidPpssppError("La preparación privada PPSSPP no coincide con el plan.")
    if not isinstance(record.get("exe"), str):
        raise HidPpssppError("La ruta PPSSPP del manifiesto es inválida.")
    path = Path(record["exe"])
    runtime = plan.private_data / "runtime"
    if (not path.is_file() or not path.resolve().is_relative_to(runtime.resolve())
            or path.name != plan.emulator.name):
        raise HidPpssppError("Ejecutable PPSSPP privado ausente o fuera del sandbox.")
    installed = path.parent / "installed.txt"
    if not installed.is_file() or installed.read_text(encoding="utf-8").strip() != str(plan.private_memstick):
        raise HidPpssppError("PPSSPP privado no apunta a la Memory Stick local del PC.")
    if (digest(path) != record.get("exe_sha256")
            or digest(plan.emulator) != record.get("source_exe_sha256")):
        raise HidPpssppError("El ejecutable PPSSPP privado ha cambiado desde la preparación.")
    ini = plan.private_memstick / "PSP/SYSTEM/ppsspp.ini"
    if not ini.is_file():
        raise HidPpssppError("Falta el perfil privado PPSSPP.")
    return path


def validate_psp_controls(plan: LaunchPlan) -> Path:
    """Ensure our isolated PPSSPP profile has the physical PSP layout.

    Don't silently overwrite a user's manual mapping during gameplay.
    The repair command is an explicit, backed-up PC-only operation.
    """
    from ppsspp_psp_button_map import MappingError, remap_controls

    path = inside(plan.private_memstick, "PSP/SYSTEM/controls.ini")
    if not path.is_file():
        raise HidPpssppError(
            "Falta el perfil de botones de PPSSPP. "
            "Ejecuta Corregir-botones-PPSSPP-PSP.cmd antes de jugar."
        )
    try:
        _, needs_correction = remap_controls(path.read_bytes())
    except (MappingError, OSError, ValueError) as exc:
        raise HidPpssppError(
            "Mapeo PPSSPP personalizado o no reconocido: no lanzar a ciegas."
        ) from exc
    if needs_correction:
        raise HidPpssppError(
            "El perfil PPSSPP todavía tiene los botones PSP cruzados. "
            "Ejecuta Corregir-botones-PPSSPP-PSP.cmd."
        )
    return path


def start_ppsspp(plan: LaunchPlan) -> int:
    """Start local ISO in isolated PPSSPP *only* with verified PDK1 HID."""
    _prevent_concurrent_usage(plan)
    validate_psp_controls(plan)
    first = validate_hid_only()
    # Require a prepared profile so the 90-second HID session starts quickly.
    exe = verified_prepared_runtime(plan)
    if not exe.is_file():
        raise HidPpssppError("PPSSPP privado no pudo prepararse.")
    # Check *again* as late as possible; no unconditional launcher fallback.
    second = validate_hid_only()
    if second != first:
        raise HidPpssppError("La sesión PDK1 cambió durante la preparación.")
    if not plan.game.is_file() or plan.game.stat().st_size != plan.size:
        raise HidPpssppError("La ISO local cambió antes de iniciar PPSSPP.")
    cmd = [
        str(exe), "--fullscreen", "--pause-menu-exit",
        "--graphics=" + BACKEND, str(plan.game)
    ]
    print("PSP HID validada. PPSSPP ejecutará:", plan.game.name, flush=True)
    print("Partidas SOLO EN PC:", plan.private_memstick, flush=True)
    print("Este laboratorio NO devuelve partidas a la PSP automáticamente.", flush=True)
    process = subprocess.Popen(cmd, cwd=exe.parent)
    return process.wait()


def wait_for_hid_and_play(plan: LaunchPlan, seconds: int = 180) -> int:
    """Wait for the known PSP HID, then launch PC-only PPSSPP once.

    The watcher does NOT request any device changes. In particular it does
    not trigger Windows eject or write FAT to ask the PSP to switch roles.
    An ambiguous/foreign HID is an error; absence of HID is just waiting.
    """
    if not 10 <= seconds <= 600:
        raise HidPpssppError("Espera HID fuera de límites (10-600 segundos).")
    _prevent_concurrent_usage(plan)
    verified_prepared_runtime(plan)
    validate_psp_controls(plan)
    end = time.monotonic() + seconds
    last_state = None
    print("Buscando PSP Dock HID. Inicia HID Only LAB en la consola.", flush=True)
    print("El juego se abrirá automáticamente cuando aparezca PDK1 válido.", flush=True)
    while time.monotonic() < end:
        storage = windows_devices()
        if len(storage) > 1:
            raise HidPpssppError("Varias PSP USB detectadas: no iniciar PPSSPP.")
        report = hid_probe()
        devices = report.get("devices")
        if not report.get("supported") or report.get("errors") or not isinstance(devices, list):
            raise HidPpssppError("El dispositivo HID produjo un estado ambiguo.")
        if storage and devices:
            raise HidPpssppError("PSP HID y almacenamiento visibles a la vez: cancelado.")
        state = ("PSP en modo almacenamiento" if storage
                 else "Esperando mando PSP HID" if not devices
                 else "HID PDK1 candidato")
        if state != last_state:
            print(state, flush=True)
            last_state = state
        if devices:
            # Must be exactly one, with a verified PDK1 session and no G:.
            validate_hid_only()
            # Rechecks all safety gates immediately before Popen().
            return start_ppsspp(plan)
        time.sleep(1)
    raise HidPpssppError("No llegó una PSP HID PDK1 válida dentro del límite.")


def main() -> int:
    parser = argparse.ArgumentParser(description="PPSSPP local con mando PSP Dock HID real")
    parser.add_argument("--live-data", type=Path,
                        help="Carpeta de datos LIVE de PSP Dock, SOLO en el PC")
    parser.add_argument("--game", help="Ruta relativa exacta de ISO/CSO local")
    exclusive = parser.add_mutually_exclusive_group()
    exclusive.add_argument("--prepare", action="store_true",
                           help="Preparar PPSSPP en PC antes de abrir el LAB HID")
    exclusive.add_argument("--play", action="store_true",
                           help="Ejecutar solo si HID PDK1 está conectado y PSP USBSTOR no")
    exclusive.add_argument("--wait-and-play", action="store_true",
                           help="Laboratorio LEGACY: abrir PPSSPP automáticamente al ver HID; no es la UX del Dock")
    parser.add_argument("--explicit-legacy-autoplay", action="store_true",
                        help="Reconoce explícitamente que se desea autolanzar un juego SIN pasar por XMB")
    parser.add_argument("--wait-seconds", type=int, default=180)
    args = parser.parse_args()
    try:
        plan = plan_game(args.live_data, args.game)
        print("Juego copiado en PC:", plan.game.name, "("+str(plan.size)+" bytes)")
        print("PPSSPP:", plan.emulator)
        print("Sandbox de guardados exclusivo:", plan.private_memstick)
        if args.prepare:
            plan.private_data.mkdir(parents=True, exist_ok=True)
            with acquire_instance_lock(plan.private_data):
                runtime = prepare_private_runtime(plan)
            print("PPSSPP privado PREPARADO en:", runtime)
            print("NO se ha lanzado el juego ni escrito archivos en la PSP USB.")
            return 0
        if args.play:
            with acquire_instance_lock(plan.private_data):
                return start_ppsspp(plan)
        if args.wait_and_play:
            if not args.explicit_legacy_autoplay:
                raise HidPpssppError(
                    "Autolanzamiento LEGACY desactivado. Abre el XMB de PSP Dock "
                    "y elige el juego con la PSP. Úsalo solo si "
                    "añades --explicit-legacy-autoplay deliberadamente."
                )
            with acquire_instance_lock(plan.private_data):
                return wait_for_hid_and_play(plan, args.wait_seconds)
        print("PREVIEW: sin crear archivos ni iniciar PPSSPP.")
        print("Ejecuta --prepare antes de jugar. --wait-and-play espera al mando HID.")
        return 0
    except (HidPpssppError, OSError, ValueError, RuntimeError) as exc:
        print("PPSSPP HID no iniciado:", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
