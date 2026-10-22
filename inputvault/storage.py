"""SQLite 存储：记录表 + FTS5 全文索引（trigram 分词，中文可直接搜）。"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from .classify import KIND_LABELS

SCHEMA_VERSION = 3
ATTACHMENT_KINDS = ("file", "image")  # payload 里登记了副本 / 图片文件的记录

SCHEMA = """
CREATE TABLE IF NOT EXISTS clips (
    id           INTEGER PRIMARY KEY,
    content      TEXT    NOT NULL,
    content_hash TEXT    NOT NULL UNIQUE,
    kind         TEXT    NOT NULL DEFAULT 'text',
    source       TEXT    NOT NULL DEFAULT 'copy',
    source_app   TEXT    NOT NULL DEFAULT '',
    source_exe   TEXT    NOT NULL DEFAULT '',
    window_title TEXT    NOT NULL DEFAULT '',
    created_at   REAL    NOT NULL,
    last_used_at REAL    NOT NULL,
    use_count    INTEGER NOT NULL DEFAULT 1,
    pinned       INTEGER NOT NULL DEFAULT 0,  -- 收藏（早期版本叫“置顶”，列名沿用）
    payload      TEXT    NOT NULL DEFAULT ''
);
-- 早期版本收藏的记录排在最前，用的是 (pinned, last_used_at) 索引；现在只按时间排
DROP INDEX IF EXISTS idx_clips_order;
CREATE INDEX IF NOT EXISTS idx_clips_recent ON clips(last_used_at DESC);

CREATE VIRTUAL TABLE IF NOT EXISTS clips_fts USING fts5(
    content, content='clips', content_rowid='id', tokenize='trigram'
);
-- content 按哈希去重、不会被修改，所以只需要插入和删除两个触发器
CREATE TRIGGER IF NOT EXISTS clips_ai AFTER INSERT ON clips BEGIN
    INSERT INTO clips_fts(rowid, content) VALUES (new.id, new.content);
END;
CREATE TRIGGER IF NOT EXISTS clips_ad AFTER DELETE ON clips BEGIN
    INSERT INTO clips_fts(clips_fts, rowid, content) VALUES ('delete', old.id, old.content);
