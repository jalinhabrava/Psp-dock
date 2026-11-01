#!/usr/bin/env bash
set -euo pipefail
export PSPDEV=/home/david/projects/psp-dock-build/sdk/pspdev
export PATH="$PSPDEV/bin:/usr/local/bin:/usr/bin:/bin"
src="$(cd -- "$(dirname -- "$0")" && pwd)"
work=/home/david/projects/psp-dock-build/psp-dock-app
mkdir -p "$work"
cp "$src/main.c" "$src/lab_core.c" "$src/PSPDockHid.S" \
   "$src/exports.exp" "$src/lab_gate.h" "$src/Makefile" \
   "$src/Makefile.core" "$work/"
cp "$src/../bridge-hid/bridge_hid.h" "$work/bridge_hid.h"
cd "$work"

# Mandatory source safeguards: no watchdog/timeout that would unexpectedly
# end a product session; exit remains manual and cable-loss fail-safe.
if grep -nE 'TIMEOUT_US|Auto-stop|auto-exit|elapsed[[:space:]]*<' \
    main.c lab_core.c >/dev/null; then
    echo 'Unexpected fixed-duration watchdog in product build' >&2
    exit 1
fi
if ! grep -q 'START+SELECT' main.c ||
   ! grep -q 'if (stop_requested)' lab_core.c ||
   ! grep -q 'else if (cable_seen)' lab_core.c; then
    echo 'Missing explicit exit or cable-loss stop' >&2
    exit 1
fi

make -j2
if ! grep -q 'STUB_START "PSPDockHid",0x00090000' PSPDockHid.S; then
    echo 'Verified HID import flags changed' >&2
    exit 1
fi
if psp-readelf -Ws "$work/pspdockapp.elf" |
    grep -E 'sceUsb|sceUsbstor|__stub_module_.*ForKernel' >/dev/null; then
    echo 'Unexpected direct USER USB or kernel import' >&2
    exit 1
fi
mv Makefile Makefile.user
cp "$src/Makefile.core" Makefile
make -j2
if ! psp-readelf -Ws "$work/dockcore.elf" |
    grep -q '__stub_modulestr_PSPDockHid'; then
    echo 'Missing verified dynamic PSPDockHid import' >&2
    exit 1
fi
if psp-readelf -Ws "$work/dockcore.elf" |
    grep -E 'sceUsb(Start|Stop).*USBStor|sceUsbstorBoot' >/dev/null; then
    echo 'Unexpected USBSTOR-specific import in HID core' >&2
    exit 1
fi
mkdir -p "$src/dist"
cp "$work/EBOOT.PBP" "$work/dockcore.prx" "$src/dist/"
cp "$src/../bridge-hid/dist/bridgehid.prx" "$src/dist/"
cp "$src/LICENSE-FuSa-GPL-3.0.txt" "$src/dist/LICENSE-FuSa-GPL-3.0.txt"
printf '\nPSP Dock persistent HID compiled ONLY on PC; NOT INSTALLED\n'
sha256sum "$src/dist/"*
