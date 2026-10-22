"""右侧详情：标题、标签、内容、操作按钮和原始信息。"""
from __future__ import annotations

import os
import time
from collections.abc import Callable

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QStackedLayout,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..classify import KIND_LABELS
from ..filerefs import STATUS_LABELS, Status
from ..restore import Restorer
from ..storage import SOURCE_LABELS, Clip
from ..textutil import human_size
from .clip_list import Thumbnails, clip_size, clip_title, visual_for
from .icons import G, glyph_icon, glyph_pixmap

_PREVIEW_MAX = 20_000
_PREVIEW_FILES = 50
_TIME = "%Y-%m-%d %H:%M"


def _short_time(ts: float) -> str:
    """今年的不写年份，省出位置。"""
    t = time.localtime(ts)
    return time.strftime("%m-%d %H:%M" if t.tm_year == time.localtime().tm_year else _TIME, t)


def _spaced(text: str) -> str:
    """按钮文字前加一个空格：Qt 按钮的图标和文字之间没有间距，贴在一起不好看。"""
    return f" {text}" if text else text


class _ElidedLabel(QLabel):
    """放不下时末尾显示省略号的标签。"""

    def __init__(self):
        super().__init__()
        self._full = ""
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

    def set_full_text(self, text: str) -> None:
        self._full = text
        self.setToolTip(text if len(text) > 30 else "")
        self._elide()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._elide()

    def _elide(self) -> None:
        self.setText(self.fontMetrics().elidedText(self._full, Qt.TextElideMode.ElideRight, max(10, self.width())))


