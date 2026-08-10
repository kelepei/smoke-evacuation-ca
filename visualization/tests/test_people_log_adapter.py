from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from visualization.people_log_adapter import (
    PeopleLogError,
    load_people_log,
    replay_people,
)


class PeopleLogAdapterTests(unittest.TestCase):
    def test_preserves_b_overlap_and_empty_optional_fields(self) -> None:
        content = (
            "step,time_s,person_id,x,y,evacuated,heading,risk,dose,conflict,exit_switch\n"
            "3,1.5,1,4,5,false,,,,,\n"
            "3,1.5,2,4,5,0,east,0.2,0.0,,\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "people_log.csv"
            path.write_text(content, encoding="utf-8")
            rows = load_people_log(path)

        replay = replay_people(rows, 3)
        self.assertEqual([(person["x"], person["y"]) for person in replay], [(4, 5), (4, 5)])
        self.assertIsNone(replay[0]["heading"])
        self.assertIsNone(replay[0]["risk"])
        self.assertEqual(replay[1]["heading"], "east")
        self.assertEqual(replay[1]["risk"], 0.2)

    def test_rejects_missing_required_column(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "people_log.csv"
            path.write_text("step,time_s,person_id,x,y\n0,0,1,1,1\n", encoding="utf-8")
            with self.assertRaisesRegex(PeopleLogError, "evacuated"):
                load_people_log(path)
