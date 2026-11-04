"""Build the Excel key-parameter record used by D result packages.

The workbook is a presentation artifact only.  It reads the same finalized
snapshot, log-derived metrics, and academic records as the existing result
package and never writes back to those sources.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import re
import statistics
import zipfile
from pathlib import Path
from typing import Any, Iterable, Mapping

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from experiments.academic_crowd import academic_crowd_fields
from experiments.week6_analysis import analyze_run


WORKBOOK_FILENAME = "关键参数记录.xlsx"
RECORD_SHEET = "关键参数记录"
NOTES_SHEET = "数据说明"
NA = "NA"

EXIT_COUNT_HEADER = "出口{number}人数"
EXIT_UTILIZATION_HEADER = "出口{number}利用率"
EXIT_HEADERS = [
    header
    for number in range(1, 9)
    for header in (
        EXIT_COUNT_HEADER.format(number=number),
        EXIT_UTILIZATION_HEADER.format(number=number),
    )
]
HEADERS = [
    "bonus",
    *EXIT_HEADERS,
    "疏散时间",
    "平均疏散时间",
    "T90",
    "平均暴露剂量",
    "",
    "出口H",
    "出口G",
    "",
    "CL最大",
    "CN最大",
    "CL平均",
    "CN平均",
]

_EXIT_ENTITY_PATTERN = re.compile(r"^exit_entity_0*([1-9]\d*)$", re.IGNORECASE)
_DARK_BLUE = "1F4E78"
_LIGHT_BLUE = "D9EAF7"
_LIGHT_BORDER = Side(style="thin", color="B7C9D9")
_BODY_FONT = "Microsoft YaHei"


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or value in (None, "", NA):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _nonnegative_int(value: Any) -> int | None:
    if isinstance(value, bool) or value in (None, "", NA):
        return None
    if isinstance(value, float) and not value.is_integer():
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _exit_ordinal(exit_id: Any) -> int | None:
    match = _EXIT_ENTITY_PATTERN.fullmatch(str(exit_id or "").strip())
    return int(match.group(1)) if match else None


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _json_value(value: Any, default: Any = NA) -> Any:
    if value is None or value == "":
        return default
    return value


def _numeric_or_na(value: Any) -> float | str:
    parsed = _finite(value)
    return parsed if parsed is not None else NA


def _exit_context(
    final_snapshot: Mapping[str, Any], metrics: Mapping[str, Any]
) -> tuple[list[Any], list[Any], dict[int, list[str]], dict[int, str]]:
    existing_ids: set[str] = set()
    entity_ids = final_snapshot.get("exit_entities")
    if isinstance(entity_ids, list):
        for entity in entity_ids:
            if isinstance(entity, Mapping):
                entity_id = entity.get("exit_entity_id")
                if entity_id not in (None, ""):
                    existing_ids.add(str(entity_id))
    universe = _mapping(final_snapshot.get("exit_universe"))
    valid_ids = universe.get("valid_exit_ids")
    if isinstance(valid_ids, list):
        existing_ids.update(
            str(exit_id) for exit_id in valid_ids if exit_id not in (None, "")
        )

    usage = metrics.get("exit_usage")
    usage_mapping = usage if isinstance(usage, Mapping) else {}
    distribution = metrics.get("exit_distribution")
    distribution_mapping = distribution if isinstance(distribution, Mapping) else {}
    existing_ids.update(
        str(exit_id)
        for exit_id in (*usage_mapping.keys(), *distribution_mapping.keys())
        if exit_id not in (None, "")
    )

    ids_by_ordinal: dict[int, list[str]] = {}
    for exit_id in sorted(existing_ids):
        ordinal = _exit_ordinal(exit_id)
        if ordinal is not None:
            ids_by_ordinal.setdefault(ordinal, []).append(exit_id)

    actual_counts: dict[int, int] = {}
    invalid_usage_ordinals: set[int] = set()
    count_source = usage_mapping or distribution_mapping
    for exit_id, raw_count in count_source.items():
        ordinal = _exit_ordinal(exit_id)
        count = _nonnegative_int(raw_count)
        if ordinal is not None and count is not None:
            actual_counts[ordinal] = actual_counts.get(ordinal, 0) + count
        elif ordinal is not None:
            invalid_usage_ordinals.add(ordinal)

    successful_count = _nonnegative_int(metrics.get("evacuated_count"))
    count_values: list[Any] = []
    utilization_values: list[Any] = []
    reasons: dict[int, str] = {}
    for number in range(1, 9):
        if not ids_by_ordinal.get(number):
            count_values.append(NA)
            utilization_values.append(NA)
            reasons[number] = f"场景中不存在出口{number}"
            continue
        count = actual_counts.get(number)
        if count is None and usage_mapping and number not in invalid_usage_ordinals:
            count = 0
        if count is None and successful_count == 0:
            count = 0
        count_values.append(count if count is not None else NA)
        if count is None:
            utilization_values.append(NA)
            reasons[number] = f"缺少出口{number}的实际撤离计数"
        elif successful_count is None:
            utilization_values.append(NA)
            reasons[number] = "缺少实际成功撤离总人数"
        elif successful_count == 0:
            utilization_values.append(NA)
            reasons[number] = "没有实际成功撤离人员，利用率分母为0"
        else:
            utilization_values.append(count / successful_count)
            if count == 0:
                reasons[number] = "出口存在但未使用，人数为0"

    return count_values, utilization_values, ids_by_ordinal, reasons


def _final_doses(
    people_rows: Iterable[Mapping[str, Any]],
) -> tuple[float | str, int]:
    latest: dict[str, tuple[tuple[float, float, int], float]] = {}
    for row_index, row in enumerate(people_rows):
        person_id = str(row.get("person_id") or "").strip()
        dose = _finite(row.get("dose"))
        if not person_id or dose is None:
            continue
        step = _finite(row.get("step"))
        time_s = _finite(row.get("time_s"))
        key = (
            step if step is not None else float("-inf"),
            time_s if time_s is not None else float("-inf"),
            row_index,
        )
        previous = latest.get(person_id)
        if previous is None or key > previous[0]:
            latest[person_id] = (key, dose)
    if not latest:
        return NA, 0
    return statistics.fmean(value for _, value in latest.values()), len(latest)


def _academic_stats(
    academic: Mapping[str, Any],
) -> tuple[Any, Any, Any, Any, int, int, int, str, str]:
    raw_records = academic.get("records")
    records = raw_records if isinstance(raw_records, list) else []
    cl_values: list[float] = []
    cn_values: list[float] = []
    for record in records:
        if not isinstance(record, Mapping):
            continue
        cl_value = _finite(record.get("cl_m_inv"))
        if str(record.get("cl_validity", "")).lower() == "valid" and cl_value is not None:
            cl_values.append(cl_value)
        cn_value = _finite(record.get("cn"))
        if str(record.get("cn_validity", "")).lower() == "valid" and cn_value is not None:
            cn_values.append(cn_value)

    cl_max = max(cl_values) if cl_values else NA
    cn_max = max(cn_values) if cn_values else NA
    cl_mean = statistics.fmean(cl_values) if cl_values else NA
    cn_mean = statistics.fmean(cn_values) if cn_values else NA
    source_reason = str(academic.get("reason") or "")
    if not records:
        cl_reason = source_reason or "没有学术分析记录"
        cn_reason = source_reason or "没有学术分析记录"
    else:
        cl_reason = (
            source_reason or "没有通过cl_validity=valid的有限CL样本"
            if not cl_values
            else ""
        )
        cn_reason = (
            source_reason or "没有通过cn_validity=valid的有限CN样本"
            if not cn_values
            else ""
        )
    return (
        cl_max,
        cn_max,
        cl_mean,
        cn_mean,
        len(records),
        len(cl_values),
        len(cn_values),
        cl_reason,
        cn_reason,
    )


def _na_reason(metrics: Mapping[str, Any], key: str, fallback: str) -> str:
    value = metrics.get(key)
    if value in (None, ""):
        return fallback
    return str(value)


def build_key_parameter_workbook(
    *,
    final_snapshot: Mapping[str, Any],
    metrics: Mapping[str, Any],
    academic: Mapping[str, Any],
    people_rows: Iterable[Mapping[str, Any]],
) -> bytes:
    """Return an in-memory ``关键参数记录.xlsx`` for one finished run."""

    c_runtime = _mapping(final_snapshot.get("c_runtime"))
    bonus = _finite(c_runtime.get("preference_bonus"))
    exit_counts, exit_utilizations, ids_by_ordinal, exit_reasons = _exit_context(
        final_snapshot, metrics
    )
    mean_dose, dose_people = _final_doses(people_rows)
    (
        cl_max,
        cn_max,
        cl_mean,
        cn_mean,
        academic_record_count,
        cl_valid_count,
        cn_valid_count,
        cl_na_reason,
        cn_na_reason,
    ) = _academic_stats(academic)

    total_time = _numeric_or_na(metrics.get("total_evacuation_time_s"))
    mean_time = _numeric_or_na(metrics.get("mean_evacuation_time_s"))
    t90 = _numeric_or_na(metrics.get("t90_time_s"))
    entropy_h = _numeric_or_na(metrics.get("exit_entropy_h"))
    gini_g = _numeric_or_na(metrics.get("exit_gini_g"))
    row_values: list[Any] = [
        bonus if bonus is not None else NA,
        *[
            item
            for pair in zip(exit_counts, exit_utilizations, strict=True)
            for item in pair
        ],
        total_time,
        mean_time,
        t90,
        mean_dose,
        None,
        entropy_h,
        gini_g,
        None,
        cl_max,
        cn_max,
        cl_mean,
        cn_mean,
    ]

    total_people = _nonnegative_int(metrics.get("total_persons"))
    dose_coverage = (
        dose_people / total_people
        if total_people is not None and total_people > 0
        else NA
    )
    if total_people is None:
        dose_coverage_reason = "缺少实验总人数"
    elif dose_people == 0:
        dose_coverage_reason = "没有人员的有效累计dose记录"
    elif dose_people < total_people:
        dose_invalid_people = total_people - dose_people
        dose_coverage_reason = (
            f"{dose_invalid_people}人缺少有效累计dose；均值仅使用{dose_people}名有效人员"
        )
    else:
        dose_coverage_reason = "所有人员均有有效累计dose记录"

    workbook = Workbook()
    record_sheet = workbook.active
    record_sheet.title = RECORD_SHEET
    notes_sheet = workbook.create_sheet(NOTES_SHEET)

    _write_record_sheet(record_sheet, row_values)
    _write_notes_sheet(
        notes_sheet,
        final_snapshot=final_snapshot,
        metrics=metrics,
        ids_by_ordinal=ids_by_ordinal,
        exit_reasons=exit_reasons,
        bonus_reason=(
            ""
            if bonus is not None
            else "最终快照c_runtime.preference_bonus缺失或不是有限数值"
        ),
        dose_people=dose_people,
        dose_coverage=dose_coverage,
        dose_coverage_reason=dose_coverage_reason,
        academic_record_count=academic_record_count,
        cl_valid_count=cl_valid_count,
        cn_valid_count=cn_valid_count,
        cl_na_reason=cl_na_reason,
        cn_na_reason=cn_na_reason,
    )

    stream = io.BytesIO()
    workbook.save(stream)
    return stream.getvalue()


def _write_record_sheet(sheet: Any, row_values: list[Any]) -> None:
    sheet.sheet_view.showGridLines = False
    sheet.freeze_panes = "A2"
    sheet.sheet_properties.tabColor = _DARK_BLUE
    sheet.append(HEADERS)
    sheet.append(row_values)

    for column, header in enumerate(HEADERS, start=1):
        cell = sheet.cell(row=1, column=column)
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.font = Font(name=_BODY_FONT, size=10, bold=True, color="FFFFFF")
        if header:
            cell.fill = PatternFill("solid", fgColor=_DARK_BLUE)
            cell.border = Border(bottom=Side(style="medium", color="FFFFFF"))
            sheet.column_dimensions[get_column_letter(column)].width = (
                16 if header in {"平均暴露剂量", "平均疏散时间", "疏散时间"} else 13
            )
        else:
            sheet.column_dimensions[get_column_letter(column)].width = 2.5

    sheet.cell(row=1, column=1).alignment = Alignment(
        horizontal="left", vertical="center"
    )
    sheet.column_dimensions["A"].width = 11
    sheet.column_dimensions["B"].width = 12
    sheet.column_dimensions["C"].width = 14
    sheet.row_dimensions[1].height = 34
    sheet.row_dimensions[2].height = 24

    for column in range(1, len(HEADERS) + 1):
        cell = sheet.cell(row=2, column=column)
        cell.font = Font(name=_BODY_FONT, size=10, color="1F2937")
        cell.alignment = Alignment(horizontal="right", vertical="center")
        cell.border = Border(bottom=_LIGHT_BORDER)
        cell.fill = PatternFill("solid", fgColor="F8FBFE")
        if cell.value is None:
            cell.fill = PatternFill(fill_type=None)
            cell.border = Border()

    for number in range(1, 9):
        count_column = 2 + (number - 1) * 2
        utilization_column = count_column + 1
        sheet.cell(row=2, column=count_column).number_format = "0"
        sheet.cell(row=2, column=utilization_column).number_format = "0.0%"
    for column in (18, 19, 20):
        sheet.cell(row=2, column=column).number_format = "0.000"
    for column in (21, 23, 24, 26, 27, 28, 29):
        sheet.cell(row=2, column=column).number_format = "0.000000"


def _write_notes_sheet(
    sheet: Any,
    *,
    final_snapshot: Mapping[str, Any],
    metrics: Mapping[str, Any],
    ids_by_ordinal: Mapping[int, list[str]],
    exit_reasons: Mapping[int, str],
    bonus_reason: str,
    dose_people: int,
    dose_coverage: Any,
    dose_coverage_reason: str,
    academic_record_count: int,
    cl_valid_count: int,
    cn_valid_count: int,
    cl_na_reason: str,
    cn_na_reason: str,
) -> None:
    sheet.sheet_view.showGridLines = False
    sheet.freeze_panes = "A2"
    sheet.sheet_properties.tabColor = _LIGHT_BLUE
    sheet.append(["项目", "内容"])
    run_id = final_snapshot.get("run_id") or metrics.get("run_id") or NA
    seed = _json_value(
        final_snapshot.get("random_seed")
        if final_snapshot.get("random_seed") is not None
        else metrics.get("random_seed")
    )
    dt_s = _json_value(
        final_snapshot.get("time_step")
        if final_snapshot.get("time_step") is not None
        else final_snapshot.get("time_step_s")
    )
    existing_exits = ", ".join(
        exit_id
        for number in sorted(ids_by_ordinal)
        for exit_id in ids_by_ordinal[number]
    )
    rows: list[tuple[str, Any]] = [
        ("run_id", run_id),
        ("seed", seed),
        ("实验总人数", _json_value(metrics.get("total_persons"))),
        ("dt / s", dt_s),
        ("数据版本", _json_value(final_snapshot.get("schema_version"))),
        ("实际成功撤离人数", _json_value(metrics.get("evacuated_count"))),
        ("识别的出口实体", existing_exits or NA),
        ("bonus定义", "最终快照c_runtime.preference_bonus，即本次运行实际生效的偏好加成"),
        (
            "出口人数定义",
            "按actual_exit_entity统计实际成功撤离人数；目标出口不参与统计",
        ),
        (
            "出口利用率定义",
            "该出口实际成功撤离人数 / 全部实际成功撤离人数",
        ),
        (
            "疏散时间定义",
            "total_evacuation_time_s；非全员成功撤离时为NA",
        ),
        (
            "平均疏散时间定义",
            "mean_evacuation_time_s，按每名人员首次成功撤离时间计算",
        ),
        ("T90定义", "t90_time_s，达到90%人员撤离时的时间"),
        (
            "平均暴露剂量定义",
            "按每名人员最后一条有效累计dose求人员平均；缺失剂量不按0处理",
        ),
        ("剂量有效人数", dose_people),
        ("剂量覆盖率", dose_coverage),
        ("剂量覆盖说明", dose_coverage_reason),
        (
            "出口H定义",
            "当前正式分析的exit_entropy_h，使用有效出口全集",
        ),
        (
            "出口G定义",
            "当前正式分析的exit_gini_g，使用有效出口全集",
        ),
        (
            "CL/CN定义",
            "CL、CN均只使用validity=valid且有限值的学术分析记录；最大值为样本最大值，平均值为算术平均",
        ),
        ("学术分析记录数", academic_record_count),
        ("CL有效样本数", cl_valid_count),
        ("CN有效样本数", cn_valid_count),
        ("CL NA原因", cl_na_reason or "无"),
        ("CN NA原因", cn_na_reason or "无"),
        (
            "疏散时间NA原因",
            _na_reason(
                metrics,
                "total_evacuation_time_s_note",
                "未提供total_evacuation_time_s",
            )
            if metrics.get("total_evacuation_time_s") == NA
            else "无",
        ),
        (
            "平均疏散时间NA原因",
            _na_reason(
                metrics,
                "mean_evacuation_time_s_note",
                "未提供mean_evacuation_time_s",
            )
            if metrics.get("mean_evacuation_time_s") == NA
            else "无",
        ),
        (
            "T90NA原因",
            "未达到90%撤离或实验总人数为0"
            if metrics.get("t90_time_s") == NA
            else "无",
        ),
        (
            "平均暴露剂量NA原因",
            dose_coverage_reason if dose_people == 0 else "无",
        ),
        (
            "bonus NA原因",
            bonus_reason or "无",
        ),
        (
            "H/G NA原因",
            "有效出口全集不可用或没有实际成功撤离人员"
            if metrics.get("exit_entropy_h") == NA
            or metrics.get("exit_gini_g") == NA
            else "无",
        ),
    ]
    rows.extend(
        (f"出口{number}NA原因", exit_reasons[number])
        for number in range(1, 9)
        if number in exit_reasons
    )
    for row in rows:
        sheet.append(row)

    for cell in sheet[1]:
        cell.fill = PatternFill("solid", fgColor=_DARK_BLUE)
        cell.font = Font(name=_BODY_FONT, size=10, bold=True, color="FFFFFF")
        cell.alignment = Alignment(horizontal="center", vertical="center")
    sheet.row_dimensions[1].height = 26
    sheet.column_dimensions["A"].width = 28
    sheet.column_dimensions["B"].width = 88
    for row in sheet.iter_rows(min_row=2):
        row[0].font = Font(name=_BODY_FONT, size=10, bold=True, color="1F4E78")
        row[0].alignment = Alignment(vertical="top")
        row[1].font = Font(name=_BODY_FONT, size=10, color="1F2937")
        row[1].alignment = Alignment(vertical="top", wrap_text=True)
        row[0].border = Border(bottom=_LIGHT_BORDER)
        row[1].border = Border(bottom=_LIGHT_BORDER)
    coverage_row = next(
        (
            row
            for row in range(2, sheet.max_row + 1)
            if sheet.cell(row=row, column=1).value == "剂量覆盖率"
        ),
        None,
    )
    if coverage_row is not None:
        sheet.cell(row=coverage_row, column=2).number_format = "0.0%"


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return dict(payload) if isinstance(payload, Mapping) else {}


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _run_snapshot(config: Mapping[str, Any], metrics: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "run_id": config.get("run_id") or metrics.get("run_id"),
        "schema_version": config.get("schema_version"),
        "random_seed": config.get("random_seed", metrics.get("random_seed")),
        "time_step": config.get("time_step_s"),
        "analysis_contract": config.get("analysis_contract"),
        "grid": config.get("grid"),
        "exit_entities": config.get("exit_entities", []),
        "c_runtime": config.get("c_runtime"),
    }


def generate_from_run_directory(
    run_dir: str | Path, *, output_path: str | Path | None = None
) -> Path:
    """Generate the workbook from an existing run directory without simulation."""

    source = Path(run_dir)
    config = _read_json(source / "config_used.json")
    people_rows = _read_csv(source / "people_log.csv")
    metrics = _read_json(source / "week6_metrics.json")
    if not metrics:
        metrics = analyze_run(
            source,
            valid_exit_ids=_configured_valid_exit_ids(config),
        )
    academic = _read_json(source / "academic_crowd_fields.json")
    if not academic:
        analysis_contract = config.get("analysis_contract")
        grid = config.get("grid")
        if isinstance(analysis_contract, Mapping) and isinstance(grid, Mapping):
            academic = academic_crowd_fields(
                people_rows,
                grid=grid,
                analysis_contract=analysis_contract,
            )
    snapshot = _run_snapshot(config, metrics)
    content = build_key_parameter_workbook(
        final_snapshot=snapshot,
        metrics=metrics,
        academic=academic,
        people_rows=people_rows,
    )
    destination = Path(output_path) if output_path else source / WORKBOOK_FILENAME
    destination.write_bytes(content)
    return destination


def _configured_valid_exit_ids(
    config: Mapping[str, Any],
) -> list[str] | None:
    entities = config.get("exit_entities")
    if not isinstance(entities, list):
        return None
    if not any(
        isinstance(entity, Mapping) and "is_open" in entity
        for entity in entities
    ):
        return None
    return [
        str(entity["exit_entity_id"])
        for entity in entities
        if isinstance(entity, Mapping)
        and entity.get("exit_entity_id") not in (None, "")
        and entity.get("is_open") is True
    ]


def generate_from_result_zip(
    zip_path: str | Path, *, output_path: str | Path | None = None
) -> Path:
    """Generate the workbook from an existing D result ZIP without simulation."""

    source = Path(zip_path)
    with zipfile.ZipFile(source) as archive:
        names = set(archive.namelist())
        people_names = [
            name for name in names if name.endswith("/people_log.csv") and name.count("/") == 1
        ]
        if len(people_names) != 1:
            raise ValueError("result ZIP must contain exactly one run's people_log.csv")
        prefix = people_names[0][: -len("people_log.csv")]
        metadata = _json_from_zip(archive, prefix + "metadata.json")
        config = _json_from_zip(archive, prefix + "config.json")
        metrics = _json_from_zip(archive, prefix + "week6_metrics.json")
        academic = _json_from_zip(archive, prefix + "academic_crowd_fields.json")
        people_rows = list(
            csv.DictReader(
                io.StringIO(
                    archive.read(prefix + "people_log.csv").decode("utf-8-sig")
                )
            )
        )

    snapshot = {
        "run_id": metadata.get("run_id") or config.get("run_id"),
        "schema_version": metadata.get("schema_version"),
        "random_seed": metadata.get("random_seed", config.get("random_seed")),
        "time_step": metadata.get("time_step_s", config.get("time_step_s")),
        "c_runtime": metadata.get("c_runtime_final"),
    }
    content = build_key_parameter_workbook(
        final_snapshot=snapshot,
        metrics=metrics,
        academic=academic,
        people_rows=people_rows,
    )
    destination = (
        Path(output_path) if output_path else source.with_name(WORKBOOK_FILENAME)
    )
    destination.write_bytes(content)
    return destination


def _json_from_zip(archive: zipfile.ZipFile, name: str) -> dict[str, Any]:
    if name not in archive.namelist():
        return {}
    try:
        payload = json.loads(archive.read(name).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return dict(payload) if isinstance(payload, Mapping) else {}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate 关键参数记录.xlsx from a D result ZIP or run directory."
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--zip", type=Path, help="existing result package ZIP")
    source.add_argument("--run-dir", type=Path, help="existing run output directory")
    parser.add_argument("--output", type=Path, help="output xlsx path")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    if args.zip is not None:
        output = generate_from_result_zip(args.zip, output_path=args.output)
    else:
        output = generate_from_run_directory(args.run_dir, output_path=args.output)
    print(output)


if __name__ == "__main__":
    main()
