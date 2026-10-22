"""剪贴板采集：监听复制事件，按文件 / 文字 / 图片分别处理后写入存储。

文件和图片的耗时部分（备份文件、编码图片、生成缩略图）放在后台线程，结果通过信号排队回到主线程
再写数据库（sqlite 连接只在主线程用）。
"""
from __future__ import annotations

import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import QCoreApplication, QObject, QTimer, Signal
from PySide6.QtGui import QGuiApplication, QImage, QPixmap

from . import filerefs, media, privacy, win32
from .apps import AppRegistry
from .blobs import BlobStore
from .classify import classify
from .config import Config
from .restore import MARKER
from .storage import Storage

log = logging.getLogger(__name__)

MB = 1024 * 1024
_DROP_EFFECT = 'application/x-qt-windows-mime;value="Preferred DropEffect"'
_DROPEFFECT_MOVE = 2

APP_NAMES = {
    "weixin.exe": "微信",
    "wechat.exe": "微信",
    "wechatdevtools.exe": "微信开发者工具",
    "qq.exe": "QQ",
    "dingtalk.exe": "钉钉",
    "feishu.exe": "飞书",
    "lark.exe": "飞书",
    "wxwork.exe": "企业微信",
    "chrome.exe": "Chrome",
    "msedge.exe": "Edge",
    "firefox.exe": "Firefox",
    "code.exe": "VS Code",
    "cursor.exe": "Cursor",
    "idea64.exe": "IntelliJ IDEA",
    "pycharm64.exe": "PyCharm",
    "winword.exe": "Word",
    "excel.exe": "Excel",
    "powerpnt.exe": "PowerPoint",
    "wps.exe": "WPS",
    "et.exe": "WPS 表格",
    "notepad.exe": "记事本",
    "explorer.exe": "资源管理器",
    "windowsterminal.exe": "终端",
    "powershell.exe": "PowerShell",
    "pwsh.exe": "PowerShell",
    "cmd.exe": "命令提示符",
    "webstorm64.exe": "WebStorm",
    "youdaodict.exe": "有道词典",
    "apifox.exe": "Apifox",
    "snippingtool.exe": "截图工具",
    "screenclippinghost.exe": "截图工具",
    "claude.exe": "Claude",
}


def friendly_app_name(exe: str) -> str:
    if not exe:
        return ""
    return APP_NAMES.get(exe.lower(), os.path.splitext(exe)[0])


def local_file_paths(mime) -> list[str]:
    """剪贴板里是本地文件（资源管理器里复制的）时返回路径列表，否则返回空列表。"""
    if not mime.hasUrls():
        return []
    urls = mime.urls()
    if not urls or not all(u.isLocalFile() for u in urls):
        return []
    return [os.path.normpath(u.toLocalFile()) for u in urls]


def _is_cut(mime) -> bool:
    data = bytes(mime.data(_DROP_EFFECT))
    return len(data) >= 4 and bool(int.from_bytes(data[:4], "little") & _DROPEFFECT_MOVE)


