"""剪贴板采集：直接喂构造好的 QMimeData，离屏运行，不碰系统剪贴板。"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tempfile  # noqa: E402
import unittest  # noqa: E402
from pathlib import Path  # noqa: E402

from PySide6.QtCore import QByteArray, QMimeData, QUrl  # noqa: E402
from PySide6.QtGui import QColor, QImage  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from inputvault.blobs import BlobStore  # noqa: E402
from inputvault.collector import ClipboardCollector  # noqa: E402
from inputvault.config import Config  # noqa: E402
from inputvault.restore import MARKER, Restorer  # noqa: E402
from inputvault.storage import Storage  # noqa: E402

# 用 QApplication（而不是 QGuiApplication）：同一次运行里界面测试也要创建窗口部件
_app = QApplication.instance() or QApplication([])


def file_mime(*paths, cut=False):
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(p)) for p in paths])
    effect = 2 if cut else 1
    mime.setData('application/x-qt-windows-mime;value="Preferred DropEffect"', QByteArray(effect.to_bytes(4, "little")))
    return mime


def image_mime(width=200, height=100, color="#3366cc"):
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor(color))
    mime = QMimeData()
    mime.setImageData(image)
    return mime


class CollectorTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.storage = Storage(":memory:")
        self.blobs = BlobStore(self.dir / "files", self.dir / "restore")
        self.config = Config(backup_max_mb=1)
        self.collector = ClipboardCollector(self.storage, self.blobs, self.config)

    def tearDown(self):
        self.collector.shutdown()
        self.storage.close()
        self._tmp.cleanup()

    def feed(self, mime, exe="explorer.exe", title="下载"):
        self.collector.process(mime, exe, title)
        self.collector.wait_for_background()

    def write(self, name, data=b"hello"):
        path = self.dir / "src" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def test_files_are_registered_and_small_ones_backed_up(self):
        doc = self.write("报价单.txt", b"price")
        photo = self.dir / "src" / "照片.png"
        QImage(400, 300, QImage.Format.Format_RGB32).save(str(photo))
        big = self.write("大文件.zip", b"z" * (2 * 1024 * 1024))
        secret = self.write("id_rsa", b"key")
        self.feed(file_mime(doc, photo, big, secret, cut=True))

        clips = self.storage.search("type:文件")
        self.assertEqual(len(clips), 1)
        clip = clips[0]
        self.assertEqual(clip.source_app, "资源管理器")
        data = clip.data
        self.assertTrue(data["cut"])
        files = {f["name"]: f for f in data["files"]}
        self.assertTrue(files["报价单.txt"]["backup"])
        self.assertTrue(files["照片.png"]["backup"])
        self.assertTrue(files["照片.png"]["thumb"])
        self.assertEqual(files["大文件.zip"]["backup"], "")  # 超过 1 MB 上限
        self.assertEqual(files["id_rsa"]["backup"], "")  # 敏感文件不备份
        self.assertEqual(len(self.storage.search("报价单")), 1)  # 能按文件名搜到

        # 原文件删掉后，用副本还原，文件名不变
        doc.unlink()
        mime, missing = Restorer(self.blobs).mime_for(self.storage.get(clip.id))
        self.assertEqual(missing, [])
        restored = sorted(Path(u.toLocalFile()).name for u in mime.urls())
        self.assertEqual(restored, sorted(["报价单.txt", "照片.png", "大文件.zip", "id_rsa"]))
        self.assertTrue(mime.hasFormat(MARKER))

    def test_same_files_again_merge_and_keep_backup(self):
        doc = self.write("a.txt")
        self.feed(file_mime(doc))
        first = self.storage.search("type:文件")[0]
        backup = first.data["files"][0]["backup"]
        self.feed(file_mime(doc))
        clips = self.storage.search("type:文件")
        self.assertEqual(len(clips), 1)
        self.assertEqual(clips[0].use_count, 2)
        self.assertEqual(clips[0].data["files"][0]["backup"], backup)

    def test_image_is_saved_and_deduped(self):
        self.feed(image_mime(), exe="SnippingTool.exe", title="")
        self.feed(image_mime(), exe="SnippingTool.exe", title="")
        clips = self.storage.search("type:图片")
        self.assertEqual(len(clips), 1)
        clip = clips[0]
        self.assertEqual(clip.content, "图片 200×100")
        self.assertEqual(clip.use_count, 2)
        self.assertEqual(clip.source_app, "截图工具")
        self.assertTrue(self.blobs.exists(clip.data["blob"]))
        self.assertTrue(self.blobs.exists(clip.data["thumb"]))

        restorer = Restorer(self.blobs)
        mime, _ = restorer.mime_for(clip)
        self.assertTrue(mime.hasImage())
        self.assertFalse(mime.hasUrls())
        mime, _ = restorer.mime_for(clip, as_file=True)
        self.assertFalse(mime.hasImage())
        self.assertTrue(Path(mime.urls()[0].toLocalFile()).name.startswith("图片_"))
        mime, _ = restorer.mime_for(clip, with_file=True)  # 拖拽：两种都有
        self.assertTrue(mime.hasImage() and mime.hasUrls())

    def test_text_wins_over_image(self):
        mime = image_mime()
        mime.setText("Excel 里复制的表格")
        self.feed(mime, exe="EXCEL.EXE", title="")
        self.assertEqual([c.content for c in self.storage.search()], ["Excel 里复制的表格"])

    def test_skips_own_restore_and_blacklisted_apps(self):
        mime = QMimeData()
        mime.setText("我们自己还原出去的")
        mime.setData(MARKER, QByteArray(b"1"))
        self.feed(mime)
        self.feed(image_mime(), exe="KeePass.exe")
        self.feed(file_mime(self.write("a.txt")), exe="KeePass.exe")
        self.assertEqual(self.storage.count(), 0)

    def test_switches(self):
        self.config.capture_files = False
        self.config.capture_images = False
        self.feed(file_mime(self.write("a.txt")))
        self.feed(image_mime())
        self.assertEqual(self.storage.count(), 0)  # 关掉后也不会把 file:/// 当文字记下来


if __name__ == "__main__":
    unittest.main()
