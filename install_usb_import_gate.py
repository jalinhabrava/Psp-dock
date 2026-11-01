"""Install the USB *import gate* diagnostic in a NEW PSP folder only.

This copies a USER-mode launcher with no USB imports plus a read-only USER
module, for a future supervised hardware test. It never runs either program
or touches existing games, savedata, kernel settings, or flash.
"""
from __future__ import annotations

from pathlib import Path
import os
import shutil
import sys
import uuid

from dock import ROOT, inside, windows_devices
from install_safe_probe import digest
from lab_eject import ppsspp_open, real_dock_open
from psp_usb import identify, UsbTransitionError


PACKAGE = {
    "EBOOT.PBP": (
        "homebrew/usb-import-gate/dist/EBOOT.PBP",
        "7586403b47b316db1126bcce05b899f84864a2b9ebbdd4c31bb3ae0964240125",
    ),
    "usbstate.prx": (
        "homebrew/usb-import-gate/dist/usbstate.prx",
        "09e39d1459463cb043eb1b0f7a8a8cb472bedd7b856746011da5be47f70c1151",
    ),
}
DEST = "PSP/GAME/PSPDockUsbGate"


def install_package(psp_root: Path, sources: dict[str, tuple[Path, str]],
                    relative_dest: str = DEST) -> Path:
    """Never overwrite: stage both checksummed files, then publish directory.

    Caller is responsible for verifying the PSP's hardware identity before
    calling. On any pre-publish failure, leave target untouched.
    """
    if relative_dest not in (DEST, "PSP/GAME/PSPDockUsbGate06",
                            "PSP/GAME/PSPDockUsbStatus07",
                            "PSP/GAME/PSPDockBusCycle08"):
        raise ValueError("Destino de instalación no permitido.")
    target = inside(psp_root, relative_dest)
    if target.exists():
        raise ValueError("La prueba USB Import Gate ya existe. No se sobrescribe.")
    verified: dict[str, Path] = {}
    required = 0
    for name, (path, sha256) in sources.items():
        if Path(name).name != name or name not in PACKAGE:
            raise ValueError("Archivo de instalación no permitido.")
        path = Path(path)
        if not path.is_file() or digest(path) != sha256:
            raise ValueError("Origen ausente o checksum incorrecto: " + name)
        verified[name] = path
        required += path.stat().st_size
    if set(verified) != set(PACKAGE):
        raise ValueError("Faltan componentes del paquete.")
    if shutil.disk_usage(psp_root).free < required + 8 * 1024 * 1024:
        raise ValueError("No hay espacio para la prueba y su margen de seguridad.")
    stage = inside(psp_root, "PSP/GAME/.dock-gate-stage-" + uuid.uuid4().hex)
    stage.mkdir(parents=True, exist_ok=False)
    published = False
    try:
        for name, path in verified.items():
            destination = stage / name
            with path.open("rb") as reader, destination.open("xb") as writer:
                shutil.copyfileobj(reader, writer)
                writer.flush()
                os.fsync(writer.fileno())
            if digest(destination) != sources[name][1]:
                raise ValueError("Copia incompleta; etapa no publicada: " + name)
        if target.exists():
            raise ValueError("El destino apareció durante la copia.")
        os.rename(stage, target)
        published = True
        for name in verified:
            if digest(target / name) != sources[name][1]:
                raise ValueError("Error verificando el directorio publicado.")
        return target
    finally:
        if not published and stage.exists():
            shutil.rmtree(stage)


def main() -> int:
    try:
        if ppsspp_open() or real_dock_open():
            raise ValueError("Cierra PPSSPP y el PSP Dock real antes de instalar diagnósticos.")
        devices = windows_devices()
        if len(devices) != 1:
            raise ValueError("Conecta exactamente una PSP Sony usando el USB nativo del XMB.")
        root = Path(devices[0]["root"])
        identity, _ = identify(str(root))
        target = inside(root, DEST)
        if target.exists():
            raise ValueError("La carpeta del diagnóstico ya existe; no se sustituye.")
        sources = {name: (ROOT / rel, sha) for name, (rel, sha) in PACKAGE.items()}
        for name, (path, sha) in sources.items():
            if not path.is_file() or digest(path) != sha:
                raise ValueError("Binario no verificado: " + name)
        print("PSP identificada:", identity.model, identity.drive)
        print("Nueva carpeta:", target)
        print("Copia únicamente EBOOT.PBP y usbstate.prx.")
        print("La interfaz NO importa USB y no ejecutará llamadas USB hasta pulsar X.")
        print("La prueba permanece sin validar en hardware físico.")
        print("No toca firmware, partidas ni el homebrew anterior.")
        if input("Escribe PREPARAR para copiar (otra cosa cancela): ").strip().upper() != "PREPARAR":
            print("Cancelado sin escribir.")
            return 0
        new_identity, _ = identify(str(root))
        if new_identity != identity:
            raise ValueError("La identidad de la PSP cambió; instalación cancelada.")
        result = install_package(root, sources)
        print("Instalación verificada:", result)
        print("Expulsa la unidad de forma segura antes de desconectar la PSP.")
        return 0
    except (ValueError, OSError, UsbTransitionError) as exc:
        print("INSTALACIÓN CANCELADA:", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
