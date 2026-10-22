"""副本和图片的存放：按内容的 SHA-256 命名，同样的内容只存一份。

还原备份文件时不能直接用哈希名（粘贴出去会变成 3f2a….zip），所以先复制一份到 restore 目录、
改回原来的文件名再交给剪贴板。用复制而不是硬链接：如果别的程序直接打开并改了这个文件，
不会连带改坏副本。
"""
from __future__ import annotations

import hashlib
import os
import re
import secrets
import shutil
import time
from pathlib import Path

_CHUNK = 1 << 20
# 后台刚写进来的文件，可能还没登记到记录里，这段时间内不清理
GC_GRACE_SECONDS = 600
STAGING_KEEP_SECONDS = 86400
_UNSAFE_NAME = re.compile(r'[\\/:*?"<>|\r\n\t]')


def safe_filename(name: str, fallback: str = "文件") -> str:
    name = _UNSAFE_NAME.sub("_", name).strip(" .")
    return name[:200] or fallback


class BlobStore:
    def __init__(self, root: Path, staging: Path):
        self.root = Path(root)
        self.staging = Path(staging)
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, name: str) -> Path:
        return self.root / name[:2] / name

    def exists(self, name: str) -> bool:
        return bool(name) and self.path(name).is_file()

    def put_bytes(self, data: bytes, suffix: str) -> str:
        name = hashlib.sha256(data).hexdigest() + suffix
        target = self.path(name)
        if not target.exists():
            target.parent.mkdir(exist_ok=True)
            tmp = target.with_name(target.name + f".{secrets.token_hex(4)}.tmp")
            tmp.write_bytes(data)
            os.replace(tmp, target)
        return name

    def put_file(self, src: str | os.PathLike) -> str:
        """一边复制一边算哈希，最后改名成哈希名；已经有同样内容的就丢掉这次复制的。"""
        suffix = Path(src).suffix.lower()[:16]
        tmp = self.root / f"incoming-{secrets.token_hex(8)}.tmp"
        digest = hashlib.sha256()
        try:
            with open(src, "rb") as fin, open(tmp, "wb") as fout:
                while chunk := fin.read(_CHUNK):
                    digest.update(chunk)
                    fout.write(chunk)
            name = digest.hexdigest() + suffix
            target = self.path(name)
            if target.exists():
                tmp.unlink()
            else:
                target.parent.mkdir(exist_ok=True)
                os.replace(tmp, target)
            return name
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise

    def delete(self, name: str) -> int:
        """删除一个文件，返回释放的字节数。"""
        path = self.path(name)
        try:
            size = path.stat().st_size
            path.unlink()
        except OSError:
            return 0
        return size

    def clear(self) -> int:
        freed = 0
        for f in self.root.rglob("*"):
            if f.is_file():
                freed += f.stat().st_size
                f.unlink(missing_ok=True)
        return freed

    def total_size(self) -> int:
        return sum(f.stat().st_size for f in self.root.rglob("*") if f.is_file())

    def gc(self, referenced: set[str], now: float | None = None) -> int:
        """删除没有任何记录引用的文件，返回释放的字节数。"""
        now = time.time() if now is None else now
        freed = 0
        for f in self.root.rglob("*"):
            if not f.is_file() or f.name in referenced:
                continue
            stat = f.stat()
            if now - stat.st_mtime < GC_GRACE_SECONDS:
                continue
            f.unlink(missing_ok=True)
            freed += stat.st_size
        return freed

    def stage(self, name: str, filename: str, mtime: float | None = None) -> Path:
        """把副本复制到一个临时目录，恢复原文件名（和修改时间），返回这个临时文件的路径。"""
        folder = self.staging / f"{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}"
        folder.mkdir(parents=True, exist_ok=True)
        dest = folder / safe_filename(filename)
        shutil.copyfile(self.path(name), dest)
        if mtime:
            os.utime(dest, (mtime, mtime))
        return dest

    def clean_staging(self, now: float | None = None) -> None:
        if not self.staging.exists():
            return
        now = time.time() if now is None else now
        for folder in self.staging.iterdir():
            if folder.is_dir() and now - folder.stat().st_mtime > STAGING_KEEP_SECONDS:
                shutil.rmtree(folder, ignore_errors=True)


def referenced_names(clips) -> set[str]:
    """记录里登记的所有副本、图片、缩略图文件名。"""
    names: set[str] = set()
    for clip in clips:
        data = clip.data
        if clip.kind == "image":
            names.update(n for n in (data.get("blob"), data.get("thumb")) if n)
        elif clip.kind == "file":
            for f in data.get("files", []):
                names.update(n for n in (f.get("backup"), f.get("thumb")) if n)
    return names
