"""中间的记录列表：按天分组，每行有类型图标 / 缩略图、标题、来源、时间和“更多”按钮，可以直接拖出去。"""
from __future__ import annotations

import os
from collections.abc import Callable

from PySide6.QtCore import QFileInfo, QMimeData, QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QDrag, QFont, QPainter, QPixmap
from PySide6.QtWidgets import QAbstractItemView, QFileIconProvider, QListWidget, QListWidgetItem, QStyle, QStyledItemDelegate

from .. import filerefs
from ..classify import KIND_LABELS
from ..filerefs import FileEntry
from ..restore import Restorer
from ..storage import SOURCE_LABELS, Clip
from ..textutil import day_label, human_size, preview_line, relative_time
from .icons import G, draw_glyph, rounded, tile_pixmap

_CLIP = Qt.ItemDataRole.UserRole
_ROW = Qt.ItemDataRole.UserRole + 1
_HEADER = Qt.ItemDataRole.UserRole + 2
ROW_HEIGHT = 66
HEADER_HEIGHT = 34
TILE = 40
_MORE_WIDTH = 30

# 每种类型的图标和配色（theme 里的 tile_* 键）
KIND_VISUALS = {
    "text": (G.DOC, "tile_blue"),
    "url": (G.LINK, "tile_blue"),
    "file": (G.FOLDER, "tile_folder"),
    "path": (G.FOLDER, "tile_folder"),
    "image": (G.PHOTO, "tile_image"),
    "code": (G.CODE, "tile_code"),
    "json": (G.CODE, "tile_code"),
    "email": (G.TAG, "tile_gray"),
    "phone": (G.TAG, "tile_gray"),
    "number": (G.HASH, "tile_gray"),
}


def visual_for(clip: Clip) -> tuple[str, str]:
    """（图标字符，配色键）。复制的文件里，文件夹用黄色文件夹，普通文件用文档图标。"""
    if clip.kind == "file":
        files = clip.data.get("files", [])
        if files and not files[0].get("is_dir"):
            return G.PAGE, "tile_blue"
    return KIND_VISUALS.get(clip.kind, (G.DOC, "tile_blue"))


def clip_title(clip: Clip) -> str:
    if clip.kind == "file":
        data = clip.data
        files = data.get("files", [])
        return filerefs.title([FileEntry.from_dict(f) for f in files[:1]], data.get("total", len(files)))
    if clip.kind == "image":
        data = clip.data
        return f"图片 {data.get('w', '?')}×{data.get('h', '?')}"
    return preview_line(clip.content, 200)


def clip_size(clip: Clip) -> int:
    if clip.kind == "file":
        return sum(f.get("size", 0) for f in clip.data.get("files", []))
    if clip.kind == "image":
        return clip.data.get("bytes", 0)
    return 0


