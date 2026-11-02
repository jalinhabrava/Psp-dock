#!/usr/bin/env bash
set -euo pipefail
export PSPDEV="${PSPDEV:-/home/david/projects/psp-dock-build/sdk/pspdev}"
export PATH="$PSPDEV/bin:/usr/local/bin:/usr/bin:/bin"
src="$(cd -- "$(dirname -- "$0")" && pwd)"
work=/home/david/projects/psp-dock-build/native-dock-0307
assets="$src/assets"
mkdir -p "$work"
cp "$src"/*.c "$src"/*.h "$src/Makefile" "$src/exports.exp" "$work/"
cp "$src/../hid-only-lab/lab_gate.h" "$work/"

# Compile the actual source snapshot with asynchronous host-only doubles.
testroot="$work/test_snapshot/homebrew"
mkdir -p "$testroot/native-dock/tests/psp_stub" "$testroot/hid-only-lab"
cp "$work"/*.c "$work"/*.h "$testroot/native-dock/"
cp "$src/tests/test_native_transport.c" "$src/tests/test_native_ui.c" "$testroot/native-dock/tests/"
cp "$src/tests/psp_stub"/*.h "$testroot/native-dock/tests/psp_stub/"
cp "$work/lab_gate.h" "$testroot/hid-only-lab/"
cd "$work"
gcc -std=c11 -Wall -Wextra -Werror -O2 -I"$testroot/native-dock/tests/psp_stub" \
    "$testroot/native-dock/tests/test_native_transport.c" -o native-test
./native-test
gcc -std=c11 -Wall -Wextra -Werror -O2 \
    "$testroot/native-dock/tests/test_native_ui.c" -o native-ui-test
./native-ui-test
gcc -std=c11 -Wall -Wextra -Werror -O1 -g -fno-omit-frame-pointer \
    -fsanitize=address,undefined -I"$testroot/native-dock/tests/psp_stub" \
    "$testroot/native-dock/tests/test_native_transport.c" -o native-sanitized
ASAN_OPTIONS="${ASAN_OPTIONS:-detect_leaks=1:halt_on_error=1}" \
UBSAN_OPTIONS="${UBSAN_OPTIONS:-halt_on_error=1}" ./native-sanitized

make -j2
psp-nm docknative.elf > kernel-symbols.txt
psp-objdump -h docknative.elf > kernel-sections.txt
mkdir -p app/assets
cp "$src/app.c" "$src"/*.h app/
cp "$src/Makefile.app" app/Makefile
cp "$assets/ICON0.PNG" "$assets/PIC0.PNG" "$assets/PIC1.PNG" app/assets/
make -C app -j2
psp-nm app/pspdocknative.elf > user-symbols.txt
psp-nm -u app/app.o > user-direct-imports.txt
python3 "$src/audit_elf.py" docknative.elf --user-elf app/pspdocknative.elf \
    --user-object app/app.o --pbp app/EBOOT.PBP --assets "$assets" --output compiled-audit.json

mkdir -p "$src/dist"
cp docknative.prx docknative.elf app/EBOOT.PBP app/pspdocknative.elf \
    kernel-symbols.txt kernel-sections.txt user-symbols.txt user-direct-imports.txt \
    compiled-audit.json "$src/dist/"
cp "$src/../bridge-hid/LICENSE" "$src/dist/LICENSE-FuSa-GPL-3.0.txt"
shopt -s nullglob
asset_licenses=("$assets"/LICENSE*)
if ((${#asset_licenses[@]} == 0)); then
    printf 'Missing license for reused XMB artwork font\n' >&2
    exit 1
fi
cp "${asset_licenses[@]}" "$src/dist/"
sha256sum "$src/dist/docknative.prx" "$src/dist/EBOOT.PBP"
printf '\n0307 BUILT; NO INSTALLATION OR PSP ACCESS; SESSION HANDSHAKE NEEDS PHYSICAL VALIDATION\n'
