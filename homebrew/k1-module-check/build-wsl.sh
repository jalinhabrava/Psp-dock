#!/usr/bin/env bash
set -euo pipefail
export PSPDEV="${PSPDEV:-/home/david/projects/psp-dock-build/sdk/pspdev}"
export PATH="$PSPDEV/bin:/usr/local/bin:/usr/bin:/bin"
src="$(cd -- "$(dirname -- "$0")" && pwd)"
build=/home/david/projects/psp-dock-build/k1-module-check
mkdir -p "$build"
cp "$src/main.c" "$src/Makefile" "$build/"
cd "$build"
make -j2
# The already physically-tested screen-check also links sceIoWrite through
# libpspdebug's standard screen/stdio support; this diagnostic never calls it.
# Reject file *opening* and all USB/kernel-only imports. Check direct calls
# separately with the source unit tests, not with the library's symbol table.
imports="$(psp-readelf -Ws "$build/pspdockk1check.elf" | grep -E '__stub_module_.*ForKernel|sceUsb|sceUsbstor|sceIo(Open|Remove|Rename)|pspSdkInstall.*Patch' || true)"
if [[ -n "$imports" ]]; then
    echo "Forbidden kernel/USB/file imports in USER diagnostic: $imports" >&2
    exit 1
fi
mkdir -p "$src/dist"
cp EBOOT.PBP "$src/dist/EBOOT.PBP"
sha256sum "$src/dist/EBOOT.PBP"
