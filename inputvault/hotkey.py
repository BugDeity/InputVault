"""全局热键：RegisterHotKey + Qt 原生事件过滤器。"""
from __future__ import annotations

from ctypes import wintypes

from PySide6.QtCore import QAbstractNativeEventFilter, QCoreApplication, QObject, QTimer, Signal

from . import win32

_HOTKEY_ID = 0xB001


class _Filter(QAbstractNativeEventFilter):
    def __init__(self, on_hotkey):
        super().__init__()
        self._on_hotkey = on_hotkey

    def nativeEventFilter(self, event_type, message):
        if bytes(event_type) == b"windows_generic_MSG":
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == win32.WM_HOTKEY and msg.wParam == _HOTKEY_ID:
                self._on_hotkey()
                return True, 0
        return False, 0


class GlobalHotkey(QObject):
    activated = Signal(object)  # 按下热键那一刻的前台窗口句柄

    def __init__(self, spec: str, parent: QObject | None = None):
        super().__init__(parent)
        mods, vk = win32.parse_hotkey(spec)
        self._filter = _Filter(self._on_hotkey)
        QCoreApplication.instance().installNativeEventFilter(self._filter)
        if not win32.register_hotkey(_HOTKEY_ID, mods, vk):
            QCoreApplication.instance().removeNativeEventFilter(self._filter)
            raise OSError("热键已被其他程序占用")

    def _on_hotkey(self) -> None:
        hwnd = win32.foreground_window()
        # 不在原生事件回调里直接弹窗，交给事件循环下一轮处理
        QTimer.singleShot(0, lambda: self.activated.emit(hwnd))

    def unregister(self) -> None:
        win32.unregister_hotkey(_HOTKEY_ID)
        QCoreApplication.instance().removeNativeEventFilter(self._filter)
