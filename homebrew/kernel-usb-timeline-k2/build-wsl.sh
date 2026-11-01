#!/usr/bin/env bash
set -euo pipefail
export PSPDEV="${PSPDEV:-/home/david/projects/psp-dock-build/sdk/pspdev}"
export PATH="$PSPDEV/bin:/usr/local/bin:/usr/bin:/bin"
src="$(cd -- "$(dirname -- "$0")" && pwd)"
build=/home/david/projects/psp-dock-build/kernel-usb-timeline-k2
mkdir -p "$build"
cp "$src/main.c" "$src/timeline.h" "$src/Makefile" "$src/exports.exp" "$build/"
cd "$build"
make -j2
imports="$(psp-readelf -Ws "$build/pspdockk2timeline.elf" |
  grep -E 'sceUsb(Start|Stop|Activate|Deactivate)|sceUsbstorBoot|sceUsbbd|sceIo(Open|Write|Remove|Rename)' || true)"
if [[ -n "$imports" ]]; then
  echo "K2 timeline contains a forbidden USB/FAT mutation: $imports" >&2
  exit 1
fi
mkdir -p "$src/dist"
cp "$build/pspdockk2timeline.prx" "$src/dist/"
sha256sum "$src/dist/pspdockk2timeline.prx"
