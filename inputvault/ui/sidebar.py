"""左侧栏：分类和应用来源。两组可以叠加：比如“图片”+“微信”= 微信里复制的图片。"""
from __future__ import annotations

from PySide6.QtCore import QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import QFrame, QLabel, QListWidget, QListWidgetItem, QStyle, QStyledItemDelegate, QVBoxLayout, QWidget

from ..storage import CATEGORY_KINDS
from .icons import AppIcons, G, draw_glyph

CATEGORIES = [
    ("all", "全部", G.LIST),
    ("pinned", "置顶", G.PIN),
    ("text", "文本", G.DOC),
    ("url", "链接", G.LINK),
    ("file", "文件", G.FOLDER),
    ("image", "图片", G.PHOTO),
    ("other", "其他", G.MORE),
]
CATEGORY_LABELS = {key: label for key, label, _ in CATEGORIES}
OTHERS = "__others__"  # “其他应用”：前几名以外的所有程序
TOP_APPS = 5

_KEY = Qt.ItemDataRole.UserRole
_COUNT = Qt.ItemDataRole.UserRole + 1
_GLYPH = Qt.ItemDataRole.UserRole + 2
_PIXMAP = Qt.ItemDataRole.UserRole + 3
_ROW = 44


class _NavDelegate(QStyledItemDelegate):
    def __init__(self, theme: dict[str, str], parent=None):
        super().__init__(parent)
        self.t = theme

    def sizeHint(self, option, index):
        return QSize(0, _ROW)

    def paint(self, painter: QPainter, option, index):
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = option.rect.adjusted(0, 2, 0, -2)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        if selected or hovered:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(self.t["nav_sel"] if selected else self.t["hover"]))
            painter.drawRoundedRect(r, 10, 10)
        if selected:  # 左边一条蓝色竖线
            painter.setBrush(QColor(self.t["accent"]))
            painter.drawRoundedRect(QRect(r.left(), r.top() + 10, 3, r.height() - 20), 1.5, 1.5)

        icon_rect = QRect(r.left() + 14, r.top() + (r.height() - 24) // 2, 24, 24)
        pixmap = index.data(_PIXMAP)
        if pixmap is not None:
            painter.drawPixmap(icon_rect, pixmap)
        else:
            color = self.t["accent"] if selected else self.t["fg_soft"]
            draw_glyph(painter, icon_rect, index.data(_GLYPH), color, 18)

        font = QFont(option.font)
        font.setPixelSize(15)
        font.setWeight(QFont.Weight.DemiBold if selected else QFont.Weight.Normal)
        painter.setFont(font)
        painter.setPen(QColor(self.t["accent_text"] if selected else self.t["fg"]))
        text_rect = QRect(icon_rect.right() + 14, r.top(), r.width() - 110, r.height())
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                         painter.fontMetrics().elidedText(index.data(Qt.ItemDataRole.DisplayRole),
                                                          Qt.TextElideMode.ElideRight, text_rect.width()))

        count = index.data(_COUNT)
        if count is not None:
            font.setPixelSize(13)
            font.setWeight(QFont.Weight.Normal)
            painter.setFont(font)
            text = str(count)
            count_rect = QRect(r.right() - 58, r.top() + (r.height() - 22) // 2, 46, 22)
            if selected:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(self.t["card"]))
                width = painter.fontMetrics().horizontalAdvance(text) + 16
                painter.drawRoundedRect(QRect(count_rect.right() - width, count_rect.top(), width, 22), 11, 11)
            painter.setPen(QColor(self.t["accent_text"] if selected else self.t["muted"]))
            painter.drawText(count_rect.adjusted(0, 0, -8, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, text)
        painter.restore()


class _NavList(QListWidget):
    def __init__(self, theme: dict[str, str]):
        super().__init__()
        self.setItemDelegate(_NavDelegate(theme, self))
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setMouseTracking(True)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

    def fit_height(self) -> None:
        self.setFixedHeight(self.count() * _ROW + 4)


class Sidebar(QWidget):
    categoryChanged = Signal(str)
    appChanged = Signal(object)  # exe / OTHERS / None（不限应用）

    def __init__(self, theme: dict[str, str], app_icons: AppIcons):
        super().__init__()
        self._icons = app_icons
        self._top_exes: tuple[str, ...] = ()
        self.categories = _NavList(theme)
        for key, label, glyph in CATEGORIES:
            item = QListWidgetItem(label)
            item.setData(_KEY, key)
            item.setData(_GLYPH, glyph)
            self.categories.addItem(item)
        self.categories.fit_height()
        self.categories.setCurrentRow(0)
        self.categories.itemClicked.connect(lambda item: self.categoryChanged.emit(item.data(_KEY)))

        self.apps = _NavList(theme)
        self.apps.itemClicked.connect(self._on_app_clicked)
        self._app_selected: object = None

        divider = QFrame()
        divider.setObjectName("divider")
        section = QLabel("应用来源")
        section.setObjectName("section")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(self.categories)
        layout.addSpacing(6)
        layout.addWidget(divider)
        layout.addSpacing(8)
        layout.addWidget(section)
        layout.addWidget(self.apps)
        layout.addStretch(1)

    @property
    def top_exes(self) -> tuple[str, ...]:
        """前几名程序，“其他应用”就是排除掉它们。"""
        return self._top_exes

    def set_counts(self, total: int, pinned: int, kind_counts: dict[str, int]) -> None:
        for i in range(self.categories.count()):
            item = self.categories.item(i)
            key = item.data(_KEY)
            if key == "all":
                count = total
            elif key == "pinned":
                count = pinned
            else:
                count = sum(kind_counts.get(k, 0) for k in CATEGORY_KINDS[key])
            item.setData(_COUNT, count)

    def set_apps(self, apps: list[tuple[str, str, int]]) -> None:
        ratio = self.devicePixelRatioF()
        self.apps.clear()
        top = apps[:TOP_APPS]
        self._top_exes = tuple(exe for exe, _, _ in top)
        for exe, name, count in top:
            item = QListWidgetItem(name)
            item.setData(_KEY, exe)
            item.setData(_COUNT, count)
            item.setData(_PIXMAP, self._icons.pixmap(exe, name, 24, ratio))
            self.apps.addItem(item)
        rest = sum(n for _, _, n in apps[TOP_APPS:])
        if rest:
            item = QListWidgetItem("其他应用")
            item.setData(_KEY, OTHERS)
            item.setData(_COUNT, rest)
            item.setData(_GLYPH, G.APPS)
            self.apps.addItem(item)
        self.apps.fit_height()
        self.select_app(self._app_selected, notify=False)

    def select_category(self, key: str) -> None:
        for i in range(self.categories.count()):
            if self.categories.item(i).data(_KEY) == key:
                self.categories.setCurrentRow(i)

    def select_app(self, exe: object, notify: bool = True) -> None:
        self._app_selected = exe
        self.apps.clearSelection()
        for i in range(self.apps.count()):
            item = self.apps.item(i)
            key = item.data(_KEY)
            if exe is not None and (key == exe or (isinstance(exe, str) and key.lower() == exe.lower())):
                self.apps.setCurrentItem(item)
        if notify:
            self.appChanged.emit(exe)

    def _on_app_clicked(self, item: QListWidgetItem) -> None:
        key = item.data(_KEY)
        # 再点一次已选中的应用 = 取消，回到不限应用
        self.select_app(None if key == self._app_selected else key)
