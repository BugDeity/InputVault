"""Windows API 封装（ctypes），不依赖 pywin32。"""
from __future__ import annotations

import ctypes
import os
import re
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.IsWindow.argtypes = [wintypes.HWND]
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.BringWindowToTop.argtypes = [wintypes.HWND]
user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
user32.RegisterClipboardFormatW.argtypes = [wintypes.LPCWSTR]
user32.RegisterClipboardFormatW.restype = wintypes.UINT
user32.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
user32.OpenClipboard.argtypes = [wintypes.HWND]
user32.GetClipboardData.argtypes = [wintypes.UINT]
user32.GetClipboardData.restype = wintypes.HANDLE
user32.GetClipboardOwner.restype = wintypes.HWND
user32.keybd_event.argtypes = [wintypes.BYTE, wintypes.BYTE, wintypes.DWORD, ctypes.c_size_t]
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [
    wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)
]
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalLock.restype = wintypes.LPVOID
kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.CreateMutexW.restype = wintypes.HANDLE
kernel32.GetCurrentThreadId.restype = wintypes.DWORD

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
ERROR_ALREADY_EXISTS = 183
WM_HOTKEY = 0x0312
KEYEVENTF_KEYUP = 0x0002
VK_CONTROL, VK_V = 0x11, 0x56
MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x8, 0x4000


# ---------- 窗口和进程 ----------

def foreground_window() -> int:
    return user32.GetForegroundWindow() or 0


def is_window(hwnd: int) -> bool:
    return bool(hwnd) and bool(user32.IsWindow(hwnd))


def window_title(hwnd: int) -> str:
    if not hwnd:
        return ""
    length = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


def window_pid(hwnd: int) -> int:
    if not hwnd:
        return 0
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def process_name(hwnd: int) -> str:
    """窗口所属进程的 exe 文件名，比如 Weixin.exe。"""
    return process_name_of_pid(window_pid(hwnd))


def process_path(hwnd: int) -> str:
    """窗口所属进程的 exe 完整路径。"""
    return process_path_of_pid(window_pid(hwnd))


def process_name_of_pid(pid: int) -> str:
    return os.path.basename(process_path_of_pid(pid))


def process_path_of_pid(pid: int) -> str:
    if not pid:
        return ""
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(1024)
        buf = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return buf.value
        return ""
    finally:
        kernel32.CloseHandle(handle)


_WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.EnumWindows.argtypes = [_WNDENUMPROC, wintypes.LPARAM]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsIconic.argtypes = [wintypes.HWND]
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
SW_RESTORE = 9


def visible_windows() -> list[int]:
    """所有可见、有标题的顶层窗口。"""
    result: list[int] = []

    def callback(hwnd, _lparam):
        if user32.IsWindowVisible(hwnd) and user32.GetWindowTextLengthW(hwnd) > 0:
            result.append(hwnd)
        return True

    user32.EnumWindows(_WNDENUMPROC(callback), 0)
    return result


def find_app_window(exe: str) -> int:
    """这个程序的一个可见主窗口，没有返回 0。"""
    exe = exe.lower()
    return next((h for h in visible_windows() if process_name(h).lower() == exe), 0)


def show_window(hwnd: int) -> None:
    """把窗口切到前台，最小化了就先还原。"""
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    force_foreground(hwnd)


def force_foreground(hwnd: int) -> bool:
    """把窗口切到前台。直接切失败时，借用当前前台线程的输入状态再试一次。"""
    if not is_window(hwnd):
        return False
    if user32.SetForegroundWindow(hwnd):
        return True
    fg_thread = user32.GetWindowThreadProcessId(foreground_window(), None)
    my_thread = kernel32.GetCurrentThreadId()
    if not fg_thread or fg_thread == my_thread:
        return False
    user32.AttachThreadInput(my_thread, fg_thread, True)
    try:
        user32.BringWindowToTop(hwnd)
        return bool(user32.SetForegroundWindow(hwnd))
    finally:
        user32.AttachThreadInput(my_thread, fg_thread, False)


def send_ctrl_v() -> None:
    user32.keybd_event(VK_CONTROL, 0, 0, 0)
    user32.keybd_event(VK_V, 0, 0, 0)
    user32.keybd_event(VK_V, 0, KEYEVENTF_KEYUP, 0)
    user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)


