#!/usr/bin/env bash
set -euo pipefail
export PSPDEV=/home/david/projects/psp-dock-build/sdk/pspdev
export PATH="$PSPDEV/bin:/usr/local/bin:/usr/bin:/bin"
src="$(cd -- "$(dirname -- "$0")" && pwd)"

# Compile to a TEMP object file only. Never build a loadable module,
# never use ms0: / flash0: / SCSI / UsbStart and never install drivers.
if grep -En 'sceUsb(Start|Stop|Activate|Deactivate)|sceUsbbdRegister|module_start|flash0:|ms0:' \
    "$src/composite_layout.c"; then
    echo 'Unsafe module or physical USB operation in the descriptor prototype' >&2
    exit 1
fi
psp-gcc -std=c11 -O2 -Wall -Wextra -Werror \
    -I"$PSPDEV/psp/sdk/include" \
    -c "$src/composite_layout.c" -o /tmp/pspdock-composite-layout.o
psp-readelf -h /tmp/pspdock-composite-layout.o | grep -E 'Machine:|Type:'
rm -f /tmp/pspdock-composite-layout.o
echo 'PSPDEV composite descriptor object compiled: NOT a PRX, NOT installed'
