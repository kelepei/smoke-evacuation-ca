from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from experiments.scene_config_pipeline import (
    SceneConfigPipelineError,
    generate_positioned_population,
    validate_canonical_scene_config,
)


class SceneConfigPipelineTests(unittest.TestCase):
    def _map(self, root: Path, free: int = 8) -> Path:
        cells = []
        for y in range(3):
            for x in range(4):
                kind = "free" if len(cells) < free else "wall"
                if (x, y) == (3, 1):
                    kind = "exit"
                cells.append({"x": x, "y": y, "type": kind, "semantic": "hall"})
        path = root / "map.json"
        path.write_text(json.dumps({"width": 4, "height": 3, "cells": cells}), encoding="utf-8")
        return path

    def test_ui_config_generates_exact_count_and_legal_positions(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            result = generate_positioned_population(
                scene_config={"total_persons": 4, "random_seed": 42, "relation_intensity": 0.6,
                              "profile_ratios": {"student": 0.75, "teacher": 0.25}},
                map_path=self._map(root),
                destination=root / "out",
            )
            payload = json.loads(result["population_path"].read_text(encoding="utf-8"))
            self.assertEqual(4, len(payload["persons"]))
            positions = {(p["x"], p["y"]) for p in payload["persons"]}
            self.assertEqual(4, len(positions))
            self.assertTrue(all(p["profile"] in {"student", "teacher"} for p in payload["persons"]))

    def test_same_seed_reproduces_generated_population(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            config = {"total_persons": 3, "random_seed": 7, "relation_intensity": 0.6,
                      "profile_ratios": {"student": 1.0}}
            first = generate_positioned_population(scene_config=config, map_path=self._map(root), destination=root / "a")
            second = generate_positioned_population(scene_config=config, map_path=self._map(root), destination=root / "b")
            self.assertEqual(first["population_path"].read_text(encoding="utf-8"), second["population_path"].read_text(encoding="utf-8"))

    def test_invalid_ratio_and_capacity_are_explicit(self) -> None:
        with self.assertRaises(SceneConfigPipelineError):
            validate_canonical_scene_config({"total_persons": 2, "profile_ratios": {"student": 0.8}, "relation_intensity": 0.5})
        with tempfile.TemporaryDirectory() as raw:
            with self.assertRaises(SceneConfigPipelineError):
                generate_positioned_population(
                    scene_config={"total_persons": 20, "profile_ratios": {"student": 1.0}, "relation_intensity": 0.5},
                    map_path=self._map(Path(raw), free=1), destination=Path(raw) / "out",
                )


if __name__ == "__main__":
    unittest.main()
