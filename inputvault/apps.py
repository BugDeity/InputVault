"""记住每个程序 exe 的完整路径：界面上显示程序图标、“打开来源应用”时用。

记录里只存了 exe 文件名，采集时顺手把完整路径记下来，存在 apps.json。
打字采集线程也会调用 remember，所以加了锁。
"""
from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path

from . import win32

log = logging.getLogger(__name__)


class AppRegistry:
    def __init__(self, path: Path):
        self._path = Path(path)
        self._lock = threading.Lock()
        self._paths: dict[str, str] = {}
        self._dirty = False
        try:
            data = json.loads(self._path.read_text("utf-8"))
            self._paths = {k.lower(): v for k, v in data.items() if isinstance(v, str)}
        except (OSError, ValueError):
            pass

    def remember(self, exe_path: str) -> None:
        if not exe_path:
            return
        key = os.path.basename(exe_path).lower()
        with self._lock:
            if self._paths.get(key) != exe_path:
                self._paths[key] = exe_path
                self._dirty = True

    def path_for(self, exe: str) -> str:
        with self._lock:
            return self._paths.get(exe.lower(), "")

    def seed_from_windows(self) -> None:
        """启动时把正在运行的程序记一遍，左侧栏马上就有图标可用。"""
        for hwnd in win32.visible_windows():
            self.remember(win32.process_path(hwnd))

    def save(self) -> None:
        with self._lock:
            if not self._dirty:
                return
            data = dict(self._paths)
            self._dirty = False
        try:
            self._path.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
        except OSError:
            log.exception("保存 apps.json 失败")
