import time
import unittest

from inputvault.classify import classify
from inputvault.privacy import sensitive_reason, skip_reason
from inputvault.textutil import preview_line, relative_time


class ClassifyTest(unittest.TestCase):
    def test_kinds(self):
        cases = {
            "https://example.com/a?b=1": "url",
            "someone@example.com": "email",
            "13800138000": "phone",
            "138 0013 8000": "phone",
            "D:\\my_workSpace\\InputVault": "path",
            "1,234.56": "number",
            '{"a": 1, "b": [2, 3]}': "json",
            "def foo():\n    return 1\n": "code",
            "const a = 1;\nconsole.log(a);": "code",
            "今天天气不错，我们出去走走吧。": "text",
            "第一行\n第二行": "text",
            "   ": "text",
        }
        for text, kind in cases.items():
            with self.subTest(text=text):
                self.assertEqual(classify(text), kind)


class PrivacyTest(unittest.TestCase):
    def test_sensitive(self):
        self.assertEqual(sensitive_reason("key: sk-ant-abcdefghijklmnopqrstuvwxyz123"), "API Key")
        self.assertEqual(sensitive_reason("-----BEGIN RSA PRIVATE KEY-----\nabc"), "私钥")
        self.assertEqual(sensitive_reason("身份证 11010519491231002X"), "身份证号")
        self.assertEqual(sensitive_reason("卡号 4111111111111111"), "银行卡号")

    def test_not_sensitive(self):
        self.assertIsNone(sensitive_reason("你好，明天见"))
        self.assertIsNone(sensitive_reason("订单号 110105194912310021"))  # 校验位不对
        self.assertIsNone(sensitive_reason("4111111111111112"))  # Luhn 不通过

    def test_blacklist(self):
        self.assertIsNotNone(skip_reason("abc", "KeePass.exe", ["keepass.exe"], True))
        self.assertIsNone(skip_reason("abc", "chrome.exe", ["keepass.exe"], True))
        self.assertIsNone(skip_reason("卡号 4111111111111111", "chrome.exe", [], False))


class TextUtilTest(unittest.TestCase):
    def test_preview_line(self):
        self.assertEqual(preview_line("  第一行\n\n  第二行\t\t尾巴 "), "第一行 ⏎ 第二行 尾巴")

    def test_relative_time(self):
        now = time.mktime((2026, 10, 22, 15, 0, 0, 0, 0, -1))
        self.assertEqual(relative_time(now - 10, now), "刚刚")
        self.assertEqual(relative_time(now - 300, now), "5 分钟前")
        self.assertEqual(relative_time(now - 5 * 3600, now), "今天 10:00")
        self.assertEqual(relative_time(now - 24 * 3600, now), "昨天 15:00")
        self.assertEqual(relative_time(now - 10 * 86400, now), "10-12 15:00")


if __name__ == "__main__":
    unittest.main()
