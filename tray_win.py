# -*- coding: utf-8 -*-
"""Windows 托盘图标（纯 ctypes Shell_NotifyIcon，零第三方依赖）。

实现要点：
  * Shell_NotifyIcon 必须在拥有消息泵的窗口上注册。这里在后台线程里
    创建一个 HWND_MESSAGE 隐藏窗口，用自己的 GetMessage 循环泵消息，
    完全不碰 Tk 的窗口过程（避免子类化 Tk WndProc 的脆弱做法）。
  * 用户点击通过一个线程安全的 queue 送回主线程，由调用方（Tk 的
    root.after 轮询）在主线程执行，绝不在托盘线程碰 Tk。
  * remove() 先 Shell_NotifyIcon(NIM_DELETE) 删图标，再 PostMessage(WM_QUIT)
    让消息循环退出，线程随后自行 DestroyWindow 并结束，退出时零残留。
"""

from __future__ import annotations

import ctypes
import os
import queue
import sys
import threading
from ctypes import wintypes

# ---------- 常量 ----------
WM_USER = 0x0400
WM_TRAY = WM_USER + 20
WM_COMMAND = 0x0111
WM_DESTROY = 0x0002
WM_QUIT = 0x0012
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205

NIM_ADD = 0x0000
NIM_MODIFY = 0x0001
NIM_DELETE = 0x0002
NIF_MESSAGE = 0x0001
NIF_ICON = 0x0002
NIF_TIP = 0x0004
NIF_INFO = 0x0010

TPM_RIGHTBUTTON = 0x0002
TPM_RETURNCMD = 0x0100
TPM_NONOTIFY = 0x0080
MF_STRING = 0x0000
MF_SEPARATOR = 0x0800

IDM_SHOW = 1001
IDM_REFRESH = 1002
IDM_EXIT = 1003

# 事件名：由主线程轮询消费
EV_SHOW = "show"
EV_REFRESH = "refresh"
EV_EXIT = "exit"

_MENU_EVENTS = {IDM_SHOW: EV_SHOW, IDM_REFRESH: EV_REFRESH, IDM_EXIT: EV_EXIT}


# ---------- ctypes 类型 ----------
if ctypes.sizeof(ctypes.c_void_p) == 8:
    _WPARAM = ctypes.c_ulonglong
    _LPARAM = ctypes.c_longlong
    _LRESULT = ctypes.c_longlong
else:
    _WPARAM = ctypes.c_ulong
    _LPARAM = ctypes.c_long
    _LRESULT = ctypes.c_long

_WNDPROC = ctypes.WINFUNCTYPE(_LRESULT, wintypes.HWND, wintypes.UINT,
                              _WPARAM, _LPARAM)


class _WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", _WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HCURSOR),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


class _NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT),
        ("hIcon", wintypes.HICON),
        ("szTip", wintypes.WCHAR * 128),
        ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD),
        ("szInfo", wintypes.WCHAR * 256),
        ("uVersionOrTimeout", wintypes.UINT),
        ("szInfoTitle", wintypes.WCHAR * 64),
        ("dwInfoFlags", wintypes.DWORD),
        ("guidItem", ctypes.c_byte * 16),
        ("hBalloonIcon", wintypes.HICON),
    ]


# ---------- 用户库 ----------
_user32 = ctypes.windll.user32
_kernel32 = ctypes.windll.kernel32
_shell32 = ctypes.windll.shell32


