#!/usr/bin/env bash
set -euo pipefail
export PSPDEV="${PSPDEV:-/home/david/projects/psp-dock-build/sdk/pspdev}"
export PATH="$PSPDEV/bin:/usr/local/bin:/usr/bin:/bin"
src="$(cd -- "$(dirname -- "$0")" && pwd)"
# Do not share a build directory with another active source worktree.
work=/home/david/projects/psp-dock-build/composite-reviewed-v3
mkdir -p "$work"
cp "$src"/*.c "$src"/*.h "$src/Makefile" "$src/exports.exp" "$work/"
cp "$src/../psp-bulk-prototype/pdb2_wire.c" "$src/../psp-bulk-prototype/pdb2_wire.h" "$work/"
cp "$src/../bridge-hid/report_descriptor.h" "$work/"
cp "$src/../hid-only-lab/lab_gate.h" "$work/"
python3 "$src/generate_identity.py" "$work"
cd "$work"
# Native tests use fake files. No PSP access or module installation.
gcc -std=c11 -Wall -Wextra -Werror -O2 pdc_service.c pdc_sha256.c \
    pdb2_wire.c pdc_os_descriptors.c test_native.c -o native-test
./native-test
gcc -std=c11 -Wall -Wextra -Werror -O1 -g -fno-omit-frame-pointer \
    -fsanitize=address,undefined pdc_service.c pdc_sha256.c pdb2_wire.c \
    pdc_os_descriptors.c fuzz_native.c -o native-fuzz
ASAN_OPTIONS=detect_leaks=1:halt_on_error=1 UBSAN_OPTIONS=halt_on_error=1 ./native-fuzz
gcc -std=c11 -Wall -Wextra -Werror -O2 -shared -fPIC pdc_service.c \
    pdc_sha256.c pdb2_wire.c native_fixture.c -o native-service.so
root="$(cd "$src/../.." && pwd)"
PDC_NATIVE_LIBRARY="$work/native-service.so" PYTHONPATH="$root" \
    python3 -m unittest tests.test_psp_composite_native -v
make -j2
python3 "$src/audit_elf.py" dockcomposite.elf
# Reject unintended writable-filesystem imports in the compiled PRX.
# Imports resolved by PSP stubs are *defined* symbols, so inspect all names,
# not just ELF undefined entries (which would miss linked PSP imports).
psp-nm dockcomposite.elf > kernel-symbols.txt
if grep -E '[[:space:]]sceIo(Write|Remove|Rename|Mkdir|Rmdir|Format|Devctl)$|[[:space:]]sceUsbstorBoot' kernel-symbols.txt; then
    echo 'Unexpected filesystem mutation import' >&2; exit 1
fi
for symbol in sceIoOpen sceIoRead sceIoDopen sceIoDread sceUsbbdReqSend sceUsbbdReqRecv; do
    grep -q "[[:space:]]$symbol$" kernel-symbols.txt || {
        echo "Missing actual read-only USB import: $symbol" >&2; exit 1;
    }
done
mkdir -p user-app
cp "$src/app.c" "$src/pdc_common.h" user-app/
cp "$src/Makefile.app" user-app/Makefile
make -C user-app -j2
psp-nm user-app/pspdockcompositetest.elf > user-symbols.txt
# Standard USER libc brings a stdout sceIoWrite stub. Audit the app object
# for direct file mutation calls, and the final image for privileged imports.
psp-nm -u user-app/app.o > user-direct-imports.txt
if grep -E '[[:space:]]sceIo(Write|Remove|Rename|Devctl|Mkdir|Rmdir)$|[[:space:]]sceUsb' user-direct-imports.txt || \
   grep -E '[[:space:]]sceUsb|__stub_module_.*ForKernel' user-symbols.txt; then
    echo 'User diagnostic contains unexpected privileged imports' >&2; exit 1
fi
mkdir -p "$src/dist"
cp dockcomposite.prx dockcomposite.elf pdc_identity.h PROBE.DAT probe-manifest.json "$src/dist/"
cp user-app/EBOOT.PBP "$src/dist/"
cp "$src/../bridge-hid/LICENSE" "$src/dist/LICENSE-FuSa-GPL-3.0.txt"
sha256sum "$src/dist/dockcomposite.prx" "$src/dist/EBOOT.PBP" "$src/dist/PROBE.DAT"
printf '\nCONTROLLER BUILT ON PC ONLY; NOT INSTALLED ON PSP\n'