# ---------- 剪贴板 ----------

def clipboard_owner() -> int:
    """最后一次写剪贴板的窗口；有些程序写剪贴板时不指定窗口，这时返回 0。"""
    return user32.GetClipboardOwner() or 0


_FMT_EXCLUDE = user32.RegisterClipboardFormatW("ExcludeClipboardContentFromMonitorProcessing")
_FMT_CAN_INCLUDE = user32.RegisterClipboardFormatW("CanIncludeInClipboardHistory")


def clipboard_is_private() -> bool:
    """密码管理器等程序会给剪贴板打上“不要记录”的标记，遵守这个约定。"""
    if user32.IsClipboardFormatAvailable(_FMT_EXCLUDE):
        return True
    if not user32.IsClipboardFormatAvailable(_FMT_CAN_INCLUDE):
        return False
    if not user32.OpenClipboard(None):
        return False
    try:
        handle = user32.GetClipboardData(_FMT_CAN_INCLUDE)
        if not handle:
            return False
        ptr = kernel32.GlobalLock(handle)
        if not ptr:
            return False
        try:
            return ctypes.c_uint32.from_address(ptr).value == 0
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


# ---------- 文件编号 ----------
# NTFS 给每个文件一个编号，同一个磁盘里移动、改名都不变。复制时记下来，
# 原路径找不到时用它定位文件的新位置。文件被删除后编号会作废，不会找错文件。

FILE_READ_ATTRIBUTES = 0x80
FILE_SHARE_ALL = 0x7
OPEN_EXISTING = 3
FILE_FLAG_BACKUP_SEMANTICS = 0x02000000  # 打开文件夹必须带这个
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


class BY_HANDLE_FILE_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("dwFileAttributes", wintypes.DWORD),
        ("ftCreationTime", wintypes.FILETIME),
        ("ftLastAccessTime", wintypes.FILETIME),
        ("ftLastWriteTime", wintypes.FILETIME),
        ("dwVolumeSerialNumber", wintypes.DWORD),
        ("nFileSizeHigh", wintypes.DWORD),
        ("nFileSizeLow", wintypes.DWORD),
        ("nNumberOfLinks", wintypes.DWORD),
        ("nFileIndexHigh", wintypes.DWORD),
        ("nFileIndexLow", wintypes.DWORD),
    ]


class _FileIdUnion(ctypes.Union):
    _fields_ = [("FileId", ctypes.c_longlong), ("ExtendedFileId", ctypes.c_ubyte * 16)]


class FILE_ID_DESCRIPTOR(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("Type", ctypes.c_int), ("u", _FileIdUnion)]


kernel32.CreateFileW.argtypes = [
    wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
    wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
]
kernel32.CreateFileW.restype = wintypes.HANDLE
kernel32.GetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.POINTER(BY_HANDLE_FILE_INFORMATION)]
kernel32.OpenFileById.argtypes = [
    wintypes.HANDLE, ctypes.POINTER(FILE_ID_DESCRIPTOR), wintypes.DWORD,
    wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
]
kernel32.OpenFileById.restype = wintypes.HANDLE
kernel32.GetFinalPathNameByHandleW.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
kernel32.GetFinalPathNameByHandleW.restype = wintypes.DWORD


def _valid(handle) -> bool:
    return bool(handle) and handle != INVALID_HANDLE_VALUE


def _open_for_attributes(path: str):
    handle = kernel32.CreateFileW(
        path, FILE_READ_ATTRIBUTES, FILE_SHARE_ALL, None, OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS, None
    )
    return handle if _valid(handle) else None


def _volume_root(path: str) -> str:
    """"D:\\a\\b.txt" -> "D:\\"；网络路径返回 ""（网络磁盘不用文件编号）。"""
    drive = os.path.splitdrive(path)[0]
    return drive + "\\" if len(drive) == 2 and drive[1] == ":" else ""


def file_id(path: str) -> str:
    """返回 "卷序列号:文件编号"，拿不到（网络路径、非 NTFS、没权限）时返回 ""。"""
    if not _volume_root(path):
        return ""
    handle = _open_for_attributes(path)
    if handle is None:
        return ""
    try:
        info = BY_HANDLE_FILE_INFORMATION()
        if not kernel32.GetFileInformationByHandle(handle, ctypes.byref(info)):
            return ""
        index = (info.nFileIndexHigh << 32) | info.nFileIndexLow
        return f"{info.dwVolumeSerialNumber:08x}:{index:016x}"
    finally:
        kernel32.CloseHandle(handle)


