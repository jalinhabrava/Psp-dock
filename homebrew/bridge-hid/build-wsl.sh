#!/usr/bin/env bash
set -euo pipefail
# Invoke explicitly with bash from WSL. Override PSPDEV to use another SDK.
export PSPDEV="${PSPDEV:-/home/david/projects/psp-dock-build/sdk/pspdev}"
export PATH="$PSPDEV/bin:/usr/local/bin:/usr/bin:/bin"
source_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
build_dir=/home/david/projects/psp-dock-build/hid-driver
mkdir -p "$build_dir"
cp "$source_dir"/*.[chS] "$source_dir/Makefile" "$source_dir/exports.exp" "$build_dir/"
cd "$build_dir"
psp-gcc --version | head -1
make -j2
mkdir -p "$source_dir/dist"
cp bridgehid.prx bridgehid.elf "$source_dir/dist/"
sha256sum bridgehid.prx
