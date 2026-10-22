"""副本库、文件引用、还原路径。用临时目录里的真实文件（在 NTFS 上，文件编号可用）。"""
import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from inputvault import filerefs
from inputvault.blobs import GC_GRACE_SECONDS, STAGING_KEEP_SECONDS, BlobStore, referenced_names
from inputvault.filerefs import FileEntry, Status, capture, resolve
from inputvault.restore import Restorer
from inputvault.storage import Clip


def make_clip(kind, data, created_at=0.0):
    return Clip(id=1, content="", kind=kind, source="copy", source_app="", source_exe="", window_title="",
                created_at=created_at, last_used_at=0, use_count=1, favorite=False, payload=json.dumps(data))


class TempDirTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.store = BlobStore(self.dir / "files", self.dir / "restore")

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, name, data=b"hello"):
        path = self.dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return str(path)


class BlobStoreTest(TempDirTest):
    def test_put_bytes_dedupes(self):
        a = self.store.put_bytes(b"abc", ".png")
        b = self.store.put_bytes(b"abc", ".png")
        self.assertEqual(a, b)
        self.assertTrue(a.endswith(".png"))
        self.assertEqual(self.store.path(a).read_bytes(), b"abc")

    def test_put_file_dedupes_and_leaves_no_temp(self):
        src = self.write("报价单.XLSX", b"x" * 3000)
        a = self.store.put_file(src)
        b = self.store.put_file(src)
        self.assertEqual(a, b)
        self.assertTrue(a.endswith(".xlsx"))
        files = [f for f in (self.dir / "files").rglob("*") if f.is_file()]
        self.assertEqual(len(files), 1)
        self.assertEqual(self.store.total_size(), 3000)

    def test_gc_keeps_referenced_and_recent(self):
        keep = self.store.put_bytes(b"keep", ".png")
        drop = self.store.put_bytes(b"drop", ".png")
        self.assertEqual(self.store.gc({keep}), 0)  # 刚写入的，宽限期内不删
        freed = self.store.gc({keep}, now=time.time() + GC_GRACE_SECONDS + 1)
        self.assertEqual(freed, 4)
        self.assertTrue(self.store.exists(keep))
        self.assertFalse(self.store.exists(drop))

    def test_stage_restores_name_and_is_a_copy(self):
        name = self.store.put_file(self.write("a.txt", b"original"))
        staged = self.store.stage(name, '报价单:最终版?.txt', mtime=1_000_000_000)
        self.assertEqual(staged.name, "报价单_最终版_.txt")
        self.assertAlmostEqual(staged.stat().st_mtime, 1_000_000_000, delta=2)
        staged.write_bytes(b"changed")
        self.assertEqual(self.store.path(name).read_bytes(), b"original")  # 改临时文件不影响副本

    def test_clean_staging(self):
        name = self.store.put_bytes(b"x", ".txt")
        staged = self.store.stage(name, "x.txt")
        self.store.clean_staging()
        self.assertTrue(staged.exists())
        self.store.clean_staging(now=time.time() + STAGING_KEEP_SECONDS + 10)
        self.assertFalse(staged.exists())

    def test_referenced_names(self):
        clips = [
            make_clip("image", {"blob": "a.png", "thumb": "a.thumb.png"}),
            make_clip("file", {"files": [{"backup": "b.zip", "thumb": ""}, {"backup": "", "thumb": "c.thumb.png"}]}),
        ]
        self.assertEqual(referenced_names(clips), {"a.png", "a.thumb.png", "b.zip", "c.thumb.png"})


