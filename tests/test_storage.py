import sqlite3
import tempfile
import unittest
from pathlib import Path

from inputvault.storage import Storage, parse_query


class StorageTest(unittest.TestCase):
    def setUp(self):
        self.db = Storage(":memory:")

    def tearDown(self):
        self.db.close()

    def test_duplicate_content_is_merged(self):
        a = self.db.add("你好世界", source_app="微信", now=100)
        b = self.db.add("你好世界", source_app="Chrome", now=200)
        self.assertEqual(a, b)
        clip = self.db.get(a)
        self.assertEqual(clip.use_count, 2)
        self.assertEqual(clip.source_app, "Chrome")
        self.assertEqual(clip.created_at, 100)
        self.assertEqual(clip.last_used_at, 200)
        self.assertEqual(self.db.count(), 1)

    def test_search_chinese_long_and_short_terms(self):
        self.db.add("明天下午三点开会讨论报价")
        self.db.add("快递单号已经发你了")
        self.assertEqual(len(self.db.search("讨论报价")), 1)  # FTS
        self.assertEqual(len(self.db.search("报价")), 1)  # 两个字走 LIKE
        self.assertEqual(len(self.db.search("开会 报价")), 1)
        self.assertEqual(self.db.search("不存在的内容"), [])

    def test_search_is_case_insensitive_and_escapes_quotes(self):
        self.db.add('print("Hello World")')
        self.assertEqual(len(self.db.search("hello")), 1)
        self.assertEqual(len(self.db.search('"Hello')), 1)
        self.assertEqual(len(self.db.search("100%")), 0)

    def test_filters(self):
        self.db.add("https://example.com", kind="url", source_app="Chrome", source_exe="chrome.exe")
        self.db.add("一段普通文字", source_app="微信", source_exe="Weixin.exe")
        self.assertEqual(len(self.db.search("app:微信")), 1)
        self.assertEqual(len(self.db.search("app:chrome")), 1)
        self.assertEqual(len(self.db.search("type:链接")), 1)
        self.assertEqual(len(self.db.search("type:url")), 1)
        self.assertEqual(len(self.db.search("type:url app:微信")), 0)

    def test_favorite_does_not_jump_to_top(self):
        old = self.db.add("旧内容", now=100)
        self.db.add("新内容", now=200)
        self.db.toggle_favorite(old)
        self.assertTrue(self.db.get(old).favorite)
        self.assertEqual(self.db.search()[0].content, "新内容")  # 收藏不改变顺序
        self.assertEqual([c.id for c in self.db.search(favorites_only=True)], [old])
        self.db.toggle_favorite(old)
        self.assertFalse(self.db.get(old).favorite)
        self.assertEqual(self.db.favorite_count(), 0)

    def test_delete_removes_from_index(self):
        cid = self.db.add("要删除的内容")
        self.db.delete(cid)
        self.assertEqual(self.db.search("要删除的"), [])

    def test_prune_keeps_favorites(self):
        keep = self.db.add("收藏的旧内容", now=0)
        self.db.toggle_favorite(keep)
        self.db.add("普通旧内容", now=0)
        self.db.add("新内容", now=40 * 86400)
        removed = self.db.prune(30, now=40 * 86400)
        self.assertEqual(removed, 1)
        self.assertEqual(self.db.count(), 2)

    def test_parse_query(self):
        terms, filters = parse_query("报价 app:微信 https://a.com type:链接 src:打字")
        self.assertEqual(terms, ["报价", "https://a.com"])
        self.assertEqual(filters, {"app": "微信", "type": "链接", "src": "打字"})

    def test_source_filter_and_duplicate_keeps_first_source(self):
        self.db.add("打出来的一句话", source="type")
        self.db.add("复制来的一句话")
        self.assertEqual(len(self.db.search("src:打字")), 1)
        self.assertEqual(len(self.db.search("src:copy")), 1)
        self.db.add("打出来的一句话", source="copy")
        self.assertEqual(self.db.search("打出来的")[0].source, "type")

    def test_dedupe_key_and_payload(self):
        a = self.db.add("报价单.xlsx  D:\\工作", kind="file", payload='{"v": 1}', dedupe_key="files:d:\\工作\\报价单.xlsx")
        # 同一组文件再复制一次：合并成一条，payload 换成新的
        b = self.db.add("报价单.xlsx  D:\\工作", kind="file", payload='{"v": 2}', dedupe_key="files:d:\\工作\\报价单.xlsx")
        self.assertEqual(a, b)
        self.assertEqual(self.db.get(a).data, {"v": 2})
        self.assertEqual(self.db.get(a).use_count, 2)
        # 去重键不同，即使 content 一样也是两条
        c = self.db.add("报价单.xlsx  D:\\工作", kind="file", dedupe_key="files:e:\\备份\\报价单.xlsx")
        self.assertNotEqual(a, c)
        self.db.update_payload(c, '{"v": 3}')
        self.assertEqual(self.db.get(c).data, {"v": 3})

    def test_attachment_filters(self):
        self.db.add("报价单.xlsx  D:\\工作", kind="file", dedupe_key="f1", now=100)
        self.db.add("合同.pdf  D:\\工作", kind="file", dedupe_key="f2", now=200)
        self.db.add("图片 1920×1080", kind="image", dedupe_key="i1", now=300)
        self.db.add("一段文字里提到了 .pdf")
        self.assertEqual(len(self.db.search("type:文件")), 2)
        self.assertEqual(len(self.db.search("type:图片")), 1)
        self.assertEqual([c.content for c in self.db.search("ext:pdf")], ["合同.pdf  D:\\工作"])
        self.assertEqual(len(self.db.search("ext:.xlsx")), 1)
        self.assertEqual([c.kind for c in self.db.attachment_clips()], ["file", "file", "image"])

    def test_panel_filters_counts_and_order(self):
        self.db.add("微信里的一句话", source_app="微信", source_exe="Weixin.exe", now=100)
        self.db.add("https://a.com", kind="url", source_app="Edge", source_exe="msedge.exe", now=200)
        self.db.add("def f(): pass\nreturn 1;", kind="code", source_app="VS Code", source_exe="Code.exe", now=300)
        self.db.add("微信里的一句话", source_app="微信", source_exe="Weixin.exe", now=400)  # 用过 2 次
        self.db.add("微信里的另一条", source_app="微信", source_exe="WeiXin.exe", now=150)
        favorite = self.db.add("收藏的笔记", source_exe="Notepad.exe", now=50)
        self.db.toggle_favorite(favorite)

        self.assertEqual(self.db.kind_counts(), {"text": 3, "url": 1, "code": 1})
        self.assertEqual(self.db.favorite_count(), 1)
        exe, app, n = self.db.app_counts()[0]  # exe 大小写不同也算同一个程序
        self.assertEqual((exe.lower(), app, n), ("weixin.exe", "微信", 2))
        self.assertEqual(len(self.db.app_counts()), 4)

        self.assertEqual([c.content for c in self.db.search(kinds=("url", "code"))],
                         ["def f(): pass\nreturn 1;", "https://a.com"])
        self.assertEqual(self.db.search(kinds=()), [])
        self.assertEqual([c.id for c in self.db.search(favorites_only=True)], [favorite])
        others = self.db.search(exclude_exes=("weixin.exe", "MSEDGE.EXE"))
        self.assertEqual({c.source_exe for c in others}, {"Code.exe", "Notepad.exe"})
        self.assertEqual(self.db.count_matching(kinds=("text",)), 3)
        self.assertEqual(self.db.count_matching("微信"), 2)

        by_time = [c.content for c in self.db.search()]
        self.assertEqual(by_time[:2], ["微信里的一句话", "def f(): pass\nreturn 1;"])
        oldest_first = [c.content for c in self.db.search(ascending=True)]
        self.assertEqual(oldest_first[:3], ["收藏的笔记", "微信里的另一条", "https://a.com"])
        self.assertEqual(self.db.search(order="count")[0].content, "微信里的一句话")

    def test_filter_by_exe_and_delete_by_exe(self):
        self.db.add("微信里的内容", source_exe="Weixin.exe")
        self.db.add("浏览器里的内容", source_exe="msedge.exe")
        self.assertEqual(len(self.db.search(source_exe="weixin.exe")), 1)
        self.assertEqual(self.db.count("WEIXIN.EXE"), 1)
        self.assertEqual(self.db.delete_by_exe("weixin.exe"), 1)
        self.assertEqual(self.db.count(), 1)
        self.assertEqual(self.db.search("微信里的"), [])


