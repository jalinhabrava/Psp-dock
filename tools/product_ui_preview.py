"""Windows visual QA for the product session, using isolated synthetic HTTP data.

Run from the repository: ``python tools/product_ui_preview.py``.
Screenshots and result.json go to data/product-session-ui. An optional --browser
selects a Chrome/Edge executable; --fixture-check validates data without opening
any browser. No Dock object, real PSP, emulator or user profile is opened. One
temporary headless browser is closed, including its children, before returning.

Adapted from product-merge/ui-preview.py: loopback fixtures + standard-library CDP.
This is UI evidence only; it does not certify USB, copy durability or safe eject.
"""
from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import html
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, build_opener

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
# These two modules are pure models. Never import/instantiate the live Dock.
from dock_preflight import assess_storage
from dock_product_flow import MESSAGES, PHASES, plan_session_return

VOLUME = "QA-FIXTURE-ONLY"
PSP_ROOT = "Q:\\"
PID, REVISION = 0x01CD, 0x0301
SCENARIOS = (
    ("choosing", "choosing", "initial", None),
    ("syncing_in", "syncing_in", "initial", "all"),
    ("ready", "ready", "initial", "all"),
    ("ready_pc_saves", "ready", "initial", "none"),
    ("copying", "copying_game", "game_copy", "all"),
    ("copying_window_close", "copying_game", "game_copy", "all"),
    ("waiting_game_exit", "waiting_game_exit", "exit", "all"),
    ("syncing_out", "syncing_out", "exit", "all"),
    ("finalizing", "finalizing", "exit", "all"),
    ("safe_to_disconnect", "safe_to_disconnect", "exit", "all"),
    ("attention", "attention", "exit", "all"),
    ("attention_initial", "attention", "initial", "all"),
    ("attention_resolved", "attention", "exit", "all"),
)
NARROW_SCENARIOS = {"choosing", "attention", "attention_initial", "attention_resolved"}
IDLE_PROGRESS = {"active": False, "phase": "idle", "copied_bytes": 0, "total_bytes": 0}
UNSUPPORTED_NOTE = "Los estados de emuladores sin formato compatible verificado se conservan en el PC."
SETTINGS = {
    "storage_root": "C:\\PSPDock-UI-Fixture", "library": "C:\\PSPDock-UI-Fixture\\games",
    "memstick": "C:\\PSPDock-UI-Fixture\\memstick", "psp_root": "",
    "emulator": "C:\\PSPDock-UI-Fixture\\PPSSPP.exe",
    "retroarch": "C:\\PSPDock-UI-Fixture\\retroarch.exe",
    "retro_install_policy": "ask", "retro_button_layout": "nintendo",
    "scale": 3, "backend": "directx11",
}


def signature(label):
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def game_id(label):
    return signature(label)[:24]


def return_plan(prefix):
    """Real pure return planner, with synthetic before/after SHA-256 values."""
    slot, clean = prefix + "_CONFLICT", prefix + "_CHANGED"
    before = {slot: signature(slot + "-before"), clean: signature(clean + "-before")}
    psp = {**before, slot: signature(slot + "-psp-after")}
    pc = {slot: signature(slot + "-pc-after"), clean: signature(clean + "-pc-after")}
    plan = plan_session_return(before, before, psp, pc)
    revision = signature(json.dumps(plan, sort_keys=True))
    for row in plan["actions"] + plan["conflicts"]:
        row["revision"] = revision
    return plan


