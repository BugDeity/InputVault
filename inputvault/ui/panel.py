"""InputVault 主窗口：左侧分类和应用来源，中间记录列表，右侧详情。

两种打开方式：
- 快捷键唤起：像弹窗一样，聚焦搜索框，回车把内容粘贴回唤起前的窗口，点到别处自动收起；
- 从托盘打开：普通窗口，可以最小化、最大化，点到别处也不会消失。
窗口没有系统标题栏：拖顶部空白处移动，拖四周的阴影边缘改变大小，双击顶部空白处最大化。
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QCursor, QGuiApplication, QLinearGradient, QPainter, QPainterPath
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .. import win32
from ..collector import friendly_app_name
from ..restore import Restorer
from ..storage import CATEGORY_KINDS, Clip, Storage
from .clip_list import ClipList, Thumbnails
from .detail import DetailPane
from .icons import AppIcons, G, glyph_icon, tile_pixmap
from .sidebar import CATEGORY_LABELS, OTHERS, Sidebar
from .theme import app_icon, window_stylesheet

_SHADOW = 16  # 四周给阴影留的透明边，也是拖动改变大小的区域
_RADIUS = 16
_RESIZE_INSIDE = 4  # 窗口边缘往里这么多像素也可以拖动改变大小
_PAGE = 8


class Panel(QWidget):
    pasteRequested = Signal(object, bool)  # (Clip, 是否自动粘贴到原窗口)
    pasteAsFileRequested = Signal(object)  # 图片以文件形式粘贴
    openLocationRequested = Signal(object)  # 打开文件所在位置
    openSourceRequested = Signal(object)  # 打开来源应用
    saveImageRequested = Signal(object)  # 图片另存为
    blockAppRequested = Signal(str, str)  # (exe, 显示名)：不再记录这个应用

    def __init__(self, storage: Storage, restorer: Restorer, app_icons: AppIcons,
                 theme: dict[str, str], limit: int = 200):
        super().__init__(None, Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        self.setWindowTitle("InputVault")
        self.setWindowIcon(app_icon())
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMouseTracking(True)
        self.setMinimumSize(1040 + 2 * _SHADOW, 620 + 2 * _SHADOW)
        self.resize(1240 + 2 * _SHADOW, 780 + 2 * _SHADOW)
        self.storage = storage
        self.restorer = restorer
        self.t = theme
        self.limit = limit
        self.target_hwnd = 0
        self.target_exe = ""
        self.target_app = ""
        self.category = "all"
        self.app_filter: str | None = None  # exe / OTHERS / None
        self.order = "time"
        self.ascending = False
        self._quick = False  # 快捷键唤起的：失去焦点就收起
        self._user_placed = False  # 用户拖动 / 改变过大小后，再打开时不再自动居中
        self.thumbs = Thumbnails(restorer, theme)

        self._build(app_icons)
        self.setStyleSheet(window_stylesheet(theme))
        self._apply_margins()

        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(120)
        self._search_timer.timeout.connect(self.refresh)
        self.search.textChanged.connect(self._search_timer.start)

    # ---------- 布局 ----------

    def _build(self, app_icons: AppIcons) -> None:
        t = self.t
        self._root = QVBoxLayout(self)
        self._root.setSpacing(10)

        chrome = QHBoxLayout()
        chrome.setSpacing(2)
        chrome.addStretch(1)
        self.max_button = None
        for glyph, slot, name, tip in (
            (G.MINIMIZE, self.showMinimized, "chrome", "最小化"),
            (G.MAXIMIZE, self._toggle_maximized, "chrome", "最大化"),
            (G.CLOSE, self.hide, "close", "关闭（程序继续在托盘运行）"),
        ):
            button = QToolButton()
            button.setObjectName(name)
            button.setIcon(glyph_icon(glyph, t["fg_soft"], 10))
            button.setIconSize(QSize(10, 10))
            button.setToolTip(tip)
            button.clicked.connect(slot)
            chrome.addWidget(button)
            if glyph == G.MAXIMIZE:
                self.max_button = button
        self._root.addLayout(chrome)

        top = QHBoxLayout()
        top.setSpacing(16)
        self.logo = QLabel()
        self.logo.setFixedSize(52, 52)
        self.logo.setPixmap(tile_pixmap(G.DOC, t["accent"], "#FFFFFF", 52, 2.0, 14, 24))
        self.search = QLineEdit()
        self.search.setObjectName("search")
        self.search.setPlaceholderText("搜索复制和输入过的内容…    过滤：app:微信   type:链接   src:打字")
        self.search.setClearButtonEnabled(True)
        self.search.addAction(glyph_icon(G.SEARCH, t["fg_soft"], 18), QLineEdit.ActionPosition.LeadingPosition)
        self.search.setMinimumHeight(46)
        self.search.installEventFilter(self)
        self.scope = QToolButton()
        self.scope.setObjectName("primary")
        self.scope.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.scope.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.scope.setLayoutDirection(Qt.LayoutDirection.RightToLeft)  # 箭头在文字右边
        self.scope.setIcon(glyph_icon(G.DOWN, "#FFFFFF", 12))
        self.scope.setMinimumHeight(46)
        self.scope_menu = QMenu(self)
        self.scope_menu.aboutToShow.connect(self._fill_scope_menu)
        self.scope.setMenu(self.scope_menu)
        top.addWidget(self.logo)
        top.addWidget(self.search, 1)
        top.addWidget(self.scope)
        self._root.addLayout(top)
        self._root.addSpacing(6)

        body = QHBoxLayout()
        body.setSpacing(16)
        self.sidebar = Sidebar(t, app_icons)
        self.sidebar.setFixedWidth(232)
        self.sidebar.categoryChanged.connect(self._set_category)
        self.sidebar.appChanged.connect(self._set_app_filter)
        body.addWidget(self.sidebar)

        list_card = QFrame()
        list_card.setObjectName("card")
        list_layout = QVBoxLayout(list_card)
        list_layout.setContentsMargins(14, 16, 10, 8)
        list_layout.setSpacing(6)
        header = QHBoxLayout()
        header.setContentsMargins(6, 0, 4, 0)
        self.list_title = QLabel("全部记录")
        self.list_title.setObjectName("h1")
        self.list_count = QLabel()
        self.list_count.setObjectName("muted")
        self.direction = QToolButton()
        self.direction.setObjectName("flat")
        self.direction.setIcon(glyph_icon(G.SORT, t["fg_soft"], 16))
        self.direction.setIconSize(QSize(16, 16))
        self.direction.clicked.connect(self._toggle_direction)
        self.sort = QToolButton()
        self.sort.setObjectName("pill")
        self.sort.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.sort.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.sort.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.sort.setIcon(glyph_icon(G.DOWN, t["fg_soft"], 11))
        sort_menu = QMenu(self)
        for order, label in (("time", "按时间排序"), ("count", "按使用次数排序")):
            sort_menu.addAction(label, lambda o=order: self._set_order(o))
        self.sort.setMenu(sort_menu)
        header.addWidget(self.list_title)
        header.addSpacing(6)
        header.addWidget(self.list_count, 0, Qt.AlignmentFlag.AlignBottom)
        header.addStretch(1)
        header.addWidget(self.direction)
        header.addWidget(self.sort)
        list_layout.addLayout(header)
        self.list = ClipList(t, self.thumbs, self._drag_mime)
        self.list.currentItemChanged.connect(lambda *_: self._show_current())
        self.list.itemDoubleClicked.connect(lambda _item: self._do("paste" if self._can_paste() else "copy"))
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._context_menu)
        self.list.moreRequested.connect(lambda clip, pos: self.menu_for(clip).exec(pos))
        list_layout.addWidget(self.list, 1)

        self.detail = DetailPane(t, self.thumbs, self.restorer, self.menu_for)
        self.detail.action.connect(self._do)
        # 两栏宽度严格按比例分，不让详情里按钮的宽度把列表挤窄
        for card, minimum, stretch in ((list_card, 380, 1), (self.detail, 420, 1)):
            card.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            card.setMinimumWidth(minimum)
            body.addWidget(card, stretch)
        self._root.addLayout(body, 1)
        self._update_sort_controls()
        self._update_scope_button()

    def _apply_margins(self) -> None:
        m = 0 if self.isMaximized() else _SHADOW
        self._root.setContentsMargins(m + 22, m + 8, m + 22, m + 20)
        if self.max_button is not None:
            maximized = self.isMaximized()
            self.max_button.setIcon(glyph_icon(G.RESTORE if maximized else G.MAXIMIZE, self.t["fg_soft"], 10))
            self.max_button.setToolTip("还原" if maximized else "最大化")
        self.update()

    # ---------- 打开 / 关闭 ----------

    def open(self, target_hwnd: int, quick: bool = True) -> None:
        """target_hwnd 是唤起前的前台窗口，“引用到输入框”时粘贴回去；为 0 时只能复制。"""
        self._quick = quick
        self.target_hwnd = target_hwnd
        self.target_exe = win32.process_name(target_hwnd)
        self.target_app = friendly_app_name(self.target_exe)
        if quick:  # 快捷键唤起每次从“全部”开始，结果可预期
            self.category, self.app_filter = "all", None
            self.sidebar.select_category("all")
            self.sidebar.select_app(None, notify=False)
            self._update_scope_button()
        self.search.blockSignals(True)
        self.search.clear()
        self.search.blockSignals(False)
        self.refresh_sidebar()
        self.refresh()
        if self.isMinimized():
            self.showNormal()
        if not self._user_placed or not QGuiApplication.screenAt(self.geometry().center()):
            screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
            geo = screen.availableGeometry()
            self.resize(min(self.width(), geo.width()), min(self.height(), geo.height()))
            self.move(geo.center() - self.rect().center())
        self.show()
        self.raise_()
        self.activateWindow()
        win32.force_foreground(int(self.winId()))
        self.search.setFocus()

    def changeEvent(self, event):
        if event.type() == QEvent.Type.WindowStateChange:
            self._apply_margins()
        elif event.type() == QEvent.Type.ActivationChange:
            if self._quick and self.isVisible() and not self.isActiveWindow():
                self.hide()
        super().changeEvent(event)

    def _toggle_maximized(self) -> None:
        self.showNormal() if self.isMaximized() else self.showMaximized()

    # ---------- 无边框窗口：画背景、移动、改变大小 ----------

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        m = 0 if self.isMaximized() else _SHADOW
        radius = 0 if self.isMaximized() else _RADIUS
        rect = self.rect().adjusted(m, m, -m, -m)
        if m:
            # 一圈圈越来越淡的描边当作阴影：比 QGraphicsDropShadowEffect 便宜得多，滚动列表也不卡
            painter.setBrush(Qt.BrushStyle.NoBrush)
            for i in range(1, m + 1):
                alpha = round(26 * (1 - i / (m + 1)) ** 2)
                painter.setPen(QColor(15, 30, 60, alpha))
                painter.drawRoundedRect(rect.adjusted(-i, -i + 2, i, i + 2), radius + i, radius + i)
        gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
        gradient.setColorAt(0, QColor(self.t["window"]))
        gradient.setColorAt(1, QColor(self.t["window2"]))
        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)
        painter.fillPath(path, gradient)
        painter.setPen(QColor(self.t["border"]))
        painter.drawPath(path)

    def _edges(self, pos: QPoint) -> Qt.Edge:
        edges = Qt.Edge(0)
        if self.isMaximized():
            return edges
        zone = _SHADOW + _RESIZE_INSIDE
        if pos.x() < zone:
            edges |= Qt.Edge.LeftEdge
        if pos.x() > self.width() - zone:
            edges |= Qt.Edge.RightEdge
        if pos.y() < zone:
            edges |= Qt.Edge.TopEdge
        if pos.y() > self.height() - zone:
            edges |= Qt.Edge.BottomEdge
        return edges

    def _in_drag_zone(self, pos: QPoint) -> bool:
        bottom = self.search.mapTo(self, QPoint(0, self.search.height())).y() + 10
        return pos.y() <= bottom

    def mouseMoveEvent(self, event):
        edges = self._edges(event.position().toPoint())
        cursors = {
            Qt.Edge.LeftEdge: Qt.CursorShape.SizeHorCursor,
            Qt.Edge.RightEdge: Qt.CursorShape.SizeHorCursor,
            Qt.Edge.TopEdge: Qt.CursorShape.SizeVerCursor,
            Qt.Edge.BottomEdge: Qt.CursorShape.SizeVerCursor,
            Qt.Edge.LeftEdge | Qt.Edge.TopEdge: Qt.CursorShape.SizeFDiagCursor,
            Qt.Edge.RightEdge | Qt.Edge.BottomEdge: Qt.CursorShape.SizeFDiagCursor,
            Qt.Edge.RightEdge | Qt.Edge.TopEdge: Qt.CursorShape.SizeBDiagCursor,
            Qt.Edge.LeftEdge | Qt.Edge.BottomEdge: Qt.CursorShape.SizeBDiagCursor,
        }
        if edges in cursors:
            self.setCursor(cursors[edges])
        else:
            self.unsetCursor()
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.windowHandle() is not None:
            pos = event.position().toPoint()
            edges = self._edges(pos)
            if edges:
                self._user_placed = True
                self.windowHandle().startSystemResize(edges)
                return
            if self._in_drag_zone(pos):
                self._user_placed = True
                self.windowHandle().startSystemMove()
                return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._in_drag_zone(event.position().toPoint()):
            self._toggle_maximized()
            return
        super().mouseDoubleClickEvent(event)

    # ---------- 筛选和列表 ----------

    def _filter_kwargs(self) -> dict:
        app = self.app_filter
        return {
            "source_exe": app if app not in (None, OTHERS) else None,
            "kinds": CATEGORY_KINDS.get(self.category),
            "pinned_only": self.category == "pinned",
            "exclude_exes": self.sidebar.top_exes if app == OTHERS else (),
        }

    def _app_name(self, exe: str | None) -> str:
        if exe is None:
            return "全部应用"
        if exe == OTHERS:
            return "其他应用"
        return friendly_app_name(exe)

    def refresh(self, keep_id: int | None = None, keep_index: int | None = None) -> None:
        query = self.search.text()
        kwargs = self._filter_kwargs()
        clips = self.storage.search(query, self.limit, order=self.order, ascending=self.ascending, **kwargs)
        self.list.populate(clips, group_by_day=self.order == "time")
        title = "全部记录" if self.category == "all" else CATEGORY_LABELS[self.category]
        if self.app_filter is not None:
            title = f"{self._app_name(self.app_filter)} · {title}"
        self.list_title.setText(title)
        self.list_count.setText(f"{self.storage.count_matching(query, **kwargs)} 条")
        if clips:
            self.list.select_clip(keep_id, keep_index)
        else:
            self._show_current()

    def refresh_sidebar(self) -> None:
        self.sidebar.set_counts(self.storage.count(), self.storage.pinned_count(), self.storage.kind_counts())
        self.sidebar.set_apps(self.storage.app_counts())

    def _set_category(self, key: str) -> None:
        self.category = key
        self.refresh()

    def _set_app_filter(self, exe: object) -> None:
        self.app_filter = exe
        self._update_scope_button()
        self.refresh()

    def _choose_app(self, exe: str | None) -> None:
        """顶部按钮、Tab 键选择应用时，左侧栏也跟着变。"""
        self.sidebar.select_app(exe, notify=False)
        self._set_app_filter(exe)

    def _update_scope_button(self) -> None:
        self.scope.setText(self._app_name(self.app_filter))

    def _fill_scope_menu(self) -> None:
        menu = self.scope_menu
        menu.clear()
        menu.addAction("全部应用", lambda: self._choose_app(None))
        current = menu.addAction(
            f"只看当前应用（{self.target_app}）\tTab" if self.target_exe else "只看当前应用（用快捷键唤起时可用）",
            lambda: self._choose_app(self.target_exe),
        )
        current.setEnabled(bool(self.target_exe))
        apps = self.storage.app_counts()[:12]
        if apps:
            menu.addSeparator()
            for exe, name, count in apps:
                menu.addAction(f"{name}\t{count}", lambda e=exe: self._choose_app(e))

    def _toggle_current_app(self) -> None:
        if self.target_exe:
            same = isinstance(self.app_filter, str) and self.app_filter.lower() == self.target_exe.lower()
            self._choose_app(None if same else self.target_exe)

    def _set_order(self, order: str) -> None:
        self.order = order
        self._update_sort_controls()
        self.refresh()

    def _toggle_direction(self) -> None:
        self.ascending = not self.ascending
        self._update_sort_controls()
        self.refresh()

    def _update_sort_controls(self) -> None:
        self.sort.setText("按时间排序" if self.order == "time" else "按使用次数排序")
        if self.order == "time":
            tip = "现在：最早的在前" if self.ascending else "现在：最新的在前"
        else:
            tip = "现在：用得最少的在前" if self.ascending else "现在：用得最多的在前"
        self.direction.setToolTip(f"{tip}（点击切换）")

    def _show_current(self) -> None:
        clip = self.list.current_clip()
        if clip is not None:
            self.detail.show_clip(clip, self._can_paste(), self.target_app)
        elif self.storage.count() == 0:
            self.detail.show_empty("还没有记录\n复制一段文字、文件或截图试试")
        else:
            self.detail.show_empty("没有符合条件的记录")

    def _can_paste(self) -> bool:
        return win32.is_window(self.target_hwnd) and self.target_hwnd != int(self.winId())

    # ---------- 操作 ----------

    def _drag_mime(self, clip: Clip):
        mime, _missing = self.restorer.mime_for(clip, with_file=True)
        if mime is not None:
            self.storage.touch(clip.id)
        return mime

    def _do(self, action: str, clip: Clip | None = None) -> None:
        clip = clip or self.list.current_clip()
        if clip is None:
            return
        if action == "paste":
            if self._can_paste():
                self.pasteRequested.emit(clip, True)
        elif action == "copy":
            self.pasteRequested.emit(clip, False)
        elif action == "paste_file":
            self.pasteAsFileRequested.emit(clip)
        elif action == "open_location" and clip.kind == "file":
            self.openLocationRequested.emit(clip)
        elif action == "open_source" and clip.source_exe:
            self.openSourceRequested.emit(clip)
        elif action == "save_image" and clip.kind == "image":
            self.saveImageRequested.emit(clip)
        elif action == "pin":
            self.storage.toggle_pin(clip.id)
            self.refresh(keep_id=clip.id)
            self.refresh_sidebar()
        elif action == "delete":
            index = self.list.clip_index()
            self.storage.delete(clip.id)
            self.refresh(keep_index=index)
            self.refresh_sidebar()
        elif action == "block" and clip.source_exe:
            self.blockAppRequested.emit(clip.source_exe, clip.source_app or clip.source_exe)

    def menu_for(self, clip: Clip) -> QMenu:
        """右键、每行的“⋯”、详情右上角的“⋯”共用这一个菜单。"""
        menu = QMenu(self)
        color = self.t["fg_soft"]

        def add(text: str, glyph: str, action: str, enabled: bool = True):
            item = menu.addAction(glyph_icon(glyph, color, 16), text, lambda: self._do(action, clip))
            item.setEnabled(enabled)

        add("引用到输入框\tEnter", G.REPLY, "paste", self._can_paste())
        add("复制内容\tShift+Enter", G.COPY, "copy")
        if clip.kind == "image":
            add("以文件形式粘贴", G.PAGE, "paste_file")
            add("另存为…", G.SAVE, "save_image")
        if clip.kind == "file":
            add("打开所在位置\tCtrl+O", G.FOLDER_OPEN, "open_location")
        if clip.source_exe:
            add("打开来源应用", G.OPEN, "open_source")
        menu.addSeparator()
        add("取消置顶\tCtrl+P" if clip.pinned else "置顶\tCtrl+P", G.PIN, "pin")
        add("删除\tCtrl+D", G.DELETE, "delete")
        if clip.source_exe:
            menu.addSeparator()
            add(f"不再记录「{clip.source_app or clip.source_exe}」", G.BLOCK, "block")
        return menu

    def _context_menu(self, pos) -> None:
        item = self.list.itemAt(pos)
        clip = item.data(Qt.ItemDataRole.UserRole) if item else None
        if clip is None:
            return
        self.list.setCurrentItem(item)
        self.menu_for(clip).exec(self.list.viewport().mapToGlobal(pos))

    def eventFilter(self, obj, event):
        if obj is self.search and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            mods = event.modifiers()
            ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
            steps = {Qt.Key.Key_Down: 1, Qt.Key.Key_Up: -1, Qt.Key.Key_PageDown: _PAGE, Qt.Key.Key_PageUp: -_PAGE}
            if key in steps:
                self.list.move(steps[key])
                return True
            if key == Qt.Key.Key_Tab:
                self._toggle_current_app()
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                copy_only = bool(mods & Qt.KeyboardModifier.ShiftModifier) or not self._can_paste()
                self._do("copy" if copy_only else "paste")
                return True
            if key == Qt.Key.Key_Escape:
                self.hide()
                return True
            shortcuts = {Qt.Key.Key_O: "open_location", Qt.Key.Key_P: "pin", Qt.Key.Key_D: "delete"}
            if ctrl and key in shortcuts:
                self._do(shortcuts[key])
                return True
        return super().eventFilter(obj, event)
