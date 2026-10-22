"""UIA 探测工具：看看各个软件的输入框能不能被读取。

只输出控件类型和文字长度，不输出文字内容。

    python tools/uia_probe.py          扫描所有可见窗口里的输入框
    python tools/uia_probe.py --focus  20 秒内实时显示当前焦点所在的控件，去各个软件里点一下输入框试试
"""
from __future__ import annotations

import ctypes
import os
import sys
import time
from collections import defaultdict
from ctypes import wintypes

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from inputvault import uia, win32  # noqa: E402

user32 = ctypes.WinDLL("user32", use_last_error=True)
WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
user32.IsWindowVisible.argtypes = [wintypes.HWND]


def visible_windows() -> list[int]:
    result = []

    def callback(hwnd, _):
        if user32.IsWindowVisible(hwnd) and win32.window_title(hwnd):
            result.append(hwnd)
        return True

    user32.EnumWindows(WNDENUMPROC(callback), 0)
    return result


def describe(info: uia.FieldInfo) -> str:
    parts = [
        info.control_type_name,
        f"框架={info.framework or '-'}",
        f"类名={info.class_name or '-'}",
    ]
    if info.is_password:
        parts.append("密码框")
    parts.append(f"可编辑={'是' if info.editable else '否'}")
    parts.append(f"读取方式={info.read_via or '读不到'}")
    if info.text is not None:
        parts.append(f"{len(info.text)} 字")
    return "  ".join(parts)


def scan() -> None:
    auto = uia.Automation()
    by_exe: dict[str, list[str]] = defaultdict(list)
    for hwnd in visible_windows():
        exe = win32.process_name(hwnd) or "?"
        started = time.perf_counter()
        try:
            fields = auto.editable_descendants(hwnd, limit=8)
        except Exception as e:  # noqa: BLE001  探测工具，什么错误都只打印
            by_exe[exe].append(f"出错：{type(e).__name__}")
            continue
        cost = (time.perf_counter() - started) * 1000
        if not fields:
            by_exe[exe].append(f"没找到输入框（{cost:.0f} ms）")
        for f in fields:
            by_exe[exe].append(f"{describe(f)}  （{cost:.0f} ms）")
    for exe in sorted(by_exe, key=str.lower):
        print(f"\n== {exe}")
        for line in dict.fromkeys(by_exe[exe]):  # 去重，保持顺序
            print("   ", line)


def watch_focus(seconds: int = 20) -> None:
    auto = uia.Automation()
    print(f"{seconds} 秒内去各个软件的输入框里点一下或打几个字……")
    last = None
    end = time.time() + seconds
    while time.time() < end:
        hwnd = win32.foreground_window()
        exe = win32.process_name(hwnd)
        try:
            info = auto.focused_field()
        except Exception as e:  # noqa: BLE001
            line = f"{exe}: 出错 {type(e).__name__}"
        else:
            line = f"{exe}: {describe(info)}" if info else f"{exe}: 焦点不在输入框上"
        if line != last:
            print(time.strftime("%H:%M:%S"), line)
            last = line
        time.sleep(0.3)


if __name__ == "__main__":
    watch_focus() if "--focus" in sys.argv else scan()