def build_fixture(name, phase, purpose, choice):
    if phase not in PHASES | {"waiting_game_exit"}:
        raise ValueError("Unknown production phase: " + phase)
    native_phase = 2 if phase in ("ready", "waiting_game_exit") else 3 if purpose == "game_copy" else 4 if purpose == "exit" else 1
    if phase in ("finalizing", "safe_to_disconnect"):
        native_phase = 5
    storage = native_phase in (1, 3, 4)
    safe = phase == "safe_to_disconnect"
    ready = phase == "ready"
    attention = phase == "attention"
    review = attention and name != "attention_resolved"
    use_pc = choice in ("games", "none")
    waiting_game = phase == "waiting_game_exit"
    closing_window = waiting_game or name == "copying_window_close"
    closing_message = ("Has pedido salir. Cierra el emulador normalmente para devolver las partidas." if waiting_game else
                       "Has pedido salir. Espera a que termine la copia actual." if closing_window else "")
    games = [
        {"id": game_id("psp-ready"), "title": "Aventura PSP de prueba", "format": "ISO",
         "size": 180 * 1024**2, "availability": "ready", "on_psp": True, "on_pc": True},
        {"id": game_id("psp-missing"), "title": "Carreras PSP de prueba", "format": "ISO",
         "size": 640 * 1024**2, "availability": "missing", "on_psp": True, "on_pc": False},
        {"id": game_id("psp-long"), "title": "Exploración portátil — un título largo para comprobar la biblioteca",
         "format": "CSO", "size": 320 * 1024**2, "availability": "missing", "on_psp": True, "on_pc": False},
        {"id": game_id("pc-only"), "title": "Juego conservado sólo en el PC", "format": "ISO",
         "size": 96 * 1024**2, "availability": "ready", "on_psp": False, "on_pc": True},
    ]
    emulator = {"id": game_id("gpsp"), "name": "gpSP", "systems": ["gba"]}
    unsupported_emulator = {"id": game_id("daedalus"), "name": "DaedalusX64", "systems": ["n64"]}
    rom = {"id": game_id("retro-ready"), "title": "Aventura retro de prueba", "system": "gba",
           "system_name": "Game Boy Advance", "emulator_id": emulator["id"],
           "availability": "ready", "core_available": True, "size": 8 * 1024**2}
    unsupported_rom = {"id": game_id("retro-unmanaged"), "title": "Guardado sin perfil compatible",
                       "system": "n64", "system_name": "Nintendo 64", "emulator_id": unsupported_emulator["id"],
                       "availability": "ready", "core_available": True, "size": 16 * 1024**2}
    native_plan = return_plan("ULUS90001")
    saves = native_plan if review or name in ("choosing", "ready_pc_saves") else {"actions": [], "conflicts": [], "unchanged": 4}
    if purpose == "exit":
        saves = {**saves, "direction_scope": "to_psp"}
    raw_plan = return_plan("SRAM")
    raw = raw_plan["conflicts"][0]
    retro_rows = [
        {"rom_id": rom["id"], "title": rom["title"], "system": "gba", "emulator": "gpSP",
         "status": "conflict" if review or name == "ready_pc_saves" else "identical",
         "revision": raw["revision"], "psp_present": True, "pc_present": True,
         "source_relative": "PSP/GAME/gpSP/GBA/SAV/Aventura.sav", "suggested_path": False,
         "skipped": False, "reason": "Las versiones de PSP y PC han cambiado." if review else ""},
        {"rom_id": unsupported_rom["id"], "title": unsupported_rom["title"], "system": "n64",
         "emulator": "DaedalusX64", "status": "unsupported", "revision": None,
         "psp_present": False, "pc_present": False, "source_relative": None, "suggested_path": False,
         "skipped": False, "reason": "Este guardado no tiene un perfil de SRAM compatible verificado."},
    ]
    progress = dict(IDLE_PROGRESS)
    if phase in ("syncing_in", "copying_game"):
        progress.update(active=True, phase="copying", title=games[1]["title"],
                        total_bytes=games[1]["size"], copied_bytes=218 * 1024**2)
    flow = {
        "enabled": True, "phase": phase, "purpose": purpose, "blocking": not ready,
        "message": MESSAGES.get(phase, closing_message), "can_choose": phase == "choosing",
        "offer_id": signature("ui-offer-" + name), "choice": choice, "use_pc_saves": use_pc,
        "counts": {"psp_games": 3, "pc_games": 2, "missing_games": 2,
                   "save_actions": len(saves["actions"]), "save_conflicts": len(saves["conflicts"]) + int(review)},
        "capabilities": {"automatic_transition": True, "controller_during_storage": True},
        "attention_reason": "Hay partidas de PSP y SRAM con cambios en ambos dispositivos. Elige qué versión conservar." if review else
                            "Las decisiones ya están revisadas. Reintenta para continuar la devolución." if attention else "",
        "can_review_saves": attention, "can_retry": attention, "can_close": safe,
        "operation_active": phase in ("syncing_in", "copying_game", "syncing_out", "finalizing"),
        "game_running": waiting_game, "recovery_required": False, "revision": 3, "last_result": {},
        "window_close_requested": closing_window, "window_close_message": closing_message,
        "progress": progress, "unsupported_state_formats": [UNSUPPORTED_NOTE],
    }
    session = {"running": waiting_game, "pending_return": purpose == "exit" and not safe,
               "exit_requested": purpose == "exit" and not safe, "product_volume": VOLUME,
               "return_result": "verified" if safe else "data_verified" if phase == "finalizing" else ""}
    if ((choice in ("all", "saves") and purpose != "initial") or (ready and not use_pc)):
        session["prepared_volume"] = VOLUME
    state = {
        "demo": False, "devices": [] if safe else [{"root": PSP_ROOT, "label": "PSP · fixture UI"}],
        "selected_device": PSP_ROOT if storage else None, "connection_state": "dock_connected" if native_phase in (1, 2) else "waiting_psp",
        "games": games, "retro": {"emulators": [emulator, unsupported_emulator], "roms": [rom, unsupported_rom]},
        "retro_install": {"status": "ready", "needed": False, "external": False, "missing_cores": [],
                          "requires_license_review": [], "managed_path": SETTINGS["retroarch"], "progress": {"active": False, "phase": "idle"}},
        "retro_saves": {"rows": retro_rows, "pending": int(review or name == "ready_pc_saves"),
                        "conflicts": int(review or name == "ready_pc_saves"), "unmanaged": 1, "offline": not storage},
        "retro_button_layout": "nintendo", "product_flow": flow, "saves": saves,
        "copy_queue": {"status": "idle"}, "copy_policy": "skip_missing", "session": session,
        "controller_status": {"mode": "native_dock", "connected": native_phase in (1, 2),
                              "can_play": native_phase == 2, "synthetic_storage": False, "phase": native_phase,
                              "product_id": PID, "revision": REVISION, "path": "ui-fixture-native-hid",
                              "session": "ui-fixture-native-session", "reason": "PSP Dock conectado." if ready else "Sincronizando PSP."},
        "can_launch_game": ready, "can_launch_retro": ready, "runtime_prepared": True, "runtime_error": "",
        "library_source": "psp_live" if storage else "cached_manifest", "error": "", "recovery_required": False,
        "emulator": {"path": SETTINGS["emulator"], "available": True}, "controller": "Fixture de interfaz; no controla una PSP real.",
    }
    state["preflight"] = assess_storage(connected=storage, demo=False, games=games, saves=saves,
                                       session=session, recovering=False, current_volume_serial=VOLUME,
                                       hid_connected=ready).snapshot()
    return state


