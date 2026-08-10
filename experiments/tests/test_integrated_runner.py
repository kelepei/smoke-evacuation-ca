"""Tests for D's no-position-fabrication integrated runtime entry point."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from experiments.integrated_runner import (
    IntegrationInputError,
    build_runtime_scene,
    create_integrated_runner,
)


def _write_map(path: Path) -> None:
    cells = []
    for y in range(5):
        for x in range(5):
            cell_type = "wall" if x in (0, 4) or y in (0, 4) else "free"
            if (x, y) == (4, 2):
                cell_type = "exit"
            cells.append({"x": x, "y": y, "type": cell_type})
    path.write_text(
        json.dumps({"width": 5, "height": 5, "cell_size": 0.5, "cells": cells}),
        encoding="utf-8",
    )


def _write_population(path: Path, persons: list[dict]) -> None:
    path.write_text(
        json.dumps({"persons": persons, "relations": []}), encoding="utf-8"
    )


class IntegratedRunnerTests(unittest.TestCase):
    def test_builds_b_scene_from_a_map_and_already_positioned_people(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            map_path = root / "map.json"
            population_path = root / "population.json"
            _write_map(map_path)
            _write_population(
                population_path,
                [
                    {"id": 0, "x": 1, "y": 1, "profile": "student"},
                    {"id": 1, "x": 2, "y": 1, "profile": "teacher"},
                ],
            )

            scene = build_runtime_scene(
                map_path=map_path, population_path=population_path, random_seed=7
            )

        self.assertEqual([person.id for person in scene.persons], [1, 2])
        self.assertEqual([(person.x, person.y) for person in scene.persons], [(1, 1), (2, 1)])
        self.assertEqual(scene.parameters["d_position_policy"], "reject_missing_invalid_or_overlapping")
        self.assertEqual(len(scene.exits), 1)

    def test_rejects_missing_a_assigned_position(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            map_path = root / "map.json"
            population_path = root / "population.json"
            _write_map(map_path)
            _write_population(population_path, [{"id": 0, "profile": "student"}])

            with self.assertRaisesRegex(IntegrationInputError, "A-assigned integer x"):
                build_runtime_scene(map_path=map_path, population_path=population_path)

    def test_rejects_overlapping_a_assigned_positions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            map_path = root / "map.json"
            population_path = root / "population.json"
            _write_map(map_path)
            _write_population(
                population_path,
                [{"id": 0, "x": 1, "y": 1}, {"id": 1, "x": 1, "y": 1}],
            )

            with self.assertRaisesRegex(IntegrationInputError, "overlaps"):
                build_runtime_scene(map_path=map_path, population_path=population_path)

    def test_runs_b_one_step_without_d_position_fabrication(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            map_path = root / "map.json"
            population_path = root / "population.json"
            _write_map(map_path)
            _write_population(population_path, [{"id": 0, "x": 1, "y": 1}])
            runner = create_integrated_runner(
                map_path=map_path,
                population_path=population_path,
                output_root=root / "outputs",
                max_steps=2,
            )
            try:
                initial = runner.initialize()
                after_step = runner.step()
            finally:
                runner.close()

        self.assertEqual(initial["step"], 0)
        self.assertEqual(after_step["step"], 1)
        self.assertEqual(after_step["people"][0]["person_id"], 1)
