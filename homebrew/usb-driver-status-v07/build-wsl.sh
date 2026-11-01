#!/usr/bin/env bash
set -euo pipefail
export PSPDEV=/home/david/projects/psp-dock-build/sdk/pspdev
export PATH="$PSPDEV/bin:/usr/local/bin:/usr/bin:/bin"
src="$(cd -- "$(dirname -- "$0")" && pwd)"
build=/home/david/projects/psp-dock-build/usb-driver-status-v07
mkdir -p "$build/ui" "$build/plugin"
cp "$src/ui.c" "$build/ui/ui.c"
cp "$src/gate_protocol.h" "$build/ui/gate_protocol.h"
cp "$src/Makefile.ui" "$build/ui/Makefile"
cp "$src/usbstate.c" "$build/plugin/usbstate.c"
cp "$src/gate_protocol.h" "$build/plugin/gate_protocol.h"
cp "$src/Makefile.plugin" "$build/plugin/Makefile"
cp "$src/exports.exp" "$build/plugin/exports.exp"
cd "$build/plugin"
make -j2
cd "$build/ui"
make -j2
ui_imports="$(psp-readelf -Ws "$build/ui/pspdockusbstatus07.elf" | grep '__stub_module_' || true)"
plugin_imports="$(psp-readelf -Ws "$build/plugin/usbstate.elf" | grep '__stub_module_' || true)"
printf 'EBOOT IMPORTS:\n%s\n\nPRX IMPORTS:\n%s\n' "$ui_imports" "$plugin_imports"
if printf '%s\n' "$ui_imports" | grep -Ei '(ForKernel|sceUsb|sceUsbstor|_driver)' >/dev/null; then
  echo 'UNSAFE UI IMPORTS: build aborted' >&2
  exit 1
fi
if printf '%s\n' "$plugin_imports" | grep -Ei '(ForKernel|sceUsbstor|_driver)' >/dev/null; then
  echo 'UNSAFE PLUGIN IMPORTS: build aborted' >&2
  exit 1
fi
if ! printf '%s\n' "$plugin_imports" | grep -F '__stub_module_sceUsb' >/dev/null; then
  echo 'Plugin missing expected sceUsb import' >&2
  exit 1
fi
mkdir -p "$src/dist"
cp "$build/ui/EBOOT.PBP" "$src/dist/EBOOT.PBP"
cp "$build/plugin/usbstate.prx" "$src/dist/usbstate.prx"
sha256sum "$src/dist/"*