class Thumbnails:
    """列表和详情里用的图片：缩略图、类型图标、文件图标，带缓存。"""

    def __init__(self, restorer: Restorer, theme: dict[str, str]):
        self.restorer = restorer
        self.t = theme
        self._cache: dict[tuple, QPixmap] = {}
        self._provider = QFileIconProvider()

    def blob(self, name: str, box: int, ratio: float) -> QPixmap | None:
        """副本库里的图片，缩放到 box × box 以内。"""
        if not name or not self.restorer.blobs.exists(name):
            return None
        key = ("blob", name, box, ratio)
        if key not in self._cache:
            pixmap = QPixmap(str(self.restorer.blobs.path(name)))
            if pixmap.isNull():
                return None
            pixmap = pixmap.scaled(round(box * ratio), round(box * ratio), Qt.AspectRatioMode.KeepAspectRatio,
                                   Qt.TransformationMode.SmoothTransformation)
            pixmap.setDevicePixelRatio(ratio)
            self._cache[key] = pixmap
        return self._cache[key]

    def tile(self, clip: Clip, size: int, ratio: float, solid: bool = False) -> QPixmap:
        """一条记录的图标：图片和照片文件用缩略图，其他用类型图标。solid 是详情里的蓝底白图标样式。"""
        thumb = ""
        if clip.kind == "image":
            thumb = clip.data.get("thumb", "")
        elif clip.kind == "file":
            thumb = next((f["thumb"] for f in clip.data.get("files", []) if f.get("thumb")), "")
        if thumb:
            key = ("thumb", thumb, size, ratio)
            if key not in self._cache:
                source = self.blob(thumb, 160, ratio)
                if source is not None:
                    self._cache[key] = rounded(source, size, ratio, radius=size * 0.25)
            if key in self._cache:
                return self._cache[key]
        if clip.kind == "file" and (icon := self._file_icon(clip, size, ratio)) is not None:
            return icon
        glyph, tone = visual_for(clip)
        key = ("tile", glyph, tone, size, ratio, solid)
        if key not in self._cache:
            if solid:
                self._cache[key] = tile_pixmap(glyph, self.t["accent"], "#FFFFFF", size, ratio, size * 0.25)
            else:
                self._cache[key] = tile_pixmap(glyph, self.t[tone], self.t[tone + "_fg"], size, ratio, size * 0.25)
        return self._cache[key]

    def _file_icon(self, clip: Clip, size: int, ratio: float) -> QPixmap | None:
        """文件夹用黄色文件夹图标；程序、快捷方式用它们自己的图标；其他文件用默认的类型图标。"""
        files = clip.data.get("files", [])
        if not files:
            return None
        first = FileEntry.from_dict(files[0])
        if first.is_dir or first.ext not in (".exe", ".lnk", ".url", ".ico") or not os.path.exists(first.path):
            return None
        key = ("exe", first.path, size, ratio)
        if key not in self._cache:
            icon = self._provider.icon(QFileInfo(first.path)).pixmap(QSize(size - 8, size - 8), ratio)
            tile = tile_pixmap("", self.t["tile_gray"], self.t["tile_gray"], size, ratio, size * 0.25)
            painter = QPainter(tile)
            painter.drawPixmap(4, 4, icon)
            painter.end()
            self._cache[key] = tile
        return self._cache[key]


