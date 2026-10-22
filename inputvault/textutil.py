"""界面上用到的文字格式化。"""
from __future__ import annotations

import re
import time

_LINE_BREAKS = re.compile(r"\s*\n\s*")
_SPACES = re.compile(r"[ \t\r\f\v]+")


def preview_line(content: str, max_len: int = 300) -> str:
    """把多行内容压成一行预览，换行显示为 ⏎。"""
    s = _LINE_BREAKS.sub(" ⏎ ", content.strip())
    return _SPACES.sub(" ", s)[:max_len]


def human_size(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


_WEEKDAYS = "一二三四五六日"


def day_label(ts: float, now: float | None = None) -> str:
    """列表里的日期分组标题：今天 / 昨天 / 10月20日 周一 / 2025年10月20日。"""
    now = time.time() if now is None else now
    t, today = time.localtime(ts), time.localtime(now)
    yesterday = time.localtime(now - 86400)
    if (t.tm_year, t.tm_yday) == (today.tm_year, today.tm_yday):
        return "今天"
    if (t.tm_year, t.tm_yday) == (yesterday.tm_year, yesterday.tm_yday):
        return "昨天"
    if t.tm_year == today.tm_year:
        return f"{t.tm_mon}月{t.tm_mday}日 周{_WEEKDAYS[t.tm_wday]}"
    return f"{t.tm_year}年{t.tm_mon}月{t.tm_mday}日"


def relative_time(ts: float, now: float | None = None) -> str:
    now = time.time() if now is None else now
    delta = now - ts
    if delta < 60:
        return "刚刚"
    if delta < 3600:
        return f"{int(delta // 60)} 分钟前"
    t, today = time.localtime(ts), time.localtime(now)
    yesterday = time.localtime(now - 86400)
    if (t.tm_year, t.tm_yday) == (today.tm_year, today.tm_yday):
        return time.strftime("今天 %H:%M", t)
    if (t.tm_year, t.tm_yday) == (yesterday.tm_year, yesterday.tm_yday):
        return time.strftime("昨天 %H:%M", t)
    if t.tm_year == today.tm_year:
        return time.strftime("%m-%d %H:%M", t)
    return time.strftime("%Y-%m-%d", t)
