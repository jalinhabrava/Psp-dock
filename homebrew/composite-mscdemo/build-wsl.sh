#!/usr/bin/env bash
set -euo pipefail
export PSPDEV="${PSPDEV:-/home/david/projects/psp-dock-build/sdk/pspdev}"
export PATH="$PSPDEV/bin:/usr/local/bin:/usr/bin:/bin"
src="$(cd -- "$(dirname -- "$0")" && pwd)"
root="$(cd -- "$src/../.." && pwd)"
# Separate scratch build directory; never an installed PSP application.
work=/home/david/projects/psp-dock-build/composite-msc-v1
mkdir -p "$work"
cp "$src"/*.c "$src"/*.h "$src/Makefile" "$src/exports.exp" "$work/"
cp "$src/../bridge-hid/report_descriptor.h" "$work/"
cp "$src/../hid-only-lab/lab_gate.h" "$work/"
cd "$work"

# Native tests and sector-image generation contain no PSP/device access.
gcc -std=c11 -Wall -Wextra -Werror -Wconversion -Wshadow -pedantic -O2 \
    msc_scsi.c test_msc_native.c -o native-test
./native-test --dump-image synthetic-reference.img
gcc -std=c11 -Wall -Wextra -Werror -Wconversion -Wshadow -pedantic -O1 -g \
    -fno-omit-frame-pointer -fsanitize=address,undefined \
    msc_scsi.c test_msc_native.c -o native-sanitized
ASAN_OPTIONS="${ASAN_OPTIONS:-detect_leaks=1:halt_on_error=1}" \
UBSAN_OPTIONS="${UBSAN_OPTIONS:-halt_on_error=1}" ./native-sanitized

# Exercise the actual USB wrapper with asynchronous transport doubles. Keep
# its repository-relative include layout and use the same source snapshot
# copied above for the MIPS build. This cannot validate Sony USBBus behavior.
mkdir -p transport-tests/tests/msc_psp_stub transport-tests/homebrew/composite-mscdemo
cp "$root/tests/test_msc_transport_native.c" transport-tests/tests/
cp "$root/tests/msc_psp_stub"/*.h transport-tests/tests/msc_psp_stub/
cp msc_driver.c msc_scsi.c msc_common.h msc_scsi.h msc_descriptors.h \
    transport-tests/homebrew/composite-mscdemo/
gcc -std=c11 -Wall -Wextra -Werror -O2 -Itransport-tests/tests/msc_psp_stub \
    transport-tests/tests/test_msc_transport_native.c \
    transport-tests/homebrew/composite-mscdemo/msc_scsi.c -o native-transport
./native-transport
gcc -std=c11 -Wall -Wextra -Werror -O1 -g -fno-omit-frame-pointer \
    -fsanitize=address,undefined -Itransport-tests/tests/msc_psp_stub \
    transport-tests/tests/test_msc_transport_native.c \
    transport-tests/homebrew/composite-mscdemo/msc_scsi.c -o native-transport-sanitized
ASAN_OPTIONS="${ASAN_OPTIONS:-detect_leaks=1:halt_on_error=1}" \
UBSAN_OPTIONS="${UBSAN_OPTIONS:-halt_on_error=1}" ./native-transport-sanitized

make -j2
psp-nm dockmsc.elf > kernel-symbols.txt
psp-objdump -h dockmsc.elf > kernel-sections.txt
psp-objdump -p dockmsc.elf > kernel-headers.txt
mkdir -p app/assets
cp "$src/app.c" "$src"/*.h app/
cp "$src/Makefile.app" app/Makefile
cp "$src/assets/ICON0.PNG" "$src/assets/PIC0.PNG" "$src/assets/PIC1.PNG" app/assets/
make -C app -j2
psp-nm app/pspdockstoragetest.elf > user-symbols.txt
psp-nm -u app/app.o > user-direct-imports.txt
python3 "$src/audit_elf.py" dockmsc.elf \
    --user-elf app/pspdockstoragetest.elf --user-object app/app.o \
    --pbp app/EBOOT.PBP --assets "$src/assets" --image synthetic-reference.img \
    --output compiled-audit.json

mkdir -p "$src/dist"
cp dockmsc.prx dockmsc.elf app/EBOOT.PBP app/pspdockstoragetest.elf \
    kernel-symbols.txt kernel-sections.txt kernel-headers.txt \
    user-symbols.txt user-direct-imports.txt compiled-audit.json \
    synthetic-reference.img "$src/dist/"
cp "$src/../bridge-hid/LICENSE" "$src/dist/LICENSE-FuSa-GPL-3.0.txt"
shopt -s nullglob
asset_licenses=("$src"/assets/LICENSE*)
if ((${#asset_licenses[@]} == 0)); then
    printf 'Missing license for the reused XMB artwork font\n' >&2
    exit 1
fi
cp "${asset_licenses[@]}" "$src/dist/"
sha256sum "$src/dist/dockmsc.prx" "$src/dist/EBOOT.PBP" "$src/dist/synthetic-reference.img"
printf '\nSYNTHETIC USBSTOR + HID BUILT ON PC; NO PSP INSTALLATION OR MEMORY STICK ACCESS\n'
