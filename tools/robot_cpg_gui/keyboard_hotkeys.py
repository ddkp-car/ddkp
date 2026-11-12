"""Windows Numpad shortcuts without requiring Tk focus.

Uses Win32 RegisterHotKey on a dedicated message-pump thread so it works
when widgets, graphs, or another window have keyboard focus. No extra pip
packages or administrator privileges are needed. The GUI is the ONLY thread
that applies a mode change; this worker posts numeric key IDs to a Queue.

With Num Lock OFF Windows reports navigation VKs rather than VK_NUMPADn.
Tk's local binding covers those when the application is focused; for global
use keep Num Lock ON. Disable the global listener to release keys for other
programs.
"""
from __future__ import annotations

import os
import queue
import threading


class WindowsNumpadHotkeys:
    WM_HOTKEY = 0x0312
    WM_QUIT = 0x0012
    PM_NOREMOVE = 0x0000
    MOD_NOREPEAT = 0x4000
    HOTKEY_BASE = 0x5A10
    VK_NUMPAD0 = 0x60

    def __init__(self) -> None:
        self.events: queue.Queue[int] = queue.Queue()
        self.statuses: queue.Queue[tuple[int, ...]] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self._stop = threading.Event()
        self._ready = threading.Event()

    def start(self) -> None:
        if os.name != 'nt':
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._ready.clear()
        self._thread = threading.Thread(target=self._run, name='NumpadHotkeys', daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if os.name == 'nt' and self._thread_id:
            import ctypes
            ctypes.windll.user32.PostThreadMessageW(self._thread_id, self.WM_QUIT, 0, 0)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=0.8)

    def _run(self) -> None:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
        user32.RegisterHotKey.restype = wintypes.BOOL
        user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.UnregisterHotKey.restype = wintypes.BOOL
        user32.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                                      wintypes.UINT, wintypes.UINT, wintypes.UINT]
        user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                                     wintypes.UINT, wintypes.UINT]
        user32.GetMessageW.restype = ctypes.c_int
        user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        registered: list[int] = []
        try:
            msg = wintypes.MSG()
            # Create this thread's message queue before shutdown can post WM_QUIT.
            user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, self.PM_NOREMOVE)
            self._thread_id = kernel32.GetCurrentThreadId()
            if self._stop.is_set():
                return
            failed: list[int] = []
            for num in range(5):
                hotkey_id = self.HOTKEY_BASE + num
                if user32.RegisterHotKey(None, hotkey_id, self.MOD_NOREPEAT,
                                         self.VK_NUMPAD0 + num):
                    registered.append(hotkey_id)
                else:
                    failed.append(num)
            self.statuses.put(tuple(failed))
            self._ready.set()
            while not self._stop.is_set():
                result = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if result <= 0:
                    break
                if msg.message == self.WM_HOTKEY:
                    num = int(msg.wParam) - self.HOTKEY_BASE
                    if 0 <= num <= 4:
                        self.events.put(num)
        finally:
            for hotkey_id in registered:
                user32.UnregisterHotKey(None, hotkey_id)
            self._thread_id = 0
            self._ready.set()
