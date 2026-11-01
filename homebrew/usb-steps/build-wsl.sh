#!/usr/bin/env bash
set -euo pipefail
export PSPDEV=/home/david/projects/psp-dock-build/sdk/pspdev
export PATH="$PSPDEV/bin:/usr/local/bin:/usr/bin:/bin"
source_dir="$(cd -- "$(dirname -- "$0")" && pwd)"
build_dir=/home/david/projects/psp-dock-build/usb-steps
mkdir -p "$build_dir"
cp "$source_dir/main.c" "$source_dir/Makefile" "$build_dir/"
cd "$build_dir"
make -j2
mkdir -p "$source_dir/dist"
cp EBOOT.PBP "$source_dir/dist/"
sha256sum "$source_dir/dist/EBOOT.PBP"
