from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from visualization.people_log_adapter import PeopleLogError, load_people_log


class PeopleLogAdapterTests(unittest.TestCase):
    def write_csv(self, directory: str, content: str) -> Path:
        path = Path(directory, "people_log.csv")
        path.write_text(content, encoding="utf-8")
        return path

    def test_b_week_four_columns_are_read_without_filling_empty_values(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            log = load_people_log(self.write_csv(
                directory,
                "step,time_s,person_id,x,y,evacuated,heading,risk,dose,conflict,exit_switch\n"
                "0,0.0,0,1,1,False,,,,,\n"
                "0,0.0,1,1,1,false,,,,,\n"
                "1,0.5,0,1,2,false,east,0.2,0.1,,\n"
                "1,0.5,1,1,2,true,,,,conflict,exit_a\n",
            ))
        self.assertEqual([frame.step for frame in log.frames], [0, 1])
        self.assertIsNone(log.frames[0].people[0].heading)
        self.assertEqual(log.frames[1].people[0].risk, 0.2)
        self.assertEqual(log.frames[1].evacuated_count, 1)
        self.assertEqual(log.frames[1].people[1].exit_switch, "exit_a")

    def test_rejects_missing_required_data_and_duplicate_people(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            missing = self.write_csv(directory, "step,time_s,person_id,x,y\n0,0,0,1,1\n")
            with self.assertRaisesRegex(PeopleLogError, "evacuated"):
                load_people_log(missing)
            duplicate = self.write_csv(
                directory,
                "step,time_s,person_id,x,y,evacuated\n0,0,0,1,1,false\n0,0,0,2,1,false\n",
            )
            with self.assertRaisesRegex(PeopleLogError, "duplicate person_id"):
                load_people_log(duplicate)


if __name__ == "__main__":
    unittest.main()