class _Delegate(QStyledItemDelegate):
    def __init__(self, theme: dict[str, str], thumbs: Thumbnails, parent=None):
        super().__init__(parent)
        self.t = theme
        self.thumbs = thumbs

    def sizeHint(self, option, index):
        return QSize(0, HEADER_HEIGHT if index.data(_HEADER) else ROW_HEIGHT)

    def paint(self, painter: QPainter, option, index):
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        font = QFont(option.font)
        header = index.data(_HEADER)
        if header:
            font.setPixelSize(13)
            painter.setFont(font)
            painter.setPen(QColor(self.t["muted"]))
            painter.drawText(option.rect.adjusted(14, 0, 0, -4), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom, header)
            painter.restore()
            return

        clip: Clip = index.data(_CLIP)
        r = option.rect.adjusted(4, 2, -4, -2)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        if selected or option.state & QStyle.StateFlag.State_MouseOver:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(self.t["sel"] if selected else self.t["hover"]))
            painter.drawRoundedRect(r, 10, 10)
        if selected:
            painter.setBrush(QColor(self.t["accent"]))
            painter.drawRoundedRect(QRect(r.left(), r.top() + 8, 3, r.height() - 16), 1.5, 1.5)

        ratio = painter.device().devicePixelRatioF()
        tile_rect = QRect(r.left() + 14, r.top() + (r.height() - TILE) // 2, TILE, TILE)
        painter.drawPixmap(tile_rect, self.thumbs.tile(clip, TILE, ratio))

        more_rect = more_button_rect(option.rect)
        draw_glyph(painter, more_rect, G.MORE, self.t["muted"], 16)
        font.setPixelSize(13)
        painter.setFont(font)
        time_text = relative_time(clip.last_used_at)
        time_width = painter.fontMetrics().horizontalAdvance(time_text)
        time_rect = QRect(more_rect.left() - time_width - 8, r.top(), time_width, r.height())
        painter.setPen(QColor(self.t["muted"]))
        painter.drawText(time_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, time_text)

        left = tile_rect.right() + 14
        width = time_rect.left() - left - 12
        title_font = QFont(option.font)
        title_font.setPixelSize(15)
        painter.setFont(title_font)
        fm = painter.fontMetrics()
        title_top = r.top() + 12
        x = left
        if clip.favorite:
            draw_glyph(painter, QRect(x, title_top, 16, fm.height()), G.STAR_FILL, self.t["star"], 13)
            x += 18
        painter.setPen(QColor(self.t["fg"]))
        title = index.data(_ROW)
        painter.drawText(QRect(x, title_top, width - (x - left), fm.height()), Qt.AlignmentFlag.AlignVCenter,
                         fm.elidedText(title, Qt.TextElideMode.ElideRight, width - (x - left)))

        meta_parts = [SOURCE_LABELS.get(clip.source, clip.source)]
        if clip.kind != "text":
            meta_parts.append(KIND_LABELS.get(clip.kind, clip.kind))
        if clip.source_app:
            meta_parts.append(clip.source_app)
        if size := clip_size(clip):
            meta_parts.append(human_size(size))
        if clip.use_count > 1:
            meta_parts.append(f"用过 {clip.use_count} 次")
        painter.setFont(font)
        sfm = painter.fontMetrics()
        painter.setPen(QColor(self.t["muted"]))
        painter.drawText(QRect(left, r.bottom() - 12 - sfm.height(), width, sfm.height()), Qt.AlignmentFlag.AlignVCenter,
                         sfm.elidedText("  ·  ".join(meta_parts), Qt.TextElideMode.ElideRight, width))
        painter.restore()


def more_button_rect(row_rect: QRect) -> QRect:
    return QRect(row_rect.right() - 6 - _MORE_WIDTH, row_rect.top() + (row_rect.height() - 28) // 2, _MORE_WIDTH, 28)


class ClipList(QListWidget):
    moreRequested = Signal(object, QPoint)  # (Clip, 菜单弹出的全局位置)

    def __init__(self, theme: dict[str, str], thumbs: Thumbnails, make_drag: Callable[[Clip], QMimeData | None]):
        super().__init__()
        self._make_drag = make_drag
        self.thumbs = thumbs
        self.setItemDelegate(_Delegate(theme, thumbs, self))
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setMouseTracking(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)

    def populate(self, clips: list[Clip], group_by_day: bool) -> None:
        self.clear()
        last_group = None
        for clip in clips:
            if group_by_day:
                group = day_label(clip.last_used_at)
                if group != last_group:
                    header = QListWidgetItem()
                    header.setData(_HEADER, group)
                    header.setFlags(Qt.ItemFlag.NoItemFlags)
                    self.addItem(header)
                    last_group = group
            item = QListWidgetItem()
            item.setData(_CLIP, clip)
            item.setData(_ROW, clip_title(clip))
            self.addItem(item)

    def current_clip(self) -> Clip | None:
        item = self.currentItem()
        return item.data(_CLIP) if item else None

    def clip_rows(self) -> list[int]:
        return [i for i in range(self.count()) if self.item(i).data(_CLIP) is not None]

    def select_clip(self, clip_id: int | None = None, row_hint: int | None = None) -> None:
        """选中某条记录；找不到时选第 row_hint 条（删除后停在原来的位置），都没有就选第一条。"""
        rows = self.clip_rows()
        if not rows:
            return
        target = rows[0]
        if clip_id is not None:
            target = next((i for i in rows if self.item(i).data(_CLIP).id == clip_id), rows[0])
        elif row_hint is not None:
            target = rows[min(row_hint, len(rows) - 1)]
        self.setCurrentRow(target)

    def clip_index(self) -> int:
        """当前选中的是第几条记录（不算分组标题）。"""
        rows = self.clip_rows()
        return rows.index(self.currentRow()) if self.currentRow() in rows else 0

    def move(self, step: int) -> None:
        rows = self.clip_rows()
        if not rows:
            return
        index = self.clip_index() if self.currentRow() in rows else -1
        self.setCurrentRow(rows[max(0, min(len(rows) - 1, index + step))])

    def mousePressEvent(self, event):
        item = self.itemAt(event.position().toPoint())
        clip = item.data(_CLIP) if item else None
        if clip is not None and event.button() == Qt.MouseButton.LeftButton:
            rect = self.visualItemRect(item)
            if more_button_rect(rect).contains(event.position().toPoint()):
                self.setCurrentItem(item)
                self.moreRequested.emit(clip, self.viewport().mapToGlobal(more_button_rect(rect).bottomLeft()))
                return
        super().mousePressEvent(event)

    def startDrag(self, _actions):
        clip = self.current_clip()
        mime = self._make_drag(clip) if clip else None
        if mime is None:
            return
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.setPixmap(self.thumbs.tile(clip, TILE, self.devicePixelRatioF()))
        drag.exec(Qt.DropAction.CopyAction)
