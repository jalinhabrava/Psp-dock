"""Read-only classification of a live PSP Dock controller.

The application calls this while it is open. Classification grants no USB
role transition, disk access or game launch. In particular the 01CC/0207
MSC proof remains a synthetic-storage diagnostic, even with a valid PDK1
session. The older 01CB/0204 WinUSB prototype is not the stable controller.
"""
from __future__ import annotations

import re

import psp_dock.devices.bridge_hid as bridge_hid


class DockIdentityError(ValueError):
    """A candidate is unsupported, ambiguous, stale or unverifiable."""


_PROFILES = {
    (bridge_hid.PID, 0x0104): ("hid_manual", True, False),
    (bridge_hid.MSC_TEST_PID, 0x0207): ("msc_diagnostic", False, True),
}
_MSC_PATH = re.compile(r"^\\\\\?\\hid#vid_054c&pid_01cc&mi_00(?:&|#)", re.I)


def _classify(device: dict, expected_product: int) -> dict:
    if not isinstance(device, dict):
        raise DockIdentityError("La identidad HID no tiene un formato válido.")
    vendor, product, revision = (device.get(key) for key in
                                 ("vendor_id", "product_id", "revision"))
    if (any(type(value) is not int for value in (vendor, product, revision)) or
            vendor != bridge_hid.VID or product != expected_product or
            (product, revision) not in _PROFILES):
        raise DockIdentityError("La revisión HID no corresponde a un modo Dock admitido.")
    path = device.get("path")
    if (not isinstance(path, str) or
            not bridge_hid.expected_product_pattern(product).match(path) or
            (product == bridge_hid.MSC_TEST_PID and not _MSC_PATH.match(path))):
        raise DockIdentityError("La ruta HID no coincide con la identidad del Dock.")
    status = device.get("status")
    if (not isinstance(status, dict) or
            status.get("connected") is not True or
            status.get("pending") is not False or status.get("consumed") is not False or
            any(type(status.get(key)) is not int or status[key] != value
                for key, value in (("version", 1), ("mode", 2),
                                   ("sequence", 0), ("result", 0)))):
        raise DockIdentityError("La sesión PDK1 no está conectada y disponible.")
    session = status.get("session")
    if (not isinstance(session, str) or
            re.fullmatch(r"[0-9a-fA-F]{32}", session) is None or
            not any(bytes.fromhex(session))):
        raise DockIdentityError("La sesión PDK1 no es válida.")
    mode, can_play, synthetic_storage = _PROFILES[(product, revision)]
    return {"path": path, "session": session.lower(), "product_id": product,
            "revision": revision, "mode": mode, "can_play": can_play,
            "synthetic_storage": synthetic_storage}


def probe_dock(*, probe=None) -> dict | None:
    """Return one verified Dock, None when absent, or fail closed on doubt.

    Both product families are checked before accepting either. A second
    candidate, failed feature query or unknown revision never becomes a
    fallback to whichever controller happened to answer first.
    """
    reader = bridge_hid.probe if probe is None else probe
    candidates = []
    for product in (bridge_hid.PID, bridge_hid.MSC_TEST_PID):
        try:
            report = reader(expected_product=product)
        except (OSError, ValueError, bridge_hid.BridgeHidError) as exc:
            raise DockIdentityError("No se pudo comprobar la identidad HID del Dock.") from exc
        if (not isinstance(report, dict) or report.get("supported") is not True or
                not isinstance(report.get("errors"), list) or report["errors"] or
                not isinstance(report.get("devices"), list)):
            raise DockIdentityError("La comprobación HID está incompleta o contiene errores.")
        candidates.extend((device, product) for device in report["devices"])
    if not candidates:
        return None
    if len(candidates) != 1:
        raise DockIdentityError("Hay más de un mando candidato; no se puede elegir una PSP.")
    return _classify(*candidates[0])
