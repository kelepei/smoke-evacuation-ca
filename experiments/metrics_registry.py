"""Canonical, log-backed metric definitions owned by D.

Only values that the current A+B+C runtime records are registered here. A
missing upstream field is represented by ``NA``; no planned target or
browser-side estimate is promoted to an observed result.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


NA = "NA"


@dataclass(frozen=True)
class MetricDefinition:
    key: str
    label: str
    unit: str
    source: str
    unavailable_note: str = ""
    batch: bool = False


METRIC_REGISTRY: tuple[MetricDefinition, ...] = (
    MetricDefinition("total_persons", "总人数", "person", "people_log first step", batch=True),
    MetricDefinition("evacuated_count", "已撤离人数", "person", "people_log latest step", batch=True),
    MetricDefinition("evacuation_rate", "撤离率", "ratio", "people_log latest step", batch=True),
    MetricDefinition("remaining_count", "滞留人数", "person", "people_log latest step", batch=True),
    MetricDefinition("first_evacuation_time_s", "首次疏散时间", "s", "people_log first evacuated transition", "NA: no person has evacuated"),
    MetricDefinition("total_evacuation_time_s", "总疏散时间", "s", "people_log first evacuated transition per person", "NA: run is not fully evacuated", True),
    MetricDefinition("mean_evacuation_time_s", "平均疏散时间", "s", "people_log first evacuated transition per person", "NA: no person has evacuated", True),
    MetricDefinition("t90_time_s", "T90", "s", "people_log evacuated count by time", "NA: 90% evacuation has not been reached", True),
    MetricDefinition("simulation_steps", "模拟步数", "step", "people_log latest step", batch=True),
    MetricDefinition("simulation_time_s", "当前模拟时间", "s", "people_log latest time_s", batch=True),
    MetricDefinition("overlap_cells", "重叠元胞数", "cell", "people_log latest active-person positions", "NA: no valid active-person positions"),
    MetricDefinition("overlap_steps", "发生重叠的步数", "step", "people_log active-person positions by step"),
    MetricDefinition("max_overlap_cells", "最大重叠元胞数", "cell", "people_log active-person positions by step"),
    MetricDefinition("max_persons_per_cell", "单元胞最大人员数", "person", "people_log active-person positions by step"),
    MetricDefinition("max_smoke", "最大人员烟雾暴露", "concentration", "people_log smoke/smoke_concentration", "NA: runtime did not log person smoke"),
    MetricDefinition("avg_smoke", "平均人员烟雾暴露", "concentration", "people_log smoke/smoke_concentration", "NA: runtime did not log person smoke"),
    MetricDefinition("avg_dose", "平均剂量", "dose", "people_log dose", "NA: B did not provide dose"),
    MetricDefinition("avg_risk", "平均风险", "risk", "people_log risk", "NA: B did not provide risk"),
    MetricDefinition("exit_utilization", "出口利用率", "NA", "people_log actual_exit", "NA: B did not provide actual_exit"),
)


METRICS_BY_KEY = {definition.key: definition for definition in METRIC_REGISTRY}


def metric_rows(metrics: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return registry-ordered rows for JSON/CSV/package consumers."""

    rows: list[dict[str, Any]] = []
    for definition in METRIC_REGISTRY:
        value = metrics.get(definition.key, NA)
        note = str(metrics.get(f"{definition.key}_note", "") or "")
        if value == NA and not note:
            note = definition.unavailable_note
        rows.append({"metric_name": definition.key, "label": definition.label, "value": value, "unit": definition.unit, "source": definition.source, "note": note})
    return rows


def batch_metric_keys() -> tuple[str, ...]:
    return tuple(definition.key for definition in METRIC_REGISTRY if definition.batch)
