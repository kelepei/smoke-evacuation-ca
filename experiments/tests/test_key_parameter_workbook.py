from __future__ import annotations

import csv
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from openpyxl import load_workbook

from experiments.key_parameter_workbook import (
    NA,
    WORKBOOK_FILENAME,
    build_key_parameter_workbook,
    generate_from_result_zip,
    generate_from_run_directory,
)
from experiments.result_package import build_result_package


EXPECTED_HEADERS = [
    "bonus",
    "出口1人数", "出口1利用率",
    "出口2人数", "出口2利用率",
    "出口3人数", "出口3利用率",
    "出口4人数", "出口4利用率",
    "出口5人数", "出口5利用率",
    "出口6人数", "出口6利用率",
    "出口7人数", "出口7利用率",
    "出口8人数", "出口8利用率",
    "疏散时间", "平均疏散时间", "T90", "平均暴露剂量",
    None,
    "出口H", "出口G",
    None,
    "CL最大", "CN最大", "CL平均", "CN平均",
]


class KeyParameterWorkbookTests(unittest.TestCase):
    def _workbook(self, content: bytes):
        return load_workbook(io.BytesIO(content), data_only=False)

    def test_exact_headers_formats_and_real_values(self) -> None:
        metrics = {
            "run_id": "run_excel",
            "random_seed": 17,
            "total_persons": 3,
            "evacuated_count": 2,
            "total_evacuation_time_s": 2.0,
            "mean_evacuation_time_s": 1.5,
            "t90_time_s": 2.0,
            "exit_usage": {
                "exit_entity_01": 1,
                "exit_entity_02": 1,
                "exit_entity_03": 0,
            },
            "exit_entropy_h": 0.9,
            "exit_gini_g": 0.2,
        }
        snapshot = {
            "run_id": "run_excel",
            "schema_version": "0.1-draft",
            "random_seed": 17,
            "time_step": 0.5,
            "c_runtime": {"preference_bonus": 0.37},
            "exit_entities": [
                {"exit_entity_id": "exit_entity_01"},
                {"exit_entity_id": "exit_entity_02"},
                {"exit_entity_id": "exit_entity_03"},
            ],
        }
        academic = {
            "status": "available",
            "records": [
                {"cl_m_inv": 10, "cl_validity": "valid", "cn": 1, "cn_validity": "valid"},
                {"cl_m_inv": 14, "cl_validity": "valid", "cn": 3, "cn_validity": "valid"},
                {"cl_m_inv": NA, "cl_validity": "invalid", "cn": 99, "cn_validity": "invalid"},
            ],
        }
        people_rows = [
            {"step": 0, "time_s": 0, "person_id": 1, "dose": 0.1},
            {"step": 1, "time_s": 0.5, "person_id": 1, "dose": 0.2},
            {"step": 0, "time_s": 0, "person_id": 2, "dose": 1},
            {"step": 2, "time_s": 1, "person_id": 2, "dose": 2},
            {"step": 1, "time_s": 0.5, "person_id": 3, "dose": NA},
        ]

        workbook = self._workbook(
            build_key_parameter_workbook(
                final_snapshot=snapshot,
                metrics=metrics,
                academic=academic,
                people_rows=people_rows,
            )
        )

        self.assertEqual(workbook.sheetnames, ["关键参数记录", "数据说明"])
        sheet = workbook["关键参数记录"]
        self.assertEqual(len(EXPECTED_HEADERS), 29)
        self.assertEqual([cell.value for cell in sheet[1]], EXPECTED_HEADERS)
        self.assertEqual(sheet.freeze_panes, "A2")
        self.assertEqual(sheet["A2"].value, 0.37)
        self.assertEqual([sheet.cell(row=2, column=column).value for column in (2, 4, 6, 8)], [1, 1, 0, NA])
        self.assertEqual([sheet.cell(row=2, column=column).value for column in (3, 5, 7, 9)], [0.5, 0.5, 0.0, NA])
        self.assertEqual(sheet["C2"].number_format, "0.0%")
        self.assertEqual(sheet["R2"].value, 2.0)
        self.assertEqual(sheet["S2"].value, 1.5)
        self.assertEqual(sheet["T2"].value, 2.0)
        self.assertAlmostEqual(sheet["U2"].value, 1.1)
        self.assertEqual([sheet["Z2"].value, sheet["AA2"].value, sheet["AB2"].value, sheet["AC2"].value], [14, 3, 12, 2])
        self.assertIsNone(sheet["V1"].value)
        self.assertIsNone(sheet["Y1"].value)

        notes = workbook["数据说明"]
        self.assertEqual(notes.freeze_panes, "A2")
        notes_values = {
            notes.cell(row=row, column=1).value: notes.cell(row=row, column=2).value
            for row in range(2, notes.max_row + 1)
        }
        self.assertEqual(notes_values["run_id"], "run_excel")
        self.assertEqual(notes_values["seed"], 17)
        self.assertEqual(notes_values["实验总人数"], 3)
        self.assertEqual(notes_values["dt / s"], 0.5)
        self.assertEqual(notes_values["数据版本"], "0.1-draft")
        self.assertEqual(notes_values["剂量有效人数"], 2)
        self.assertAlmostEqual(notes_values["剂量覆盖率"], 2 / 3)
        self.assertEqual(notes_values["CL有效样本数"], 2)
        self.assertEqual(notes_values["CN有效样本数"], 2)

    def test_zero_na_death_and_missing_values_are_not_fabricated(self) -> None:
        workbook = self._workbook(
            build_key_parameter_workbook(
                final_snapshot={
                    "run_id": "death_run",
                    "schema_version": "0.1-draft",
                    "random_seed": 4,
                    "time_step": 1.0,
                    "c_runtime": {},
                    "exit_universe": {
                        "status": "available",
                        "valid_exit_ids": ["exit_entity_1", "exit_entity_2"],
                    },
                },
                metrics={
                    "total_persons": 3,
                    "evacuated_count": 1,
                    "total_evacuation_time_s": NA,
                    "mean_evacuation_time_s": 0.5,
                    "t90_time_s": NA,
                    "exit_usage": {"exit_entity_1": 1, "exit_entity_2": 0},
                    "exit_entropy_h": NA,
                    "exit_gini_g": NA,
                },
                academic={"status": "unavailable", "reason": "missing physical scale", "records": []},
                people_rows=[
                    {"step": 0, "time_s": 0, "person_id": 1, "dose": 1},
                    {"step": 1, "time_s": 0.5, "person_id": 1, "dose": NA},
                    {"step": 0, "time_s": 0, "person_id": 2, "is_dead": True},
                    {"step": 0, "time_s": 0, "person_id": 3},
                ],
            )
        )
        sheet = workbook["关键参数记录"]
        self.assertEqual(sheet["A2"].value, NA)
        self.assertEqual(sheet["B2"].value, 1)
        self.assertEqual(sheet["C2"].value, 1.0)
        self.assertEqual(sheet["D2"].value, 0)
        self.assertEqual(sheet["E2"].value, 0.0)
        self.assertEqual(sheet["F2"].value, NA)
        self.assertEqual(sheet["R2"].value, NA)
        self.assertEqual(sheet["T2"].value, NA)
        self.assertEqual(sheet["U2"].value, 1.0)
        self.assertEqual(sheet["W2"].value, NA)
        self.assertEqual(sheet["Z2"].value, NA)

        notes = workbook["数据说明"]
        notes_values = {
            notes.cell(row=row, column=1).value: notes.cell(row=row, column=2).value
            for row in range(2, notes.max_row + 1)
        }
        self.assertIn("preference_bonus", notes_values["bonus NA原因"])
        self.assertIn("missing physical scale", notes_values["CL NA原因"])
        self.assertIn("missing physical scale", notes_values["CN NA原因"])
        self.assertIn("2人缺少有效累计dose", notes_values["剂量覆盖说明"])
        self.assertIn("未达到90%撤离", notes_values["T90NA原因"])
        self.assertEqual("无", notes_values["平均暴露剂量NA原因"])
        self.assertIn("场景中不存在出口3", str(notes_values.get("出口3NA原因")))

    def test_result_zip_adds_workbook_without_dropping_existing_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_dir = root / "run_death"
            run_dir.mkdir()
            with (run_dir / "people_log.csv").open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=[
                        "step", "time_s", "person_id", "x", "y", "evacuated", "is_dead",
                        "target_exit_entity", "actual_exit_entity", "dose",
                    ],
                )
                writer.writeheader()
                writer.writerows([
                    {"step": 0, "time_s": 0, "person_id": 1, "x": 1, "y": 1, "evacuated": False, "target_exit_entity": "exit_entity_2", "dose": 0.1},
                    {"step": 0, "time_s": 0, "person_id": 2, "x": 2, "y": 1, "evacuated": False, "target_exit_entity": "exit_entity_1", "dose": 1},
                    {"step": 0, "time_s": 0, "person_id": 3, "x": 3, "y": 1, "evacuated": False, "is_dead": True},
                    {"step": 1, "time_s": 0.5, "person_id": 1, "x": 4, "y": 1, "evacuated": True, "target_exit_entity": "exit_entity_2", "actual_exit_entity": "exit_entity_1", "dose": 0.2},
                    {"step": 2, "time_s": 1.0, "person_id": 2, "x": 5, "y": 1, "evacuated": True, "target_exit_entity": "exit_entity_2", "actual_exit_entity": "exit_entity_2", "dose": 2},
                    {"step": 2, "time_s": 1.0, "person_id": 1, "x": 4, "y": 1, "evacuated": True, "target_exit_entity": "exit_entity_2", "actual_exit_entity": "exit_entity_1", "dose": 0.2},
                    {"step": 2, "time_s": 1.0, "person_id": 3, "x": 3, "y": 1, "evacuated": False, "is_dead": True},
                ])
            (run_dir / "event_log.csv").write_text("event_type\n", encoding="utf-8")
            map_path = root / "map.json"
            population_path = root / "population.json"
            map_path.write_text("{}", encoding="utf-8")
            population_path.write_text("{}", encoding="utf-8")
            snapshot = {
                "run_id": "run_death",
                "scenario_id": "bounded_death_case",
                "schema_version": "0.1-draft",
                "random_seed": 22,
                "time_step": 0.5,
                "step": 2,
                "grid": {"width": 6, "height": 3},
                "exit_entities": [
                    {"exit_entity_id": "exit_entity_1"},
                    {"exit_entity_id": "exit_entity_2"},
                    {"exit_entity_id": "exit_entity_3"},
                ],
                "exit_universe": {
                    "status": "available",
                    "valid_exit_ids": ["exit_entity_1", "exit_entity_2", "exit_entity_3"],
                },
                "metrics": {
                    "exit_usage": {
                        "exit_entity_1": 1,
                        "exit_entity_2": 1,
                        "exit_entity_3": 0,
                    }
                },
                "c_runtime": {"preference_bonus": 0.4},
                "analysis_contract": {
                    "physical_scale": {"source": "unavailable", "value": None, "unit": "m"}
                },
            }
            package = build_result_package(
                output_dir=run_dir,
                final_snapshot=snapshot,
                input_files={"map": map_path, "population": population_path},
                max_steps=10,
            )
            with zipfile.ZipFile(io.BytesIO(package.content)) as archive:
                names = set(archive.namelist())
                workbook_name = f"run_death/{WORKBOOK_FILENAME}"
                self.assertIn(workbook_name, names)
                for preserved in (
                    "people_log.csv",
                    "event_log.csv",
                    "week6_metrics.json",
                    "academic_crowd_fields.json",
                    "person_id_mapping.csv",
                ):
                    self.assertIn(f"run_death/{preserved}", names)
                content = archive.read(workbook_name)
            workbook = self._workbook(content)
            sheet = workbook["关键参数记录"]
            self.assertEqual([sheet["B2"].value, sheet["D2"].value, sheet["F2"].value], [1, 1, 0])
            self.assertEqual([sheet["C2"].value, sheet["E2"].value, sheet["G2"].value], [0.5, 0.5, 0.0])
            self.assertEqual(sheet["R2"].value, NA)
            self.assertAlmostEqual(sheet["U2"].value, 1.1)

            zip_path = root / package.filename
            zip_path.write_bytes(package.content)
            regenerated_from_zip = generate_from_result_zip(
                zip_path, output_path=root / "from_zip.xlsx"
            )
            self.assertTrue(regenerated_from_zip.is_file())

            (run_dir / "config_used.json").write_text(
                json.dumps(
                    {
                        "run_id": "run_death",
                        "schema_version": "0.1-draft",
                        "random_seed": 22,
                        "time_step_s": 0.5,
                        "exit_entities": [
                            {**entity, "is_open": True}
                            for entity in snapshot["exit_entities"]
                        ],
                        "c_runtime": snapshot["c_runtime"],
                        "grid": snapshot["grid"],
                        "analysis_contract": snapshot["analysis_contract"],
                    }
                ),
                encoding="utf-8",
            )
            regenerated_from_dir = generate_from_run_directory(
                run_dir, output_path=root / "from_run.xlsx"
            )
            self.assertTrue(regenerated_from_dir.is_file())
            regenerated_sheet = self._workbook(
                regenerated_from_dir.read_bytes()
            )["关键参数记录"]
            self.assertEqual(
                [regenerated_sheet["B2"].value, regenerated_sheet["D2"].value],
                [1, 1],
            )


if __name__ == "__main__":
    unittest.main()
