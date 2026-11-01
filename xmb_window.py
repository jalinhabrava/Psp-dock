"""Reuse/maximize only the verified Chrome/Edge PSP Dock app window.

No key injection, arbitrary desktop automation or USB access.
The uniquely titled Chrome_WidgetWin_1 window is the only target.
"""
from __future__ import annotations

import ctypes as C
from ctypes import wintypes as W
import os
import time


TITLE = 'PSP Dock · Tu próxima partida'
CHROME_CLASS = 'Chrome_WidgetWin_1'


def unique_target(rows: list[dict]) -> int | None:
    """Reject ambiguous titles and browser tabs or foreign window classes."""
    matches = [row for row in rows
               if row.get('visible') is True and
               row.get('title') == TITLE and
               row.get('window_class') == CHROME_CLASS and
               type(row.get('handle')) is int and row['handle'] > 0]
    return matches[0]['handle'] if len(matches) == 1 else None


def enumerate_xmb_windows() -> list[dict]:
    if os.name != 'nt':
        return []
    user32=C.WinDLL('user32',use_last_error=True)
    cb=C.WINFUNCTYPE(W.BOOL,W.HWND,W.LPARAM)
    user32.EnumWindows.argtypes=[cb,W.LPARAM]
    user32.EnumWindows.restype=W.BOOL
    user32.GetWindowTextLengthW.argtypes=[W.HWND]
    user32.GetWindowTextLengthW.restype=C.c_int
    user32.GetWindowTextW.argtypes=[W.HWND,W.LPWSTR,C.c_int]
    user32.GetWindowTextW.restype=C.c_int
    user32.GetClassNameW.argtypes=[W.HWND,W.LPWSTR,C.c_int]
    user32.GetClassNameW.restype=C.c_int
    user32.IsWindowVisible.argtypes=[W.HWND]
    user32.IsWindowVisible.restype=W.BOOL
    rows=[]
    def visit(hwnd,unused):
        size=user32.GetWindowTextLengthW(hwnd)
        if not 0 < size <= 256:
            return True
        title=C.create_unicode_buffer(size+1)
        user32.GetWindowTextW(hwnd,title,size+1)
        if title.value != TITLE:
            return True
        class_name=C.create_unicode_buffer(80)
        user32.GetClassNameW(hwnd,class_name,len(class_name))
        rows.append({
            'handle':int(hwnd), 'title':title.value,
            'window_class':class_name.value,
            'visible':bool(user32.IsWindowVisible(hwnd)),
        })
        return True
    user32.EnumWindows(cb(visit),0)
    return rows


def maximize_existing_xmb() -> bool:
    """Make an existing Dock window occupy Windows' usable primary monitor."""
    if os.name != 'nt':
        return False
    target=unique_target(enumerate_xmb_windows())
    if target is None:
        return False
    user32=C.WinDLL('user32',use_last_error=True)
    user32.ShowWindow.argtypes=[W.HWND,C.c_int]
    user32.ShowWindow.restype=W.BOOL
    user32.SetForegroundWindow.argtypes=[W.HWND]
    user32.SetForegroundWindow.restype=W.BOOL
    user32.ShowWindow(target,3)  # SW_MAXIMIZE (avoid fake F11/keyboard input)
    user32.SetForegroundWindow(target)
    return True


def close_existing_xmb() -> bool:
    """Close only a uniquely identified PSP Dock window, not other apps.

    Never use Alt-F4 or kill Chrome. Only WM_CLOSE is delivered after the
    original Sony USBSTOR returned with SAVEDATA verified. Ambiguous
    matching browser windows are deliberately left alone.
    """
    if os.name != 'nt':
        return False
    hwnd=unique_target(enumerate_xmb_windows())
    if hwnd is None:
        return False
    user32=C.WinDLL('user32',use_last_error=True)
    user32.PostMessageW.argtypes=[W.HWND,W.UINT,W.WPARAM,W.LPARAM]
    user32.PostMessageW.restype=W.BOOL
    return bool(user32.PostMessageW(hwnd,0x0010,0,0))  # WM_CLOSE


def maximize_when_ready(*, seconds=5, interval=0.15) -> bool:
    """Graceful wait for an app window when Chrome starts asynchronously."""
    if not 0 < seconds <= 10 or not 0.05 <= interval <= 1:
        raise ValueError('Tiempo de preparación de ventana fuera de límites.')
    if os.name != 'nt':
        return False
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        if maximize_existing_xmb():
            return True
        time.sleep(interval)
    return False
