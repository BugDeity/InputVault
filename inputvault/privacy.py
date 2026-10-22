"""隐私过滤：决定一条内容要不要被记录。"""
from __future__ import annotations

import re

_PRIVATE_KEY = re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")
_API_KEY = re.compile(
    r"\b(sk-(ant-|proj-)?[A-Za-z0-9_-]{20,}"
    r"|AKIA[0-9A-Z]{16}"
    r"|gh[pousr]_[A-Za-z0-9]{36,}"
    r"|github_pat_\w{22,}"
    r"|xox[abprs]-[A-Za-z0-9-]{10,}"
    r"|AIza[0-9A-Za-z_-]{35})"
)
_ID_CARD = re.compile(r"(?<![0-9])[1-9]\d{16}[0-9Xx](?![0-9A-Za-z])")
_BANK_CARD = re.compile(r"(?<!\d)[3-6]\d{15,18}(?!\d)")

_ID_WEIGHTS = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
_ID_CHECK = "10X98765432"


def _valid_id_card(s: str) -> bool:
    total = sum(int(c) * w for c, w in zip(s[:17], _ID_WEIGHTS))
    return _ID_CHECK[total % 11] == s[17].upper()


def _luhn(s: str) -> bool:
    total = 0
    for i, c in enumerate(reversed(s)):
        d = int(c)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def sensitive_reason(text: str) -> str | None:
    """内容里有敏感信息时返回类型名，否则返回 None。"""
    if _PRIVATE_KEY.search(text):
        return "私钥"
    if _API_KEY.search(text):
        return "API Key"
    if any(_valid_id_card(m.group()) for m in _ID_CARD.finditer(text)):
        return "身份证号"
    if any(_luhn(m.group()) for m in _BANK_CARD.finditer(text)):
        return "银行卡号"
    return None


def is_blacklisted(exe: str, blacklist: list[str]) -> bool:
    return bool(exe) and exe.lower() in {b.lower() for b in blacklist}


def skip_reason(text: str, exe: str, blacklist: list[str], skip_sensitive: bool) -> str | None:
    """不该记录时返回原因，可以记录时返回 None。"""
    if is_blacklisted(exe, blacklist):
        return f"黑名单应用 {exe}"
    if skip_sensitive:
        return sensitive_reason(text)
    return None