class DetailPane(QFrame):
    # "paste" / "copy" / "paste_file" / "open_source" / "open_location" / "save_image" / "pin"
    action = Signal(str)

    def __init__(self, theme: dict[str, str], thumbs: Thumbnails, restorer: Restorer,
                 menu_factory: Callable[[Clip], QMenu]):
        super().__init__()
        self.setObjectName("card")
        self.t = theme
        self.thumbs = thumbs
        self.restorer = restorer
        self._menu_factory = menu_factory
        self.clip: Clip | None = None
        self._image: QPixmap | None = None
        self._image_max_height = 0

        self._stack = QStackedLayout(self)
        self.empty = QLabel()
        self.empty.setObjectName("muted")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._stack.addWidget(self.empty)
        page = QWidget()
        self._stack.addWidget(page)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(12)

        # 头部：图标 + 标题 / 标签 / 时间
        self.tile = QLabel()
        self.tile.setFixedSize(46, 46)
        self.title = _ElidedLabel()
        self.title.setObjectName("h2")
        self.star = self._flat_button(G.STAR, "置顶（Ctrl+P）", lambda: self.action.emit("pin"))
        self.more = self._flat_button(G.MORE, "更多操作", self._show_menu)
        title_row = QHBoxLayout()
        title_row.setSpacing(4)
        title_row.addWidget(self.title, 1)
        title_row.addWidget(self.star)
        title_row.addWidget(self.more)
        self.source_chip = QLabel()
        self.source_chip.setObjectName("chip")
        self.kind_icon = QLabel()
        self.kind_label = QLabel()
        self.kind_label.setObjectName("muted")
        chips = QHBoxLayout()
        chips.setSpacing(8)
        chips.addWidget(self.source_chip)
        chips.addSpacing(4)
        chips.addWidget(self.kind_icon)
        chips.addWidget(self.kind_label)
        chips.addStretch(1)
        self.times = QLabel()
        self.times.setObjectName("muted")
        head_text = QVBoxLayout()
        head_text.setSpacing(6)
        head_text.addLayout(title_row)
        head_text.addLayout(chips)
        head_text.addWidget(self.times)
        head = QHBoxLayout()
        head.setSpacing(14)
        head.addWidget(self.tile, 0, Qt.AlignmentFlag.AlignTop)
        head.addLayout(head_text, 1)
        layout.addLayout(head)

        # 内容
        self.box = QFrame()
        self.box.setObjectName("contentBox")
        box_layout = QVBoxLayout(self.box)
        box_layout.setContentsMargins(8, 8, 8, 8)
        self.image = QLabel()
        self.image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.text = QPlainTextEdit()
        self.text.setObjectName("content")
        self.text.setReadOnly(True)
        self.text.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        box_layout.addWidget(self.image, 1)
        box_layout.addWidget(self.text, 1)
        self.box.setMinimumHeight(150)
        layout.addWidget(self.box, 1)

        # 操作按钮
        self.copy_button = self._button("复制内容", G.COPY, "primary", "#FFFFFF", lambda: self.action.emit("copy"))
        self.paste_button = self._button("引用到输入框", G.REPLY, "secondary", theme["fg_soft"],
                                         lambda: self.action.emit("paste"))
        self.third_button = self._button("", G.OPEN, "secondary", theme["fg_soft"], self._third_action)
        self._third = ""
        buttons = QHBoxLayout()
        buttons.setSpacing(10)
        for b in (self.copy_button, self.paste_button, self.third_button):
            buttons.addWidget(b, 1)
        layout.addLayout(buttons)

        divider = QFrame()
        divider.setObjectName("divider")
        layout.addWidget(divider)

        # 原始信息
        info_title = QHBoxLayout()
        info_title.setSpacing(8)
        icon = QLabel()
        icon.setPixmap(glyph_pixmap(G.SYNC, theme["accent"], 16, self.devicePixelRatioF()))
        label = QLabel("原始信息")
        label.setObjectName("h3")
        info_title.addWidget(icon)
        info_title.addWidget(label)
        info_title.addStretch(1)
        layout.addLayout(info_title)
        self.info = QGridLayout()
        self.info.setHorizontalSpacing(12)
        self.info.setVerticalSpacing(10)
        self.info.setColumnStretch(2, 1)
        self.info.setColumnMinimumWidth(1, 90)
        layout.addLayout(self.info)

    # ---------- 控件 ----------

    def _flat_button(self, glyph: str, tip: str, slot) -> QToolButton:
        button = QToolButton()
        button.setObjectName("flat")
        button.setIcon(glyph_icon(glyph, self.t["fg_soft"], 18))
        button.setIconSize(QSize(18, 18))
        button.setToolTip(tip)
        button.clicked.connect(slot)
        return button

    def _button(self, text: str, glyph: str, style: str, color: str, slot) -> QPushButton:
        button = QPushButton(_spaced(text))
        button.setObjectName(style)
        button.setIcon(glyph_icon(glyph, color, 16))
        button.setIconSize(QSize(16, 16))
        button.setMinimumHeight(42)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.clicked.connect(slot)
        return button

    def _show_menu(self) -> None:
        if self.clip is not None:
            self._menu_factory(self.clip).exec(self.more.mapToGlobal(self.more.rect().bottomLeft()))

    def _third_action(self) -> None:
        if self._third:
            self.action.emit(self._third)

    # ---------- 显示 ----------

    def show_empty(self, message: str) -> None:
        self.clip = None
        self.empty.setText(message)
        self._stack.setCurrentIndex(0)

    def show_clip(self, clip: Clip, can_paste: bool, target_app: str) -> None:
        self.clip = clip
        self._stack.setCurrentIndex(1)
        ratio = self.devicePixelRatioF()
        self.tile.setPixmap(self.thumbs.tile(clip, 46, ratio, solid=True))
        self.title.set_full_text(clip_title(clip))
        self.star.setIcon(glyph_icon(G.STAR_FILL if clip.pinned else G.STAR,
                                     self.t["accent"] if clip.pinned else self.t["fg_soft"], 18))
        self.star.setToolTip("取消置顶（Ctrl+P）" if clip.pinned else "置顶（Ctrl+P）")
        self.source_chip.setText(SOURCE_LABELS.get(clip.source, clip.source))
        glyph, _ = visual_for(clip)
        self.kind_icon.setPixmap(glyph_pixmap(glyph, self.t["muted"], 14, ratio))
        kind = KIND_LABELS.get(clip.kind, clip.kind)
        self.kind_label.setText(f"{kind}  ·  来自 {clip.source_app or '未知'}")
        self.times.setText(
            f"首次 {_short_time(clip.created_at)}  ·  最近 {_short_time(clip.last_used_at)}  ·  用过 {clip.use_count} 次"
        )

        self._image = None
        if clip.kind == "image":
            path = self.restorer.image_path(clip)
            self._set_image(QPixmap(str(path)) if path else None, 0)
            self.text.hide()
        elif clip.kind == "file":
            thumb = next((f["thumb"] for f in clip.data.get("files", []) if f.get("thumb")), "")
            self._set_image(self.thumbs.blob(thumb, 160, ratio) if thumb else None, 170)
            self.text.setPlainText(self._file_report(clip))
            self.text.show()
        else:
            self._set_image(None, 0)
            text = clip.content
            if len(text) > _PREVIEW_MAX:
                text = text[:_PREVIEW_MAX] + f"\n\n……（只显示前 {_PREVIEW_MAX} 字）"
            self.text.setPlainText(text)
            self.text.show()

        self.paste_button.setEnabled(can_paste)
        self.paste_button.setToolTip(f"粘贴到「{target_app}」（Enter）" if can_paste
                                     else "用快捷键唤起时可用：会粘贴到唤起前的那个窗口")
        self.copy_button.setToolTip("只放进剪贴板，不粘贴（Shift+Enter）")
        if clip.kind == "file":
            self._set_third("打开所在位置", G.FOLDER_OPEN, "open_location")
        elif clip.kind == "image":
            self._set_third("另存为…", G.SAVE, "save_image")
        else:
            self._set_third("打开来源应用", G.OPEN, "open_source" if clip.source_exe else "")
        self._fill_info(clip)

    def _set_third(self, text: str, glyph: str, action: str) -> None:
        self._third = action
        self.third_button.setText(_spaced(text))
        self.third_button.setIcon(glyph_icon(glyph, self.t["fg_soft"], 16))
        self.third_button.setEnabled(bool(action))

    def _set_image(self, pixmap: QPixmap | None, max_height: int) -> None:
        """max_height 为 0 表示图片占满内容框（图片记录），否则是文件记录上方的小缩略图。"""
        self._image = pixmap if pixmap is not None and not pixmap.isNull() else None
        self._image_max_height = max_height
        self.image.setVisible(self._image is not None)
        self.image.setMaximumHeight(max_height or 16777215)
        self._rescale()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._rescale()

    def _rescale(self) -> None:
        if self._image is None:
            return
        ratio = self.devicePixelRatioF()
        width = max(50, self.box.width() - 24)
        height = max(50, (self._image_max_height or self.box.height()) - 24)
        source = self._image
        natural_w = source.width() / source.devicePixelRatio()
        natural_h = source.height() / source.devicePixelRatio()
        # 小图不放大，大图缩到框里
        scale = min(1.0, width / natural_w, height / natural_h)
        scaled = source.scaled(round(natural_w * scale * ratio), round(natural_h * scale * ratio),
                               Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        scaled.setDevicePixelRatio(ratio)
        self.image.setPixmap(scaled)

    def _file_report(self, clip: Clip) -> str:
        data = clip.data
        files = data.get("files", [])
        lines = []
        if data.get("cut"):
            lines += ["当时是剪切的；还原时按复制处理，不会再把文件移走。", ""]
        for r in self.restorer.resolutions(clip, _PREVIEW_FILES):
            e = r.entry
            lines.append(f"{'📁' if e.is_dir else '📄'} {e.name}" + ("" if e.is_dir else f"    {human_size(e.size)}"))
            lines.append(f"      {STATUS_LABELS[r.status]}")
            if r.status is Status.MOVED:
                renamed = os.path.basename(r.path) != e.name
                lines.append(f"      现在在：{r.path if renamed else os.path.dirname(r.path)}")
            else:
                lines.append(f"      位置：{os.path.dirname(e.path)}")
            if r.status is Status.MODIFIED and r.has_backup:
                lines.append("      粘贴时可以选现在的版本，或复制时的版本")
        if len(files) > _PREVIEW_FILES:
            lines.append(f"……还有 {len(files) - _PREVIEW_FILES} 个文件")
        if extra := self.restorer.not_registered(clip):
            lines.append(f"另外 {extra} 个文件没有登记（一次复制太多）")
        return "\n".join(lines)

    def _fill_info(self, clip: Clip) -> None:
        while self.info.count():
            widget = self.info.takeAt(0).widget()
            if widget is not None:
                widget.hide()  # deleteLater 要等下一轮事件循环，先藏起来免得和新内容叠在一起
                widget.deleteLater()
        rows = [(G.APPS, "应用", clip.source_app or "未知")]
        if clip.window_title:
            rows.append((G.WINDOW, "窗口", clip.window_title))
        rows.append((G.CLOCK, "时间", time.strftime("%Y-%m-%d %H:%M", time.localtime(clip.created_at))))
        rows.append((G.PAGE, "类型", f"{KIND_LABELS.get(clip.kind, clip.kind)}（{SOURCE_LABELS.get(clip.source, clip.source)}）"))
        data = clip.data
        if clip.kind == "file":
            files = data.get("files", [])
            rows.append((G.HASH, "文件数", f"{data.get('total', len(files))} 个  ·  共 {human_size(clip_size(clip))}"))
        elif clip.kind == "image":
            rows.append((G.HASH, "尺寸", f"{data.get('w', '?')} × {data.get('h', '?')}  ·  {human_size(clip_size(clip))}"))
        else:
            rows.append((G.HASH, "字符数", str(len(clip.content))))
        rows.append((G.INFO, "使用次数", f"{clip.use_count} 次"))

        ratio = self.devicePixelRatioF()
        for i, (glyph, key, value) in enumerate(rows):
            icon = QLabel()
            icon.setPixmap(glyph_pixmap(glyph, self.t["muted"], 16, ratio))
            key_label = QLabel(key)
            key_label.setObjectName("infoKey")
            value_label = _ElidedLabel()
            value_label.set_full_text(value)
            self.info.addWidget(icon, i, 0)
            self.info.addWidget(key_label, i, 1)
            self.info.addWidget(value_label, i, 2)
