"""还原：把一条记录重新放回剪贴板（或拖出去）时，构造对应的剪贴板数据。"""
from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import QByteArray, QMimeData, QUrl
from PySide6.QtGui import QImage

from .blobs import BlobStore
from .filerefs import DETAIL_LIMIT, FileEntry, Resolution, Status, resolve
from .storage import Clip

# 自己写进剪贴板的内容带上这个标记，采集时据此跳过，不会把还原出来的东西再记一遍
MARKER = "application/x-inputvault"
# 告诉资源管理器这是“复制”而不是“剪切”：即使当初是剪切的，还原时也不会再把文件移走
_DROP_EFFECT = 'application/x-qt-windows-mime;value="Preferred DropEffect"'
_DROPEFFECT_COPY = 1


def _mime() -> QMimeData:
    mime = QMimeData()
    mime.setData(MARKER, QByteArray(b"1"))
    return mime


def text_mime(text: str) -> QMimeData:
    mime = _mime()
    mime.setText(text)
    return mime


def _set_files(mime: QMimeData, paths: list[str]) -> None:
    mime.setUrls([QUrl.fromLocalFile(p) for p in paths])
    mime.setData(_DROP_EFFECT, QByteArray(_DROPEFFECT_COPY.to_bytes(4, "little")))


def files_mime(paths: list[str]) -> QMimeData:
    mime = _mime()
    _set_files(mime, paths)
    return mime


def image_filename(clip: Clip) -> str:
    return time.strftime("图片_%Y%m%d_%H%M%S.png", time.localtime(clip.created_at))


class Restorer:
    def __init__(self, blobs: BlobStore):
        self.blobs = blobs

    def resolutions(self, clip: Clip, limit: int | None = None) -> list[Resolution]:
        files = clip.data.get("files", [])[:limit]
        return [resolve(FileEntry.from_dict(f), self.blobs.exists) for f in files]

    def not_registered(self, clip: Clip) -> int:
        """一次复制超过 DETAIL_LIMIT 个文件时，没登记的个数。"""
        data = clip.data
        return max(0, data.get("total", 0) - len(data.get("files", [])))

    def file_paths(
        self, resolutions: list[Resolution], prefer_backup_when_modified: bool = False
    ) -> tuple[list[str], list[str]]:
        """返回（能还原的路径，找不到的文件名）。用副本时会先复制一份、恢复原文件名。"""
        paths, missing = [], []
        for r in resolutions:
            modified = r.status is Status.MODIFIED and prefer_backup_when_modified
            if r.has_backup and (not r.path or modified):
                paths.append(str(self.blobs.stage(r.entry.backup, r.entry.name, r.entry.mtime)))
            elif r.path:
                paths.append(r.path)
            else:
                missing.append(r.entry.name)
        return paths, missing

    def image_path(self, clip: Clip) -> Path | None:
        name = clip.data.get("blob", "")
        return self.blobs.path(name) if self.blobs.exists(name) else None

    def mime_for(
        self, clip: Clip, *, as_file: bool = False, with_file: bool = False,
        prefer_backup_when_modified: bool = False,
    ) -> tuple[QMimeData | None, list[str]]:
        """返回（剪贴板数据，找不到的文件名）。一个都还原不了时剪贴板数据为 None。

        图片默认放图片数据；as_file 只放成 PNG 文件；with_file 两种都放（拖拽时用，
        拖到文件夹得到文件，拖到聊天框 / 文档得到图片）。
        """
        if clip.kind == "file":
            paths, missing = self.file_paths(self.resolutions(clip), prefer_backup_when_modified)
            if extra := self.not_registered(clip):
                missing.append(f"另外 {extra} 个文件（一次复制超过 {DETAIL_LIMIT} 个，没有登记）")
            return (files_mime(paths) if paths else None), missing
        if clip.kind == "image":
            source = self.image_path(clip)
            if source is None:
                return None, ["图片文件已被清理"]
            mime = _mime()
            if not as_file:
                mime.setImageData(QImage(str(source)))
            if as_file or with_file:
                staged = self.blobs.stage(clip.data["blob"], image_filename(clip))
                _set_files(mime, [str(staged)])
            return mime, []
        return text_mime(clip.content), []
