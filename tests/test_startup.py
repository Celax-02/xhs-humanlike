"""启动归位：不在 App 里要能拉回来，首页 tab 要认得准"""
import os
import sys
import unittest
import xml.etree.ElementTree as ET
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import xhs_auto as x


def root_of(xml):
    return ET.fromstring(xml)


class FakeResult:
    def __init__(self, stdout=""):
        self.stdout = stdout


def focus_line(w):
    return f"  mCurrentFocus=Window{{abc123 u0 {w}}}\n"


class TestWindowState(unittest.TestCase):

    def test_feed(self):
        with mock.patch.object(x, "sh", return_value=FakeResult(
                focus_line("com.xingin.xhs/com.xingin.xhs.index.v2.IndexActivityV2"))):
            self.assertEqual(x.window_state(), ("feed", "com.xingin.xhs"))

    def test_detail(self):
        with mock.patch.object(x, "sh", return_value=FakeResult(
                focus_line("com.xingin.xhs/com.xingin.matrix.notedetail.NoteDetailActivity"))):
            self.assertEqual(x.window_state(), ("detail", "com.xingin.xhs"))

    def test_outside_app_launcher(self):
        """在桌面时 pkg 不是小红书 —— 这是旧版永远只按返回、救不回来的情形"""
        with mock.patch.object(x, "sh", return_value=FakeResult(
                focus_line("com.android.launcher3/com.android.launcher3.Launcher"))):
            st, pkg = x.window_state()
            self.assertEqual(st, "other")
            self.assertNotEqual(pkg, x.PKG)

    def test_screen_off_no_focus(self):
        with mock.patch.object(x, "sh", return_value=FakeResult("  mCurrentFocus=null\n")):
            self.assertEqual(x.window_state(), ("unknown", None))


class TestHomeTab(unittest.TestCase):
    BOTTOM = 'bounds="[0,2150][216,2194]"'

    def test_selected(self):
        r = root_of(f'<hierarchy><node content-desc="首页" selected="true" '
                   f'clickable="true" {self.BOTTOM}/></hierarchy>')
        on_home, btn = x.home_tab(r)
        self.assertTrue(on_home)
        self.assertEqual(btn, (108, 2172))

    def test_other_tab_needs_tap(self):
        r = root_of(f'<hierarchy>'
                    f'<node content-desc="首页" selected="false" clickable="true" {self.BOTTOM}/>'
                    f'<node content-desc="我" selected="true" clickable="true" '
                    f'bounds="[864,2150][1080,2194]"/></hierarchy>')
        on_home, btn = x.home_tab(r)
        self.assertFalse(on_home)
        self.assertEqual(btn, (108, 2172))

    def test_no_home_tab(self):
        r = root_of('<hierarchy><node content-desc="点赞" selected="true" '
                    'bounds="[0,2150][216,2194]"/></hierarchy>')
        self.assertEqual(x.home_tab(r), (False, None))

    def test_ignore_home_word_in_title(self):
        """标题里的「首页」不算底栏按钮"""
        r = root_of('<hierarchy><node content-desc="首页推荐" selected="true" '
                    'bounds="[0,300][216,400]"/></hierarchy>')
        self.assertEqual(x.home_tab(r), (False, None))


if __name__ == "__main__":
    unittest.main()
