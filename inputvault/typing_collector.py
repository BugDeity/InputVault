"""打字采集：后台线程轮询焦点所在的输入框，主线程把快照整理成记录。"""
from __future__ import annotations

import logging
import os
import threading
from collections.abc import Callable

from PySide6.QtCore import QObject, Signal

# uia 会导入 comtypes，而且必须在主线程导入：comtypes 第一次导入时会把当前线程初始化成单线程套间，
# 如果第一次导入发生在采集线程里，它就没法再切成多线程套间
from . import privacy, uia, win32
from .apps import AppRegistry
from .classify import classify
from .collector import friendly_app_name
from .config import Config
from .storage import Storage
from .typing_tracker import FieldContext, TypingTracker
from .uia import comtypes

log = logging.getLogger(__name__)

# 浏览器地址栏：记下来的会是访问过的网址，不是打的字
EXCLUDED_CLASSES = {"OmniboxViewViews"}
POLL_SECONDS = 0.25
SLOW_POLL_SECONDS = 1.0  # 内容很长（大文档）时降低读取频率
LONG_TEXT = 20_000
MAX_ERRORS = 4  # 连续读取出错这么多次（约 1 秒）才结束当前输入会话

Snapshot = tuple[FieldContext | None, str | None]
_NO_FIELD: Snapshot = (None, None)


class _Watcher(threading.Thread):
    def __init__(self, emit: Callable[[Snapshot], None], is_excluded: Callable[[str], bool],
                 apps: AppRegistry | None = None):
        super().__init__(name="InputVault-UIA", daemon=True)
        self._emit = emit
        self._is_excluded = is_excluded
        self._apps = apps
        self._stop_event = threading.Event()

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
        try:
            auto = uia.Automation()
            log.info("打字采集已启动")
            last = _NO_FIELD
            interval = POLL_SECONDS
            errors = 0
            while not self._stop_event.wait(interval):
                try:
                    snapshot = self._poll(auto)
                    errors = 0
                except Exception:  # noqa: BLE001  目标程序各式各样，偶发错误跳过这一轮，不打断会话
                    log.debug("读取输入框失败", exc_info=True)
                    errors += 1
                    # 连续出错一段时间（比如切到了一个一直读不了的程序）才当作离开了输入框
                    if errors < MAX_ERRORS or last == _NO_FIELD:
                        continue
                    snapshot = _NO_FIELD
                if snapshot != last:
                    self._emit(snapshot)
                    last = snapshot
                text = snapshot[1]
                interval = SLOW_POLL_SECONDS if text and len(text) > LONG_TEXT else POLL_SECONDS
        except Exception:
            log.exception("打字采集线程异常退出")
        finally:
            comtypes.CoUninitialize()

    def _poll(self, auto) -> Snapshot:
        hwnd = win32.foreground_window()
        exe = win32.process_name(hwnd)
        # 先按前台程序过滤，黑名单里的程序连读都不读
        if not exe or self._is_excluded(exe):
            return _NO_FIELD
        info = auto.focused_field()
        if (
            info is None
            or info.is_password
            or info.text is None
            or info.pid == os.getpid()
            or info.class_name in EXCLUDED_CLASSES
        ):
            return _NO_FIELD
        field_path = win32.process_path_of_pid(info.pid)
        field_exe = os.path.basename(field_path) or exe
        if field_exe != exe and self._is_excluded(field_exe):
            return _NO_FIELD
        if self._apps is not None:
            self._apps.remember(field_path)
        ctx = FieldContext(info.key, field_exe, friendly_app_name(field_exe), win32.window_title(hwnd))
        return ctx, info.text


class TypingCollector(QObject):
    captured = Signal(int)  # 新记录（或被合并的记录）的 id
    _snapshot = Signal(object)  # 从采集线程排队送到主线程

    def __init__(self, storage: Storage, config: Config, parent: QObject | None = None,
                 apps: AppRegistry | None = None):
        super().__init__(parent)
        self.storage = storage
        self.config = config
        self.apps = apps
        self.tracker = TypingTracker(self._store, config.typing_min_chars)
        self._snapshot.connect(self._on_snapshot)
        self._watcher: _Watcher | None = None
        self._excluded: frozenset[str] = frozenset()
        self.reload_config()

    @property
    def running(self) -> bool:
        return self._watcher is not None

    def reload_config(self) -> None:
        """配置改了之后调用。黑名单整体替换成新的集合，采集线程读到的总是完整的一份。"""
        apps = self.config.blacklist_apps + self.config.typing_blacklist_apps
        self._excluded = frozenset(a.lower() for a in apps)
        self.tracker.min_chars = self.config.typing_min_chars

    def start(self) -> None:
        if self._watcher is None:
            self._watcher = _Watcher(self._snapshot.emit, lambda exe: exe.lower() in self._excluded, self.apps)
            self._watcher.start()

    def stop(self) -> None:
        if self._watcher is not None:
            self._watcher.stop()
            self._watcher.join(timeout=3)
            self._watcher = None
        self.tracker.end()

    def _on_snapshot(self, snapshot: Snapshot) -> None:
        if self._watcher is None:  # 停止后还在排队的快照不要了
            return
        self.tracker.observe(*snapshot)

    def _store(self, content: str, ctx: FieldContext) -> None:
        reason = privacy.skip_reason(content, ctx.exe, self.config.blacklist_apps, self.config.skip_sensitive)
        if reason:
            log.info("跳过一段输入：%s", reason)
            return
        content = content[: self.config.max_chars]
        clip_id = self.storage.add(content, classify(content), ctx.app, ctx.exe, ctx.title, source="type")
        self.captured.emit(clip_id)
