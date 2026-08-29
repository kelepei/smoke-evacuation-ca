"""D-side adapter for C's per-step social-behavior output.

D must not rebuild C's relations or invent behavior decisions.  This module
only normalizes behavior dictionaries produced by C and maps C's source IDs
to the IDs currently used by B's runtime.  It is deliberately optional: when
C has not supplied a provider, B continues to receive an empty mapping and
the UI labels that state honestly.
"""

from __future__ import annotations

from collections.abc import Mapping
from numbers import Integral
from typing import Any


class CBehaviorAdapterError(ValueError):
    """Raised when C step behavior cannot be matched to B runtime people."""


_KNOWN_OUTPUT_GROUPS = ("group", "herd", "guide", "signage")


def _as_person_id(value: Any, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, Integral) or int(value) < 0:
        raise CBehaviorAdapterError(f"{field} must be a non-negative integer")
    return int(value)


def _behavior_maps(raw: Mapping[Any, Any]) -> list[Mapping[Any, Any]]:
    """Accept C's direct or named-engine result shape without guessing fields."""

    direct: dict[Any, Any] = {}
    grouped: list[Mapping[Any, Any]] = []
    for key, value in raw.items():
        if isinstance(key, Integral) and not isinstance(key, bool):
            direct[key] = value
        elif isinstance(key, str) and key.isdigit():
            direct[key] = value
        elif key in _KNOWN_OUTPUT_GROUPS and isinstance(value, Mapping):
            grouped.append(value)
    return ([direct] if direct else []) + grouped


class CStepBehaviorAdapter:
    """Convert actual C behavior output to B's ``c_step_data`` mapping.

    The provider must be supplied by C (or by the integrator) and receive the
    current B engine.  It can return either ``{person_id: behavior}`` or the
    named engine shape ``{"group": {...}, "herd": {...}, "guide": {...}}``.
    C IDs may be its original zero-based IDs; D maps them using each B
    person's ``source_person_id`` when that attribute is available.
    """

    def __init__(self, provider: Any) -> None:
        if not callable(provider):
            raise TypeError("C behavior provider must be callable")
        self._provider = provider

    def __call__(self, engine: Any) -> dict[int, dict[str, Any]]:
        raw = self._provider(engine)
        if not isinstance(raw, Mapping):
            raise CBehaviorAdapterError("C behavior provider must return a mapping")

        people = getattr(engine, "person_map", None)
        if not isinstance(people, Mapping):
            raise CBehaviorAdapterError("B engine must expose person_map")
        source_to_runtime: dict[int, int] = {}
        for runtime_key, person in people.items():
            runtime_id = _as_person_id(getattr(person, "id", runtime_key), field="B person id")
            source_id = getattr(person, "source_person_id", runtime_id)
            source_to_runtime[_as_person_id(source_id, field="C source person id")] = runtime_id

        normalized: dict[int, dict[str, Any]] = {}
        for result_map in _behavior_maps(raw):
            for raw_id, behavior in result_map.items():
                source_id = _as_person_id(raw_id, field="C behavior person id")
                runtime_id = source_to_runtime.get(source_id, source_id)
                if runtime_id not in people:
                    raise CBehaviorAdapterError(
                        f"C behavior person id {source_id} is not present in B runtime"
                    )
                if not isinstance(behavior, Mapping):
                    raise CBehaviorAdapterError(
                        f"C behavior for person {source_id} must be a mapping"
                    )
                normalized.setdefault(runtime_id, {}).update(dict(behavior))
        return normalized
