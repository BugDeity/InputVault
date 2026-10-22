"""配置：数据目录和 config.json。"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

# 测试时可以用 INPUTVAULT_DATA_DIR 指到别的目录，避免污染真实数据
DATA_DIR = Path(
    os.environ.get("INPUTVAULT_DATA_DIR")
    or Path(os.environ.get("LOCALAPPDATA", Path.home())) / "InputVault"
)

DEFAULT_BLACKLIST = [
    "keepass.exe",
    "keepassxc.exe",
    "1password.exe",
    "bitwarden.exe",
    "lastpass.exe",
    "dashlane.exe",
    "mstsc.exe",
]


# 这些程序里的输入框内容不是“打的字”：终端里大部分是命令输出，资源管理器里是文件名和搜索框
DEFAULT_TYPING_BLACKLIST = [
    "windowsterminal.exe",
    "cmd.exe",
    "powershell.exe",
    "pwsh.exe",
    "conhost.exe",
    "explorer.exe",
]


@dataclass
class Config:
    hotkey: str = "ctrl+shift+v"
    blacklist_apps: list[str] = field(default_factory=lambda: list(DEFAULT_BLACKLIST))
    skip_sensitive: bool = True  # 身份证、银行卡、API Key、私钥不记录
    max_chars: int = 100_000  # 单条最多保存的字符数
    retention_days: int = 0  # 超过多少天的记录自动删除（收藏的除外），0 = 永久保留
    panel_limit: int = 200  # 面板里最多显示多少条
    capture_typing: bool = True  # 记录打字输入
    typing_blacklist_apps: list[str] = field(default_factory=lambda: list(DEFAULT_TYPING_BLACKLIST))
    typing_min_chars: int = 2  # 少于这么多字的输入不记录
    capture_files: bool = True  # 记录复制的文件（登记位置）
    capture_images: bool = True  # 记录复制的图片（保存图片本身）
    backup_max_mb: int = 20  # 不超过这个大小的文件自动备份；图片超过这个大小不保存
    blob_quota_mb: int = 2048  # 副本和图片总共最多占用的空间，超出时清理最久没用的


def save_config(cfg: Config, path: Path | None = None) -> None:
    path = path or DATA_DIR / "config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(cfg), ensure_ascii=False, indent=2), "utf-8")


def load_config(path: Path | None = None) -> Config:
    path = path or DATA_DIR / "config.json"
    if not path.exists():
        cfg = Config()
        save_config(cfg, path)
        return cfg
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        log.exception("config.json 读取失败，使用默认配置")
        return Config()
    known = {k: v for k, v in data.items() if k in Config.__dataclass_fields__}
    return Config(**known)
