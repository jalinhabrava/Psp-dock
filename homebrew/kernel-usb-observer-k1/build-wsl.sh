#!/usr/bin/env bash
set -euo pipefail
export PSPDEV="${PSPDEV:-/home/david/projects/psp-dock-build/sdk/pspdev}"
export PATH="$PSPDEV/bin:/usr/local/bin:/usr/bin:/bin"
src="$(cd -- "$(dirname -- "$0")" && pwd)"
build=/home/david/projects/psp-dock-build/kernel-usb-observer-k1
mkdir -p "$build"
cp "$src/main.c" "$src/protocol.h" "$src/Makefile" "$src/exports.exp" "$build/"
cd "$build"
make -j2
imports="$(psp-readelf -Ws "$build/pspdockk1observer.elf" | grep -E 'sceUsb(Start|Stop|Activate|Deactivate)|sceUsbstorBoot|sceUsbbd(Register|Unregister|ReqSend|ReqRecv)' || true)"
if [[ -n "$imports" ]]; then
  echo "Unsafe USB mutation import in K1 kernel observer: $imports" >&2
  exit 1
fi
mkdir -p "$src/dist"
cp "$build/pspdockk1observer.prx" "$src/dist/"
sha256sum "$src/dist/pspdockk1observer.prx"
