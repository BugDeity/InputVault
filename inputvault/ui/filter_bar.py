"""列表上方的“筛选中”提示条。

只要分类、应用、排序、搜索有任何一项不是默认状态就显示，固定在列表上方不随列表滚动；
每个条件是一个标签，点 × 单独取消；右边“恢复默认”一键全部还原。条件多时标签自动换行。
"""
from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QSize, Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QLayout, QPushButton, QToolButton, QWidget

from .icons import G, glyph_icon


class _FlowLayout(QLayout):
    """从左到右排，放不下就换行（Qt 文档里 Flow Layout 示例的 Python 版）。"""

    def __init__(self, spacing: int = 6):
        super().__init__()
        self._items = []
        self.setSpacing(spacing)
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index):
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._arrange(QRect(0, 0, width, 0), dry_run=True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._arrange(rect, dry_run=False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        return size

    def _arrange(self, rect: QRect, dry_run: bool) -> int:
        x, y, line_height = rect.x(), rect.y(), 0
        spacing = self.spacing()
        for item in self._items:
            hint = item.sizeHint()
            if x > rect.x() and x + hint.width() > rect.right() + 1:
                x, y, line_height = rect.x(), y + line_height + spacing, 0
            if not dry_run:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + spacing
            line_height = max(line_height, hint.height())
        return y + line_height - rect.y()


class FilterBar(QFrame):
    removeRequested = Signal(str)  # 取消一个条件："search" / "category" / "app" / "order" / "direction"
    resetRequested = Signal()  # 全部恢复默认

    def __init__(self, theme: dict[str, str]):
        super().__init__()
        self.setObjectName("filterBar")
        self.t = theme
        self._keys: list[str] = []
        self._chips = QWidget()
        self._flow = _FlowLayout()
        self._chips.setLayout(self._flow)
        reset = QPushButton("恢复默认")
        reset.setObjectName("link")
        reset.setIcon(glyph_icon(G.SYNC, theme["accent_text"], 13))
        reset.setCursor(Qt.CursorShape.PointingHandCursor)
        reset.setToolTip("清空搜索词，回到全部记录、全部应用、最新的在前")
        reset.clicked.connect(self.resetRequested)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 6, 4, 6)
        layout.setSpacing(8)
        layout.addWidget(self._chips, 1)
        layout.addWidget(reset, 0, Qt.AlignmentFlag.AlignTop)
        self.hide()

    @property
    def keys(self) -> list[str]:
        """当前显示的条件（测试和调试用）。"""
        return list(self._keys)

    def set_conditions(self, conditions: list[tuple[str, str, str]]) -> None:
        """conditions：[(键, 标签文字, 提示)]，为空时隐藏整条。"""
        while self._flow.count():
            widget = self._flow.takeAt(0).widget()
            if widget is not None:
                widget.hide()
                widget.deleteLater()
        self._keys = [key for key, _, _ in conditions]
        if not conditions:
            self.hide()
            return
        label = QLabel("筛选中")
        label.setObjectName("filterLabel")
        label.setMinimumHeight(26)
        self._flow.addWidget(label)
        for key, text, tip in conditions:
            chip = QToolButton()
            chip.setObjectName("filterChip")
            chip.setText(text)
            chip.setToolTip(f"{tip}（点击取消这个条件）")
            chip.setIcon(glyph_icon(G.CLOSE, self.t["accent_text"], 9))
            chip.setIconSize(QSize(9, 9))
            chip.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            chip.setLayoutDirection(Qt.LayoutDirection.RightToLeft)  # × 在文字右边
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.clicked.connect(lambda _checked=False, k=key: self.removeRequested.emit(k))
            self._flow.addWidget(chip)
        self.show()
        self._fit_height()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_height()

    def _fit_height(self) -> None:
        # 外层的垂直布局不会按“宽度决定高度”重新分配，标签换行后要自己把高度撑开
        width = self._chips.width() if self._chips.width() > 50 else self.width() - 110
        self._chips.setMinimumHeight(self._flow.heightForWidth(max(50, width)))
