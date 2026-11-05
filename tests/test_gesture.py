"""手势参数：起步位移必须跨出触摸容差，否则会触发长按（分享面板）"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import xhs_auto as x


class TestDragPlan(unittest.TestCase):
    SLOP_PX = 16          # 安卓触摸容差，超过它长按就会被取消

    def test_first_move_clears_slop(self):
        """第一个 MOVE 的位移要明显大于触摸容差"""
        for dist in (60, 120, 400, 750, 1400):
            for dur in (120, 300, 900, 1600):
                steps, start = x.plan_drag(dur, dist)
                e1 = start + (1 - start) * x._ease(1 / steps, "smooth")
                self.assertGreater(dist * e1, self.SLOP_PX,
                                   f"dist={dist} dur={dur} 首帧只走了 {dist*e1:.1f}px")

    def test_slowest_ease_still_clears_slop(self):
        """最慢的 in 曲线同样要跨过容差"""
        for dist in (100, 400, 900):
            steps, start = x.plan_drag(1300, dist)
            e1 = start + (1 - start) * x._ease(1 / steps, "in")
            self.assertGreater(dist * e1, self.SLOP_PX)

    def test_steps_fit_duration(self):
        """步数×真实开销要贴近设定时长，不能拖成两倍"""
        for dur in (150, 400, 900, 1600):
            steps, _ = x.plan_drag(dur, 600)
            actual = steps * x.STEP_COST_MS
            self.assertLessEqual(actual, max(400, dur * 1.8),
                                 f"dur={dur} 实际按了 {actual:.0f}ms")
            self.assertGreaterEqual(steps, 4)

    def test_steps_capped(self):
        steps, _ = x.plan_drag(5000, 600)
        self.assertLessEqual(steps, 22)


if __name__ == "__main__":
    unittest.main()