class FileRefsTest(TempDirTest):
    def test_capture_skips_missing_and_records_file_id(self):
        path = self.write("a.txt")
        entries = capture([path, str(self.dir / "不存在.txt")])
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].name, "a.txt")
        self.assertEqual(entries[0].size, 5)
        self.assertRegex(entries[0].fid, r"^[0-9a-f]{8}:[0-9a-f]{16}$")
        self.assertEqual(resolve(entries[0], self.store.exists).status, Status.OK)

    def test_modified(self):
        path = self.write("a.txt")
        entry = capture([path])[0]
        Path(path).write_bytes(b"hello, changed")
        r = resolve(entry, self.store.exists)
        self.assertEqual(r.status, Status.MODIFIED)
        self.assertFalse(r.has_backup)

    def test_moved_and_renamed_is_found_by_file_id(self):
        path = self.write("下载/安装包.zip", b"zip")
        entry = capture([path])[0]
        new_path = self.dir / "整理好的" / "安装包-v2.zip"
        new_path.parent.mkdir()
        os.rename(path, new_path)
        r = resolve(entry, self.store.exists)
        self.assertEqual(r.status, Status.MOVED)
        self.assertEqual(os.path.normcase(r.path), os.path.normcase(str(new_path)))

    def test_moved_folder(self):
        folder = self.dir / "项目资料"
        folder.mkdir()
        entry = capture([str(folder)])[0]
        self.assertTrue(entry.is_dir)
        os.rename(folder, self.dir / "项目资料-归档")
        self.assertEqual(resolve(entry, self.store.exists).status, Status.MOVED)

    def test_deleted_with_and_without_backup(self):
        path = self.write("合同.pdf", b"pdf")
        entry = capture([path])[0]
        entry.backup = self.store.put_file(path)
        no_backup = FileEntry(**{**entry.to_dict(), "backup": ""})
        os.remove(path)
        self.assertEqual(resolve(entry, self.store.exists).status, Status.BACKUP)
        self.assertEqual(resolve(no_backup, self.store.exists).status, Status.MISSING)

    def test_helpers(self):
        self.assertEqual(filerefs.dedupe_key(["D:\\B.txt", "d:\\a.txt"]), filerefs.dedupe_key(["d:\\A.TXT", "d:\\b.txt"]))
        a, b = FileEntry("d:\\x\\a.txt", "a.txt"), FileEntry("d:\\x\\照片", "照片", is_dir=True)
        self.assertEqual(filerefs.title([a], 1), "a.txt")
        self.assertEqual(filerefs.title([b, a], 2), "照片（文件夹） 等 2 个")
        self.assertEqual(filerefs.search_text([a]), "a.txt  d:\\x")
        self.assertTrue(filerefs.should_backup(FileEntry("p", "a.txt", size=10), 100))
        self.assertFalse(filerefs.should_backup(FileEntry("p", "big.zip", size=1000), 100))
        self.assertFalse(filerefs.should_backup(FileEntry("p", "密码.kdbx", size=10), 100))
        self.assertFalse(filerefs.should_backup(FileEntry("p", "id_rsa", size=10), 100))
        self.assertTrue(filerefs.wants_thumb(FileEntry("p", "照片.JPG", size=10)))

    def test_carry_over(self):
        new = [FileEntry("p1", "a", size=1, mtime=5.0), FileEntry("p2", "b", size=2, mtime=6.0)]
        old = [{"path": "p1", "size": 1, "mtime": 5.0, "backup": "x", "thumb": "t"},
               {"path": "p2", "size": 999, "mtime": 6.0, "backup": "y", "thumb": ""}]
        filerefs.carry_over(new, old)
        self.assertEqual((new[0].backup, new[0].thumb), ("x", "t"))
        self.assertEqual(new[1].backup, "")  # 文件变了，旧副本不能沿用


class RestorerTest(TempDirTest):
    def test_file_paths(self):
        ok = self.write("在原处.txt")
        gone = self.write("被删了.txt", b"bak")
        lost = self.write("没副本.txt")
        changed = self.write("改过了.txt", b"v1")
        entries = capture([ok, gone, lost, changed])
        entries[1].backup = self.store.put_file(gone)
        entries[3].backup = self.store.put_file(changed)
        os.remove(gone)
        os.remove(lost)
        Path(changed).write_bytes(b"version 2")
        clip = make_clip("file", {"files": [e.to_dict() for e in entries], "total": 4})

        restorer = Restorer(self.store)
        statuses = [r.status for r in restorer.resolutions(clip)]
        self.assertEqual(statuses, [Status.OK, Status.BACKUP, Status.MISSING, Status.MODIFIED])

        paths, missing = restorer.file_paths(restorer.resolutions(clip))
        self.assertEqual(missing, ["没副本.txt"])
        self.assertEqual(paths[0], ok)
        self.assertEqual(Path(paths[1]).name, "被删了.txt")  # 用副本还原，文件名恢复
        self.assertEqual(Path(paths[1]).read_bytes(), b"bak")
        self.assertEqual(paths[2], changed)  # 默认用现在的版本

        paths, _ = restorer.file_paths(restorer.resolutions(clip), prefer_backup_when_modified=True)
        self.assertEqual(Path(paths[2]).read_bytes(), b"v1")  # 选择用复制时的版本

    def test_not_registered_count(self):
        clip = make_clip("file", {"files": [{"path": "a", "name": "a"}], "total": 1500})
        self.assertEqual(Restorer(self.store).not_registered(clip), 1499)


if __name__ == "__main__":
    unittest.main()
