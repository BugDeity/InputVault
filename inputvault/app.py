"""程序入口：托盘 + 热键 + 剪贴板 / 打字采集 + 搜索面板。"""
from __future__ import annotations

import dataclasses
import hashlib
import logging
import os
import shutil
import sys
import threading
from pathlib import Path

from PySide6.QtCore import QObject, QTimer
from PySide6.QtGui import QFont, QGuiApplication
from PySide6.QtWidgets import QApplication, QFileDialog, QMenu, QMessageBox, QSystemTrayIcon

from . import housekeeping, win32
from .apps import AppRegistry
from .blobs import BlobStore
from .collector import MB, ClipboardCollector
from .config import DATA_DIR, Config, load_config, save_config
from .filerefs import Resolution, Status
from .hotkey import GlobalHotkey
from .restore import Restorer, image_filename
from .storage import Clip, Storage
from .textutil import human_size
from .typing_collector import TypingCollector
from .ui.icons import AppIcons
from .ui.panel import Panel
from .ui.settings import SettingsDialog
from .ui.theme import app_icon, current_theme

log = logging.getLogger("inputvault")


class Controller(QObject):
    def __init__(self, app: QApplication, storage: Storage, blobs: BlobStore, apps: AppRegistry, config: Config):
        super().__init__()
        self.app = app
        self.storage = storage
        self.blobs = blobs
        self.apps = apps
        self.config = config
        self.paused = False
        self.restorer = Restorer(blobs)

        self.panel = Panel(storage, self.restorer, AppIcons(apps), current_theme(app), config.panel_limit)
        self.panel.pasteRequested.connect(self.deliver)
        self.panel.pasteAsFileRequested.connect(lambda clip: self.deliver(clip, True, as_file=True))
        self.panel.openLocationRequested.connect(self.open_location)
        self.panel.openSourceRequested.connect(self.open_source_app)
        self.panel.saveImageRequested.connect(self.save_image)
        self.panel.blockAppRequested.connect(self.block_app)
        self.collector = ClipboardCollector(storage, blobs, config, self, apps=apps)
        self.collector.captured.connect(lambda _id: self._update_tooltip())
        self.typing = TypingCollector(storage, config, self, apps=apps)
        self.typing.captured.connect(lambda _id: self._update_tooltip())
        self._apps_timer = QTimer(self)
        self._apps_timer.setInterval(60_000)
        self._apps_timer.timeout.connect(apps.save)
        self._apps_timer.start()

        # 新写进副本库后过几秒检查一次空间，连续复制时只检查一次
        self._quota_timer = QTimer(self)
        self._quota_timer.setSingleShot(True)
        self._quota_timer.setInterval(3000)
        self._quota_timer.timeout.connect(self.enforce_quota)
        self.collector.attachmentsChanged.connect(self._quota_timer.start)
        self._quota_timer.start()  # 启动后也检查一次（顺便清理没人引用的文件）

        self._build_tray()
        self.hotkey: GlobalHotkey | None = None
        error = self._register_hotkey(config.hotkey)
        self._sync_typing()
        if error:
            self.notify(f"热键 {win32.hotkey_label(config.hotkey)} 不可用（{error}），"
                        "可以在设置里换一个。现在可以先点托盘图标打开。", warning=True)
        else:
            what = "复制和输入" if config.capture_typing else "复制"
            self.notify(f"已开始记录{what}的内容，按 {win32.hotkey_label(config.hotkey)} 搜索。")

    # ---------- 托盘 ----------

    def _build_tray(self) -> None:
        self.tray = QSystemTrayIcon(app_icon(), self)
        self.menu = QMenu()
        self.open_action = self.menu.addAction("", lambda: self.panel.open(0, quick=False))
        self.pause_action = self.menu.addAction("暂停记录")
        self.pause_action.setCheckable(True)
        self.pause_action.toggled.connect(self._set_paused)
        self.menu.addAction("设置…", self.open_settings)
        self.menu.addAction("打开数据目录", lambda: os.startfile(DATA_DIR))
        self.menu.addSeparator()
        self.menu.addAction("退出", self.quit)
        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(self._on_tray_activated)
        self._update_open_action()
        self._update_tooltip()
        self.tray.show()

    def _update_open_action(self) -> None:
        self.open_action.setText(f"打开 InputVault\t{win32.hotkey_label(self.config.hotkey)}")

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.panel.open(0, quick=False)

    def _update_tooltip(self) -> None:
        if self.paused:
            state = "已暂停"
        else:
            state = "记录复制和打字" if self.typing.running else "记录复制"
        self.tray.setToolTip(f"InputVault · {state} · 共 {self.storage.count()} 条")

    def notify(self, message: str, warning: bool = False) -> None:
        icon = QSystemTrayIcon.MessageIcon.Warning if warning else QSystemTrayIcon.MessageIcon.Information
        self.tray.showMessage("InputVault", message, icon, 4000)

    # ---------- 采集开关 ----------

    def _set_paused(self, paused: bool) -> None:
        self.paused = paused
        self.collector.paused = paused
        self.tray.setIcon(app_icon(paused))
        self._sync_typing()

    def _sync_typing(self) -> None:
        if self.config.capture_typing and not self.paused:
            self.typing.start()
        else:
            self.typing.stop()
        self._update_tooltip()

    # ---------- 热键 ----------

    def _register_hotkey(self, spec: str) -> str | None:
        """注册热键，成功返回 None，失败返回原因。"""
        if self.hotkey is not None:
            self.hotkey.unregister()
            self.hotkey = None
        try:
            self.hotkey = GlobalHotkey(spec, self)
        except (ValueError, OSError) as e:
            log.warning("热键 %s 注册失败：%s", spec, e)
            return str(e)
        self.hotkey.activated.connect(self.toggle_panel)
        return None

    def toggle_panel(self, foreground_hwnd: int) -> None:
        if self.panel.isVisible():
            self.panel.hide()
        else:
            self.panel.open(foreground_hwnd)

    # ---------- 还原 ----------

    def deliver(self, clip: Clip, paste: bool, as_file: bool = False) -> None:
        """把记录还原到剪贴板。

        paste 为真（引用到输入框）：收起窗口、切回原窗口并模拟 Ctrl+V；
        只复制：窗口留着，底部提示“已复制”，方便接着复制下一条。
        """
        prefer_backup = False
        if clip.kind == "file":
            modified = [r for r in self.restorer.resolutions(clip) if r.status is Status.MODIFIED and r.has_backup]
            if modified:
                choice = self._ask_version(modified)
                if choice is None:
                    return
                prefer_backup = choice
        try:
            mime, missing = self.restorer.mime_for(clip, as_file=as_file, prefer_backup_when_modified=prefer_backup)
        except OSError as e:
            log.exception("还原失败")
            self._tell(f"还原失败：{e}", warning=True)
            return
        if mime is None:
            self._tell("还原不了：" + _names(missing) + "。原文件找不到，也没有副本。", warning=True)
            return

        QGuiApplication.clipboard().setMimeData(mime)
        self.storage.touch(clip.id)
        if paste:
            target = self.panel.target_hwnd
            self.panel.hide()
            if missing:
                self.notify(f"有 {len(missing)} 个找不到，其余的已还原：{_names(missing)}", warning=True)
            if win32.is_window(target):
                win32.force_foreground(target)
                QTimer.singleShot(60, win32.send_ctrl_v)
            return
        # 只更新这一条的使用次数，不重新排序：连续复制几条时列表不会跳来跳去
        self.panel.update_clip(clip.id)
        if missing:
            self._tell(f"已复制，但有 {len(missing)} 个找不到：{_names(missing)}", warning=True)
        else:
            self._tell("已复制到剪贴板")

    def _tell(self, message: str, warning: bool = False) -> None:
        """窗口开着就在窗口里提示，否则用托盘通知。"""
        if self.panel.isVisible():
            self.panel.show_toast(message, warning)
        else:
            self.notify(message, warning)

    def _dialog_parent(self):
        """对话框显示在主窗口上面（主窗口没开时没有父窗口）。"""
        return self.panel if self.panel.isVisible() else None

    def _ask_version(self, modified: list[Resolution]) -> bool | None:
        """文件复制之后被改过：问用哪个版本。返回 True = 复制时的版本，False = 现在的版本，None = 取消。"""
        box = QMessageBox(QMessageBox.Icon.Question, "InputVault",
                          f"「{_names([r.entry.name for r in modified])}」在复制之后被修改过。\n\n要用哪个版本？",
                          parent=self._dialog_parent())
        current = box.addButton("现在的版本", QMessageBox.ButtonRole.AcceptRole)
        old = box.addButton("复制时的版本", QMessageBox.ButtonRole.ActionRole)
        box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        if clicked is current:
            return False
        return True if clicked is old else None

    # 下面几个操作都不主动收起窗口：切到别的程序时，快捷键唤起的窗口会自己收起；从托盘打开的窗口一直留着

    def open_location(self, clip: Clip) -> None:
        resolutions = self.restorer.resolutions(clip)
        for r in resolutions:
            if r.path:
                win32.reveal_in_explorer(r.path)
                return
        if any(r.has_backup for r in resolutions):
            self._tell("原文件已经不在了。可以按回车把副本粘贴到需要的地方。")
        else:
            self._tell("文件已经找不到了，也没有副本。", warning=True)

    def open_source_app(self, clip: Clip) -> None:
        """切到这条记录的来源程序；程序没开着就启动它。"""
        name = clip.source_app or clip.source_exe
        hwnd = win32.find_app_window(clip.source_exe)
        if hwnd:
            win32.show_window(hwnd)
            return
        path = self.apps.path_for(clip.source_exe)
        if path and os.path.exists(path):
            os.startfile(path)
        else:
            self._tell(f"「{name}」没有在运行，也不知道它装在哪里。", warning=True)

    def save_image(self, clip: Clip) -> None:
        source = self.restorer.image_path(clip)
        if source is None:
            self._tell("这张图片已经被清理掉了。", warning=True)
            return
        default = str(Path.home() / "Pictures" / image_filename(clip))
        path, _ = QFileDialog.getSaveFileName(self._dialog_parent(), "图片另存为", default, "PNG 图片 (*.png)")
        if path:
            shutil.copyfile(source, path)
            self._tell(f"已保存：{Path(path).name}")

    # ---------- 其他面板操作 ----------

    def block_app(self, exe: str, name: str) -> None:
        if exe.lower() not in (a.lower() for a in self.config.blacklist_apps):
            self.config.blacklist_apps.append(exe)
        self.typing.reload_config()
        save_config(self.config)

        count = self.storage.count(exe)
        if count:
            box = QMessageBox(QMessageBox.Icon.Question, "InputVault",
                              f"以后不再记录「{name}」里复制和输入的内容。\n\n已有的 {count} 条记录要删除吗？",
                              parent=self._dialog_parent())
            delete = box.addButton("删除", QMessageBox.ButtonRole.DestructiveRole)
            box.addButton("保留", QMessageBox.ButtonRole.RejectRole)
            box.exec()
            if box.clickedButton() is delete:
                self.storage.delete_by_exe(exe)
                self._tell(f"已删除「{name}」的 {count} 条记录，以后也不再记录")
            else:
                self._tell(f"以后不再记录「{name}」，已有记录保留")
        else:
            self._tell(f"以后不再记录「{name}」里复制和输入的内容。")
        if self.panel.isVisible():
            self.panel.refresh_sidebar()
            self.panel.refresh()
        self._update_tooltip()

    # ---------- 空间 ----------

    def enforce_quota(self) -> None:
        try:
            housekeeping.enforce_quota(self.storage, self.blobs, self.config.blob_quota_mb * MB)
        except OSError:
            log.exception("清理副本空间时出错")
        self._update_tooltip()

    def clear_attachments(self) -> int:
        freed = housekeeping.clear_attachments(self.storage, self.blobs)
        self._update_tooltip()
        return freed

    # ---------- 设置 ----------

    def open_settings(self) -> None:
        dialog = SettingsDialog(self.config, self.blobs.total_size, self.clear_attachments, self._dialog_parent())
        if dialog.exec():
            self.apply_config(dialog.values())

    def apply_config(self, new: Config) -> None:
        old_hotkey = self.config.hotkey
        # 各个模块持有的是同一个 Config 对象，原地更新它们就都能看到
        for f in dataclasses.fields(Config):
            setattr(self.config, f.name, getattr(new, f.name))

        if self.config.hotkey != old_hotkey:
            error = self._register_hotkey(self.config.hotkey)
            if error:
                self.notify(f"热键 {win32.hotkey_label(self.config.hotkey)} 不可用（{error}），"
                            f"继续使用 {win32.hotkey_label(old_hotkey)}。", warning=True)
                self.config.hotkey = old_hotkey
                self._register_hotkey(old_hotkey)
            self._update_open_action()

        self.typing.reload_config()
        self._sync_typing()
        if self.config.retention_days > 0:
            self.storage.prune(self.config.retention_days)
        self.enforce_quota()
        save_config(self.config)

    def quit(self) -> None:
        if self.hotkey is not None:
            self.hotkey.unregister()
        self.typing.stop()  # 先把正在进行的输入存下来
        self.collector.shutdown()
        self.apps.save()
        self.tray.hide()
        self.storage.close()
        self.app.quit()


