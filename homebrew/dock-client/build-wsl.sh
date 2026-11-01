#!/usr/bin/env bash
set -euo pipefail
PSPDEV=/home/david/projects/psp-dock-build/sdk/pspdev
export PSPDEV
export PATH="$PSPDEV/bin:/usr/local/bin:/usr/bin:/bin"
source_dir="$(cd -- "$(dirname -- "$0")" && pwd)"
project_dir="$(cd -- "$source_dir/../.." && pwd)"
build_dir=/home/david/projects/psp-dock-build/dock-client
mkdir -p "$build_dir"
cp "$source_dir/main.c" "$source_dir/Makefile" "$build_dir/"
cp "$project_dir/homebrew/bridge-hid/PSPDockHid.S" "$build_dir/"
mkdir -p "$build_dir/../bridge-hid"
cp "$project_dir/homebrew/bridge-hid/bridge_hid.h" "$build_dir/../bridge-hid/"
cd "$build_dir"
make -j2
mkdir -p "$source_dir/dist"
cp EBOOT.PBP "$source_dir/dist/"
cp "$project_dir/homebrew/bridge-hid/dist/bridgehid.prx" "$source_dir/dist/"
sha256sum "$source_dir/dist/"*
