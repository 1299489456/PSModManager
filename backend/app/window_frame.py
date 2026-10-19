"""Frameless window controls for the custom title bar (Windows only).

The page asks for a fixed set of operations through the closed command
table; nothing here accepts coordinates or handles from the page. Moving and
resizing hand the gesture to Windows' own move/size loop on the UI thread.
"""
import ctypes
from ctypes import wintypes
import threading

OPERATIONS = ('state', 'minimize', 'toggle_maximize', 'close', 'drag',
              'resize_n', 'resize_s', 'resize_e', 'resize_w',
              'resize_ne', 'resize_nw', 'resize_se', 'resize_sw')
_HIT = {'drag': 2, 'resize_w': 10, 'resize_e': 11, 'resize_n': 12, 'resize_nw': 13,
        'resize_ne': 14, 'resize_s': 15, 'resize_sw': 16, 'resize_se': 17}
_WM_CLOSE, _WM_NCLBUTTONDOWN, _WM_SYSCOMMAND, _SC_MINIMIZE = 0x0010, 0x00A1, 0x0112, 0xF020
_SWP_NOZORDER, _SWP_NOACTIVATE, _MONITOR_DEFAULTTONEAREST = 0x0004, 0x0010, 2


class _MonitorInfo(ctypes.Structure):
    _fields_ = [('cbSize', wintypes.DWORD), ('rcMonitor', wintypes.RECT),
                ('rcWork', wintypes.RECT), ('dwFlags', wintypes.DWORD)]


class WindowFrame:
    def __init__(self, window):
        self._window = window
        self._lock = threading.Lock()
        self._maximized = False
        self._restore = None
        self._user32 = ctypes.windll.user32
        self._user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        self._user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        self._user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                              ctypes.c_int, ctypes.c_int, wintypes.UINT]
        self._user32.MonitorFromWindow.restype = wintypes.HANDLE

    def _hwnd(self):
        native = getattr(self._window, 'native', None)
        if native is None:
            raise RuntimeError('window not ready')
        return wintypes.HWND(native.Handle.ToInt64())

    def _on_ui(self, work):
        from System import Action
        self._window.native.BeginInvoke(Action(work))

    def _rect(self, hwnd):
        rect = wintypes.RECT()
        self._user32.GetWindowRect(hwnd, ctypes.byref(rect))
        return rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top

    def _work_area(self, hwnd):
        info = _MonitorInfo(cbSize=ctypes.sizeof(_MonitorInfo))
        monitor = self._user32.MonitorFromWindow(hwnd, _MONITOR_DEFAULTTONEAREST)
        self._user32.GetMonitorInfoW(monitor, ctypes.byref(info))
        work = info.rcWork
        return work.left, work.top, work.right - work.left, work.bottom - work.top

    def _place(self, hwnd, bounds):
        self._user32.SetWindowPos(hwnd, None, *bounds, _SWP_NOZORDER | _SWP_NOACTIVATE)

    def apply_style(self, rounded=True):
        """Rounded corners on Windows 11 (square while maximized); ignored elsewhere."""
        try:
            preference = ctypes.c_int(2 if rounded else 1)  # DWMWCP_ROUND / DWMWCP_DONOTROUND
            ctypes.windll.dwmapi.DwmSetWindowAttribute(self._hwnd(), 33, ctypes.byref(preference), ctypes.sizeof(preference))
        except (OSError, AttributeError, RuntimeError):
            pass

    def control(self, operation):
        if operation not in OPERATIONS:
            raise ValueError('unsupported window operation')
        hwnd = self._hwnd()
        if operation == 'minimize':
            # Same message as the native minimize button, so WinForms keeps its window state.
            self._user32.PostMessageW(hwnd, _WM_SYSCOMMAND, _SC_MINIMIZE, 0)
        elif operation == 'close':
            # Same path as the native close button, so every close hook still runs.
            self._user32.PostMessageW(hwnd, _WM_CLOSE, 0, 0)
        elif operation == 'toggle_maximize':
            self._toggle(hwnd)
        elif operation in _HIT:
            if operation != 'drag' and self._maximized:
                return {'maximized': True}
            self._on_ui(lambda: self._gesture(operation))
        return {'maximized': self._maximized}

    def _toggle(self, hwnd):
        with self._lock:
            if self._maximized:
                self._maximized = False
                bounds, self._restore = self._restore, None
            else:
                self._restore = self._rect(hwnd)
                self._maximized = True
                bounds = self._work_area(hwnd)
        if bounds:
            self._place(hwnd, bounds)
        self.apply_style(not self._maximized)

    def _gesture(self, operation):
        hwnd = self._hwnd()
        point = wintypes.POINT()
        self._user32.GetCursorPos(ctypes.byref(point))
        with self._lock:
            restore = self._restore if operation == 'drag' and self._maximized else None
            if restore:
                self._maximized, self._restore = False, None
        if restore:
            # Dragging a maximized window restores it under the pointer, like the native title bar.
            left, top, width, _ = self._rect(hwnd)
            ratio = (point.x - left) / max(1, width)
            self._place(hwnd, (int(point.x - restore[2] * ratio), point.y - 18, restore[2], restore[3]))
            self.apply_style(True)
        self._user32.ReleaseCapture()
        lparam = (point.y & 0xFFFF) << 16 | (point.x & 0xFFFF)
        self._user32.SendMessageW(hwnd, _WM_NCLBUTTONDOWN, _HIT[operation], lparam)
