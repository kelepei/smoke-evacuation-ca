from __future__ import annotations

import unittest
from pathlib import Path


class FinalPlatformPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.page = Path("visualization/prototype/final_platform.html").read_text(encoding="utf-8")

    def test_exposes_only_real_runtime_controls(self) -> None:
        for control in ("randomSeed", "timeStep", "maxSteps", "yamlFile"):
            self.assertIn(f'id="{control}"', self.page)
        self.assertNotIn('id="relationIntensity"', self.page)
        self.assertNotIn("Math.random", self.page)
        self.assertIn("当前不改变 B 移动", self.page)
        self.assertIn('id="totalPersons" value="等待初始化" disabled', self.page)
        self.assertIn("播放速度只影响页面请求间隔，不改变模型", self.page)

    def test_has_only_round_one_real_layers(self) -> None:
        for control in (
            "layerPeople",
            "layerExits",
            "layerSmoke",
            "layerOccupancy",
            "layerTrajectories",
        ):
            self.assertIn(f'id="{control}"', self.page)
        self.assertIn("/api/session/layers", self.page)
        self.assertIn("people_log.csv", self.page)
        self.assertIn("当前场景无烟源；不生成视觉烟雾", self.page)

    def test_history_ui_reads_only_real_output_api(self) -> None:
        self.assertIn("/api/experiments", self.page)
        self.assertIn('id="historyPanel"', self.page)
        self.assertIn("不会创建示例历史", self.page)
        self.assertNotIn("实时拥堵热力图", self.page)
        self.assertIn("仅统计 B 返回的 actual_exit", self.page)
        self.assertIn("NA · 上游未提供", self.page)


if __name__ == "__main__":
    unittest.main()
