"""设置窗口。"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from .. import win32
from ..config import Config
from ..textutil import human_size


def _lines(edit: QPlainTextEdit) -> list[str]:
    return [line.strip() for line in edit.toPlainText().splitlines() if line.strip()]


def _mb_spin(value: int, low: int, high: int, step: int) -> QSpinBox:
    spin = QSpinBox()
    spin.setRange(low, high)
    spin.setSingleStep(step)
    spin.setSuffix(" MB")
    spin.setValue(value)
    return spin


class SettingsDialog(QDialog):
    def __init__(self, config: Config, usage: Callable[[], int], clear: Callable[[], int], parent=None):
        """usage() 返回副本和图片当前占用的字节数；clear() 清空它们并返回释放的字节数。"""
        super().__init__(parent)
        self.setWindowTitle("InputVault 设置")
        self.setMinimumWidth(500)
        self._config = config
        self._usage = usage
        self._clear = clear

        self.hotkey = QLineEdit(config.hotkey)
        self.hotkey.setPlaceholderText("比如 ctrl+shift+v、alt+v、ctrl+alt+space")
        self.capture_typing = QCheckBox("记录打字输入")
        self.capture_typing.setChecked(config.capture_typing)
        self.capture_files = QCheckBox("记录复制的文件（登记位置，小文件自动备份）")
        self.capture_files.setChecked(config.capture_files)
        self.capture_images = QCheckBox("记录复制的图片和截图")
        self.capture_images.setChecked(config.capture_images)
        self.skip_sensitive = QCheckBox("跳过身份证号、银行卡号、API Key、私钥")
        self.skip_sensitive.setChecked(config.skip_sensitive)
        self.retention = QSpinBox()
        self.retention.setRange(0, 3650)
        self.retention.setSuffix(" 天")
        self.retention.setSpecialValueText("永久保留")
        self.retention.setValue(config.retention_days)

        self.backup_max = _mb_spin(config.backup_max_mb, 1, 1024, 5)
        self.quota = _mb_spin(config.blob_quota_mb, 100, 102_400, 256)
        self.usage_label = QLabel()
        self.usage_label.setWordWrap(True)
        clear_button = QPushButton("清空副本和图片…")
        clear_button.clicked.connect(self._confirm_clear)
        usage_row = QHBoxLayout()
        usage_row.addWidget(self.usage_label, 1)
        usage_row.addWidget(clear_button)
        self._refresh_usage()

        self.blacklist = QPlainTextEdit("\n".join(config.blacklist_apps))
        self.typing_blacklist = QPlainTextEdit("\n".join(config.typing_blacklist_apps))
        for edit in (self.blacklist, self.typing_blacklist):
            edit.setFixedHeight(100)

        form = QFormLayout()
        form.addRow("唤起热键", self.hotkey)
        form.addRow("记录什么", self.capture_typing)
        form.addRow("", self.capture_files)
        form.addRow("", self.capture_images)
        form.addRow("", self.skip_sensitive)
        form.addRow("记录保留", self.retention)
        form.addRow("自动备份", self.backup_max)
        form.addRow("", QLabel("不超过这个大小的文件自动存一份副本；图片超过这个大小不保存"))
        form.addRow("空间上限", self.quota)
        form.addRow("", usage_row)
        form.addRow("完全不记录", self.blacklist)
        form.addRow("不记录打字", self.typing_blacklist)

        note = QLabel(
            "应用名单每行填一个 exe 文件名，比如 Weixin.exe，可以在任务管理器的“详细信息”里看到。\n"
            "更简单的办法：在搜索面板里右键某条记录，选“不再记录”。"
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: gray;")

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(note)
        layout.addWidget(buttons)

    def _refresh_usage(self) -> None:
        self.usage_label.setText(f"现在占用 {human_size(self._usage())}，超出时先清理最久没用的")

    def _confirm_clear(self) -> None:
        answer = QMessageBox.question(
            self, "清空副本和图片",
            "会删除所有图片记录和文件副本。\n文件记录本身保留，原文件还在的话照样能还原。\n\n确定清空吗？",
        )
        if answer == QMessageBox.StandardButton.Yes:
            freed = self._clear()
            self._refresh_usage()
            QMessageBox.information(self, "已清空", f"释放了 {human_size(freed)}。")

    def _accept(self) -> None:
        try:
            win32.parse_hotkey(self.hotkey.text())
        except ValueError as e:
            QMessageBox.warning(self, "热键格式不对", str(e))
            return
        self.accept()

    def values(self) -> Config:
        return replace(
            self._config,
            hotkey=self.hotkey.text().strip().lower(),
            capture_typing=self.capture_typing.isChecked(),
            capture_files=self.capture_files.isChecked(),
            capture_images=self.capture_images.isChecked(),
            skip_sensitive=self.skip_sensitive.isChecked(),
            retention_days=self.retention.value(),
            backup_max_mb=self.backup_max.value(),
            blob_quota_mb=self.quota.value(),
            blacklist_apps=_lines(self.blacklist),
            typing_blacklist_apps=_lines(self.typing_blacklist),
        )
