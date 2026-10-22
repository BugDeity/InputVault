"""打字会话：把输入框内容的一连串快照，整理成“这次输入了什么”。

一次会话 = 焦点进入某个输入框到离开它。进入时的内容是基准，只记录在基准之上新增的文字，
这样在一篇长文档里改几句话，记下的也只是那几句。聊天软件发送后会清空输入框，
检测到“有字 -> 空”时，把清空前的内容存为一条，并开始新的一轮。
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from difflib import SequenceMatcher

# 两处改动之间相同的部分少于这么多字时，合并成一段，避免被零星的相同字切碎
_MERGE_GAP = 4
_DIFF_LIMIT = 20_000


@dataclass(frozen=True)
class FieldContext:
    key: str  # 输入框的唯一标识
    exe: str
    app: str
    title: str


def extract_added(before: str, after: str) -> str:
    """after 相对 before 新增或改写的文字；多处改动用换行分开。"""
    if not before:
        return after
    if after == before:
        return ""
    # 先去掉相同的开头和结尾，大多数编辑只动了中间一段
    limit = min(len(before), len(after))
    prefix = 0
    while prefix < limit and before[prefix] == after[prefix]:
        prefix += 1
    suffix = 0
    while suffix < limit - prefix and before[-1 - suffix] == after[-1 - suffix]:
        suffix += 1
    old_mid = before[prefix : len(before) - suffix]
    new_mid = after[prefix : len(after) - suffix]
    if not new_mid or not old_mid or len(old_mid) + len(new_mid) > _DIFF_LIMIT:
        return new_mid

    spans: list[list[int]] = []
    matcher = SequenceMatcher(None, old_mid, new_mid, autojunk=False)
    for tag, _i1, _i2, j1, j2 in matcher.get_opcodes():
        if tag not in ("insert", "replace"):
            continue
        if spans and j1 - spans[-1][1] < _MERGE_GAP:
            spans[-1][1] = j2
        else:
            spans.append([j1, j2])
    return "\n".join(new_mid[a:b] for a, b in spans)


class TypingTracker:
    """sink(content, ctx) 在一段输入结束时被调用，由调用方决定怎么保存。"""

    def __init__(self, sink: Callable[[str, FieldContext], None], min_chars: int = 2):
        self._sink = sink
        self.min_chars = min_chars
        self._ctx: FieldContext | None = None
        self._baseline = ""
        self._last = ""

    def observe(self, ctx: FieldContext | None, text: str | None) -> None:
        """每次读到输入框内容时调用；ctx 为 None 表示焦点不在可记录的输入框上。"""
        if ctx is None or text is None:
            self.end()
            return
        if self._ctx is None or ctx.key != self._ctx.key:
            self.end()
            self._ctx, self._baseline, self._last = ctx, text, text
            return
        self._ctx = ctx  # 窗口标题可能变了，用最新的
        if not text.strip() and self._last.strip():
            # 输入框被清空：聊天软件发送消息后会这样
            self._commit()
            self._baseline = text
        self._last = text

    def end(self) -> None:
        """结束当前会话（焦点离开、暂停、退出时调用）。"""
        if self._ctx is not None:
            self._commit()
        self._ctx = None
        self._baseline = self._last = ""

    def _commit(self) -> None:
        added = extract_added(self._baseline, self._last).strip()
        if len(added) >= self.min_chars and self._ctx is not None:
            self._sink(added, self._ctx)
        self._baseline = self._last
