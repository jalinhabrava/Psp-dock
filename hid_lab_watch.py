"""Observe standalone PSP Dock HID v1 on Windows, NEVER send commands.

Run this watcher before opening HID-only LAB on PSP. It records live USB
storage/HID transitions in a PC-only JSONL file and reports a validated
PDK1 status. It does not eject volumes, issue SET_FEATURE, launch
emulators, or touch the PSP filesystem.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import time

from bridge_hid import probe as pdk1_probe
from dock import ROOT, windows_devices


def classify(storage_count: int, hid_devices: list[dict], errors: list) -> str:
    if errors:
        return "HID_PROBE_AMBIGUOUS"
    if len(hid_devices) > 1 or storage_count > 1:
        return "MULTIPLE_DEVICES_UNSAFE"
    if storage_count and hid_devices:
        return "STORAGE_AND_HID_UNSAFE"
    if hid_devices:
        status = hid_devices[0].get("status", {})
        if not isinstance(status, dict) or not status.get("connected"):
            return "HID_VISIBLE_NOT_CONFIGURED"
        return "HID_PDK1_VALID"
    if storage_count:
        return "STORAGE_VISIBLE"
    return "NO_PSP_INTERFACE"


def capture() -> dict:
    report = pdk1_probe()
    devices = report.get("devices") or []
    errors = report.get("errors") or []
    candidates = windows_devices()
    state = classify(len(candidates), devices, errors)
    return {
        "at_utc": datetime.now(timezone.utc).isoformat(),
        "state": state,
        "sony_storage_candidate_count": len(candidates),
        "hid_devices": devices,
        "hid_errors": errors,
        "live_firmware_fat_ack": False,
        "storage_hid_handoff_confirmed": False,
    }


def watch(seconds: int, interval: float) -> dict:
    out = ROOT / "data" / "hid-lab-observations"
    out.mkdir(parents=True, exist_ok=True)
    filename = out / ("hid-only-" + datetime.now().strftime("%Y%m%d-%H%M%S") + ".jsonl")
    deadline = time.monotonic() + seconds
    previous = None
    changes = 0
    hid_seen = False
    last_session = None
    print("Monitor HID iniciado. Abre PSP Dock - HID Only LAB desde Juegos.")
    print("NO expulsar la Memory Stick. NO abrir el modo USB nativo en el LAB.")
    print("Registro PC:", filename)
    with filename.open("x", encoding="utf-8") as output:
        while time.monotonic() < deadline:
            try:
                record = capture()
            except (ValueError, OSError, RuntimeError) as exc:
                record = {
                    "at_utc": datetime.now(timezone.utc).isoformat(),
                    "state": "OBSERVATION_FAILED",
                    "error": str(exc),
                }
            output.write(json.dumps(record, ensure_ascii=False) + "\n")
            output.flush()
            state = record["state"]
            if state != previous:
                print(record["at_utc"], state)
                previous = state
                changes += 1
            if state == "HID_PDK1_VALID":
                hid_seen = True
                status = record["hid_devices"][0]["status"]
                if status.get("session") != last_session:
                    last_session = status.get("session")
                    print("PDK1 validado. Sesión:", last_session)
                    print("Comprueba los botones en Windows con Win+R → joy.cpl.")
                # Remain active until deadline to capture device disappearance.
            time.sleep(interval)
    print("Terminado. HID PDK1 observado:", hid_seen, "cambios:", changes)
    print("Este registro NO confirma FAT ni el cambio automático USBSTOR/HID.")
    return {"file": str(filename), "hid_seen": hid_seen, "changes": changes}


def main() -> int:
    parser = argparse.ArgumentParser(description="Monitor PSP HID de solo lectura")
    parser.add_argument("--seconds", type=int, default=150)
    parser.add_argument("--interval", type=float, default=1.0)
    args = parser.parse_args()
    if not (10 <= args.seconds <= 600 and 0.5 <= args.interval <= 10):
        parser.error("Segundos 10-600, intervalo 0.5-10.")
    watch(args.seconds, args.interval)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
