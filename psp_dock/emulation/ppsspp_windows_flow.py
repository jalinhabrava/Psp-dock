"""Windows-only PPSSPP window lifecycle and PSP HOME -> pause-menu mapping.

No global Windows hotkeys, keyboard hooks, remote key injection, device
mutations, or firmware commands. The PPSSPP pause menu uses its own native
DirectInput binding (PSP HOME button 9 -> virtual key 10-196).
"""
from __future__ import annotations

import ctypes as C
from ctypes import wintypes as W
import hashlib
import os
from pathlib import Path
import re
import threading
import time
import uuid


class WindowFlowError(ValueError):
    pass


HOME_DIRECTINPUT = "10-196"
KNOWN_GAMEPAD = ("10-188", "10-189", "10-190", "10-191")


def add_home_pause_mapping(profile: Path, backups: Path) -> dict:
    """Add PSP HOME to PPSSPP's own Pause binding, preserving all others.

    For the private PC memstick only. Idempotent, bounded, SHA-backed copy,
    atomic replace. Does not map PSP Home to a Windows-wide Escape shortcut.
    """
    profile, backups = Path(profile), Path(backups)
    if (not profile.is_file() or profile.is_symlink() or
            profile.parent.is_symlink() or backups.is_symlink()):
        raise WindowFlowError("Falta el perfil de controles privado de PPSSPP.")
    initial = profile.read_bytes()
    if len(initial) > 1024*1024 or b"\x00" in initial:
        raise WindowFlowError("Perfil PPSSPP inválido.")
    try:
        text = initial.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise WindowFlowError("controls.ini no está en UTF-8.") from exc
    # Never modify an unknown/unmapped controller profile. Prior physical
    # verification established this particular 8-button DirectInput map.
    for key in KNOWN_GAMEPAD:
        if key not in text:
            raise WindowFlowError("No está el mando PSP DirectInput conocido.")
    lines=text.splitlines(keepends=True)
    section=None
    indexes=[]
    for i,line in enumerate(lines):
        trimmed=line.strip()
        if trimmed.startswith("[") and trimmed.endswith("]"):
            section=trimmed[1:-1]
        if section=="ControlMapping" and re.match(r"^Pause\s*=",trimmed):
            indexes.append(i)
    if len(indexes)!=1:
        raise WindowFlowError("La acción Pause no es inequívoca.")
    index=indexes[0]
    line=lines[index]
    before, sep, after=line.partition("=")
    value=after.strip()
    codes=[part.strip() for part in value.split(",")]
    if HOME_DIRECTINPUT in codes:
        return {"changed":False,"profile":str(profile),"code":HOME_DIRECTINPUT}
    if len(codes)>32 or any(not re.fullmatch(r"\d+-\d+",code) for code in codes):
        raise WindowFlowError("La lista Pause incluye asignaciones desconocidas.")
    terminator="\r\n" if line.endswith("\r\n") else "\n" if line.endswith("\n") else ""
    lines[index]=before+"="+ ",".join(codes+[HOME_DIRECTINPUT]) +terminator
    updated="".join(lines).encode("utf-8")
    backups.mkdir(parents=True,exist_ok=True)
    h=hashlib.sha256(initial).hexdigest()
    backup=backups/("controls.before-psp-home-"+h[:24]+".ini")
    if backup.is_file():
        if backup.read_bytes()!=initial:
            raise WindowFlowError("Backup del mapeado Home incorrecto.")
    else:
        with backup.open("xb") as destination:
            destination.write(initial)
            destination.flush()
            os.fsync(destination.fileno())
    if profile.read_bytes()!=initial:
        raise WindowFlowError("PPSSPP modificó los controles durante el cambio.")
    stage=profile.with_name(".pspdock-home-"+uuid.uuid4().hex+".tmp")
    try:
        with stage.open("xb") as destination:
            destination.write(updated)
            destination.flush()
            os.fsync(destination.fileno())
        if profile.read_bytes()!=initial:
            raise WindowFlowError("Cambio concurrente en controls.ini.")
        os.replace(stage,profile)
    finally:
        stage.unlink(missing_ok=True)
    return {"changed":True,"profile":str(profile),
            "backup":str(backup),"code":HOME_DIRECTINPUT}


def find_window_for_pid(pid: int, *, enumerate_rows=None) -> int | None:
    """Choose a unique visible top-level window belonging to PPSSPP process."""
    if type(pid) is not int or pid <= 0:
        return None
    rows=(enumerate_rows or _enum_windows)()
    matches=[row for row in rows if
             row.get("pid")==pid and row.get("visible") is True and
             type(row.get("handle")) is int and row["handle"]>0 and
             row.get("title","").strip()]
    return matches[0]["handle"] if len(matches)==1 else None


