"""Optional local PPSSPP smoke test of the diagnostic EBOOT.PBP files.

Uses a private emulator runtime and Memory Stick directory, never the user's
PPSSPP configuration or physical PSP. Does not press CROSS or activate USB.
Emulator acceptance is *not* evidence of ARK-5 hardware compatibility.
"""
from __future__ import annotations

import ctypes
from datetime import datetime
import json
from pathlib import Path
import shutil
import subprocess
import time

from dock import ROOT, atomic_json, prepare_emulator


def owned_windows(pid: int) -> list[tuple[int, str]]:
    user32 = ctypes.windll.user32
    result = []
    callback = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def inspect(hwnd, _):
        current = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(current))
        if current.value == pid:
            length = user32.GetWindowTextLengthW(hwnd)
            title = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, title, len(title))
            result.append((hwnd, title.value))
        return True

    user32.EnumWindows(callback(inspect), 0)
    return result


def smoke():
    root = ROOT / "docs" / "emulator-safe-probes"
    runtime = prepare_emulator(
        ROOT / "tools/PPSSPP/PPSSPPWindows64.exe",
        root, root / "memstick", 2, "directx11")
    # Copy the read-only helper to the isolated emulator's virtual memstick,
    # not the PSP. We do NOT issue CROSS or touch the actual USB hardware.
    helper = root / "memstick/PSP/GAME/PSPDockUsbGate/usbstate.prx"
    helper.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "homebrew/usb-import-gate/dist/usbstate.prx", helper)
    helper06 = root / "memstick/PSP/GAME/PSPDockUsbGate06/usbstate.prx"
    helper06.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "homebrew/usb-import-gate-v06/dist/usbstate.prx", helper06)
    helper07 = root / "memstick/PSP/GAME/PSPDockUsbStatus07/usbstate.prx"
    helper07.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "homebrew/usb-driver-status-v07/dist/usbstate.prx", helper07)
    helper08 = root / "memstick/PSP/GAME/PSPDockBusCycle08/usbstate.prx"
    helper08.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "homebrew/usb-bus-cycle-v08/dist/usbstate.prx", helper08)
    results = []
    for title, source in [
        ("Startup Check", ROOT / "homebrew/screen-check/dist/EBOOT.PBP"),
        ("USB Import Gate (no buttons pressed)",
         ROOT / "homebrew/usb-import-gate/dist/EBOOT.PBP"),
        ("USB Import Gate 0.6 (no buttons pressed)",
         ROOT / "homebrew/usb-import-gate-v06/dist/EBOOT.PBP"),
        ("USB Driver Status 0.7 (no buttons pressed)",
         ROOT / "homebrew/usb-driver-status-v07/dist/EBOOT.PBP"),
        ("USB Bus Cycle 0.8 (no buttons pressed)",
         ROOT / "homebrew/usb-bus-cycle-v08/dist/EBOOT.PBP"),
    ]:
        if not source.is_file():
            raise FileNotFoundError(source)
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = 0
        process = subprocess.Popen([str(runtime), "--windowed", "--graphics=directx11", str(source)],
                                   cwd=runtime.parent, startupinfo=startup)
        try:
            time.sleep(5)
            alive = process.poll() is None
            windows = owned_windows(process.pid)
            results.append({
                "homebrew": title, "binary": str(source),
                "process_alive_after_5s": alive,
                "window_titles": [name for _, name in windows],
            })
            for hwnd, _ in windows:
                ctypes.windll.user32.PostMessageW(hwnd, 0x10, 0, 0)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.wait(timeout=5)
            results[-1]["exit_code"] = process.returncode
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=5)
    output = {
        "date": datetime.now().isoformat(),
        "emulator": "PPSSPP Windows x64 isolated private runtime",
        "hardware_verified": False,
        "usb_tested": False,
        "results": results,
    }
    atomic_json(ROOT / "docs" / "safe-probes-emulator-smoke.json", output)
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    smoke()