def _setup_ctypes():
    _user32.DefWindowProcW.restype = _LRESULT
    _user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT,
                                       _WPARAM, _LPARAM]
    _user32.RegisterClassW.restype = wintypes.ATOM
    _user32.RegisterClassW.argtypes = [ctypes.POINTER(_WNDCLASSW)]
    _user32.CreateWindowExW.restype = wintypes.HWND
    _user32.CreateWindowExW.argtypes = [
        wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
    _user32.GetMessageW.restype = ctypes.c_int
    _user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG),
                                    wintypes.HWND, wintypes.UINT, wintypes.UINT]
    _user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
    _user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
    _user32.DestroyWindow.restype = wintypes.BOOL
    _user32.DestroyWindow.argtypes = [wintypes.HWND]
    _user32.PostQuitMessage.restype = None
    _user32.PostQuitMessage.argtypes = [ctypes.c_int]
    _user32.PostMessageW.restype = wintypes.BOOL
    _user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT,
                                     _WPARAM, _LPARAM]
    _user32.GetCursorPos.restype = wintypes.BOOL
    _user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
    _user32.SetForegroundWindow.restype = wintypes.BOOL
    _user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    _user32.CreatePopupMenu.restype = wintypes.HMENU
    _user32.CreatePopupMenu.argtypes = []
    _user32.AppendMenuW.restype = wintypes.BOOL
    _user32.AppendMenuW.argtypes = [wintypes.HMENU, wintypes.UINT,
                                    ctypes.c_size_t, wintypes.LPCWSTR]
    _user32.TrackPopupMenu.restype = ctypes.c_int
    _user32.TrackPopupMenu.argtypes = [
        wintypes.HMENU, wintypes.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wintypes.HWND, ctypes.c_void_p]
    _user32.DestroyMenu.restype = wintypes.BOOL
    _user32.DestroyMenu.argtypes = [wintypes.HMENU]
    _user32.LoadImageW.restype = wintypes.HANDLE
    _user32.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR,
                                   wintypes.UINT, ctypes.c_int, ctypes.c_int,
                                   wintypes.UINT]
    _user32.LoadIconW.restype = wintypes.HICON
    _user32.LoadIconW.argtypes = [wintypes.HINSTANCE, ctypes.c_void_p]
    _user32.DestroyIcon.restype = wintypes.BOOL
    _user32.DestroyIcon.argtypes = [wintypes.HICON]
    _kernel32.GetModuleHandleW.restype = wintypes.HMODULE
    _kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    _shell32.Shell_NotifyIconW.restype = wintypes.BOOL
    _shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD,
                                           ctypes.POINTER(_NOTIFYICONDATAW)]


_setup_ctypes()

_CLASS_NAME = "BatteryViewerTray"
_INSTANCES = {}
_INSTANCES_LOCK = threading.Lock()


@_WNDPROC
def _wnd_proc(hwnd, msg, wparam, lparam):
    with _INSTANCES_LOCK:
        inst = _INSTANCES.get(hwnd)
    if inst is None:
        return _user32.DefWindowProcW(hwnd, msg, wparam, lparam)
    return inst._on_message(msg, wparam, lparam)


def _load_icon(icon_path: str | None) -> tuple[int, bool]:
    """返回 (hIcon, 是否需要销毁)。优先 exe 自身图标，其次 app.ico，最后系统默认。"""
    path = icon_path
    if not path:
        if getattr(sys, "frozen", False):
            path = sys.executable
        else:
            here = os.path.dirname(os.path.abspath(__file__))
            cand = os.path.join(here, "app.ico")
            if os.path.exists(cand):
                path = cand
    if path:
        # IMAGE_ICON=1 | LR_LOADFROMFILE=0x10 | LR_DEFAULTSIZE=0x40
        h = _user32.LoadImageW(None, path, 1, 0, 0, 0x10 | 0x40)
        if h:
            return int(h), True
    return int(_user32.LoadIconW(None, ctypes.c_void_p(32512))), False