def fixtures():
    result = {name: build_fixture(name, phase, purpose, choice) for name, phase, purpose, choice in SCENARIOS}
    for name, state in result.items():
        flow, pad = state["product_flow"], state["controller_status"]
        assert len(flow["offer_id"]) == 64 and flow["phase"] in PHASES | {"waiting_game_exit"}, name
        assert (pad["mode"], pad["product_id"], pad["revision"]) == ("native_dock", PID, REVISION), name
        assert state["can_launch_game"] == state["can_launch_retro"] == (flow["phase"] == "ready"), name
        assert flow["can_close"] == (flow["phase"] == "safe_to_disconnect"), name
        assert flow["can_choose"] == (flow["phase"] == "choosing"), name
        if name == "ready_pc_saves":
            assert not state["preflight"]["saves_prepared"], name
    return result


class FixtureServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, state):
        super().__init__(("127.0.0.1", 0), FixtureHandler)
        self.lock = threading.Lock()
        self.state = copy.deepcopy(state)
        self.requests = []
        self.unexpected_gets = []

    def select(self, state):
        with self.lock:
            self.state = copy.deepcopy(state)


def icon_svg(label, background=False):
    width, height = (1600, 900) if background else (144, 80)
    text = "" if background else '<text x="12" y="49" fill="#d2eceb" font-family="Segoe UI,sans-serif" font-size="15">' + html.escape(label) + "</text>"
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">'
            '<defs><linearGradient id="g"><stop stop-color="#163d52"/><stop offset="1" stop-color="#0c1d2d"/></linearGradient></defs>'
            f'<rect width="{width}" height="{height}" fill="url(#g)"/>' + text + "</svg>").encode()


class FixtureHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, value, status=200, kind="application/json; charset=utf-8"):
        payload = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        path = urlsplit(self.path).path
        static = {"/": ("index.html", "text/html; charset=utf-8"),
                  "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                  "/style.css": ("style.css", "text/css; charset=utf-8")}
        if path in static:
            filename, kind = static[path]
            return self.reply((ROOT / "web" / filename).read_bytes(), kind=kind)
        with self.server.lock:
            state = copy.deepcopy(self.server.state)
        values = {"/api/state": state, "/api/token": {"token": "fixture-only"}, "/api/settings": SETTINGS,
                  "/api/controller-input": {"connected": False, "source": "ui_fixture", "reason": "No physical input."},
                  "/api/transfer-status": state["product_flow"]["progress"], "/api/texture-status": {"active": False},
                  "/api/retro-install-status": state["retro_install"]["progress"]}
        if path in values:
            return self.reply(values[path])
        if path == "/api/graphics" or path.startswith("/api/graphics/"):
            return self.reply({"choice": {"quality": "auto", "aspect": "fit", "texture": "off"},
                               "plan": {"internal_resolution": "1440 × 816", "warning": "", "backend": "directx11"},
                               "hardware": {"monitor": {"width": 1600, "height": 900}, "gpus": []}})
        if path.startswith("/api/icon/") or path == "/favicon.ico":
            return self.reply(icon_svg("PSP · QA"), kind="image/svg+xml")
        if path.startswith("/api/media/"):
            if path.endswith("/background"):
                return self.reply(icon_svg("", True), kind="image/svg+xml")
            if path.endswith("/overlay"):
                return self.reply(b'<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1"/>', kind="image/svg+xml")
        with self.server.lock:
            self.server.unexpected_gets.append(path)
        self.reply({"error": "Unimplemented UI fixture GET: " + path}, 404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        if length > 65536:
            return self.reply({"error": "Oversized fixture request"}, 413)
        raw = self.rfile.read(length)
        try:
            body = json.loads(raw) if raw else {}
        except ValueError:
            body = {"invalid_json": True}
        path = urlsplit(self.path).path
        with self.server.lock:
            self.server.requests.append({"path": path, "body": body})
        if path in ("/api/app/heartbeat", "/api/app/close"):
            return self.reply({"ok": True, "managed": True})
        # Never delegate to the actual application. Unexpected actions are
        # reported and rejected, even if a UI regression submits one.
        self.reply({"error": "This visual fixture implements no mutations."}, 409)


class CDP:
    def __init__(self, url, allowed_origin):
        parsed = urlsplit(url)
        if parsed.hostname not in ("127.0.0.1", "localhost") or parsed.scheme != "ws":
            raise ValueError("Only the isolated loopback browser is allowed")
        self.sock = socket.create_connection((parsed.hostname, parsed.port), timeout=10)
        self.sock.settimeout(15)
        key = base64.b64encode(secrets.token_bytes(16)).decode()
        request = ("GET " + parsed.path + " HTTP/1.1\r\nHost: " + parsed.netloc +
                   "\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: " + key +
                   "\r\nSec-WebSocket-Version: 13\r\n\r\n")
        self.sock.sendall(request.encode())
        raw = b""
        while b"\r\n\r\n" not in raw:
            chunk = self.sock.recv(1)
            if not chunk or len(raw) > 16384:
                raise ValueError("Incomplete browser WebSocket handshake")
            raw += chunk
        expected = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest())
        if not raw.startswith(b"HTTP/1.1 101") or expected not in raw:
            raise ValueError("Invalid browser WebSocket handshake")
        self.sequence, self.current_view = 0, "startup"
        self.errors, self.external_requests = [], []
        self.allowed_origin = allowed_origin

    def send_frame(self, payload, opcode=1):
        mask = secrets.token_bytes(4)
        size = len(payload)
        header = bytes([0x80 | opcode])
        header += (bytes([0x80 | size]) if size < 126 else
                   bytes([0x80 | 126]) + struct.pack("!H", size) if size < 65536 else
                   bytes([0x80 | 127]) + struct.pack("!Q", size))
        self.sock.sendall(header + mask + bytes(c ^ mask[i % 4] for i, c in enumerate(payload)))

    def read(self, length):
        data = b""
        while len(data) < length:
            chunk = self.sock.recv(length - len(data))
            if not chunk:
                raise EOFError("Browser WebSocket closed")
            data += chunk
        return data

    def receive(self):
        message = b""
        while True:
            first, second = self.read(2)
            size = second & 127
            if size == 126:
                size = struct.unpack("!H", self.read(2))[0]
            elif size == 127:
                size = struct.unpack("!Q", self.read(8))[0]
            if size > 32 * 1024**2:
                raise ValueError("Oversized browser frame")
            mask = self.read(4) if second & 128 else b""
            data = self.read(size)
            if mask:
                data = bytes(c ^ mask[i % 4] for i, c in enumerate(data))
            opcode = first & 15
            if opcode == 8:
                raise EOFError("Browser closed")
            if opcode == 9:
                self.send_frame(data, 10)
                continue
            if opcode == 10:
                continue
            message += data
            if first & 128:
                return json.loads(message)

    def observe(self, event):
        method, params = event.get("method"), event.get("params", {})
        error = (method == "Runtime.exceptionThrown" or
                 method == "Runtime.consoleAPICalled" and params.get("type") == "error" or
                 method == "Log.entryAdded" and params.get("entry", {}).get("level") == "error" or
                 method == "Network.loadingFailed" and not params.get("canceled"))
        if error:
            self.errors.append({"view": self.current_view, "method": method, "details": params})
        if method == "Network.requestWillBeSent":
            url = params.get("request", {}).get("url", "")
            if urlsplit(url).scheme in ("http", "https") and not url.startswith(self.allowed_origin + "/"):
                self.external_requests.append({"view": self.current_view, "url": url})

    def call(self, method, params=None):
        self.sequence += 1
        ident = self.sequence
        self.send_frame(json.dumps({"id": ident, "method": method, "params": params or {}}).encode())
        while True:
            event = self.receive()
            self.observe(event)
            if event.get("id") == ident:
                if "error" in event:
                    raise ValueError(event["error"])
                return event.get("result", {})

    def evaluate(self, expression):
        result = self.call("Runtime.evaluate", {"expression": expression, "returnByValue": True, "awaitPromise": True})
        if result.get("exceptionDetails"):
            raise ValueError(result["exceptionDetails"])
        return result.get("result", {}).get("value")

    def close(self):
        try:
            self.send_frame(b"", 8)
        except OSError:
            pass
        self.sock.close()


LAYOUT = r"""(() => {
  const byId = id => document.getElementById(id);
  const visible = node => !!node && !node.closest('[hidden]') && node.getClientRects().length > 0;
  const button = id => ({visible: visible(byId(id)), disabled: byId(id).disabled, text: byId(id).textContent.trim()});
  const panel = byId('dock-session'), dialog = document.querySelector('dialog[open]');
  const container = dialog || (!panel.hidden ? panel : byId('xmb'));
  const rect = container.getBoundingClientRect();
  const selectors = dialog ? '.dialog-content,.save-row,.conflict-actions,button,p,h2' :
    container.id === 'xmb' ? '#xmb-hid,#xmb-hints,.xmb-cat.on,.xmb-item.on .xmb-ic,.xmb-item.on .xmb-tx,#xmb-side.open' :
    '#dock-session .dock-session-content,#dock-session h1,#dock-session p,#dock-session dl,#dock-session button';
  const clipped = [...container.querySelectorAll(selectors)].filter(visible).filter(node => {
    const b = node.getBoundingClientRect();
    return b.left < rect.left - 2 || b.right > rect.right + 2 || node.scrollWidth > node.clientWidth + 2;
  }).map(node => ({id: node.id, tag: node.tagName, text: node.textContent.trim().slice(0, 130)}));
  return {
    title: document.title, phase: ui.state.product_flow.phase, purpose: ui.state.product_flow.purpose,
    screen: xmbConnectionView().screen, productVisible: visible(panel), waitingVisible: visible(byId('dock-connect')),
    menuVisible: visible(byId('xmb-menu')), menuInert: byId('xmb-menu').inert,
    diagnosticVisible: visible(byId('dock-diagnostic')), serviceOffline: ui.serviceOffline, busy: ui.busy,
    heading: byId('dock-session-title').textContent, message: byId('dock-session-message').textContent,
    note: byId('dock-session-note').textContent, view: xmbConnectionView(),
    launchDisabled: byId('launch-button').disabled, savesPrepared: ui.state.preflight.saves_prepared,
    selectedGame: ui.state.games.find(game => game.id === ui.selected) || null,
    choices: [...document.querySelectorAll('[data-session-choice]')].map(node => ({choice: node.dataset.sessionChoice, visible: visible(node), disabled: node.disabled})),
    review: button('dock-session-review'), retry: button('dock-session-retry'), close: button('dock-session-close'),
    progressVisible: visible(byId('dock-session-progress')), sync: button('sync-button'),
    documentOverflow: document.documentElement.scrollWidth > innerWidth + 2 || document.documentElement.scrollHeight > innerHeight + 2,
    containerOverflowX: container.scrollWidth > container.clientWidth + 2, clipped,
    containerOverflowXPolicy: getComputedStyle(container).overflowX,
    containerInsideViewport: rect.top >= -2 && rect.bottom <= innerHeight + 2 && rect.left >= -2 && rect.right <= innerWidth + 2,
    scroll: {height: container.clientHeight, content: container.scrollHeight, top: container.scrollTop},
    dialog: dialog?.id || null, activeChoice: document.activeElement?.dataset.sessionChoice || null,
    rows: dialog ? [...byId('saves-content').querySelectorAll('.save-row')].map(row => ({text: row.textContent.trim(),
      buttons: [...row.querySelectorAll('button')].filter(visible).map(node => ({text: node.textContent.trim(), disabled: node.disabled}))})) : [],
    gameRows: xmb.sections.find(section => section.id === 'juegos')?.items.filter(item => item.game).map(item => ({id: item.game.id, chip: item.chip})) || []
  };
})()"""


def validate_layout(layout, state, dialog=False):
    flow = state["product_flow"]
    ready, choosing = flow["phase"] == "ready", flow["phase"] == "choosing"
    expected_screen = "menu" if ready else "product"
    checks = {
        "document_title": layout["title"] == "PSP Dock",
        "phase": layout["phase"] == flow["phase"] and layout["purpose"] == flow["purpose"],
        "screen": layout["screen"] == expected_screen,
        "blocking_panel": layout["productVisible"] == (not ready),
        "xmb_visibility": layout["menuVisible"] == ready and layout["menuInert"] != ready,
        "no_waiting_or_diagnostic": not layout["waitingVisible"] and not layout["diagnosticVisible"],
        "online": not layout["serviceOffline"] and not layout["busy"],
        "no_document_overflow": not layout["documentOverflow"],
        "panel_inside_viewport": layout["containerInsideViewport"],
        "choices": all(item["visible"] == choosing and item["disabled"] != choosing for item in layout["choices"]),
        "review_permission": layout["review"]["visible"] == flow["can_review_saves"],
        "retry_permission": layout["retry"]["visible"] == flow["can_retry"],
        "safe_close_permission": layout["close"]["visible"] == flow["can_close"],
        "measured_progress": layout["progressVisible"] == flow["progress"]["active"],
        "launch_gate": layout["view"]["canPlay"] == ready,
    }
    if ready:
        # Closed XMB side menus deliberately live one viewport to the right.
        # Their scrollWidth is not a visible layout overflow: the root clips
        # them. Check that policy and the active controls, while product and
        # dialog panels below still require no horizontal scrolling at all.
        checks["xmb_navigation_clipped_by_viewport"] = layout["containerOverflowXPolicy"] in ("hidden", "clip") and not layout["clipped"]
        checks["native_ready"] = layout["view"]["nativeController"]
        selected = layout["selectedGame"] or {}
        checks["selected_game_known"] = selected.get("id") in {game["id"] for game in state["games"]}
        checks["selected_launch_gate"] = layout["launchDisabled"] == (selected.get("availability") != "ready")
        pc_only = next((row for row in layout["gameRows"] if row["id"] == game_id("pc-only")), {})
        checks["pc_only_game_label"] = pc_only.get("chip") == ["pc", "EN PC"]
        if flow["use_pc_saves"]:
            checks["explicit_pc_save_policy"] = layout["view"]["product"]["usePcSaves"] and not layout["savesPrepared"]
    else:
        checks["no_panel_horizontal_overflow"] = not layout["containerOverflowX"] and not layout["clipped"]
    if choosing:
        checks["explicit_question"] = layout["message"] == "¿Quieres copiar todos los juegos y sincronizar toda la PSP ahora?"
        checks["real_storage_controller_hint"] = "cruceta" in layout["note"]
    if flow["phase"] == "finalizing":
        checks["no_premature_disconnect"] = "ya puedes desconectar" not in layout["message"].lower()
    if flow.get("window_close_requested"):
        checks["window_close_explained"] = flow["window_close_message"] in layout["message"] + layout["note"]
    if flow["phase"] == "waiting_game_exit":
        checks["wait_for_normal_game_exit"] = layout["heading"] == "Terminando la sesión" and not layout["view"]["canPlay"]
    if dialog:
        checks["review_modal"] = layout["dialog"] == "saves-dialog"
        if state["saves"]["conflicts"]:
            checks["native_conflict_present"] = any("ULUS90001_CONFLICT" in row["text"] for row in layout["rows"])
        if state["retro_saves"]["conflicts"]:
            checks["retro_conflict_present"] = any("Aventura retro de prueba" in row["text"] for row in layout["rows"])
        checks["unsupported_save_present"] = any("Guardado sin perfil compatible" in row["text"] for row in layout["rows"])
        if flow["purpose"] == "exit":
            checks["no_unscoped_exit_sync"] = not layout["sync"]["visible"] and layout["sync"]["disabled"]
        for index, row in enumerate(layout["rows"]):
            labels = [button["text"] for button in row["buttons"]]
            if "ULUS90001_CONFLICT" in row["text"]:
                checks[f"native_conflict_{index}"] = labels == (["Devolver la del PC", "Conservar la PSP sin copiar"] if flow["purpose"] == "exit" else ["Usar PC", "Usar PSP"])
                checks[f"native_permission_{index}"] = all(not button["disabled"] for button in row["buttons"])
            if "Aventura retro de prueba" in row["text"] and state["retro_saves"]["conflicts"]:
                checks[f"retro_conflict_{index}"] = labels == (["Devolver la del PC", "Conservar sin copiar"] if flow["purpose"] == "exit" else ["Traer la de la PSP", "Devolver la del PC"])
                checks[f"retro_permission_{index}"] = all(not button["disabled"] for button in row["buttons"])
            if "Guardado sin perfil compatible" in row["text"]:
                checks[f"unsupported_has_no_copy_{index}"] = not labels
    return checks


def keypress(client, key, code, virtual_key):
    params = {"key": key, "code": code, "windowsVirtualKeyCode": virtual_key, "nativeVirtualKeyCode": virtual_key}
    client.call("Input.dispatchKeyEvent", {"type": "keyDown", **params})
    client.call("Input.dispatchKeyEvent", {"type": "keyUp", **params})


def settle(client):
    client.evaluate("new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")


def capture(client, output, name, size, state, report, dialog=False):
    settle(client)
    layout = client.evaluate(LAYOUT)
    checks = validate_layout(layout, state, dialog)
    filename = f"{name}-{size[0]}x{size[1]}.png"
    shot = client.call("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False})
    (output / filename).write_bytes(base64.b64decode(shot["data"]))
    view = {"name": name, "viewport": list(size), "screenshot": filename, "layout": layout, "checks": checks}
    report["views"].append(view)
    report["failures"].extend(name + ": " + check for check, ok in checks.items() if not ok)
    if layout["scroll"]["content"] > layout["scroll"]["height"] + 2:
        selector = "dialog[open]" if dialog else "#dock-session"
        client.evaluate(f"document.querySelector({json.dumps(selector)}).scrollTop=100000;true")
        settle(client)
        bottom = client.evaluate(LAYOUT)
        footer = filename.removesuffix(".png") + "-bottom.png"
        shot = client.call("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False})
        (output / footer).write_bytes(base64.b64decode(shot["data"]))
        view["scrolled_screenshot"] = footer
        view["scroll_reachable"] = bottom["scroll"]["top"] > 0 and not bottom["containerOverflowX"] and not bottom["clipped"]
        if not view["scroll_reachable"]:
            report["failures"].append(name + ": scrollable controls are unreachable or horizontally clipped")
        client.evaluate(f"document.querySelector({json.dumps(selector)}).scrollTop=0;true")


def browser_path(explicit):
    if explicit:
        candidate = Path(explicit)
        if candidate.is_file():
            return candidate
        raise FileNotFoundError("Browser does not exist: " + str(candidate))
    for variable, relative in (("PROGRAMFILES", "Google/Chrome/Application/chrome.exe"),
                               ("PROGRAMFILES(X86)", "Google/Chrome/Application/chrome.exe"),
                               ("LOCALAPPDATA", "Google/Chrome/Application/chrome.exe"),
                               ("PROGRAMFILES(X86)", "Microsoft/Edge/Application/msedge.exe"),
                               ("PROGRAMFILES", "Microsoft/Edge/Application/msedge.exe")):
        if os.environ.get(variable):
            candidate = Path(os.environ[variable]) / relative
            if candidate.is_file():
                return candidate
    for name in ("google-chrome", "chromium", "chromium-browser"):
        if shutil.which(name):
            return Path(shutil.which(name))
    raise FileNotFoundError("Chrome/Edge was not found. Pass --browser with its executable path.")


def stop_browser(process, client, report):
    if client:
        try:
            client.call("Browser.close")
        except (EOFError, OSError, ValueError):
            pass
        finally:
            client.close()
    if process:
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            if os.name == "nt":
                # Only the PID tree started above, never every Chrome process.
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10, creationflags=0x08000000)
            else:
                process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        report["browser_exit_code"] = process.returncode
        report["browser_closed"] = process.poll() is not None


def run(output, browser):
    states = fixtures()
    output.mkdir(parents=True, exist_ok=True)
    report = {"kind": "synthetic_product_ui_fixture", "physical_psp_test": False,
              "live_backend_used": False, "views": [], "failures": [], "errors": [],
              "source_sha256": {name: hashlib.sha256((ROOT / "web" / name).read_bytes()).hexdigest()
                                for name in ("app.js", "index.html", "style.css")}}
    server = FixtureServer(states["choosing"])
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client = process = None
    temporary = tempfile.TemporaryDirectory(prefix="pspdock-product-ui-")
    origin = "http://127.0.0.1:" + str(server.server_port)
    opener = build_opener(ProxyHandler({}))
    try:
        profile = Path(temporary.name) / "chrome"
        process = subprocess.Popen([str(browser), "--headless=new", "--disable-gpu", "--no-first-run",
                                    "--no-default-browser-check", "--disable-background-networking", "--disable-sync",
                                    "--disable-extensions", "--disable-component-update", "--no-proxy-server", "--mute-audio",
                                    "--remote-debugging-address=127.0.0.1", "--remote-debugging-port=0",
                                    "--user-data-dir=" + str(profile), "about:blank"],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   creationflags=0x08000000 if os.name == "nt" else 0)
        report["browser_pid"] = process.pid
        deadline = time.monotonic() + 20
        active = profile / "DevToolsActivePort"
        while not active.exists():
            if process.poll() is not None or time.monotonic() > deadline:
                raise TimeoutError("The isolated browser did not expose its debug port")
            time.sleep(.1)
        port = int(active.read_text().splitlines()[0])
        with opener.open(f"http://127.0.0.1:{port}/json", timeout=5) as response:
            page = next(item for item in json.load(response) if item["type"] == "page")
        client = CDP(page["webSocketDebuggerUrl"], origin)
        for domain in ("Page", "Runtime", "Log", "Network"):
            client.call(domain + ".enable")
        client.call("Emulation.setEmulatedMedia", {"features": [{"name": "prefers-reduced-motion", "value": "reduce"}]})
        client.call("Page.addScriptToEvaluateOnNewDocument", {"source": "try { localStorage.setItem('psp-dock-xmb-sound','off'); localStorage.setItem('psp-dock-theme','dock'); } catch {}"})
        client.call("Page.navigate", {"url": origin + "/"})
        deadline = time.monotonic() + 20
        while not client.evaluate("typeof ui !== 'undefined' && ui.state !== null"):
            if time.monotonic() > deadline:
                raise TimeoutError("The UI did not render its fixture")
            time.sleep(.1)
        for size in ((1600, 900), (540, 960)):
            client.call("Emulation.setDeviceMetricsOverride", {"width": size[0], "height": size[1], "deviceScaleFactor": 1, "mobile": False})
            for name, state in states.items():
                if size[0] == 540 and name not in NARROW_SCENARIOS:
                    continue
                client.current_view = f"{name}-{size[0]}x{size[1]}"
                server.select(state)
                client.evaluate("""(async()=>{
                  document.querySelectorAll('dialog[open]').forEach(d=>d.close());
                  // Each snapshot starts with the same selected game. Input
                  // subcases may then deliberately select a pending title.
                  xmb.nav={cat:2,idx:{},level:'list',subIdx:0,side:null,confirm:null};
                  xmb.sub=null;xmb.side=null;xmb.confirm=null;ui.selected=null;
                  await refresh();document.getElementById('dock-session').scrollTop=0;
                  return true;
                })()""")
                capture(client, output, name, size, state, report)
                if name == "choosing":
                    keypress(client, "ArrowLeft", "ArrowLeft", 37)
                    keypress(client, "Escape", "Escape", 27)
                    if client.evaluate("document.activeElement?.dataset.sessionChoice") != "none":
                        report["failures"].append(client.current_view + ": Circle/Escape did not focus the explicit no-copy option")
                elif state["product_flow"]["phase"] in ("syncing_in", "copying_game", "syncing_out", "finalizing", "waiting_game_exit"):
                    for key, code, number in (("ArrowDown", "ArrowDown", 40), ("Enter", "Enter", 13), ("t", "KeyT", 84), ("Escape", "Escape", 27)):
                        keypress(client, key, code, number)
                    if client.evaluate("xmbConnectionView().screen") != "product":
                        report["failures"].append(client.current_view + ": input escaped the blocking phase")
                elif name == "ready":
                    keypress(client, "t", "KeyT", 84)
                    if not client.evaluate("Boolean(xmb.nav.side && xmb.side)"):
                        report["failures"].append("ready: TRIANGLE/T did not open the context menu")
                    keypress(client, "Escape", "Escape", 27)
                    keypress(client, "ArrowDown", "ArrowDown", 40)
                    keypress(client, "Enter", "Enter", 13)
                    if not client.evaluate("Boolean(xmb.nav.side && xmb.side)"):
                        report["failures"].append("ready: Cross on a missing game did not open its menu")
                    capture(client, output, "ready-copy-menu", size, state, report)
                    keypress(client, "Escape", "Escape", 27)
                if state["product_flow"]["can_review_saves"]:
                    client.evaluate("document.getElementById('dock-session-review').click();true")
                    capture(client, output, name + "-review", size, state, report, dialog=True)
                    client.evaluate("document.getElementById('saves-dialog').close();true")
        client.evaluate("true")  # Drain queued Runtime/Log events before closing.
    except Exception as exc:
        report["failures"].append(repr(exc))
    finally:
        try:
            stop_browser(process, client, report)
        except Exception as exc:
            report["failures"].append("Browser cleanup failed: " + repr(exc))
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        try:
            temporary.cleanup()
            report["temporary_profile_removed"] = True
        except OSError as exc:
            report["temporary_profile_removed"] = False
            report["failures"].append("Temporary browser profile cleanup failed: " + repr(exc))
        report["server_closed"] = not thread.is_alive()
        report["errors"] = client.errors if client else []
        report["external_requests"] = client.external_requests if client else []
        report["unexpected_gets"] = server.unexpected_gets
        report["unexpected_mutations"] = [item for item in server.requests
            if item["path"] != "/api/app/heartbeat" and not
            (item["path"] == "/api/app/close" and item["body"].get("reason") == "pagehide")]
        report["lifecycle_requests"] = len(server.requests) - len(report["unexpected_mutations"])
        failed = any(report[key] for key in ("failures", "errors", "external_requests", "unexpected_gets", "unexpected_mutations"))
        report["result"] = "failed" if failed or not report.get("browser_closed") or not report["server_closed"] else "passed"
        (output / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"result": report["result"], "views": len(report["views"]), "output": str(output),
                          "failures": report["failures"], "js_errors": len(report["errors"]),
                          "unexpected_mutations": len(report["unexpected_mutations"])}, ensure_ascii=False))
    return 0 if report["result"] == "passed" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", help="Chrome/Edge executable; uses an isolated temporary profile")
    parser.add_argument("--output", type=Path, default=ROOT / "data" / "product-session-ui")
    parser.add_argument("--fixture-check", action="store_true", help="Validate snapshots only; never start a server/browser")
    args = parser.parse_args()
    if args.fixture_check:
        print(json.dumps({"result": "passed", "fixtures": list(fixtures()), "browser_started": False}))
        return 0
    return run(args.output.resolve(), browser_path(args.browser))


if __name__ == "__main__":
    raise SystemExit(main())
