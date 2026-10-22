import json
import tempfile
import unittest
from pathlib import Path

from inputvault.blobs import BlobStore
from inputvault.housekeeping import clear_attachments, enforce_quota
from inputvault.storage import Storage


class QuotaTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        self.blobs = BlobStore(tmp / "files", tmp / "restore")
        self.db = Storage(":memory:")

    def tearDown(self):
        self.db.close()
        self._tmp.cleanup()

    def image(self, data: bytes, now: float) -> int:
        blob = self.blobs.put_bytes(data, ".png")
        return self.db.add("图片", "image", payload=json.dumps({"blob": blob, "thumb": ""}),
                           dedupe_key=f"image:{blob}", now=now)

    def file_with_backup(self, data: bytes, now: float, key: str) -> int:
        backup = self.blobs.put_bytes(data, ".zip")
        payload = {"files": [{"path": "d:\\a.zip", "name": "a.zip", "backup": backup, "thumb": ""}]}
        return self.db.add("a.zip  d:\\", "file", payload=json.dumps(payload), dedupe_key=key, now=now)

    def test_under_quota_does_nothing(self):
        self.image(b"x" * 100, now=1)
        enforce_quota(self.db, self.blobs, 1000)
        self.assertEqual(self.db.count(), 1)

    def test_releases_oldest_non_favorite_first(self):
        oldest_favorite = self.image(b"a" * 400, now=1)
        self.db.toggle_favorite(oldest_favorite)
        old_file = self.file_with_backup(b"b" * 400, now=2, key="f1")
        self.image(b"c" * 400, now=3)  # 最新的
        enforce_quota(self.db, self.blobs, 900)

        self.assertIsNotNone(self.db.get(oldest_favorite))  # 收藏的不动
        old = self.db.get(old_file)
        self.assertIsNotNone(old)  # 文件记录保留，只去掉副本
        self.assertEqual(old.data["files"][0]["backup"], "")
        self.assertEqual(self.db.count(), 3)
        self.assertLessEqual(self.blobs.total_size(), 900)

    def test_shared_backup_is_kept_while_still_referenced(self):
        oldest = self.file_with_backup(b"same" * 100, now=1, key="f1")
        self.image(b"i" * 300, now=2)
        newest = self.file_with_backup(b"same" * 100, now=3, key="f2")  # 内容一样，和最旧的共用一个副本
        enforce_quota(self.db, self.blobs, 500)
        # 释放最旧的那条不会删掉共用的副本（最新那条还在用），接着删掉图片就够了
        self.assertEqual(self.db.get(oldest).data["files"][0]["backup"], "")
        backup = self.db.get(newest).data["files"][0]["backup"]
        self.assertTrue(backup and self.blobs.exists(backup))
        self.assertEqual(len(self.db.search("type:图片")), 0)

    def test_clear(self):
        self.image(b"a" * 10, now=1)
        f = self.file_with_backup(b"b" * 10, now=2, key="f1")
        self.assertEqual(clear_attachments(self.db, self.blobs), 20)
        self.assertEqual(self.db.count(), 1)
        self.assertEqual(self.db.get(f).data["files"][0]["backup"], "")
        self.assertEqual(self.blobs.total_size(), 0)


if __name__ == "__main__":
    unittest.main()
