#!/usr/bin/env bash
set -euo pipefail

# Experimental longer manual HID session, never installed automatically.
# Copy physically verified HID-only LAB sources before making build-only
# changes. Existing 90s EBOOT and PRX stay untouched.
export PSPDEV=/home/david/projects/psp-dock-build/sdk/pspdev
export PATH="$PSPDEV/bin:/usr/local/bin:/usr/bin:/bin"
src="$(cd -- "$(dirname -- "$0")" && pwd)"
lab="$(cd -- "$src/../hid-only-lab" && pwd)"
work=/home/david/projects/psp-dock-build/hid-extended-15m
mkdir -p "$work"
cp "$lab/main.c" "$lab/lab_core.c" "$lab/PSPDockHid.S" \
   "$lab/exports.exp" "$lab/lab_gate.h" "$lab/Makefile" \
   "$lab/Makefile.core" "$work/"
cp "$lab/../bridge-hid/bridge_hid.h" "$work/bridge_hid.h"

python3 - "$work" <<'PY'
from pathlib import Path
import sys
root=Path(sys.argv[1])
def replace(name,before,after):
    path=root/name
    text=path.read_text(encoding='utf-8')
    if text.count(before)!=1:
        raise SystemExit(f'Unexpected known-good source change: {name} / {before!r}')
    path.write_text(text.replace(before,after),encoding='utf-8')

replace('lab_core.c','#define TIMEOUT_US 90000000u',
        '#define TIMEOUT_US 900000000u /* experimental 15-minute watchdog */')
replace('main.c','elapsed < 1050','elapsed < 9200')
replace('main.c','Auto-stop at 90 seconds; auto-exit 105s.',
        'HID stops at 15 minutes; app exits by 920s.')
replace('main.c','PSPDockHidOnly/bridgehid.prx',
        'PSPDockHidLong/bridgehid.prx')
replace('main.c','PSPDockHidOnly/hidonlycore.prx',
        'PSPDockHidLong/hidonlycore.prx')
replace('Makefile','PSP Dock - HID Only LAB',
        'PSP Dock - HID Extended TEST')
PY

cd "$work"
make -j2
if ! grep -q 'STUB_START "PSPDockHid",0x00090000' PSPDockHid.S; then
    echo 'Unexpected PSPDockHid import stub/NID' >&2; exit 1
fi
if psp-readelf -Ws "$work/pspdockhidonly.elf" |
    grep -E 'sceUsb|sceUsbstor|__stub_module_.*ForKernel' >/dev/null; then
    echo 'Unsafe direct USB/kernel import in USER EBOOT' >&2; exit 1
fi
mv Makefile Makefile.user
cp "$lab/Makefile.core" Makefile
make -j2
if ! psp-readelf -Ws "$work/hidonlycore.elf" |
    grep -q '__stub_modulestr_PSPDockHid'; then
    echo 'Missing dynamic PSPDockHid import' >&2; exit 1
fi
if psp-readelf -Ws "$work/hidonlycore.elf" |
    grep -E 'sceUsb(Start|Stop).*USBStor|sceUsbstorBoot' >/dev/null; then
    echo 'Unexpected USBSTOR import' >&2; exit 1
fi
mkdir -p "$src/dist"
cp "$work/EBOOT.PBP" "$work/hidonlycore.prx" "$src/dist/"
cp "$lab/../bridge-hid/dist/bridgehid.prx" "$src/dist/"
printf '\nEXPERIMENTAL 15m COMPILED; NOT INSTALLED ON PSP\n'
sha256sum "$src/dist/EBOOT.PBP" "$src/dist/hidonlycore.prx" "$src/dist/bridgehid.prx"