class TrayIcon:
    """后台线程托盘图标。事件通过 queue 送回主线程。"""

    def __init__(self, events: queue.Queue, tooltip: str = "2.4G 电量",
                 icon_path: str | None = None):
        self.events = events
        self.tooltip = tooltip
        self._hico, self._own_icon = _load_icon(icon_path)
        self._hwnd = None
        self._hmenu = None
        self._added = False
        self._thread = None
        self._ready = threading.Event()
        self._lock = threading.Lock()

    # ---- 生命周期 ----
    def start(self):
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="tray-icon")
        self._thread.start()
        self._ready.wait(timeout=5)

    def remove(self):
        with self._lock:
            hwnd = self._hwnd
            added = self._added
            self._added = False
        if added and hwnd:
            self._delete_icon(hwnd)
        if hwnd:
            try:
                _user32.PostMessageW(hwnd, WM_QUIT, 0, 0)
            except Exception:
                pass
        if self._own_icon and self._hico:
            try:
                _user32.DestroyIcon(self._hico)
            except Exception:
                pass
            self._hico = None
        # 不 join（线程是 daemon，且已收到 WM_QUIT 会自行退出）；避免卡主线程。

    # ---- 对外操作（主线程调用）----
    def set_tooltip(self, text: str):
        self.tooltip = text
        with self._lock:
            if not self._added or not self._hwnd:
                return
            nid = self._base_nid(NIF_TIP)
        nid.szTip = text[:127]
        _shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))

    def show_balloon(self, title: str, msg: str):
        with self._lock:
            if not self._added or not self._hwnd:
                return
            # 同时重申图标与提示，避免个别系统上 NIF_INFO 单独修改导致图标丢失
            nid = self._base_nid(NIF_INFO | NIF_ICON | NIF_TIP)
        nid.szTip = self.tooltip[:127]
        nid.szInfoTitle = title[:63]
        nid.szInfo = msg[:255]
        nid.dwInfoFlags = 1  # NIIF_INFO
        _shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))

    # ---- 内部 ----
    def _base_nid(self, flags: int) -> _NOTIFYICONDATAW:
        nid = _NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(_NOTIFYICONDATAW)
        nid.hWnd = self._hwnd
        nid.uID = 1
        nid.uFlags = flags
        nid.uCallbackMessage = WM_TRAY
        nid.hIcon = self._hico
        return nid

    def _run(self):
        try:
            self._hwnd = self._create_window()
            with _INSTANCES_LOCK:
                _INSTANCES[self._hwnd] = self
            self._hmenu = self._create_menu()
            self._add_icon()
            self._ready.set()
            self._pump()
        finally:
            self._ready.set()
            with _INSTANCES_LOCK:
                if self._hwnd is not None:
                    _INSTANCES.pop(self._hwnd, None)
            if self._hmenu:
                try:
                    _user32.DestroyMenu(self._hmenu)
                except Exception:
                    pass
                self._hmenu = None
            if self._hwnd:
                try:
                    _user32.DestroyWindow(self._hwnd)
                except Exception:
                    pass
                self._hwnd = None

    def _create_window(self) -> int:
        wc = _WNDCLASSW()
        wc.lpfnWndProc = _wnd_proc
        wc.hInstance = _kernel32.GetModuleHandleW(None)
        wc.lpszClassName = _CLASS_NAME
        atom = _user32.RegisterClassW(ctypes.byref(wc))
        if not atom and ctypes.get_last_error() != 1410:  # 1410 = 类已存在
            raise OSError("RegisterClassW failed")
        # HWND_MESSAGE = -3；消息窗口没有父窗口也能跑消息泵
        hwnd = _user32.CreateWindowExW(
            0, _CLASS_NAME, _CLASS_NAME, 0, 0, 0, 0, 0,
            ctypes.c_void_p(-3), None, wc.hInstance, None)
        if not hwnd:
            raise OSError("CreateWindowExW failed")
        return int(hwnd)

    def _create_menu(self) -> int:
        hmenu = _user32.CreatePopupMenu()
        if not hmenu:
            raise OSError("CreatePopupMenu failed")
        _user32.AppendMenuW(hmenu, MF_STRING, IDM_SHOW, "显示窗口")
        _user32.AppendMenuW(hmenu, MF_STRING, IDM_REFRESH, "立即刷新")
        _user32.AppendMenuW(hmenu, MF_SEPARATOR, 0, None)
        _user32.AppendMenuW(hmenu, MF_STRING, IDM_EXIT, "退出")
        return int(hmenu)

    def _add_icon(self):
        nid = self._base_nid(NIF_MESSAGE | NIF_ICON | NIF_TIP)
        nid.szTip = self.tooltip[:127]
        if not _shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid)):
            raise OSError("Shell_NotifyIconW(NIM_ADD) failed")
        self._added = True

    def _delete_icon(self, hwnd):
        nid = _NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(_NOTIFYICONDATAW)
        nid.hWnd = hwnd
        nid.uID = 1
        try:
            _shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
        except Exception:
            pass

    def _pump(self):
        msg = wintypes.MSG()
        while True:
            ret = _user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if ret <= 0:  # WM_QUIT(0) 或错误(-1)
                break
            _user32.TranslateMessage(ctypes.byref(msg))
            _user32.DispatchMessageW(ctypes.byref(msg))

    def _on_message(self, msg, wparam, lparam) -> int:
        if msg == WM_TRAY:
            if lparam in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
                self.events.put(EV_SHOW)
                return 0
            if lparam == WM_RBUTTONUP:
                self._show_menu()
                return 0
        elif msg == WM_COMMAND:
            cmd = wparam & 0xFFFF
            if cmd in _MENU_EVENTS:
                self.events.put(_MENU_EVENTS[cmd])
                return 0
        elif msg == WM_DESTROY:
            _user32.PostQuitMessage(0)
            return 0
        return int(_user32.DefWindowProcW(self._hwnd, msg, wparam, lparam))

    def _show_menu(self):
        if not self._hmenu:
            return
        try:
            _user32.SetForegroundWindow(self._hwnd)
            pos = wintypes.POINT()
            _user32.GetCursorPos(ctypes.byref(pos))
            cmd = _user32.TrackPopupMenu(
                self._hmenu, TPM_RIGHTBUTTON | TPM_RETURNCMD | TPM_NONOTIFY,
                pos.x, pos.y, 0, self._hwnd, None)
            if cmd in _MENU_EVENTS:
                self.events.put(_MENU_EVENTS[cmd])
            # 空消息让菜单正确收起
            _user32.PostMessageW(self._hwnd, 0, 0, 0)
        except Exception:
            pass
