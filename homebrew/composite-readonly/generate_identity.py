"""Build-time public interface identifier and deterministic test file."""
from pathlib import Path
import hashlib
import json
import sys
import uuid

GUID = uuid.uuid5(uuid.NAMESPACE_URL,
    "https://github.com/jalinhabrava/Psp-dock/usb-composite/vendor-file-v1")

def generate(folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "pdc_identity.h").write_text(
        '#ifndef PDC_IDENTITY_H\n#define PDC_IDENTITY_H\n'
        f'#define PDC_INTERFACE_GUID "{{{str(GUID).upper()}}}"\n'
        '#endif\n', encoding="utf-8")
    data = bytes((i * 17 + 3) % 256 for i in range(150003))
    (folder / "PROBE.DAT").write_bytes(data)
    (folder / "probe-manifest.json").write_text(json.dumps({
        "relative": "PSP/GAME/PSPDockCompositeTest/PROBE.DAT",
        "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
        "interface_guid": str(GUID), "synthetic": True,
    }, indent=2) + "\n", encoding="utf-8")

if __name__ == "__main__":
    generate(Path(sys.argv[1]))
