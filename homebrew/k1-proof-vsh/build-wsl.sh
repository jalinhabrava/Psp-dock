#!/usr/bin/env bash
set -euo pipefail
export PSPDEV="${PSPDEV:-/home/david/projects/psp-dock-build/sdk/pspdev}"
export PATH="$PSPDEV/bin:/usr/local/bin:/usr/bin:/bin"
src="$(cd -- "$(dirname -- "$0")" && pwd)"
build=/home/david/projects/psp-dock-build/k1-proof-vsh
mkdir -p "$build"
cp "$src/main.c" "$src/Makefile" "$src/PSPDockK1Observer.S" "$src/exports.exp" "$build/"
cp "$src/../kernel-usb-observer-k1/protocol.h" "$build/protocol.h"
# Keep include path relative to the installed source tree via an isolated
# build copy: same include directory arrangement, do not alter K1 PRX.
sed 's|../kernel-usb-observer-k1/protocol.h|protocol.h|' "$src/main.c" > "$build/main.c"
cd "$build"
make -j2
bad="$(psp-readelf -Ws "$build/pspdockk1proof.elf" |
    grep -E 'sceUsb(Start|Stop|Activate|Deactivate)|sceUsbbd|sceUsbstorBoot|sceKernelLoadModule|sceKernelStopModule' || true)"
if [[ -n "$bad" ]]; then
    echo "Unexpected USB/module mutation imports in K1 proof: $bad" >&2
    exit 1
fi
if ! psp-readelf -Ws "$build/pspdockk1proof.elf" | grep -q '__stub_modulestr_PSPDockK1Observer'; then
    echo "Missing dynamically linked K1 observer read import" >&2
    exit 1
fi
mkdir -p "$src/dist"
cp "$build/pspdockk1proof.prx" "$src/dist/"
sha256sum "$src/dist/pspdockk1proof.prx"
