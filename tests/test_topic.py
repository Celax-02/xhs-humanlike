"""主题定向（AI + AI 办公）的单元测试。运行：python3 -m unittest discover -s tests"""
import random
import types
import unittest
from unittest.mock import patch
from urllib.parse import quote

import xhs_auto as x


class TopicStateMixin:
    """测试要改全局 TOPIC，用完必须还回去，否则会污染其他用例"""

    def setUp(self):
        self._topic, self._strict = x.TOPIC, x.TOPIC_STRICT
        x.TOPIC = x.TOPIC_SETS["ai"]

    def tearDown(self):
        x.TOPIC, x.TOPIC_STRICT = self._topic, self._strict


class TopicWordsTest(TopicStateMixin, unittest.TestCase):
    def test_ai_core_hits(self):
        for t in ["Claude 更新说明", "LLM 到底是个啥", "DeepSeek V4 实测",
                  "RAG 架构设计复盘", "大模型为什么会幻觉"]:
            self.assertTrue(x.hits_topic(t), t)

    def test_office_hits(self):
        for t in ["AI办公：一周省下10小时", "AI写PPT 全流程",
                  "会议纪要 自动整理", "AI 周报生成器",
                  "Copilot 实战", "Notion AI 知识库搭建"]:
            self.assertTrue(x.hits_topic(t), t)

    def test_bare_ai_word_boundary(self):
        # email / detail 里的 ai 不能被当成 AI 内容
        self.assertFalse(x.hits_topic("email 里的一封 detail 说明"))
        self.assertFalse(x.hits_topic("storage 扩容记录"))

    def test_local_life_is_not_topic(self):
        for t in ["深圳9店｜月子中心报价表", "留学中介怎么选", "周末探店｜湘菜小店"]:
            self.assertFalse(x.hits_topic(t), t)

    def test_ai_office_topic_is_narrow(self):
        x.TOPIC = x.TOPIC_SETS["ai_office"]
        self.assertTrue(x.hits_topic("AI办公 效率工具"))
        self.assertFalse(x.hits_topic("DeepSeek V4 实测对比"))


class TopicBiasTest(TopicStateMixin, unittest.TestCase):
    def test_bias_ordering(self):
        ai = x.topic_bias("AI办公 全流程复盘", 0.6)
        neutral = x.topic_bias("今天天气不错", 0.6)
        off = x.topic_bias("深圳9店｜月子中心报价表", 0.6)
        self.assertGreater(ai, 1.0)
        self.assertGreater(neutral, off)
        self.assertLess(off, 0.2)

    def test_bias_disabled(self):
        self.assertEqual(x.topic_bias("月子中心报价表", 0), 1.0)
        x.TOPIC = None
        self.assertEqual(x.topic_bias("月子中心报价表", 0.6), 1.0)

    def test_pick_note_prefers_on_topic(self):
        x.TOPIC_STRICT = 0.6
        cards = [("AI办公 全流程复盘：从周报到会议纪要", 10, 10),
                 ("Claude 更新说明", 20, 20),
                 ("深圳9店｜月子中心报价表", 30, 30),
                 ("周末探店｜湘菜小店", 40, 40)]
        args = types.SimpleNamespace(depth=0.6)
        random.seed(11)
        picks = [x.pick_note(cards, args)[0] for _ in range(400)]
        good = sum(1 for p in picks if x.hits_topic(p)) / len(picks)
        off = sum(1 for p in picks if x.OFF_TOPIC_WORDS.search(p)) / len(picks)
        self.assertGreater(good, 0.65)
        self.assertLess(off, 0.15)


class SearchKeywordTest(unittest.TestCase):
    def test_focus_ai(self):
        random.seed(1)
        for _ in range(40):
            self.assertIn(x.pick_search_kw("ai"), x.SEARCH_KW_AI)

    def test_focus_office(self):
        random.seed(2)
        pool = x.SEARCH_KW_OFFICE_CN + x.SEARCH_KW_OFFICE_EN
        for _ in range(60):
            self.assertIn(x.pick_search_kw("office"), pool)

    def test_mixed_keeps_office_majority(self):
        random.seed(3)
        pool = x.SEARCH_KW_OFFICE_CN + x.SEARCH_KW_OFFICE_EN
        kws = [x.pick_search_kw("mixed") for _ in range(100)]
        office = sum(1 for k in kws if k in pool)
        self.assertGreater(office, 40)

    def test_chinese_keywords_are_encodable(self):
        for kw in x.SEARCH_KW_OFFICE_CN:
            self.assertTrue(x.CJK_RE.search(kw), kw)
            self.assertNotIn(" ", quote(kw, safe=""), kw)

    def test_chinese_goes_to_url_scheme(self):
        with patch.object(x, "open_search_cn", return_value=True) as cn, \
             patch.object(x, "open_search_ascii", return_value=True) as ascii_:
            self.assertTrue(x.open_search("AI办公"))
            cn.assert_called_once_with("AI办公")
            ascii_.assert_not_called()

    def test_ascii_goes_to_input_text(self):
        with patch.object(x, "open_search_cn", return_value=True) as cn, \
             patch.object(x, "open_search_ascii", return_value=True) as ascii_:
            self.assertTrue(x.open_search("Claude"))
            cn.assert_not_called()
            ascii_.assert_called_once_with("Claude")

    def test_scheme_failure_falls_back_to_ascii(self):
        with patch.object(x, "open_search_cn", return_value=False), \
             patch.object(x, "open_search_ascii", return_value=True) as ascii_:
            self.assertTrue(x.open_search("AI会议纪要"))
            self.assertIn(ascii_.call_args[0][0], x.SEARCH_KW_OFFICE_EN)
