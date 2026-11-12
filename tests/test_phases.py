"""行为段必须自己收尾，不能有哪一段一口气吃满整场"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import xhs_auto as x


class FakeClock:
    """sleep 推进虚拟时钟，避免真的等几分钟"""

    def __init__(self):
        self.t = 0.0

    def time(self):
        return self.t

    def sleep(self, s):
        self.t += s


class TestDriftBudget(unittest.TestCase):
    def test_budget_is_shorter_than_a_session(self):
        """一段正常浏览远短于 20 分钟，且至少会点开两篇"""
        for _ in range(400):
            span, budget = x.drift_budget()
            self.assertGreaterEqual(span, 35)
            self.assertLessEqual(span, 210)
            self.assertGreaterEqual(budget, 2)
            self.assertLessEqual(budget, 10)
        self.assertLess(210, 20 * 60)

    def test_drift_returns_before_global_deadline(self):
        """即使一篇都点不开，也只跑自己那一段就交回主循环"""
        clock = FakeClock()
        stats = {"screens": 0, "notes": 0, "flipped": 0, "scrolls": 0,
                 "liked": 0, "faved": 0, "ai": 0, "dwell": [],
                 "deep": 0, "shallow": 0, "depths": [], "deep_dwell": []}
        args = type("Args", (), {"verbose": False})()
        deadline = 20 * 60

        with mock.patch.object(x.time, "time", clock.time), \
             mock.patch.object(x.time, "sleep", clock.sleep), \
             mock.patch.object(x, "drift_budget", return_value=(90.0, 999)), \
             mock.patch.object(x, "geom", return_value=1), \
             mock.patch.object(x, "feed_swipe"), \
             mock.patch.object(x, "feed_swipe_back"), \
             mock.patch.object(x, "open_note", return_value=None), \
             mock.patch.object(x, "device_alive", return_value=True), \
             mock.patch.object(x, "dismiss_overlay", return_value=True), \
             mock.patch.object(x, "ensure_feed"), \
             mock.patch.object(x, "back"), \
             mock.patch.object(x, "hard_recover"):
            x.seg_drift(deadline, set(), args, stats, 0.5)

        # 旧实现是 while time.time() < deadline，会一直跑到 1200s
        self.assertLess(clock.t, 100.0)
        self.assertLess(clock.t, deadline / 4)

    def test_drift_stops_after_budget_even_with_time_left(self):
        """点开够篇数就收尾，不必等时间耗尽"""
        clock = FakeClock()
        stats = {"screens": 0, "notes": 0, "flipped": 0, "scrolls": 0,
                 "liked": 0, "faved": 0, "ai": 0, "dwell": [],
                 "deep": 0, "shallow": 0, "depths": [], "deep_dwell": []}
        args = type("Args", (), {"verbose": False})()
        calls = {"n": 0}

        def fake_open(*a, **kw):
            calls["n"] += 1
            return {"title": "t", "style": "glance", "imgs": 1,
                    "flipped": 0, "scrolls": 0, "liked": False,
                    "faved": False, "dwell": 3.0, "ai": False, "depth": 0.5}

        with mock.patch.object(x.time, "time", clock.time), \
             mock.patch.object(x.time, "sleep", clock.sleep), \
             mock.patch.object(x, "drift_budget", return_value=(600.0, 3)), \
             mock.patch.object(x, "geom", return_value=1), \
             mock.patch.object(x, "feed_swipe"), \
             mock.patch.object(x, "feed_swipe_back"), \
             mock.patch.object(x, "open_note", side_effect=fake_open), \
             mock.patch.object(x, "report"):
            x.seg_drift(1200, set(), args, stats, 0.5)

        self.assertEqual(calls["n"], 3)
        self.assertLess(clock.t, 600.0)


class TestOtherPhasesHaveBounds(unittest.TestCase):
    """其余四种段都有显式上界，不会因为抽到一次就锁死整场"""

    def test_bounded_loops(self):
        import inspect
        for name in ("seg_skim", "seg_deep", "seg_topic_search"):
            src = inspect.getsource(getattr(x, name))
            self.assertIn("range(", src, f"{name} 没有循环次数上界")
        self.assertIn("drift_budget()", inspect.getsource(x.seg_drift))


if __name__ == "__main__":
    unittest.main()