class ClipboardCollector(QObject):
    captured = Signal(int)  # 新记录（或被合并的记录）的 id
    attachmentsChanged = Signal()  # 副本库里写进了新文件，用来触发空间检查
    _image_ready = Signal(object)  # 后台编码好的图片
    _backup_done = Signal(object)  # 后台备份完的文件

    def __init__(self, storage: Storage, blobs: BlobStore, config: Config, parent: QObject | None = None,
                 apps: AppRegistry | None = None):
        super().__init__(parent)
        self.storage = storage
        self.blobs = blobs
        self.config = config
        self.apps = apps
        self.paused = False
        self._source = ("", "")
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="InputVault-files")
        self._image_ready.connect(self._on_image_ready)
        self._backup_done.connect(self._on_backup_done)
        # 有些程序复制一次会连续写好几次剪贴板，用防抖合并成一次
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(150)
        self._timer.timeout.connect(self._capture)
        self._clipboard = QGuiApplication.clipboard()
        self._clipboard.dataChanged.connect(self._on_changed)

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)

    def wait_for_background(self, timeout: float = 30) -> None:
        """等后台任务做完、结果写进数据库（测试用）。"""
        self._pool.submit(lambda: None).result(timeout)
        QCoreApplication.processEvents()

    def _on_changed(self) -> None:
        # 优先认剪贴板的写入者；没有写入者时（少数程序会这样）才退回到前台窗口
        foreground = win32.foreground_window()
        foreground_path = win32.process_path(foreground)
        source_path = win32.process_path(win32.clipboard_owner()) or foreground_path
        exe = os.path.basename(source_path)
        title = win32.window_title(foreground) if source_path == foreground_path else ""
        self._source = (exe, title)
        if self.apps is not None:
            self.apps.remember(source_path)
        self._timer.start()

    def _capture(self) -> None:
        if self.paused or win32.clipboard_is_private():
            return
        mime = self._clipboard.mimeData()
        if mime is not None:
            exe, title = self._source
            self.process(mime, exe, title)

    def process(self, mime, exe: str, title: str) -> None:
        """处理一次复制。顺序：文件 → 文字 → 图片。

        Office 复制表格时会同时放文字和一张图片，这时应该记文字，所以文字排在图片前面；
        资源管理器复制文件时 Qt 也会给出 file:/// 形式的文字，所以文件排在最前面。
        """
        if mime.hasFormat(MARKER):  # 我们自己还原到剪贴板的内容
            return
        if privacy.is_blacklisted(exe, self.config.blacklist_apps):
            log.info("跳过一条记录：黑名单应用 %s", exe)
            return
        paths = local_file_paths(mime)
        if paths:
            if self.config.capture_files:
                self._capture_files(paths, _is_cut(mime), exe, title)
            return
        text = mime.text() if mime.hasText() else ""
        if text.strip():
            self._capture_text(text, exe, title)
        elif mime.hasImage() and self.config.capture_images:
            self._capture_image(mime.imageData(), exe, title)

    # ---------- 文字 ----------

    def _capture_text(self, text: str, exe: str, title: str) -> None:
        reason = privacy.skip_reason(text, exe, self.config.blacklist_apps, self.config.skip_sensitive)
        if reason:
            log.info("跳过一条记录：%s", reason)
            return
        text = text[: self.config.max_chars]
        clip_id = self.storage.add(text, classify(text), friendly_app_name(exe), exe, title)
        self.captured.emit(clip_id)

    # ---------- 文件 ----------

    def _capture_files(self, paths: list[str], cut: bool, exe: str, title: str) -> None:
        entries = filerefs.capture(paths)
        if not entries:
            return
        key = filerefs.dedupe_key(paths)
        previous = self.storage.get_by_key(key)
        if previous is not None:
            filerefs.carry_over(entries, previous.data.get("files", []))
        payload = {"files": [e.to_dict() for e in entries], "total": len(paths), "cut": cut}
        clip_id = self.storage.add(
            filerefs.search_text(entries), "file", friendly_app_name(exe), exe, title,
            payload=json.dumps(payload, ensure_ascii=False), dedupe_key=key,
        )
        self.captured.emit(clip_id)

        max_bytes = self.config.backup_max_mb * MB
        jobs = [
            e for e in entries
            if (not e.backup and filerefs.should_backup(e, max_bytes)) or (not e.thumb and filerefs.wants_thumb(e))
        ][: filerefs.BACKUP_COUNT_LIMIT]
        if jobs:
            self._pool.submit(self._backup_job, clip_id, jobs, max_bytes)

    def _backup_job(self, clip_id: int, jobs: list[filerefs.FileEntry], max_bytes: int) -> None:
        """后台线程：备份文件、生成缩略图。只碰文件，不碰数据库。"""
        try:
            results = []
            for e in jobs:
                try:
                    backup = e.backup
                    if not backup and filerefs.should_backup(e, max_bytes):
                        backup = self.blobs.put_file(e.path)
                        if not filerefs.unchanged(e, e.path):
                            backup = ""  # 复制过程中文件被改了，这份副本对不上，不要
                    thumb = e.thumb
                    if not thumb and filerefs.wants_thumb(e):
                        data = media.thumbnail_from_file(e.path)
                        thumb = self.blobs.put_bytes(data, ".thumb.png") if data else ""
                except OSError as err:
                    log.info("备份 %s 失败：%s", e.name, err)
                    continue
                results.append({"path": e.path, "size": e.size, "mtime": e.mtime, "backup": backup, "thumb": thumb})
            self._backup_done.emit((clip_id, results))
        except Exception:
            log.exception("备份文件时出错")

    def _on_backup_done(self, message) -> None:
        clip_id, results = message
        clip = self.storage.get(clip_id)
        if clip is None or not results:
            return
        data = clip.data
        done = {(r["path"], r["size"], r["mtime"]): r for r in results}
        for f in data.get("files", []):
            r = done.get((f["path"], f["size"], f["mtime"]))
            if r:
                f["backup"], f["thumb"] = r["backup"], r["thumb"]
        self.storage.update_payload(clip_id, json.dumps(data, ensure_ascii=False))
        self.attachmentsChanged.emit()

    # ---------- 图片 ----------

    def _capture_image(self, image, exe: str, title: str) -> None:
        if isinstance(image, QPixmap):
            image = image.toImage()
        if not isinstance(image, QImage) or image.isNull():
            return
        self._pool.submit(self._image_job, QImage(image), exe, title, self.config.backup_max_mb * MB)

    def _image_job(self, image: QImage, exe: str, title: str, max_bytes: int) -> None:
        """后台线程：编码成 PNG、存进副本库、生成缩略图。"""
        try:
            data = media.encode_png(image)
            if len(data) > max_bytes:
                log.info("图片太大（%.1f MB），不保存", len(data) / MB)
                return
            blob = self.blobs.put_bytes(data, ".png")
            thumb = self.blobs.put_bytes(media.thumbnail_png(image), ".thumb.png")
            self._image_ready.emit({
                "blob": blob, "thumb": thumb, "w": image.width(), "h": image.height(),
                "bytes": len(data), "exe": exe, "title": title,
            })
        except Exception:
            log.exception("保存图片时出错")

    def _on_image_ready(self, info: dict) -> None:
        exe, title = info.pop("exe"), info.pop("title")
        clip_id = self.storage.add(
            f"图片 {info['w']}×{info['h']}", "image", friendly_app_name(exe), exe, title,
            payload=json.dumps(info), dedupe_key="image:" + info["blob"],
        )
        self.captured.emit(clip_id)
        self.attachmentsChanged.emit()
