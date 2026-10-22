"""识别内容类型。规则尽量保守：拿不准就归为普通文本。"""
from __future__ import annotations

import json
import re

KIND_LABELS = {
    "text": "文本",
    "url": "链接",
    "email": "邮箱",
    "phone": "电话",
    "path": "路径",
    "json": "JSON",
    "code": "代码",
    "number": "数字",
    # 下面两种不是从文字识别出来的，复制文件 / 图片时直接指定
    "file": "文件",
    "image": "图片",
}

_URL = re.compile(r"^(https?|ftp)://\S+$", re.IGNORECASE)
_EMAIL = re.compile(r"^[\w.+-]+@[\w-]+(\.[\w-]+)+$")
_PHONE = re.compile(r"^(\+?86-?)?1[3-9]\d{9}$|^0\d{2,3}-?\d{7,8}$")
_PATH = re.compile(r'^([a-zA-Z]:[\\/]|\\\\)[^<>"|?*\r\n]*$')
_NUMBER = re.compile(r"^[-+]?(\d{1,3}(,\d{3})+|\d+)(\.\d+)?%?$")
_CODE_LINE = re.compile(
    r"^\s*(def |class |import |from \S+ import|function\b|const |let |var |public |private |"
    r"return\b|#include|package |using |fn |func |(?i:select |insert |update |delete from|create table))"
    r"|[{};]\s*$|=>|\)\s*\{|^\s*</?[a-zA-Z][\w-]*[\s/>]"
)


def classify(text: str) -> str:
    s = text.strip()
    if not s:
        return "text"
    if "\n" not in s:
        if _URL.match(s):
            return "url"
        if _EMAIL.match(s):
            return "email"
        if _PHONE.match(s.replace(" ", "")):
            return "phone"
        if _PATH.match(s):
            return "path"
        if _NUMBER.match(s):
            return "number"
    if s[0] in "{[" and s[-1] in "}]":
        try:
            json.loads(s)
            return "json"
        except ValueError:
            pass
    lines = [line for line in s.splitlines() if line.strip()]
    hits = sum(1 for line in lines if _CODE_LINE.search(line))
    if hits >= 2 and hits / len(lines) >= 0.3:
        return "code"
    return "text"
