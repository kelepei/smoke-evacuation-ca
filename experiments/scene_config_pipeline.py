"""One D-side entry point from browser/YAML scene settings to C's generator.

This module deliberately owns validation and provenance only.  Person and
relation generation stay in C's ``SceneConfigGenerator`` / ``SocialGraphBuilder``
and initial coordinates stay in A's ``position_allocator``.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping


class SceneConfigPipelineError(ValueError):
    """A concise, user-facing configuration error."""


_GROUP_PROBABILITIES = (
    "has_family_prob", "has_friend_prob", "has_classmate_prob",
    "has_colleague_prob", "has_staff_customer_prob", "has_doctor_patient_prob",
    "stranger_ratio",
)
_GROUP_RANGES = ("family_size_range", "friend_size_range", "classmate_size_range", "colleague_size_range")


def _number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SceneConfigPipelineError(f"{field} 必须是数字")
    result = float(value)
    if result != result or result in (float("inf"), float("-inf")):
        raise SceneConfigPipelineError(f"{field} 必须是有限数字")
    return result


def validate_canonical_scene_config(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the single UI/YAML-neutral config accepted by the D bridge."""
    if not isinstance(raw, Mapping):
        raise SceneConfigPipelineError("scene_config 必须是对象")
    total = raw.get("total_persons")
    if isinstance(total, bool) or not isinstance(total, int) or total <= 0:
        raise SceneConfigPipelineError("total_persons 必须是大于 0 的整数")
    seed = raw.get("random_seed")
    if seed is not None and (isinstance(seed, bool) or not isinstance(seed, int)):
        raise SceneConfigPipelineError("random_seed 必须是整数或留空")
    intensity = _number(raw.get("relation_intensity", 0.7), "relation_intensity")
    if not 0 <= intensity <= 1:
        raise SceneConfigPipelineError("relation_intensity 必须在 0 到 1 之间")
    initial_informed_ratio = _number(raw.get("initial_informed_ratio", 0.15), "initial_informed_ratio")
    if not 0 <= initial_informed_ratio <= 1:
        raise SceneConfigPipelineError("initial_informed_ratio 必须在 0 到 1 之间")
    alarm_enabled = raw.get("alarm_enabled", True)
    if not isinstance(alarm_enabled, bool):
        raise SceneConfigPipelineError("alarm_enabled 必须是布尔值")
    ratios = raw.get("profile_ratios")
    if not isinstance(ratios, Mapping) or not ratios:
        raise SceneConfigPipelineError("profile_ratios 至少需要一个角色比例")
    normalized_ratios = {str(key): _number(value, f"profile_ratios.{key}") for key, value in ratios.items()}
    if any(value < 0 for value in normalized_ratios.values()):
        raise SceneConfigPipelineError("profile_ratios 不能为负数")
    if abs(sum(normalized_ratios.values()) - 1.0) > 0.01:
        raise SceneConfigPipelineError("profile_ratios 之和必须约等于 1")
    group = raw.get("group_config", {})
    if not isinstance(group, Mapping):
        raise SceneConfigPipelineError("group_config 必须是对象")
    normalized_group = dict(group)
    for key in _GROUP_PROBABILITIES:
        if key in normalized_group:
            value = _number(normalized_group[key], key)
            if not 0 <= value <= 1:
                raise SceneConfigPipelineError(f"{key} 必须在 0 到 1 之间")
            normalized_group[key] = value
    for key in _GROUP_RANGES:
        if key in normalized_group:
            value = normalized_group[key]
            if not isinstance(value, (list, tuple)) or len(value) != 2 or any(isinstance(item, bool) or not isinstance(item, int) for item in value):
                raise SceneConfigPipelineError(f"{key} 必须是两个整数")
            if value[0] <= 0 or value[0] > value[1]:
                raise SceneConfigPipelineError(f"{key} 的最小值必须大于 0 且不大于最大值")
            normalized_group[key] = [value[0], value[1]]
    return {
        "scene_name": str(raw.get("scene_name") or "custom"),
        "description": str(raw.get("description") or "D UI canonical scene config"),
        "total_persons": total,
        "random_seed": seed,
        "relation_intensity": intensity,
        "initial_informed_ratio": initial_informed_ratio,
        "alarm_enabled": alarm_enabled,
        "profile_ratios": normalized_ratios,
        "group_config": normalized_group,
    }


def canonical_from_c_scene_config(config: Any) -> dict[str, Any]:
    """Make a YAML-loaded C SceneConfig enter the same canonical path."""
    group = getattr(config, "group_config", None)
    raw_group = asdict(group) if group is not None else {}
    return validate_canonical_scene_config({
        "scene_name": getattr(config, "scene_name", "custom"),
        "description": getattr(config, "description", ""),
        "total_persons": getattr(config, "total_persons", None),
        "random_seed": getattr(config, "random_seed", None),
        "relation_intensity": getattr(config, "relation_intensity", None),
        "initial_informed_ratio": getattr(config, "initial_informed_ratio", 0.15),
        "alarm_enabled": getattr(config, "alarm_enabled", True),
        "profile_ratios": getattr(config, "profile_ratios", None),
        "group_config": raw_group,
    })


def generate_positioned_population(*, scene_config: Mapping[str, Any], map_path: Path, destination: Path) -> dict[str, Any]:
    """Invoke C's existing generator and A's existing map-based allocator."""
    canonical = validate_canonical_scene_config(scene_config)
    from control.scene_config import SceneConfigGenerator, _generate_from_config

    destination.mkdir(parents=True, exist_ok=True)
    config = SceneConfigGenerator.load_config_from_dict(dict(canonical))
    raw_path = destination / "generated_population.json"
    positioned_path = destination / "generated_population_positioned.json"
    try:
        _generate_from_config(config, people_output=str(raw_path), map_file=str(map_path), position_output=str(positioned_path))
        payload = json.loads(positioned_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        raise SceneConfigPipelineError(f"人员生成或地图位置分配失败：{exc}") from exc
    persons = payload.get("persons") if isinstance(payload, Mapping) else None
    if not isinstance(persons, list) or len(persons) != canonical["total_persons"]:
        actual = len(persons) if isinstance(persons, list) else 0
        raise SceneConfigPipelineError(f"人员生成数量异常：期望 {canonical['total_persons']}，实际 {actual}")
    return {"canonical": canonical, "population_path": positioned_path, "person_count": len(persons), "profile_counts": _profile_counts(persons)}


def _profile_counts(persons: list[Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for person in persons:
        profile = str(person.get("profile", "unknown")) if isinstance(person, Mapping) else "unknown"
        counts[profile] = counts.get(profile, 0) + 1
    return counts
