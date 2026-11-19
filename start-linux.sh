#!/bin/sh
# Manual launch only: no service, autostart entry or device polling here.
set -eu
dock_root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec python3 "$dock_root/start_dock.py" "$@"
