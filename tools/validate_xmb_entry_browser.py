"""Real Edge DOM regression for choosing no-copy with a supported native build.

Only the page is real: HTTP state, controller input, catalog and media are
synthetic. No PSP, USB, emulator or LIVE library is opened. Browser gamepad
enumeration is disabled before app.js runs. The temporary browser is closed.
"""
from __future__ import annotations

import argparse
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
import time
import wave

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["PSP_DOCK_TEST_NO_REAL_USB"] = "1"
from native_dock import REVISION, FIRMWARE_BUILDS, FIRMWARE_CAPABILITIES

GAME = "a" * 24
TITLE = "Synthetic entry test"
OFFER = "isolated-entry-offer"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--app-ref", help="Optional Git commit SHA for the previous app.js; leaves working files intact")
    args = parser.parse_args()
    if args.app_ref:
        if not re.fullmatch(r"[0-9a-fA-F]{7,40}", args.app_ref):
            parser.error("--app-ref must be a Git commit SHA")
        app_js = subprocess.check_output(["git", "show", args.app_ref + ":web/app.js"], cwd=ROOT)
    else:
        app_js = (ROOT / "web/app.js").read_bytes()
    browser = next((path for path in (
        Path(os.environ.get("PROGRAMFILES(X86)", "C:/Program Files (x86)")) / "Microsoft/Edge/Application/msedge.exe",
        Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "Microsoft/Edge/Application/msedge.exe",
    ) if path.is_file()), None)
    if browser is None:
        raise SystemExit("Microsoft Edge is required for this Windows-only check.")

    image = io.BytesIO()
    Image.new("RGB", (480, 272), (24, 53, 76)).save(image, "PNG")
    png = image.getvalue()
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setparams((1, 2, 22050, 0, "NONE", "not compressed"))
        audio.writeframes(b"\0\0" * 22050)
    silence = buffer.getvalue()
    phase = ["choosing"]
    choice_posts = []
    unexpected_posts = []
    lock = threading.Lock()

    def state():
        with lock:
            ready = phase[0] == "ready"
        return {
            "demo": False, "devices": [], "selected_device": None if ready else "G:\\",
            "scanning": False, "error": None, "recovery_required": False,
            "runtime_prepared": ready, "can_launch_game": ready,
            "session": {"running": False, "pending_return": False, "exit_requested": False},
            "emulator": {"available": True},
            "games": [{"id": GAME, "title": TITLE, "size": 100, "format": "ISO",
                       "availability": "ready", "on_pc": True, "on_psp": True}],
            "retro": {"roms": [], "emulators": []},
            "saves": {"actions": [], "conflicts": []}, "retro_saves": {"rows": []},
            "preflight": {"saves_prepared": True},
            "controller_status": {
                "connected": True, "mode": "native_dock", "can_play": ready,
                "synthetic_storage": False, "product_id": 0x01CD, "protocol_version": 3,
                "firmware_revision": REVISION, "build_id": FIRMWARE_BUILDS[REVISION],
                "capabilities": FIRMWARE_CAPABILITIES[REVISION], "host_confirmed": True,
                "phase": 2 if ready else 1,
            },
            "product_flow": {
                "enabled": True, "phase": "ready" if ready else "choosing", "purpose": "initial",
                "choice": "none" if ready else None, "use_pc_saves": ready,
                "blocking": not ready, "offer_id": OFFER, "can_choose": not ready,
                "can_cancel": not ready, "entry_completed": ready, "operation_active": False,
                "game_running": False, "remembered_choice": "none", "remember_automatic": False,
                "counts": {"psp_games": 1, "pc_games": 1, "missing_games": 0,
                           "save_actions": 0, "save_conflicts": 0},
                "capabilities": {"controller_during_storage": True, "automatic_transition": True},
                "connection": {"phase": "verified", "retryable": False},
                "ui_sounds": {"status": "waiting", "ready": False},
            },
        }

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def send(self, value, kind="application/json", status=200):
            data = value if isinstance(value, bytes) else json.dumps(value).encode()
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Security-Policy",
                "default-src 'self'; img-src 'self' blob:; style-src 'self'; "
                "script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path == "/":
                html = (ROOT / "web/index.html").read_text(encoding="utf-8")
                html = html.replace("<head>", '<head><script src="/fixture-bootstrap.js"></script>', 1)
                return self.send(html.encode(), "text/html; charset=utf-8")
            if self.path == "/fixture-bootstrap.js":
                return self.send(b"Object.defineProperty(navigator,'getGamepads',{value:()=>[]});", "text/javascript")
            if self.path == "/app.js":
                return self.send(app_js, "text/javascript")
            if self.path == "/style.css":
                return self.send((ROOT / "web/style.css").read_bytes(), "text/css")
            if self.path == "/api/state":
                return self.send(state())
            if self.path == "/api/token":
                return self.send({"token": "isolated-fixture-only"})
            if self.path == "/api/settings":
                return self.send({"backend": "directx11", "scale": 3})
            if self.path == "/api/controller-input":
                return self.send({"connected": False})
            if self.path.startswith(("/api/icon/", "/api/media-hd/")):
                return self.send(png, "image/png")
            if self.path.startswith("/api/media/"):
                return self.send(silence, "audio/wav") if self.path.endswith("/sound") else self.send(png, "image/png")
            if self.path.startswith("/api/graphics"):
                return self.send({"choice": {"quality": "auto", "aspect": "fit", "texture": "off"}, "plan": {}})
            if self.path == "/fixture/outcome":
                with lock:
                    outcome = {"choice_posts": list(choice_posts), "unexpected_posts": list(unexpected_posts)}
                return self.send(outcome)
            return self.send({})

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 <= length <= 4096:
                return self.send({"error": "Unexpected fixture request size"}, status=400)
            body = json.loads(self.rfile.read(length) or b"{}")
            if self.path == "/api/session-choice":
                with lock:
                    choice_posts.append(body)
                    valid = (phase[0] == "choosing" and body.get("choice") == "none"
                             and body.get("offer_id") == OFFER and body.get("automatic") is False)
                    if valid:
                        phase[0] = "ready"
                return self.send({"ok": True} if valid else {"error": "Unexpected session choice"},
                                 status=200 if valid else 409)
            if self.path not in ("/api/app/heartbeat", "/api/app/close", "/api/ui-sound"):
                with lock:
                    unexpected_posts.append({"path": self.path, "body": body})
                return self.send({"error": "No file or launch operation is permitted in this fixture"}, status=409)
            return self.send({"ok": True})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory(prefix="pspdock-entry-browser-") as profile:
            url = f"http://127.0.0.1:{server.server_port}/"
            with (Path(profile) / "browser-stderr.txt").open("w") as log:
                process = subprocess.Popen([
                    str(browser), "--headless", "--disable-gpu", "--mute-audio",
                    "--window-size=1440,900", "--no-first-run", "--disable-background-networking",
                    "--disable-extensions", "--user-data-dir=" + profile, "--remote-debugging-port=0", url,
                ], stdout=subprocess.DEVNULL, stderr=log)
                try:
                    portfile = Path(profile) / "DevToolsActivePort"
                    deadline = time.monotonic() + 15
                    while not portfile.exists():
                        if process.poll() is not None or time.monotonic() > deadline:
                            raise RuntimeError("The isolated test browser did not become ready")
                        time.sleep(.05)
                    port = portfile.read_text().splitlines()[0]
                    result = subprocess.run(
                        ["node", str(ROOT / "tools/drive_xmb_entry_browser.js"), port, url],
                        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=35,
                    )
                    evidence = json.loads(result.stdout) if result.stdout.strip() else {
                        "passed": False, "error": "No browser result", "driver_exit": result.returncode,
                        "stderr_tail": result.stderr[-2000:],
                    }
                finally:
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.terminate()
                        process.wait(timeout=5)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    evidence.update(
        schema="pspdock-native-entry-browser-v1",
        scope="Real Edge DOM and app.js; simulated HTTP, controller and catalog; no PSP, USB, emulator or LIVE access",
        app_ref=args.app_ref or "working-tree", app_sha256=hashlib.sha256(app_js).hexdigest(),
        firmware_revision=f"{REVISION:04X}", build_id=f"{FIRMWARE_BUILDS[REVISION]:08X}",
    )
    print(json.dumps(evidence, indent=2, ensure_ascii=False))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as output:
            json.dump(evidence, output, indent=2, ensure_ascii=False)
            output.write("\n")
    raise SystemExit(0 if evidence.get("passed") else 1)


if __name__ == "__main__":
    main()
