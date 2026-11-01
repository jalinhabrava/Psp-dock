"""Supervised host-side test of the PSP Dock HID RETURN_STORAGE.

Requires one validated PDK1 HID and a recent accepted safe eject of
the original memory stick. Does not launch PPSSPP or write saves.
"""
from __future__ import annotations

from datetime import datetime, timedelta
import json
import time

from bridge_hid import BridgeHidError, probe, request_storage
from dock import ROOT, windows_devices
from lab_eject import ppsspp_open
from psp_usb import UsbTransitionError, identify


def last_accepted_eject(now: datetime | None = None) -> dict:
    now = now or datetime.now()
    for file in sorted((ROOT / "docs").glob("probe-eject-20*.json"), reverse=True):
        data = json.loads(file.read_text(encoding="utf-8"))
        if (data.get("experiment") != "host_safe_eject_psp_probe" or
                not data.get("host", {}).get("host_eject_accepted")):
            continue
        event = datetime.fromisoformat(data["time"])
        if not timedelta(0) <= now - event <= timedelta(hours=1):
            raise UsbTransitionError("La última expulsión segura es demasiado antigua.")
        storage = data["storage"]
        if not storage.get("volume_serial") or not storage.get("usb_device_id"):
            raise UsbTransitionError("Expulsión sin identidad de tarjeta: no sirve para volver.")
        return storage
    raise UsbTransitionError("No existe un ensayo reciente de expulsión segura aceptada.")


def matching_storage(expected: dict) -> list[str]:
    matched = []
    for candidate in windows_devices():
        try:
            identity, _ = identify(candidate["root"])
            if (identity.volume_serial == expected["volume_serial"] and
                    identity.usb_device_id == expected["usb_device_id"]):
                matched.append(candidate["root"])
        except (UsbTransitionError, OSError):
            continue
    return matched


def run() -> int:
    if ppsspp_open():
        raise UsbTransitionError("Cierra PPSSPP antes de volver a almacenamiento.")
    expected = last_accepted_eject()
    info = probe()
    if info["errors"] or len(info["devices"]) != 1:
        raise UsbTransitionError("No se detectó exactamente un PSP Dock HID válido.")
    device = info["devices"][0]
    status = device["status"]
    if not status["connected"] or status["pending"] or status["consumed"]:
        raise UsbTransitionError("El HID no está listo para RETURN_STORAGE.")
    print("PSP Dock HID con sesión válida:", status["session"])
    if input("Escribe VOLVER para solicitar retorno USB: ").strip() != "VOLVER":
        print("Cancelado, sin cambios.")
        return 0
    request, error = None, None
    try:
        request = request_storage(device["path"], status["session"])
    except (BridgeHidError, OSError) as exc:
        error = str(exc)
        # SET_FEATURE may have been delivered before a transport error.
        # Never blindly resend a possibly consumed command.
    matched = []
    for _ in range(30):
        matched = matching_storage(expected)
        if len(matched) == 1:
            break
        time.sleep(1)
    ok = len(matched) == 1
    record = {
        "experiment": "hid_return_to_storage",
        "time": datetime.now().isoformat(),
        "hid_session": status["session"], "request": request,
        "transport_error": error,
        "original_volume_verified": ok, "matched_roots": matched,
    }
    filename = ROOT / "docs" / ("probe-return-" + datetime.now().strftime("%Y%m%d-%H%M%S") + ".json")
    filename.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Resultado:", filename)
    if not ok:
        print("Sin confirmación de la Memory Stick original. No se sincroniza.")
        return 2
    print("Retorno al mismo volumen de almacenamiento confirmado en Windows.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(run())
    except (UsbTransitionError, OSError, ValueError) as exc:
        print("PRUEBA NO REALIZADA:", exc)
        raise SystemExit(1)
