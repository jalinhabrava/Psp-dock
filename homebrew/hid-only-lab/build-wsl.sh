#!/usr/bin/env bash
set -euo pipefail
export PSPDEV="${PSPDEV:-/home/david/projects/psp-dock-build/sdk/pspdev}"
export PATH="$PSPDEV/bin:/usr/local/bin:/usr/bin:/bin"
src="$(cd -- "$(dirname -- "$0")" && pwd)"
build=/home/david/projects/psp-dock-build/hid-only-lab
mkdir -p "$build"
cp "$src/main.c" "$src/lab_core.c" "$src/PSPDockHid.S" "$src/exports.exp" "$build/"
cp "$src/lab_gate.h" "$build/"
cp "$src/Makefile" "$build/Makefile"
cp "$src/../bridge-hid/bridge_hid.h" "$build/bridge_hid.h"
cd "$build"
make -j2
# The exported PSPDockHid stub must match the generated import flags/NIDs.
# Old lab source used 0x00010000; PSPDEV generates 0x00090000.
if ! grep -q 'STUB_START "PSPDockHid",0x00090000' PSPDockHid.S; then
    echo "Unexpected PSPDockHid import mode in lab build" >&2
    exit 1
fi
mv Makefile Makefile.user
cp "$src/Makefile.core" Makefile
make -j2
user_forbidden="$(psp-readelf -Ws "$build/pspdockhidonly.elf" |
    grep -E 'sceUsb|sceUsbstor|__stub_module_.*ForKernel' || true)"
if [[ -n "$user_forbidden" ]]; then
    echo "Unsafe kernel/USB import in USER EBOOT: $user_forbidden" >&2
    exit 1
fi
if ! psp-readelf -Ws "$build/hidonlycore.elf" | grep -q '__stub_modulestr_PSPDockHid'; then
    echo "Missing dynamic PSPDockHid import" >&2
    exit 1
fi
if psp-readelf -Ws "$build/hidonlycore.elf" |
    grep -E 'sceUsb(Start|Stop).*USBStor|sceUsbstorBoot' >/dev/null; then
    echo "USBSTOR import unexpectedly found" >&2
    exit 1
fi
mkdir -p "$src/dist"
cp "$build/EBOOT.PBP" "$build/hidonlycore.prx" "$src/dist/"
cp "$src/../bridge-hid/dist/bridgehid.prx" "$src/dist/"
sha256sum "$src/dist/EBOOT.PBP" "$src/dist/hidonlycore.prx" "$src/dist/bridgehid.prx"
