"""复制的文件：复制时登记文件信息，还原时判断文件现在在哪、能不能还原。

还原的三层保障：原路径 → 用文件编号找到移动 / 改名后的新位置 → 复制时存下的副本。
"""
from __future__ import annotations

import enum
import os
import stat
from collections.abc import Callable
from dataclasses import asdict, dataclass

from . import win32

DETAIL_LIMIT = 1000  # 一次复制超过这么多个文件时，后面的不登记
BACKUP_COUNT_LIMIT = 50  # 一次复制最多自动备份这么多个文件
THUMB_MAX_BYTES = 100 * 1024 * 1024  # 超过这个大小的图片文件不做缩略图
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tif", ".tiff", ".ico"}
# 密码库、私钥、证书这类文件不自动备份（只登记位置）
SENSITIVE_EXTS = {".kdbx", ".kdb", ".key", ".pem", ".pfx", ".p12", ".ppk", ".gpg", ".asc", ".ovpn", ".keystore", ".jks"}
SENSITIVE_NAMES = {"id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", ".env"}


@dataclass
class FileEntry:
    path: str
    name: str
    is_dir: bool = False
    size: int = 0
    mtime: float = 0.0
    fid: str = ""  # NTFS 文件编号，见 win32.file_id
    backup: str = ""  # 副本在 BlobStore 里的文件名
    thumb: str = ""  # 缩略图在 BlobStore 里的文件名

    @property
    def ext(self) -> str:
        return os.path.splitext(self.name)[1].lower()

    @property
    def is_image(self) -> bool:
        return not self.is_dir and self.ext in IMAGE_EXTS

    @property
    def is_sensitive(self) -> bool:
        return self.ext in SENSITIVE_EXTS or self.name.lower() in SENSITIVE_NAMES

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> FileEntry:
        return cls(**{k: data[k] for k in cls.__dataclass_fields__ if k in data})


def capture(paths: list[str]) -> list[FileEntry]:
    """登记文件信息。已经不存在的路径跳过。"""
    entries = []
    for path in paths[:DETAIL_LIMIT]:
        try:
            st = os.stat(path)
        except OSError:
            continue
        is_dir = stat.S_ISDIR(st.st_mode)
        entries.append(
            FileEntry(
                path=path,
                name=os.path.basename(path.rstrip("\\/")) or path,
                is_dir=is_dir,
                size=0 if is_dir else st.st_size,
                mtime=st.st_mtime,
                fid=win32.file_id(path),
            )
        )
    return entries


def dedupe_key(paths: list[str]) -> str:
    """同一组文件（不管顺序）再复制一次，算同一条记录。"""
    return "files:" + "\n".join(sorted(os.path.normcase(p) for p in paths))


def search_text(entries: list[FileEntry]) -> str:
    """存进 content 用来搜索：文件名 + 所在文件夹。"""
    return "\n".join(f"{e.name}  {os.path.dirname(e.path)}" for e in entries)


def title(entries: list[FileEntry], total: int) -> str:
    if not entries:
        return "文件"
    first = entries[0].name + ("（文件夹）" if entries[0].is_dir else "")
    return first if total <= 1 else f"{first} 等 {total} 个"


def should_backup(entry: FileEntry, max_bytes: int) -> bool:
    return not entry.is_dir and entry.size <= max_bytes and not entry.is_sensitive


def wants_thumb(entry: FileEntry) -> bool:
    return entry.is_image and entry.size <= THUMB_MAX_BYTES


def carry_over(new: list[FileEntry], old: list[dict]) -> None:
    """同一组文件再次复制时，没变过的文件沿用上次的副本和缩略图，不用重新备份。"""
    previous = {(o.get("path"), o.get("size"), o.get("mtime")): o for o in old}
    for entry in new:
        o = previous.get((entry.path, entry.size, entry.mtime))
        if o:
            entry.backup = o.get("backup", "")
            entry.thumb = o.get("thumb", "")


class Status(enum.Enum):
    OK = "ok"
    MOVED = "moved"
    MODIFIED = "modified"
    BACKUP = "backup"
    RECYCLED = "recycled"
    MISSING = "missing"


STATUS_LABELS = {
    Status.OK: "✅ 原位置可用",
    Status.MOVED: "🔄 已找到新位置",
    Status.MODIFIED: "⚠️ 复制之后被修改过",
    Status.BACKUP: "💾 原文件已不在，使用副本",
    Status.RECYCLED: "🗑️ 在回收站里，没有副本",
    Status.MISSING: "❌ 找不到，也没有副本",
}


@dataclass
class Resolution:
    entry: FileEntry
    status: Status
    path: str = ""  # 现在能用的原文件路径（OK / MOVED / MODIFIED 时有）
    has_backup: bool = False

    @property
    def restorable(self) -> bool:
        return bool(self.path) or self.has_backup


def unchanged(entry: FileEntry, path: str) -> bool:
    """path 处的文件和登记时一样（大小、修改时间都没变）。"""
    if entry.is_dir:
        return True
    try:
        st = os.stat(path)
    except OSError:
        return False
    return st.st_size == entry.size and abs(st.st_mtime - entry.mtime) < 2


def _in_recycle_bin(path: str) -> bool:
    return "\\$recycle.bin\\" in path.lower()


def resolve(entry: FileEntry, has_blob: Callable[[str], bool]) -> Resolution:
    has_backup = bool(entry.backup) and has_blob(entry.backup)
    if os.path.exists(entry.path):
        status = Status.OK if unchanged(entry, entry.path) else Status.MODIFIED
        return Resolution(entry, status, entry.path, has_backup)
    new_path = win32.find_by_file_id(entry.fid, entry.path) if entry.fid else ""
    # 删到回收站的文件编号还在，但回收站里的文件名是 $R1A2B3C.zip，不能拿来还原
    if new_path and not _in_recycle_bin(new_path):
        return Resolution(entry, Status.MOVED, new_path, has_backup)
    if has_backup:
        return Resolution(entry, Status.BACKUP, "", True)
    return Resolution(entry, Status.RECYCLED if new_path else Status.MISSING)
