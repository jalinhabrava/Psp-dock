"""Build a checked bilingual PSP ZIP from audited PSPDEV native 0308 output.

This is a PC-only packaging operation. It never inspects or modifies a PSP,
USB volume, game, save file, firmware or installed homebrew.

Usage:
    python scripts/release/build_psp_package.py --version v0.1.2

The binary producers are homebrew/native-dock/build-wsl.sh and the existing
psp_native_package verifier. We don't rebuild or patch the PBP in this script.
"""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import shutil
import tempfile
from zipfile import ZIP_DEFLATED, ZipFile
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tools.psp.psp_native_package import FILES, REMOTE_FOLDER, make_package, verify_package


EN = """PSP Dock - installation (English)

This package contains the same English/Spanish homebrew. On startup PSP Dock
uses the system language set on your PSP; press TRIANGLE on the first screen to
switch between English and Spanish before starting the connection.

New installation:
1. Back up your game saves and any existing PSP Dock application.
2. With the PSP in Sony's normal USB Storage mode or using a Memory Stick
   reader, copy the entire PSP/GAME/PSPDockNative folder from this ZIP to
   the root of your Memory Stick.
3. Check that EBOOT.PBP and docknative.prx are in the SAME folder, along with
   the two license notices. EBOOT alone cannot start the native controller.
4. Exit Sony USB mode safely; launch PSP Dock on Windows and on the PSP, then
   follow the instructions shown on the screens.

If you ALREADY have PSP/GAME/PSPDockNative, never overwrite it blindly.
Back up your existing application and consult psp_native_upgrade.py in the
source repository for the verified upgrade procedure.

The package does not modify PSP flash or ARK plugins. It does not include
PPSSPP, games, BIOS or Sony firmware. Keep USB connected while files sync.
This is a new compiled binary; physical testing across consoles is ongoing.
See https://github.com/jalinhabrava/Psp-dock/blob/main/INSTALL.md
"""

ES = """PSP Dock - instalacion (Espanol)

Este paquete incluye ingles y espanol en el mismo homebrew. Al arrancar usa
el idioma configurado en tu PSP. Puedes pulsar TRIANGULO en la pantalla
inicial para elegir ingles o espanol antes de iniciar la conexion.

Instalacion nueva:
1. Haz una copia de seguridad de tus partidas y del PSP Dock anterior.
2. Con la PSP en modo USB original de Sony o usando un lector de Memory Stick,
   copia toda la carpeta PSP/GAME/PSPDockNative del ZIP a la raiz de la tarjeta.
3. Comprueba que EBOOT.PBP y docknative.prx estan JUNTOS con las dos licencias.
   El EBOOT por si solo no permite iniciar el controlador nativo.
4. Sal de USB Sony de forma segura, abre PSP Dock en Windows y en la consola,
   y sigue las indicaciones en pantalla.

Si ya existe PSP/GAME/PSPDockNative, no sobreescribas sin respaldo. Usa el
procedimiento de actualizacion verificada en psp_native_upgrade.py del
repositorio.

El paquete no escribe en flash ni instala plugins ARK. No incluye PPSSPP,
juegos, BIOS ni firmware Sony. Manten USB conectado durante la sincronizacion.
Esta nueva compilacion requiere pruebas fisicas adicionales.
Consulta https://github.com/jalinhabrava/Psp-dock/blob/main/INSTALL.es.md
"""


def checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build(*, version: str, target: Path, dist: Path) -> dict[str, str | int]:
    if not version.startswith("v0.") or not all(ch.isdigit() or ch in ".v" for ch in version):
        raise ValueError("Provide a version such as v0.1.2")
    if target.exists():
        raise FileExistsError("Existing archive must not be silently overwritten")
    with tempfile.TemporaryDirectory(prefix="pspdock-psp-release-") as folder:
        stage = Path(folder) / "verified"
        make_package(stage, dist=dist)
        # Extra root docs do not change the pinned four app files or their
        # signed-off SHA256 records in the package's manifest.
        (stage / "README-EN.txt").write_text(EN, encoding="utf-8")
        (stage / "README-ES.txt").write_text(ES, encoding="utf-8")
        verify_package(stage)
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            with ZipFile(target, "x", compression=ZIP_DEFLATED, compresslevel=9) as archive:
                for p in sorted(stage.rglob("*")):
                    if p.is_file():
                        archive.write(p, p.relative_to(stage).as_posix())
            with ZipFile(target, "r") as archive:
                if archive.testzip() is not None:
                    raise ValueError("ZIP CRC check failed")
                expected = {
                    *(f"{REMOTE_FOLDER}/{file}" for file in FILES),
                    "manifest.json", "LEER-ANTES-DE-PROBAR.txt",
                    "README-EN.txt", "README-ES.txt",
                }
                if set(archive.namelist()) != expected:
                    raise ValueError("Archive entries differ from the verified package")
                for filename in FILES:
                    with archive.open(f"{REMOTE_FOLDER}/{filename}") as stream:
                        data = stream.read()
                    if hashlib.sha256(data).hexdigest() != checksum(dist / filename):
                        raise ValueError(f"Archive binary checksum mismatch: {filename}")
        except Exception:
            target.unlink(missing_ok=True)
            raise
    return {
        "name": target.name, "bytes": target.stat().st_size,
        "sha256": checksum(target),
        "eboot_sha256": checksum(dist / "EBOOT.PBP"),
        "kernel_sha256": checksum(dist / "docknative.prx"),
    }


def main() -> None:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--version", default="v0.1.2")
    cli.add_argument("--dist", type=Path, default=ROOT / "homebrew/native-dock/dist")
    cli.add_argument("--output", type=Path)
    args = cli.parse_args()
    destination = args.output or ROOT / "dist/releases" / args.version / f"PSP-Dock-PSP-{args.version}.zip"
    print(build(version=args.version, target=destination, dist=args.dist))


if __name__ == "__main__":
    main()
