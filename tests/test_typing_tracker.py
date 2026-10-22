import unittest

from inputvault.typing_tracker import FieldContext, TypingTracker, extract_added

CHAT = FieldContext(key="1:chat", exe="Weixin.exe", app="微信", title="张三")
DOC = FieldContext(key="2:doc", exe="Notepad.exe", app="记事本", title="笔记.txt")


class ExtractAddedTest(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(extract_added("", "你好"), "你好")
        self.assertEqual(extract_added("你好", "你好"), "")
        self.assertEqual(extract_added("今天开会", "今天下午三点在会议室开会"), "下午三点在会议室")
        self.assertEqual(extract_added("第一段\n", "第一段\n第二段新写的"), "第二段新写的")

    def test_rewrite_and_delete(self):
        self.assertEqual(extract_added("我觉得可以", "我认为不太行"), "认为不太行")
        self.assertEqual(extract_added("删掉一些字", "删掉字"), "")

    def test_separate_edits(self):
        before = "开头。" + "中间不变的很长一段内容" * 3 + "结尾。"
        after = "开头加了一句。" + "中间不变的很长一段内容" * 3 + "结尾也加了。"
        self.assertEqual(extract_added(before, after), "加了一句\n也加了")


class TrackerTest(unittest.TestCase):
    def setUp(self):
        self.saved: list[tuple[str, str]] = []
        self.tracker = TypingTracker(lambda content, ctx: self.saved.append((content, ctx.app)))

    def feed(self, ctx, *texts):
        for text in texts:
            self.tracker.observe(ctx, text)

    def test_chat_send_clears_field(self):
        self.feed(CHAT, "", "你", "你好", "你好呀", "", "第二条消息", "")
        self.assertEqual(self.saved, [("你好呀", "微信"), ("第二条消息", "微信")])

    def test_document_records_only_added_part(self):
        self.feed(DOC, "已有的旧内容\n", "已有的旧内容\n新写", "已有的旧内容\n新写的一行")
        self.tracker.observe(None, None)
        self.assertEqual(self.saved, [("新写的一行", "记事本")])

    def test_focus_switch_ends_session(self):
        self.feed(CHAT, "", "在微信里打字")
        self.feed(DOC, "文档", "文档里也打字")
        self.tracker.end()
        self.assertEqual(self.saved, [("在微信里打字", "微信"), ("里也打字", "记事本")])

    def test_focus_without_typing_saves_nothing(self):
        self.feed(DOC, "原本就有的内容")
        self.tracker.observe(None, None)
        self.feed(CHAT, "")
        self.tracker.end()
        self.assertEqual(self.saved, [])

    def test_too_short_is_ignored(self):
        self.feed(CHAT, "", "a", "")
        self.assertEqual(self.saved, [])

    def test_draft_deleted_is_still_saved(self):
        # 打了一段又全删掉，也记下来，方便找回
        self.feed(CHAT, "", "写了一半的草稿", "")
        self.assertEqual(self.saved, [("写了一半的草稿", "微信")])

    def test_end_is_idempotent(self):
        self.feed(CHAT, "", "一句话")
        self.tracker.end()
        self.tracker.end()
        self.assertEqual(len(self.saved), 1)


if __name__ == "__main__":
    unittest.main()
