"""浅色 / 深色配色和样式表。浅色版照着设计稿：浅蓝灰背景、白色卡片、蓝色主按钮。"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication

LIGHT = {
    "window": "#EDF2FA",
    "window2": "#F6F8FD",
    "card": "#FFFFFF",
    "border": "#E4E9F2",
    "divider": "#EEF1F6",
    "fg": "#1D2433",
    "fg_soft": "#3A4356",
    "muted": "#8C95A6",
    "accent": "#2F7BF6",
    "accent_hover": "#1E6CEB",
    "accent_soft": "#E8F1FE",
    "accent_text": "#2A6FE8",
    "sel": "#EAF2FF",
    "nav_sel": "#E1EBFC",
    "hover": "#F3F6FB",
    "box": "#F5F7FB",
    "search_border": "#C9D9F6",
    "danger": "#E5484D",
    "star": "#F5A623",  # 收藏的星标
    # 类型图标的底色 / 图标色
    "tile_blue": "#EAF2FF",
    "tile_blue_fg": "#2F7BF6",
    "tile_folder": "#FFF3DC",
    "tile_folder_fg": "#F2A53A",
    "tile_image": "#E7F6EE",
    "tile_image_fg": "#1FA463",
    "tile_code": "#F0EBFF",
    "tile_code_fg": "#7B5CF0",
    "tile_gray": "#F1F3F7",
    "tile_gray_fg": "#7A8394",
}

DARK = {
    "window": "#15171C",
    "window2": "#1B1E24",
    "card": "#22252C",
    "border": "#30343C",
    "divider": "#2C3038",
    "fg": "#E7EAF0",
    "fg_soft": "#C5CAD3",
    "muted": "#8D94A1",
    "accent": "#4C8DFF",
    "accent_hover": "#6AA0FF",
    "accent_soft": "#233452",
    "accent_text": "#8DB6FF",
    "sel": "#26364F",
    "nav_sel": "#24324A",
    "hover": "#2A2E36",
    "box": "#1C1F25",
    "search_border": "#3A4A66",
    "danger": "#FF6B6B",
    "star": "#FFC24D",
    "tile_blue": "#233452",
    "tile_blue_fg": "#7FB0FF",
    "tile_folder": "#3D3220",
    "tile_folder_fg": "#F5B957",
    "tile_image": "#1F3A2D",
    "tile_image_fg": "#4FD08F",
    "tile_code": "#2F2850",
    "tile_code_fg": "#A891FF",
    "tile_gray": "#2C3038",
    "tile_gray_fg": "#A0A7B4",
}


def current_theme(app: QApplication) -> dict[str, str]:
    return DARK if app.styleHints().colorScheme() == Qt.ColorScheme.Dark else LIGHT


def window_stylesheet(t: dict[str, str]) -> str:
    return f"""
    QWidget {{ color: {t['fg']}; }}
    #card {{ background: {t['card']}; border: 1px solid {t['border']}; border-radius: 14px; }}
    #search {{
        background: {t['card']}; color: {t['fg']};
        border: 1px solid {t['search_border']}; border-radius: 12px;
        padding: 9px 12px; font-size: 15px;
        selection-background-color: {t['accent']};
    }}
    #search:focus {{ border: 1px solid {t['accent']}; }}
    QPushButton#primary, QToolButton#primary {{
        background: {t['accent']}; color: white; border: none; border-radius: 10px;
        padding: 9px 18px; font-size: 14px;
    }}
    QPushButton#primary:hover, QToolButton#primary:hover {{ background: {t['accent_hover']}; }}
    QToolButton#primary::menu-indicator {{ image: none; width: 0; }}
    QPushButton#primary {{ padding: 9px 10px; }}
    QPushButton#secondary {{
        background: {t['box']}; color: {t['fg']}; border: 1px solid {t['border']}; border-radius: 10px;
        padding: 9px 10px; font-size: 14px;
    }}
    QPushButton#secondary:hover {{ background: {t['hover']}; border-color: {t['search_border']}; }}
    QPushButton:disabled {{ color: {t['muted']}; }}
    QToolButton#flat {{
        background: transparent; border: none; border-radius: 8px; padding: 5px 8px; color: {t['fg_soft']};
    }}
    QToolButton#flat:hover {{ background: {t['hover']}; }}
    QToolButton#flat::menu-indicator {{ image: none; width: 0; }}
    QToolButton#pill {{
        background: {t['box']}; border: 1px solid {t['border']}; border-radius: 8px;
        padding: 5px 10px; color: {t['fg_soft']};
    }}
    QToolButton#pill:hover {{ border-color: {t['search_border']}; }}
    QToolButton#pill::menu-indicator {{ image: none; width: 0; }}
    QToolButton#chrome {{ background: transparent; border: none; border-radius: 6px; padding: 4px 10px; }}
    QToolButton#chrome:hover {{ background: {t['hover']}; }}
    QToolButton#close:hover {{ background: {t['danger']}; }}
    QToolButton#close {{ background: transparent; border: none; border-radius: 6px; padding: 4px 10px; }}
    QLabel#h1 {{ font-size: 18px; font-weight: 600; }}
    QLabel#h2 {{ font-size: 17px; font-weight: 600; }}
    QLabel#h3 {{ font-size: 14px; font-weight: 600; }}
    QLabel#muted, QLabel#section {{ color: {t['muted']}; }}
    QLabel#section {{ font-size: 13px; padding-left: 10px; }}
    QLabel#chip {{
        background: {t['accent_soft']}; color: {t['accent_text']};
        border-radius: 6px; padding: 2px 10px; font-size: 13px;
    }}
    QLabel#infoKey {{ color: {t['muted']}; }}
    QFrame#divider {{ background: {t['divider']}; max-height: 1px; min-height: 1px; border: none; }}
    QListWidget {{ background: transparent; border: none; outline: none; }}
    #contentBox {{ background: {t['box']}; border: none; border-radius: 10px; }}
    QPlainTextEdit#content {{
        background: transparent; border: none; padding: 6px; font-size: 14px; color: {t['fg']};
        selection-background-color: {t['accent']};
    }}
    QMenu {{
        background: {t['card']}; color: {t['fg']}; border: 1px solid {t['border']};
        border-radius: 10px; padding: 6px;
    }}
    QMenu::item {{ padding: 7px 22px 7px 14px; border-radius: 6px; }}
    QMenu::item:selected {{ background: {t['sel']}; color: {t['fg']}; }}
    QMenu::item:disabled {{ color: {t['muted']}; }}
    QMenu::separator {{ height: 1px; background: {t['divider']}; margin: 5px 8px; }}
    QScrollBar:vertical {{ background: transparent; width: 8px; margin: 2px; }}
    QScrollBar::handle:vertical {{ background: {t['border']}; border-radius: 3px; min-height: 30px; }}
    QScrollBar::handle:vertical:hover {{ background: {t['muted']}; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: none; }}
    QToolTip {{ background: {t['card']}; color: {t['fg']}; border: 1px solid {t['border']}; padding: 4px 8px; }}
    """


def app_icon(paused: bool = False) -> QIcon:
    """托盘和窗口图标。"""
    pm = QPixmap(64, 64)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#9CA3AF" if paused else "#2F7BF6"))
    p.drawRoundedRect(4, 4, 56, 56, 14, 14)
    p.setPen(QColor("white"))
    p.setFont(QFont("Segoe UI", 28, QFont.Weight.Bold))
    p.drawText(pm.rect(), Qt.AlignmentFlag.AlignCenter, "V")
    p.end()
    return QIcon(pm)
