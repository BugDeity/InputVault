"""UI Automation 封装：找到焦点所在的输入框，读出里面的文字。

Automation 对象只能在创建它的线程里用（COM 线程模型），后台线程要先 CoInitializeEx。
"""
from __future__ import annotations

import warnings
from dataclasses import dataclass

with warnings.catch_warnings():
    # comtypes 对 Python 3.15 的枚举行为有一条提示，实测用到的接口不受影响
    warnings.simplefilter("ignore", FutureWarning)
    import comtypes
    import comtypes.client

_module = None


def _uia():
    global _module
    if _module is None:
        comtypes.client.GetModule("UIAutomationCore.dll")
        from comtypes.gen import UIAutomationClient

        _module = UIAutomationClient
    return _module


EDIT, DOCUMENT = 50004, 50030
_OBJECT_REPLACEMENT = "￼"
CONTROL_TYPE_NAMES = {
    EDIT: "Edit",
    DOCUMENT: "Document",
    50003: "ComboBox",
    50025: "Custom",
    50026: "Group",
    50033: "Pane",
}


@dataclass(frozen=True)
class FieldInfo:
    key: str  # 进程 + RuntimeId，用来判断焦点有没有换到别的输入框
    pid: int
    hwnd: int
    control_type: int
    framework: str
    class_name: str
    is_password: bool
    editable: bool
    read_via: str  # "value" / "text"，读不到时为 ""
    text: str | None  # 不可编辑、密码框、读不到时为 None

    @property
    def control_type_name(self) -> str:
        return CONTROL_TYPE_NAMES.get(self.control_type, str(self.control_type))


class Automation:
    def __init__(self, timeout_ms: int = 1500, max_chars: int = 200_000):
        uia = _uia()
        self.max_chars = max_chars
        try:
            self._auto = comtypes.client.CreateObject(uia.CUIAutomation8, interface=uia.IUIAutomation2)
            # 目标程序卡死时，UIA 调用最多等这么久，不会把我们也拖死
            self._auto.ConnectionTimeout = timeout_ms
            self._auto.TransactionTimeout = timeout_ms
        except (OSError, AttributeError):
            self._auto = comtypes.client.CreateObject(uia.CUIAutomation, interface=uia.IUIAutomation)

    def focused_field(self) -> FieldInfo | None:
        """当前键盘焦点所在的控件；不是文字输入类控件时返回 None。

        读取出错（比如目标程序正忙，0x8001010D）时抛出 COMError，不返回 None：
        “读不到”和“焦点不在输入框上”要分开，否则一次偶发错误就会把正在打的一段话截成两条。
        """
        element = self._auto.GetFocusedElement()
        if not element:
            return None
        return self._inspect(element)

    def editable_descendants(self, hwnd: int, limit: int = 10) -> list[FieldInfo]:
        """窗口里所有 Edit / Document 控件（探测工具用）。"""
        uia = _uia()
        root = self._auto.ElementFromHandle(hwnd)
        condition = self._auto.CreateOrCondition(
            self._auto.CreatePropertyCondition(uia.UIA_ControlTypePropertyId, EDIT),
            self._auto.CreatePropertyCondition(uia.UIA_ControlTypePropertyId, DOCUMENT),
        )
        found = root.FindAll(uia.TreeScope_Descendants, condition)
        result = []
        for i in range(min(found.Length, limit)):
            info = self._inspect(found.GetElement(i))
            if info is not None:
                result.append(info)
        return result

    def _inspect(self, element) -> FieldInfo | None:
        uia = _uia()
        control_type = element.CurrentControlType
        value = _pattern(element, uia.UIA_ValuePatternId, uia.IUIAutomationValuePattern)
        text = _pattern(element, uia.UIA_TextPatternId, uia.IUIAutomationTextPattern)
        if value is None and text is None:
            return None

        info = dict(
            key=f"{element.CurrentProcessId}:" + ".".join(str(x) for x in element.GetRuntimeId()),
            pid=element.CurrentProcessId,
            hwnd=element.CurrentNativeWindowHandle or 0,
            control_type=control_type,
            framework=element.CurrentFrameworkId or "",
            class_name=element.CurrentClassName or "",
            is_password=bool(element.CurrentIsPassword),
        )
        if info["is_password"]:
            return FieldInfo(**info, editable=True, read_via="", text=None)

        # 有 ValuePattern 就只看它：只读状态是明确的。浏览器里网页本身是一个 ValuePattern 只读
        # （值是网址）、但 TextPattern 却报告“不是只读”的 Document，不能再退回去用 TextPattern，
        # 否则焦点停在网页上时，网页内容一变就会被当成打字
        if value is not None:
            if value.CurrentIsReadOnly:
                return FieldInfo(**info, editable=False, read_via="", text=None)
            return FieldInfo(**info, editable=True, read_via="value", text=value.CurrentValue or "")

        # 只有 TextPattern 的富文本控件：Word、网页里的 contenteditable（Chrome 报告为 Group）等
        document = text.DocumentRange
        if document.GetAttributeValue(uia.UIA_IsReadOnlyAttributeId) is False:
            # U+FFFC 是图片等嵌入对象的占位符，不是文字
            content = document.GetText(self.max_chars).replace(_OBJECT_REPLACEMENT, "")
            return FieldInfo(**info, editable=True, read_via="text", text=content)
        return FieldInfo(**info, editable=False, read_via="", text=None)


def _pattern(element, pattern_id: int, interface):
    # 控件不支持这个 pattern 时返回空指针；抛 COMError 是真的读取出错，交给调用方处理
    unknown = element.GetCurrentPattern(pattern_id)
    if not unknown:
        return None
    return unknown.QueryInterface(interface)
