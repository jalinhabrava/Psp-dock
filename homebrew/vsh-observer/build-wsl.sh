#!/usr/bin/env bash
set -euo pipefail
export PSPDEV=/home/david/projects/psp-dock-build/sdk/pspdev
export PATH="$PSPDEV/bin:/usr/local/bin:/usr/bin:/bin"
src="$(cd -- "$(dirname -- "$0")" && pwd)"
build=/home/david/projects/psp-dock-build/vsh-observer
mkdir -p "$build"
cp "$src/main.c" "$src/vsh_probe_protocol.h" "$src/Makefile" "$src/exports.exp" "$build/"
cd "$build"
make -j2
imports="$(psp-readelf -Ws "$build/pspdockvsh.elf" | grep '__stub_module_' || true)"
printf 'VSH USER PRX IMPORTS:\n%s\n' "$imports"
if printf '%s\n' "$imports" | grep -Ei '(ForKernel|sceUsb|sceUsbstor|_driver)' >/dev/null; then
  echo 'Invalid VSH startup imports: refusing to release' >&2
  exit 1
fi
python3 "$src/check_vsh_probe.py" "$src/main.c" "$src/vsh_probe_protocol.h"
mkdir -p "$src/dist"
cp "$build/pspdockvsh.prx" "$src/dist/pspdockvsh.prx"
sha256sum "$src/dist/pspdockvsh.prx"