# v0.1 建库时的表结构，用来测试升级
V1_SCHEMA = """
CREATE TABLE clips (
    id INTEGER PRIMARY KEY, content TEXT NOT NULL, content_hash TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL DEFAULT 'text', source_app TEXT NOT NULL DEFAULT '',
    source_exe TEXT NOT NULL DEFAULT '', window_title TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL, last_used_at REAL NOT NULL,
    use_count INTEGER NOT NULL DEFAULT 1, pinned INTEGER NOT NULL DEFAULT 0
);
CREATE VIRTUAL TABLE clips_fts USING fts5(content, content='clips', content_rowid='id', tokenize='trigram');
CREATE TRIGGER clips_ai AFTER INSERT ON clips BEGIN
    INSERT INTO clips_fts(rowid, content) VALUES (new.id, new.content);
END;
INSERT INTO clips (content, content_hash, created_at, last_used_at, pinned) VALUES ('v0.1 时代的旧记录', 'h1', 1, 1, 1);
"""


class MigrationTest(unittest.TestCase):
    def test_upgrade_from_v01(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "old.db"
            conn = sqlite3.connect(path)
            conn.executescript(V1_SCHEMA)
            conn.close()

            db = Storage(path)
            old = db.search("旧记录")
            self.assertEqual(len(old), 1)
            self.assertEqual(old[0].source, "copy")
            self.assertEqual(old[0].payload, "")
            self.assertTrue(old[0].favorite)  # 以前置顶的，升级后就是收藏
            db.add("升级后打的字", source="type")
            self.assertEqual(len(db.search("src:打字")), 1)
            db.close()

            # 再打开一次不应该重复升级
            db = Storage(path)
            self.assertEqual(db.count(), 2)
            db.close()


if __name__ == "__main__":
    unittest.main()