def _final_path(handle) -> str:
    buf = ctypes.create_unicode_buffer(1024)
    length = kernel32.GetFinalPathNameByHandleW(handle, buf, len(buf), 0)
    if not length or length >= len(buf):
        return ""
    path = buf.value
    if path.startswith("\\\\?\\UNC\\"):
        return "\\\\" + path[8:]
    return path[4:] if path.startswith("\\\\?\\") else path


def find_by_file_id(fid: str, original_path: str) -> str:
    """用复制时记下的文件编号找文件现在的路径，找不到返回 ""。"""
    root = _volume_root(original_path)
    if not fid or not root:
        return ""
    serial_hex, _, index_hex = fid.partition(":")
    volume = _open_for_attributes(root)
    if volume is None:
        return ""
    try:
        # 同一个盘符可能已经换成了别的磁盘（比如 U 盘），卷序列号对不上就不找
        info = BY_HANDLE_FILE_INFORMATION()
        if not kernel32.GetFileInformationByHandle(volume, ctypes.byref(info)):
            return ""
        if f"{info.dwVolumeSerialNumber:08x}" != serial_hex:
            return ""
        index = int(index_hex, 16)
        if index >= 1 << 63:
            index -= 1 << 64
        desc = FILE_ID_DESCRIPTOR(ctypes.sizeof(FILE_ID_DESCRIPTOR), 0, _FileIdUnion(FileId=index))
        handle = kernel32.OpenFileById(
            volume, ctypes.byref(desc), FILE_READ_ATTRIBUTES, FILE_SHARE_ALL, None, FILE_FLAG_BACKUP_SEMANTICS
        )
        if not _valid(handle):
            return ""
        try:
            return _final_path(handle)
        finally:
            kernel32.CloseHandle(handle)
    finally:
        kernel32.CloseHandle(volume)


def reveal_in_explorer(path: str) -> None:
    """打开资源管理器并选中这个文件。"""
    import subprocess

    subprocess.Popen(f'explorer /select,"{path}"')


# ---------- 全局热键 ----------

_MODIFIERS = {"ctrl": MOD_CONTROL, "control": MOD_CONTROL, "alt": MOD_ALT, "shift": MOD_SHIFT, "win": MOD_WIN}
_NAMED_KEYS = {"space": 0x20, "tab": 0x09, "insert": 0x2D, "`": 0xC0}


def parse_hotkey(spec: str) -> tuple[int, int]:
    """把 "ctrl+shift+v" 解析成 (修饰键, 虚拟键码)。"""
    mods, vk = 0, None
    for part in spec.lower().replace(" ", "").split("+"):
        if part in _MODIFIERS:
            mods |= _MODIFIERS[part]
        elif len(part) == 1 and part.isascii() and part.isalnum():
            vk = ord(part.upper())
        elif re.fullmatch(r"f([1-9]|1\d|2[0-4])", part):
            vk = 0x6F + int(part[1:])
        elif part in _NAMED_KEYS:
            vk = _NAMED_KEYS[part]
        else:
            raise ValueError(f"无法识别的按键：{part}")
    if vk is None or not mods:
        raise ValueError(f"热键至少要有一个修饰键和一个普通键：{spec}")
    return mods, vk


def hotkey_label(spec: str) -> str:
    return "+".join(p.capitalize() if len(p) > 1 else p.upper() for p in spec.replace(" ", "").split("+"))


def register_hotkey(hotkey_id: int, mods: int, vk: int) -> bool:
    return bool(user32.RegisterHotKey(None, hotkey_id, mods | MOD_NOREPEAT, vk))


def unregister_hotkey(hotkey_id: int) -> None:
    user32.UnregisterHotKey(None, hotkey_id)


# ---------- 单实例 ----------

def acquire_single_instance(name: str) -> int | None:
    """已经有一个实例在运行时返回 None。返回的句柄要一直持有到退出。"""
    handle = kernel32.CreateMutexW(None, False, name)
    if not handle or ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        return None
    return handle