def _names(names: list[str], limit: int = 3) -> str:
    shown = "、".join(names[:limit])
    return shown + (f" 等 {len(names)} 个" if len(names) > limit else "")


def _instance_name() -> str:
    # 指定了别的数据目录（测试用）时可以和正常实例同时运行
    if os.environ.get("INPUTVAULT_DATA_DIR"):
        digest = hashlib.sha1(str(DATA_DIR).lower().encode()).hexdigest()[:12]
        return f"Local\\InputVault.{digest}"
    return "Local\\InputVault.SingleInstance"


def main() -> int:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=DATA_DIR / "inputvault.log",
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        encoding="utf-8",
    )
    logging.getLogger("comtypes").setLevel(logging.WARNING)
    # pythonw 下没有控制台，未捕获的异常（包括后台线程里的）写进日志。
    # 不开 faulthandler：它在 Windows 上会把 UIA 跨进程调用里已经被系统处理掉的异常也记下来，
    # 一小时能写出几百 KB 的无用信息
    sys.excepthook = lambda *exc: log.critical("未处理的异常", exc_info=exc)
    threading.excepthook = lambda args: log.critical(
        "线程 %s 未处理的异常", args.thread.name if args.thread else "?",
        exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
    )

    app = QApplication(sys.argv)
    app.setApplicationName("InputVault")
    app.setQuitOnLastWindowClosed(False)
    app.setFont(QFont("Microsoft YaHei UI", 10))

    instance = win32.acquire_single_instance(_instance_name())
    if instance is None:
        QMessageBox.information(None, "InputVault", "InputVault 已经在运行了，在屏幕右下角的托盘里。")
        return 0

    config = load_config()
    storage = Storage(DATA_DIR / "inputvault.db")
    if config.retention_days > 0:
        removed = storage.prune(config.retention_days)
        log.info("清理了 %d 条过期记录", removed)
    blobs = BlobStore(DATA_DIR / "files", DATA_DIR / "restore")
    blobs.clean_staging()
    apps = AppRegistry(DATA_DIR / "apps.json")
    apps.seed_from_windows()

    controller = Controller(app, storage, blobs, apps, config)  # noqa: F841  需要持有引用直到退出
    log.info("InputVault 启动，数据目录 %s，副本和图片占用 %s", DATA_DIR, human_size(blobs.total_size()))
    code = app.exec()
    log.info("InputVault 退出（%s）", code)
    return code
