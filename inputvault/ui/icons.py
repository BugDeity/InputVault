"""界面图标。

线条图标用系统自带的 Segoe Fluent Icons 字体（Windows 10 上退回 Segoe MDL2 Assets）画，
任何缩放比例下都清晰，颜色随意；程序图标从 exe 里取，取不到就画一个带首字的圆。
"""
from __future__ import annotations

import os
import zlib

from PySide6.QtCore import QFileInfo, QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QFileIconProvider

from ..apps import AppRegistry

_FAMILIES = ["Segoe Fluent Icons", "Segoe MDL2 Assets"]


class G:
    """用到的图标字符（Segoe Fluent Icons 的码位）。"""

    SEARCH = ""
    DOC = ""
    PAGE = ""
    LINK = ""
    FOLDER = ""
    FOLDER_OPEN = ""
    PHOTO = ""
    MORE = ""
    STAR = ""
    STAR_FILL = ""
    COPY = ""
    REPLY = ""
    OPEN = ""
    INFO = ""
    CLOCK = ""
    APPS = ""
    TAG = ""
    SORT = ""
    DOWN = ""
    MINIMIZE = ""
    MAXIMIZE = ""
    RESTORE = ""
    CLOSE = ""
    CODE = ""
    KEYBOARD = ""
    DELETE = ""
    SAVE = ""
    SYNC = ""
    WINDOW = ""
    HASH = "#"  # 图标字体里没有，自动退回普通字体
    BLOCK = ""
    LIST = ""


def glyph_font(px: float) -> QFont:
    font = QFont()
    font.setFamilies(_FAMILIES)
    font.setPixelSize(max(1, round(px)))
    return font


def draw_glyph(painter: QPainter, rect: QRect | QRectF, glyph: str, color: str | QColor, px: float) -> None:
    painter.save()
    painter.setFont(glyph_font(px))
    painter.setPen(QColor(color))
    painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, glyph)
    painter.restore()


def glyph_pixmap(glyph: str, color: str | QColor, px: int, ratio: float = 2.0) -> QPixmap:
    pixmap = QPixmap(round(px * ratio), round(px * ratio))
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    draw_glyph(painter, QRectF(0, 0, px * ratio, px * ratio), glyph, color, px * ratio)
    painter.end()
    pixmap.setDevicePixelRatio(ratio)
    return pixmap


def glyph_icon(glyph: str, color: str | QColor, px: int = 16) -> QIcon:
    return QIcon(glyph_pixmap(glyph, color, px))


def tile_pixmap(glyph: str, bg: str, fg: str, size: int, ratio: float, radius: float = 10, glyph_px: float = 0) -> QPixmap:
    """圆角方块里一个图标（列表和详情里的类型图标、左上角的 Logo）。"""
    pixmap = QPixmap(round(size * ratio), round(size * ratio))
    pixmap.fill(Qt.GlobalColor.transparent)
    pixmap.setDevicePixelRatio(ratio)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(bg))
    painter.drawRoundedRect(QRectF(0, 0, size, size), radius, radius)
    draw_glyph(painter, QRectF(0, 0, size, size), glyph, fg, glyph_px or size * 0.48)
    painter.end()
    return pixmap


def rounded(pixmap: QPixmap, size: int, ratio: float, radius: float = 10) -> QPixmap:
    """把缩略图裁成圆角方块（居中裁剪填满）。"""
    out = QPixmap(round(size * ratio), round(size * ratio))
    out.fill(Qt.GlobalColor.transparent)
    out.setDevicePixelRatio(ratio)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, size, size), radius, radius)
    painter.setClipPath(path)
    scaled = pixmap.scaled(round(size * ratio), round(size * ratio), Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                           Qt.TransformationMode.SmoothTransformation)
    scaled.setDevicePixelRatio(ratio)
    w, h = scaled.width() / ratio, scaled.height() / ratio
    painter.drawPixmap(QRectF((size - w) / 2, (size - h) / 2, w, h), scaled, QRectF(scaled.rect()))
    painter.end()
    return out


_AVATAR_COLORS = ["#2F7BF6", "#16A34A", "#F59E0B", "#E11D48", "#7C3AED", "#0891B2", "#EA580C", "#4F46E5"]


def avatar(name: str, size: int, ratio: float) -> QPixmap:
    pixmap = QPixmap(round(size * ratio), round(size * ratio))
    pixmap.fill(Qt.GlobalColor.transparent)
    pixmap.setDevicePixelRatio(ratio)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(_AVATAR_COLORS[zlib.crc32(name.encode()) % len(_AVATAR_COLORS)]))
    painter.drawRoundedRect(QRectF(0, 0, size, size), size * 0.28, size * 0.28)
    font = QFont()
    font.setPixelSize(round(size * 0.5))
    font.setBold(True)
    painter.setFont(font)
    painter.setPen(QColor("white"))
    painter.drawText(QRectF(0, 0, size, size), Qt.AlignmentFlag.AlignCenter, (name or "?")[:1].upper())
    painter.end()
    return pixmap


class AppIcons:
    """程序图标：用 AppRegistry 里记下的 exe 路径取系统图标，带缓存。"""

    def __init__(self, registry: AppRegistry | None):
        self._registry = registry
        self._provider = QFileIconProvider()
        self._cache: dict[tuple[str, int, float], QPixmap] = {}

    def pixmap(self, exe: str, name: str, size: int, ratio: float) -> QPixmap:
        key = (exe.lower(), size, ratio)
        if key not in self._cache:
            path = self._registry.path_for(exe) if (self._registry and exe) else ""
            pixmap = None
            if path and os.path.exists(path):
                icon = self._provider.icon(QFileInfo(path))
                pixmap = icon.pixmap(QSize(size, size), ratio)
                if pixmap.isNull():
                    pixmap = None
            self._cache[key] = pixmap or avatar(name or exe, size, ratio)
        return self._cache[key]
