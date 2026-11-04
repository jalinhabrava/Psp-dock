"""One-shot PSP HID arrival handler, invoked by Windows Task Scheduler.

Windows normally refuses AutoRun from removable storage. Rather than
emulating a keyboard or keeping a polling process alive at login,
an opt-in Windows PnP event task can start this tiny process on demand.
It validates the physically observed Sony VID/PID gamepad before
opening the XMB. This script never launches PPSSPP directly.

IMPORTANT: Event 410 is a *configuration* event and may NOT be emitted
on every reconnect. Do not claim automatic start is validated until
repeated physical attach tests confirm the Windows event subscription.
"""
from __future__ import annotations

import argparse
import os
import time


def wait_for_psp_hid(read_frame, *, seconds: float = 10,
                     poll: float = 0.25, now=time.monotonic,
                     sleep=time.sleep) -> bool:
    if not 0 < seconds <= 30 or not 0.05 <= poll <= 1.5:
        raise ValueError("Intervalo de detección HID fuera de límites.")
    until=now()+seconds
    while now() < until:
        try:
            frame=read_frame()
            if (isinstance(frame,dict) and frame.get("connected") is True
                    and frame.get("source")=="psp_winmm"):
                return True
        except (OSError,ValueError,RuntimeError):
            pass
        sleep(poll)
    return False


def handle_arrival(*, probe=None, launch=None, wait_seconds:float=10) -> bool:
    if os.name!="nt":
        return False
    if probe is None:
        from psp_dock.devices.psp_launcher_input import PSPWinMMReader
        probe=PSPWinMMReader().read
    if not wait_for_psp_hid(probe,seconds=wait_seconds):
        return False
    if launch is None:
        from start_dock import launch as launch_dock
        launch=launch_dock
    # The host verifies a real PSP HID first. Never call Dock.launch()
    # here: the user selects the game in the XMB after it appears.
    launch()
    return True


def main() -> int:
    args=argparse.ArgumentParser(
        description="PSP Dock: one-shot Windows HID PnP event handler")
    args.add_argument("--wait-seconds",type=float,default=10)
    parsed=args.parse_args()
    try:
        return 0 if handle_arrival(wait_seconds=parsed.wait_seconds) else 2
    except (OSError,ValueError) as exc:
        # No flash writes, no USB role changes, no forced keypresses.
        print("PSP Dock host event ignored:",str(exc))
        return 1


if __name__=="__main__":
    raise SystemExit(main())
