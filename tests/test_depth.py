"""深度评分的单元测试。运行：python3 -m unittest discover -s tests"""
import unittest

import xhs_auto as x


class DepthScoreTest(unittest.TestCase):
    def test_meme_is_shallow(self):
        for t in ["香蕉meme：当你开始越来越少看到AI生成",
                  "哈哈哈哈这个梗真的笑死我了",
                  "表情包九宫格",
                  "今日穿搭分享"]:
            self.assertEqual(x.depth_band(x.depth_score(t)), "shallow", t)

    def test_methodology_is_deep(self):
        for t in ["为什么大模型总会胡说八道？从原理讲清幻觉",
                  "RAG 架构设计：从0到1搭建企业知识库的踩坑复盘",
                  "DeepSeek V4 实测对比：推理成本与效果",
                  "AI产品经理的三年复盘：我踩过的五个坑"]:
            self.assertEqual(x.depth_band(x.depth_score(t)), "deep", t)

    def test_ad_is_penalised(self):
        self.assertLess(x.depth_score("限时优惠｜AI课程报名咨询"),
                        x.depth_score("AI 课程该怎么设计"))

    def test_plain_title_is_normal(self):
        self.assertEqual(x.depth_band(x.depth_score("Claude更新，禁止复刻他人特定")),
                         "normal")

    def test_short_title_drops(self):
        self.assertLess(x.depth_score("AI"), x.depth_score("AI 行业观察与思考"))

    def test_score_bounds(self):
        for t in ["", "a" * 60, "😭😭😭😭😭😭😭😭", "meme meme meme"]:
            d = x.depth_score(t)
            self.assertGreaterEqual(d, 0.0)
            self.assertLessEqual(d, 1.0)


class CardWeightTest(unittest.TestCase):
    def test_deep_outweighs_shallow(self):
        deep = x.card_weight("为什么大模型会幻觉？原理解读", 0.8)
        shallow = x.card_weight("表情包九宫格", 0.8)
        self.assertGreater(deep, shallow * 5)

    def test_zero_pref_is_flat(self):
        self.assertAlmostEqual(x.card_weight("表情包九宫格", 0.0), 1.0)
        self.assertAlmostEqual(x.card_weight("原理解读长文", 0.0), 1.0)

    def test_weight_floor(self):
        self.assertGreaterEqual(x.card_weight("meme 梗 表情包 笑死 壁纸", 1.0), 0.06)


class CommentDepthTest(unittest.TestCase):
    def test_shallow_rarely_scrolls(self):
        self.assertTrue(all(x.comment_depth(0.8, 0.1) <= 2 for _ in range(200)))

    def test_deep_gets_more(self):
        deep = sum(x.comment_depth(0.7, 0.9) for _ in range(300)) / 300
        normal = sum(x.comment_depth(0.7, 0.45) for _ in range(300)) / 300
        self.assertGreater(deep, normal)


if __name__ == "__main__":
    unittest.main()
