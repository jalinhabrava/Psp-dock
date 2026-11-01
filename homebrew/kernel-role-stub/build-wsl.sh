#!/usr/bin/env bash
set -euo pipefail
export PSPDEV=/home/david/projects/psp-dock-build/sdk/pspdev
export PATH="$PSPDEV/bin:/usr/local/bin:/usr/bin:/bin"
src="$(cd -- "$(dirname -- "$0")" && pwd)"
build=/home/david/projects/psp-dock-build/kernel-role-stub
mkdir -p "$build"
cp "$src/main.c" "$src/role_protocol.h" "$src/Makefile" "$src/exports.exp" "$build/"
cd "$build"
make -j2
if psp-objdump -d "$build/pspdockkernelstub.elf" | grep -E 'sceUsb(Start|Stop|Activate|Deactivate)' >/dev/null; then
  echo 'Kernel stub must not contain USB mutations' >&2
  exit 1
fi
mkdir -p "$src/dist"
cp pspdockkernelstub.prx "$src/dist/"
sha256sum "$src/dist/"*
