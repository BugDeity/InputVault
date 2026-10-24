"""主窗口的筛选逻辑：离屏运行，不显示窗口。"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tempfile  # noqa: E402
import unittest  # noqa: E402
from pathlib import Path  # noqa: E402

from PySide6.QtWidgets import QApplication  # noqa: E402

from inputvault.blobs import BlobStore  # noqa: E402
from inputvault.restore import Restorer  # noqa: E402
from inputvault.storage import Storage  # noqa: E402
from inputvault.ui.icons import AppIcons  # noqa: E402
from inputvault.ui.panel import Panel  # noqa: E402
from inputvault.ui.theme import LIGHT  # noqa: E402

_app = QApplication.instance() or QApplication([])


class _PanelCase(unittest.TestCase):
    """共用的准备工作：三条记录 + 一个没显示出来的主窗口。这个类本身没有测试用例。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        self.db = Storage(":memory:")
        self.db.add("微信里的一句话", source_app="微信", source_exe="Weixin.exe", now=100)
        self.db.add("https://a.com", kind="url", source_app="Edge", source_exe="msedge.exe", now=200)
        self.db.add("报价单的说明", source_app="微信", source_exe="Weixin.exe", now=300)
        self.panel = Panel(self.db, Restorer(BlobStore(tmp / "files", tmp / "restore")), AppIcons(None), LIGHT)
        self.panel.refresh_sidebar()
        self.panel.refresh()

    def tearDown(self):
        self.panel.deleteLater()
        self.db.close()
        self._tmp.cleanup()

    def bar_visible(self) -> bool:
        return not self.panel.filter_bar.isHidden()


class FilterBarTest(_PanelCase):
    def test_hidden_when_everything_is_default(self):
        self.assertFalse(self.bar_visible())
        self.assertEqual(self.panel.list_count.text(), "3 条")

    def test_each_condition_shows_and_can_be_removed_alone(self):
        p = self.panel
        p._set_category("url")
        p._choose_app("msedge.exe")
        p._set_order("count")
        p._toggle_direction()
        p.search.setText("a.com")
        p.refresh()
        self.assertTrue(self.bar_visible())
        self.assertEqual(p.filter_bar.keys, ["search", "category", "app", "order", "direction"])

        p._remove_condition("app")
        self.assertIsNone(p.app_filter)
        self.assertEqual(p.scope.text(), "全部应用")
        self.assertEqual(p.filter_bar.keys, ["search", "category", "order", "direction"])
        p._remove_condition("search")
        self.assertEqual(p.search.text(), "")
        self.assertEqual(p.filter_bar.keys, ["category", "order", "direction"])

    def test_reset_restores_all_defaults(self):
        p = self.panel
        p._set_category("text")
        p._choose_app("Weixin.exe")
        p._set_order("count")
        p._toggle_direction()
        p.search.setText("报价")
        p.refresh()
        self.assertEqual(p.list_count.text(), "1 条")

        p.reset_filters()
        self.assertFalse(self.bar_visible())
        self.assertEqual((p.category, p.app_filter, p.order, p.ascending, p.search.text()),
                         ("all", None, "time", False, ""))
        self.assertEqual(p.list_count.text(), "3 条")
        self.assertEqual(p.list_title.text(), "全部记录")


class CopyFeedbackTest(_PanelCase):
    def clips_in_list(self):
        return [self.panel.list.item(i).data(256) for i in self.panel.list.clip_rows()]

    def test_copy_updates_in_place_without_reordering(self):
        before = [c.id for c in self.clips_in_list()]
        oldest = self.clips_in_list()[-1]
        self.db.touch(oldest.id, now=999)  # 复制一次：使用次数 +1、最近使用时间变成最新
        self.panel.update_clip(oldest.id)
        after = self.clips_in_list()
        self.assertEqual([c.id for c in after], before)  # 位置不变
        self.assertEqual(after[-1].use_count, 2)

    def test_toast(self):
        self.panel.show_toast("已复制到剪贴板")
        self.assertFalse(self.panel._toast.isHidden())
        self.assertEqual(self.panel._toast.text(), "已复制到剪贴板")


if __name__ == "__main__":
    unittest.main()
