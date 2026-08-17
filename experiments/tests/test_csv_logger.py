from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from experiments.csv_logger import CsvExperimentLogger, CsvLogError


def make_snapshot(
    step: int,
    *,
    person_1_evacuated: bool = False,
    events: list[dict] | None = None,
) -> dict:
    return {
        "schema_version": "0.1-draft",
        "run_id": "run_test",
        "scenario_id": "scene_test",
        "step": step,
        "time_step": 0.5,
        "time_s": step * 0.5,
        "people": [
            {
                "person_id": 1,
                "x": 5 if person_1_evacuated else 4 + step,
                "y": 2,
                "heading": None,
                "status": "EVACUATED" if person_1_evacuated else None,
                "target_exit": None,
                "actual_exit": "exit_01" if person_1_evacuated else None,
                "evacuated": person_1_evacuated,
                "smoke_concentration": 0.0,
                "risk": None,
                "dose": None,
                "info_state": None,
                "info_source": None,
                "receive_time": None,
                "follow_target": None,
            },
            {
                "person_id": 2,
                "x": 5,
                "y": 3,
                "evacuated": False,
            },
        ],
        "events": events or [],
    }


class CsvExperimentLoggerTests(unittest.TestCase):
    def test_writes_people_rows_and_one_derived_evacuation_event(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            logger = CsvExperimentLogger(
                temp_dir,
                run_id="run_test",
                scenario_id="scene_test",
                random_seed=42,
                time_step_s=0.5,
            )
            with logger:
                logger.record_snapshot(make_snapshot(0))
                logger.record_snapshot(
                    make_snapshot(1, person_1_evacuated=True)
                )
                logger.record_snapshot(
                    make_snapshot(2, person_1_evacuated=True)
                )

            with Path(temp_dir, "people_log.csv").open(
                newline="", encoding="utf-8"
            ) as file:
                people_rows = list(csv.DictReader(file))
            with Path(temp_dir, "event_log.csv").open(
                newline="", encoding="utf-8"
            ) as file:
                event_rows = list(csv.DictReader(file))

            self.assertEqual(len(people_rows), 6)
            self.assertEqual(
                [row["evacuated"] for row in people_rows[:2]],
                ["false", "false"],
            )
            self.assertEqual(people_rows[0]["risk"], "")
            self.assertEqual(len(event_rows), 1)
            self.assertEqual(event_rows[0]["event_type"], "evac_success")
            self.assertEqual(event_rows[0]["person_id"], "1")

    def test_explicit_evacuation_event_is_not_duplicated(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            logger = CsvExperimentLogger(
                temp_dir,
                run_id="run_test",
                scenario_id="scene_test",
                random_seed=None,
                time_step_s=0.5,
            )
            with logger:
                logger.record_snapshot(make_snapshot(0))
                logger.record_snapshot(
                    make_snapshot(
                        1,
                        person_1_evacuated=True,
                        events=[
                            {
                                "type": "evac_success",
                                "person_id": 1,
                                "x": 5,
                                "y": 2,
                            }
                        ],
                    )
                )

            with Path(temp_dir, "event_log.csv").open(
                newline="", encoding="utf-8"
            ) as file:
                rows = list(csv.DictReader(file))
            self.assertEqual(len(rows), 1)

    def test_rejects_time_mismatch_and_existing_logs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            logger = CsvExperimentLogger(
                temp_dir,
                run_id="run_test",
                scenario_id="scene_test",
                random_seed=None,
                time_step_s=0.5,
            )
            with logger:
                invalid = make_snapshot(0)
                invalid["time_s"] = 9.0
                with self.assertRaisesRegex(CsvLogError, "step \\* time_step"):
                    logger.record_snapshot(invalid)

            second_logger = CsvExperimentLogger(
                temp_dir,
                run_id="run_test",
                scenario_id="scene_test",
                random_seed=None,
                time_step_s=0.5,
            )
            with self.assertRaises(FileExistsError):
                second_logger.start()

    def test_invalid_later_person_does_not_write_partial_step(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            logger = CsvExperimentLogger(
                temp_dir,
                run_id="run_test",
                scenario_id="scene_test",
                random_seed=None,
                time_step_s=0.5,
            )
            logger.start()
            invalid = make_snapshot(0)
            del invalid["people"][1]["evacuated"]
            with self.assertRaises(CsvLogError):
                logger.record_snapshot(invalid)
            logger.close()

            with Path(temp_dir, "people_log.csv").open(
                newline="", encoding="utf-8"
            ) as file:
                self.assertEqual(list(csv.DictReader(file)), [])

    def test_unserializable_event_does_not_write_partial_step(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            logger = CsvExperimentLogger(
                temp_dir,
                run_id="run_test",
                scenario_id="scene_test",
                random_seed=None,
                time_step_s=0.5,
            )
            logger.start()
            invalid = make_snapshot(
                0,
                events=[
                    {
                        "type": "conflict",
                        "person_id": 1,
                        "x": 4,
                        "y": 2,
                        "details": {"bad": object()},
                    }
                ],
            )
            with self.assertRaisesRegex(CsvLogError, "cannot be serialized"):
                logger.record_snapshot(invalid)
            logger.close()

            with Path(temp_dir, "people_log.csv").open(
                newline="", encoding="utf-8"
            ) as file:
                self.assertEqual(list(csv.DictReader(file)), [])
            with Path(temp_dir, "event_log.csv").open(
                newline="", encoding="utf-8"
            ) as file:
                self.assertEqual(list(csv.DictReader(file)), [])

    def test_rejects_person_set_change_and_evacuation_reversal(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            logger = CsvExperimentLogger(
                temp_dir,
                run_id="run_test",
                scenario_id="scene_test",
                random_seed=None,
                time_step_s=0.5,
            )
            with logger:
                logger.record_snapshot(make_snapshot(0))
                logger.record_snapshot(
                    make_snapshot(1, person_1_evacuated=True)
                )

                changed_people = make_snapshot(
                    2, person_1_evacuated=True
                )
                changed_people["people"].pop()
                with self.assertRaisesRegex(CsvLogError, "IDs must remain fixed"):
                    logger.record_snapshot(changed_people)

                reversed_state = make_snapshot(2)
                with self.assertRaisesRegex(CsvLogError, "true -> false"):
                    logger.record_snapshot(reversed_state)

    def test_rejects_invalid_ids_events_and_run_path(self) -> None:
        with self.assertRaises(ValueError):
            CsvExperimentLogger(
                Path("output"),
                run_id="../escape",
                scenario_id="scene_test",
                random_seed=None,
                time_step_s=0.5,
            )

        with tempfile.TemporaryDirectory() as temp_dir:
            logger = CsvExperimentLogger(
                temp_dir,
                run_id="run_test",
                scenario_id="scene_test",
                random_seed=None,
                time_step_s=0.5,
            )
            with logger:
                invalid_id = make_snapshot(0)
                invalid_id["people"][0]["person_id"] = -1
                with self.assertRaisesRegex(CsvLogError, "must be >= 0"):
                    logger.record_snapshot(invalid_id)

                inconsistent_event = make_snapshot(
                    0,
                    events=[
                        {
                            "type": "evac_success",
                            "person_id": 1,
                            "x": 4,
                            "y": 2,
                        }
                    ],
                )
                with self.assertRaisesRegex(
                    CsvLogError, "must have evacuated=true"
                ):
                    logger.record_snapshot(inconsistent_event)

    def test_accepts_zero_based_person_and_follow_target_ids(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            logger = CsvExperimentLogger(
                temp_dir,
                run_id="run_zero_based",
                scenario_id="scene_test",
                random_seed=None,
                time_step_s=0.5,
            )
            snapshot = make_snapshot(0)
            snapshot["run_id"] = "run_zero_based"
            snapshot["people"][0]["person_id"] = 0
            snapshot["people"][0]["follow_target"] = 0
            snapshot["people"][1]["follow_target"] = 0
            snapshot["events"] = [{"type": "conflict", "person_id": 0, "x": 4, "y": 2}]
            with logger:
                logger.record_snapshot(snapshot)
            with Path(temp_dir, "people_log.csv").open(newline="", encoding="utf-8") as file:
                rows = list(csv.DictReader(file))
            self.assertEqual(rows[0]["person_id"], "0")


if __name__ == "__main__":
    unittest.main()