END;
"""

# v0.1 的库没有 source 列，v0.2 的库没有 payload 列（文件、图片的详细信息，JSON）
MIGRATE_TO_V2 = "ALTER TABLE clips ADD COLUMN source TEXT NOT NULL DEFAULT 'copy'"
MIGRATE_TO_V3 = "ALTER TABLE clips ADD COLUMN payload TEXT NOT NULL DEFAULT ''"

SOURCE_LABELS = {"copy": "复制", "type": "打字"}

# 面板左侧栏的分类和它包含的记录类型
CATEGORY_KINDS: dict[str, tuple[str, ...]] = {
    "text": ("text",),
    "url": ("url",),
    "file": ("file", "path"),
    "image": ("image",),
    "other": ("code", "json", "email", "phone", "number"),
}
FILTER_KEYS = ("app", "type", "src", "ext")
_KIND_BY_LABEL = {label.lower(): kind for kind, label in KIND_LABELS.items()}
_SOURCE_BY_LABEL = {label: source for source, label in SOURCE_LABELS.items()}


@dataclass(frozen=True)
class Clip:
    id: int
    content: str
    kind: str
    source: str
    source_app: str
    source_exe: str
    window_title: str
    created_at: float
    last_used_at: float
    use_count: int
    favorite: bool
    payload: str = ""

    @property
    def data(self) -> dict:
        return json.loads(self.payload) if self.payload else {}


def parse_query(query: str) -> tuple[list[str], dict[str, str]]:
    """拆分搜索词和过滤条件，比如 "报价 app:微信 type:链接 src:打字"。"""
    terms: list[str] = []
    filters: dict[str, str] = {}
    for token in query.split():
        key, sep, value = token.partition(":")
        if sep and value and key.lower() in FILTER_KEYS:
            filters[key.lower()] = value
        else:
            terms.append(token)
    return terms, filters


def _fts_phrase(term: str) -> str:
    return '"' + term.replace('"', '""') + '"'


def _like_pattern(term: str) -> str:
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


class Storage:
    def __init__(self, path: str | Path):
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        if str(path) != ":memory:":
            self.conn.execute("PRAGMA journal_mode=WAL")
        self._migrate()

    def _migrate(self) -> None:
        version = self.conn.execute("PRAGMA user_version").fetchone()[0]
        exists = self.conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'clips'").fetchone()
        if exists and version < 2:
            self.conn.execute(MIGRATE_TO_V2)
        if exists and version < 3:
            self.conn.execute(MIGRATE_TO_V3)
        self.conn.executescript(SCHEMA)
        self.conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def add(
        self,
        content: str,
        kind: str = "text",
        source_app: str = "",
        source_exe: str = "",
        window_title: str = "",
        source: str = "copy",
        now: float | None = None,
        payload: str = "",
        dedupe_key: str | None = None,
    ) -> int:
        """新增一条记录；重复时只更新来源应用、时间、使用次数和 payload。返回记录 id。

        默认按内容去重；文件、图片的 content 只是用来搜索的文字，要用 dedupe_key 指定按什么去重。
        """
        now = time.time() if now is None else now
        digest = hashlib.sha256((dedupe_key or content).encode("utf-8")).hexdigest()
        row = self.conn.execute(
            """
            INSERT INTO clips (content, content_hash, kind, source, source_app, source_exe,
                               window_title, created_at, last_used_at, payload)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(content_hash) DO UPDATE SET
                last_used_at = excluded.last_used_at,
                use_count    = use_count + 1,
                source_app   = excluded.source_app,
                source_exe   = excluded.source_exe,
                window_title = excluded.window_title,
                payload      = CASE WHEN excluded.payload != '' THEN excluded.payload ELSE payload END
            RETURNING id
            """,
            (content, digest, kind, source, source_app, source_exe, window_title, now, now, payload),
        ).fetchone()
        self.conn.commit()
        return row[0]

    def update_payload(self, clip_id: int, payload: str) -> None:
        self.conn.execute("UPDATE clips SET payload = ? WHERE id = ?", (payload, clip_id))
        self.conn.commit()

    def attachment_clips(self) -> list[Clip]:
        """所有文件 / 图片记录，最久没用的在前（清理空间时按这个顺序）。"""
        marks = ", ".join("?" * len(ATTACHMENT_KINDS))
        rows = self.conn.execute(
            f"SELECT * FROM clips WHERE kind IN ({marks}) ORDER BY last_used_at ASC", ATTACHMENT_KINDS
        )
        return [_to_clip(row) for row in rows]

    def get(self, clip_id: int) -> Clip | None:
        row = self.conn.execute("SELECT * FROM clips WHERE id = ?", (clip_id,)).fetchone()
        return _to_clip(row) if row else None

    def get_by_key(self, dedupe_key: str) -> Clip | None:
        digest = hashlib.sha256(dedupe_key.encode("utf-8")).hexdigest()
        row = self.conn.execute("SELECT * FROM clips WHERE content_hash = ?", (digest,)).fetchone()
        return _to_clip(row) if row else None

    def touch(self, clip_id: int, now: float | None = None) -> None:
        """从面板里用了一次：刷新时间、使用次数加一。"""
        now = time.time() if now is None else now
        self.conn.execute(
            "UPDATE clips SET last_used_at = ?, use_count = use_count + 1 WHERE id = ?",
            (now, clip_id),
        )
        self.conn.commit()

    def toggle_favorite(self, clip_id: int) -> None:
        self.conn.execute("UPDATE clips SET pinned = 1 - pinned WHERE id = ?", (clip_id,))
        self.conn.commit()

    def delete(self, clip_id: int) -> None:
        self.conn.execute("DELETE FROM clips WHERE id = ?", (clip_id,))
        self.conn.commit()

    def count(self, source_exe: str | None = None) -> int:
        if source_exe is None:
            return self.conn.execute("SELECT COUNT(*) FROM clips").fetchone()[0]
        return self.conn.execute(
            "SELECT COUNT(*) FROM clips WHERE source_exe = ? COLLATE NOCASE", (source_exe,)
        ).fetchone()[0]

    def delete_by_exe(self, source_exe: str) -> int:
        cur = self.conn.execute("DELETE FROM clips WHERE source_exe = ? COLLATE NOCASE", (source_exe,))
        self.conn.commit()
        return cur.rowcount

    def prune(self, older_than_days: int, now: float | None = None) -> int:
        """删除超过保留期限的记录（收藏的不删），返回删除条数。"""
        now = time.time() if now is None else now
        cur = self.conn.execute(
            "DELETE FROM clips WHERE pinned = 0 AND last_used_at < ?",
            (now - older_than_days * 86400,),
        )
        self.conn.commit()
        return cur.rowcount

    def search(
        self,
        query: str = "",
        limit: int = 200,
        source_exe: str | None = None,
        *,
        kinds: tuple[str, ...] | None = None,
        favorites_only: bool = False,
        exclude_exes: tuple[str, ...] = (),
        order: str = "time",
        ascending: bool = False,
    ) -> list[Clip]:
        """按搜索框内容和面板上选的筛选条件查记录。

        source_exe：只看这个程序里的；kinds：只看这些类型；exclude_exes：排除这些程序（左侧栏的“其他应用”）。
        order 为 "time" 时按最近使用时间；为 "count" 时按使用次数。
        """
        where, params = self._where(query, source_exe, kinds, favorites_only, exclude_exes)
        direction = "ASC" if ascending else "DESC"
        if order == "count":
            order_by = f"use_count {direction}, last_used_at DESC"
        else:
            order_by = f"last_used_at {direction}"
        sql = "SELECT * FROM clips" + (" WHERE " + " AND ".join(where) if where else "")
        sql += f" ORDER BY {order_by} LIMIT ?"
        return [_to_clip(row) for row in self.conn.execute(sql, [*params, limit])]

    def count_matching(
        self,
        query: str = "",
        source_exe: str | None = None,
        *,
        kinds: tuple[str, ...] | None = None,
        favorites_only: bool = False,
        exclude_exes: tuple[str, ...] = (),
    ) -> int:
        """和 search 条件相同的记录总数（不受 limit 限制）。"""
        where, params = self._where(query, source_exe, kinds, favorites_only, exclude_exes)
        sql = "SELECT COUNT(*) FROM clips" + (" WHERE " + " AND ".join(where) if where else "")
        return self.conn.execute(sql, params).fetchone()[0]

    def kind_counts(self) -> dict[str, int]:
        return dict(self.conn.execute("SELECT kind, COUNT(*) FROM clips GROUP BY kind"))

    def favorite_count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM clips WHERE pinned = 1").fetchone()[0]

    def app_counts(self) -> list[tuple[str, str, int]]:
        """每个来源程序的记录数，多的在前：[(exe, 显示名, 条数)]。"""
        rows = self.conn.execute(
            """
            SELECT MAX(source_exe), MAX(source_app), COUNT(*) FROM clips
            WHERE source_exe != '' GROUP BY lower(source_exe) ORDER BY 3 DESC, 2
            """
        )
        return [(exe, app or exe, n) for exe, app, n in rows]

    def _where(
        self,
        query: str,
        source_exe: str | None,
        kinds: tuple[str, ...] | None,
        favorites_only: bool,
        exclude_exes: tuple[str, ...],
    ) -> tuple[list[str], list[object]]:
        terms, filters = parse_query(query)
        where: list[str] = []
        params: list[object] = []

        # trigram 索引要求至少 3 个字符，更短的词用 LIKE 兜底
        fts_terms = [t for t in terms if len(t) >= 3]
        if fts_terms:
            where.append("id IN (SELECT rowid FROM clips_fts WHERE clips_fts MATCH ?)")
            params.append(" AND ".join(_fts_phrase(t) for t in fts_terms))
        for term in terms:
            if len(term) < 3:
                where.append("content LIKE ? ESCAPE '\\'")
                params.append(_like_pattern(term))

        if "app" in filters:
            where.append("(source_app LIKE ? ESCAPE '\\' OR source_exe LIKE ? ESCAPE '\\')")
            params += [_like_pattern(filters["app"])] * 2
        if "type" in filters:
            value = filters["type"].lower()
            where.append("kind = ?")
            params.append(_KIND_BY_LABEL.get(value, value))
        if "src" in filters:
            value = filters["src"].lower()
            where.append("source = ?")
            params.append(_SOURCE_BY_LABEL.get(value, value))
        if "ext" in filters:
            # 文件记录的 content 是“文件名  所在文件夹”，按扩展名匹配文件名
            where.append("kind = 'file' AND content LIKE ? ESCAPE '\\'")
            params.append(_like_pattern("." + filters["ext"].lstrip(".")))
        if source_exe:
            where.append("source_exe = ? COLLATE NOCASE")
            params.append(source_exe)
        if kinds is not None:
            where.append(f"kind IN ({', '.join('?' * len(kinds))})" if kinds else "0")
            params += kinds
        if favorites_only:
            where.append("pinned = 1")
        if exclude_exes:
            where.append(f"lower(source_exe) NOT IN ({', '.join('?' * len(exclude_exes))})")
            params += [e.lower() for e in exclude_exes]
        return where, params


def _to_clip(row: sqlite3.Row) -> Clip:
    return Clip(
        id=row["id"],
        content=row["content"],
        kind=row["kind"],
        source=row["source"],
        source_app=row["source_app"],
        source_exe=row["source_exe"],
        window_title=row["window_title"],
        created_at=row["created_at"],
        last_used_at=row["last_used_at"],
        use_count=row["use_count"],
        favorite=bool(row["pinned"]),
        payload=row["payload"],
    )
