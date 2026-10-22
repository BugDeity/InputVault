"""副本和图片的空间管理：超出上限时清理最久没用的，以及一键清空。"""
from __future__ import annotations

import json
import logging
from collections import Counter

from .blobs import BlobStore, referenced_names
from .storage import Clip, Storage

log = logging.getLogger(__name__)


def _release(storage: Storage, clip: Clip) -> set[str]:
    """释放一条记录占用的空间，返回它不再引用的文件名。

    图片记录没有原文件可以回去找，只能整条删除；文件记录只去掉副本，位置信息和缩略图保留，
    原文件还在的话照样能还原。
    """
    data = clip.data
    if clip.kind == "image":
        storage.delete(clip.id)
        return {n for n in (data.get("blob"), data.get("thumb")) if n}
    names = set()
    for f in data.get("files", []):
        if f.get("backup"):
            names.add(f["backup"])
            f["backup"] = ""
    if names:
        storage.update_payload(clip.id, json.dumps(data, ensure_ascii=False))
    return names


def enforce_quota(storage: Storage, blobs: BlobStore, quota_bytes: int) -> int:
    """先清掉没人引用的文件；还超出上限就从最久没用的记录开始释放（收藏的不动）。返回释放的字节数。"""
    clips = storage.attachment_clips()
    freed = blobs.gc(referenced_names(clips))
    total = blobs.total_size()
    if total <= quota_bytes:
        return freed
    # 同一个副本可能被几条记录共用（内容相同的文件），引用数降到 0 才能删
    refs = Counter(name for clip in clips for name in referenced_names([clip]))
    released = 0
    for clip in clips:  # 最久没用的在前
        if total <= quota_bytes:
            break
        if clip.favorite:
            continue
        names = _release(storage, clip)
        released += bool(names)
        for name in names:
            refs[name] -= 1
            if refs[name] <= 0:
                size = blobs.delete(name)
                total -= size
                freed += size
    log.info("空间超出上限，释放了 %d 条记录的副本 / 图片，共 %.1f MB", released, freed / 1024 / 1024)
    return freed


def clear_attachments(storage: Storage, blobs: BlobStore) -> int:
    """删除所有图片记录和文件副本（文件的位置信息保留）。返回释放的字节数。"""
    for clip in storage.attachment_clips():
        if clip.kind == "image":
            storage.delete(clip.id)
            continue
        data = clip.data
        for f in data.get("files", []):
            f["backup"] = f["thumb"] = ""
        storage.update_payload(clip.id, json.dumps(data, ensure_ascii=False))
    return blobs.clear()