def _enum_windows() -> list[dict]:
    if os.name!="nt":
        return []
    user32=C.WinDLL("user32",use_last_error=True)
    cb=C.WINFUNCTYPE(W.BOOL,W.HWND,W.LPARAM)
    user32.EnumWindows.argtypes=[cb,W.LPARAM]
    user32.GetWindowTextLengthW.argtypes=[W.HWND]
    user32.GetWindowTextW.argtypes=[W.HWND,W.LPWSTR,C.c_int]
    user32.GetWindowThreadProcessId.argtypes=[W.HWND,C.POINTER(W.DWORD)]
    user32.IsWindowVisible.argtypes=[W.HWND]
    rows=[]
    def visit(hwnd,_):
        if not user32.IsWindowVisible(hwnd):
            return True
        size=user32.GetWindowTextLengthW(hwnd)
        if not 0<size<512:
            return True
        title=C.create_unicode_buffer(size+1)
        user32.GetWindowTextW(hwnd,title,size+1)
        pid=W.DWORD()
        user32.GetWindowThreadProcessId(hwnd,C.byref(pid))
        rows.append({"handle":int(hwnd),"pid":int(pid.value),
                     "title":title.value,"visible":True})
        return True
    user32.EnumWindows(cb(visit),0)
    return rows


def focus_process_window(pid: int) -> bool:
    """Foreground only a uniquely identified window owned by the new process.

    No global Alt+Tab or ESC injections. Refuse ambiguous windows rather
    than focusing a different application by title.
    """
    if os.name!="nt":
        return False
    hwnd=find_window_for_pid(pid)
    if hwnd is None:
        return False
    user32=C.WinDLL("user32",use_last_error=True)
    user32.ShowWindow.argtypes=[W.HWND,C.c_int]
    user32.IsIconic.argtypes=[W.HWND]
    user32.IsIconic.restype=W.BOOL
    user32.BringWindowToTop.argtypes=[W.HWND]
    user32.SetForegroundWindow.argtypes=[W.HWND]
    user32.GetWindowThreadProcessId.argtypes=[W.HWND,C.POINTER(W.DWORD)]
    user32.GetWindowThreadProcessId.restype=W.DWORD
    user32.AttachThreadInput.argtypes=[W.DWORD,W.DWORD,W.BOOL]
    user32.AttachThreadInput.restype=W.BOOL
    user32.GetForegroundWindow.argtypes=[]
    user32.GetForegroundWindow.restype=W.HWND
    # SW_RESTORE on an already-fullscreen PPSSPP could resize it: restore
    # only if Windows actually reports the window is minimized.
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd,9)
    user32.BringWindowToTop(hwnd)
    user32.SetForegroundWindow(hwnd)
    if int(user32.GetForegroundWindow() or 0)==hwnd:
        return True
    # Windows normally forbids a background HTTP process from stealing focus.
    # Attach ONLY our short-lived thread to the currently foreground and
    # verified PPSSPP window threads; detach immediately in finally.
    own_thread=C.windll.kernel32.GetCurrentThreadId()
    foreground=user32.GetForegroundWindow()
    fg_thread=user32.GetWindowThreadProcessId(foreground,None) if foreground else 0
    game_thread=user32.GetWindowThreadProcessId(hwnd,None)
    attached=[]
    try:
        for thread_id in (fg_thread,game_thread):
            if thread_id and thread_id!=own_thread and thread_id not in attached:
                if user32.AttachThreadInput(own_thread,thread_id,True):
                    attached.append(thread_id)
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        return int(user32.GetForegroundWindow() or 0)==hwnd
    finally:
        for thread_id in reversed(attached):
            user32.AttachThreadInput(own_thread,thread_id,False)


def focus_when_ready(process, *, seconds=12, poll=0.2) -> bool:
    """Wait for PPSSPP window after Process.Popen, then focus it."""
    if not 0<seconds<=30 or not 0.05<=poll<=2:
        raise WindowFlowError("Límites de espera de PPSSPP inválidos.")
    if os.name!="nt":
        return False
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        if process.poll() is not None:
            return False
        if focus_process_window(process.pid):
            return True
        time.sleep(poll)
    return False


def focus_async(process) -> None:
    """Nonblocking window focus, without holding Dock.lock during startup."""
    threading.Thread(target=focus_when_ready,args=(process,),
                     daemon=True,name="PSPDockFocusPPSSPP").start()


def return_to_xmb() -> bool:
    """Focus only the original uniquely titled PSP Dock app window."""
    try:
        from psp_dock.core.xmb_window import maximize_existing_xmb
        return maximize_existing_xmb()
    except (ImportError,OSError,ValueError):
        return False
