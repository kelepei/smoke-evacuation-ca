"""CSV logging for D-side simulation snapshots.

The logger accepts the normalized snapshot dictionaries produced by
``visualization.ca_snapshot_adapter``.  Missing optional upstream values are
written as empty cells instead of being replaced with demonstration data.
"""

from __future__ import annotations

import csv
import json
import math
from numbers import Integral, Real
from pathlib import Path
from typing import Any, Mapping, Sequence, TextIO


PEOPLE_LOG_FIELDS = [
    "run_id",
    "schema_version",
    "scenario_id",
    "random_seed",
    "step",
    "time_step_s",
    "time_s",
    "person_id",
    "x",
    "y",
    "heading",
    "status",
    "target_exit",
    "actual_exit",
    "evacuated",
    "smoke_concentration",
    "risk",
    "dose",
    "info_state",
    "info_source",
    "receive_time",
    "follow_target",
]

EVENT_LOG_FIELDS = [
    "run_id",
    "schema_version",
    "scenario_id",
    "random_seed",
    "step",
    "time_step_s",
    "time_s",
    "event_type",
    "person_id",
    "x",
    "y",
    "source",
    "target",
    "details",
]

ALLOWED_EVENT_TYPES = {"exit_switch", "evac_success", "conflict"}


class CsvLogError(ValueError):
    """Raised when a snapshot cannot be logged safely."""


def _csv_value(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=isinstance(value, dict),
        )
    return value


def _required(mapping: Mapping[str, Any], field: str, owner: str) -> Any:
    if field not in mapping or mapping[field] is None:
        raise CsvLogError(f"{owner}.{field} is required")
    return mapping[field]


def _strict_int(
    value: Any,
    *,
    field: str,
    minimum: int | None = None,
) -> int:
    if isinstance(value, bool) or not isinstance(value, Integral):
        raise CsvLogError(f"{field} must be an integer")
    normalized = int(value)
    if minimum is not None and normalized < minimum:
        raise CsvLogError(f"{field} must be >= {minimum}")
    return normalized


def _finite_float(value: Any, *, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise CsvLogError(f"{field} must be numeric")
    normalized = float(value)
    if not math.isfinite(normalized):
        raise CsvLogError(f"{field} must be finite")
    return normalized


class CsvExperimentLogger:
    """Write people and event CSV files for one reproducible run."""

    def __init__(
        self,
        output_dir: str | Path,
        *,
        run_id: str,
        scenario_id: str,
        random_seed: int | None,
        time_step_s: float,
        schema_version: str = "0.1-draft",
        overwrite: bool = False,
        derive_evac_success: bool = True,
    ) -> None:
        if not run_id:
            raise ValueError("run_id must not be empty")
        if (
            Path(run_id).name != run_id
            or run_id in {".", ".."}
            or "/" in run_id
            or "\\" in run_id
        ):
            raise ValueError("run_id must be a single safe path component")
        if not scenario_id:
            raise ValueError("scenario_id must not be empty")
        if random_seed is not None and (
            isinstance(random_seed, bool) or not isinstance(random_seed, Integral)
        ):
            raise ValueError("random_seed must be an integer or None")
        try:
            normalized_time_step = float(time_step_s)
        except (TypeError, ValueError) as exc:
            raise ValueError("time_step_s must be numeric") from exc
        if (
            isinstance(time_step_s, bool)
            or not math.isfinite(normalized_time_step)
            or normalized_time_step <= 0
        ):
            raise ValueError("time_step_s must be greater than zero")

        self.output_dir = Path(output_dir)
        self.run_id = run_id
        self.scenario_id = scenario_id
        self.random_seed = None if random_seed is None else int(random_seed)
        self.time_step_s = normalized_time_step
        self.schema_version = schema_version
        self.overwrite = overwrite
        self.derive_evac_success = derive_evac_success

        self.people_log_path = self.output_dir / "people_log.csv"
        self.event_log_path = self.output_dir / "event_log.csv"

        self._people_file: TextIO | None = None
        self._event_file: TextIO | None = None
        self._people_writer: csv.DictWriter | None = None
        self._event_writer: csv.DictWriter | None = None
        self._last_step: int | None = None
        self._person_ids: set[int] | None = None
        self._previous_evacuation: dict[Any, bool] = {}
        self._previous_positions: dict[Any, tuple[int, int]] = {}
        self._logged_evacuation_ids: set[Any] = set()
        self._failed = False

    def start(self) -> "CsvExperimentLogger":
        if self._failed:
            raise RuntimeError("logger is in a failed state")
        if self._people_file is not None:
            return self

        self.output_dir.mkdir(parents=True, exist_ok=True)
        if not self.overwrite:
            existing = [
                path
                for path in (self.people_log_path, self.event_log_path)
                if path.exists()
            ]
            if existing:
                names = ", ".join(path.name for path in existing)
                raise FileExistsError(
                    f"refusing to overwrite existing log file(s): {names}"
                )

        try:
            self._people_file = self.people_log_path.open(
                "w", newline="", encoding="utf-8"
            )
            self._event_file = self.event_log_path.open(
                "w", newline="", encoding="utf-8"
            )
            self._people_writer = csv.DictWriter(
                self._people_file, fieldnames=PEOPLE_LOG_FIELDS
            )
            self._event_writer = csv.DictWriter(
                self._event_file, fieldnames=EVENT_LOG_FIELDS
            )
            self._people_writer.writeheader()
            self._event_writer.writeheader()
            self._flush()
        except Exception:
            self._failed = True
            self.close()
            raise
        return self

    def record_snapshot(self, snapshot: Mapping[str, Any]) -> None:
        if self._failed:
            raise RuntimeError("logger is in a failed state")
        if self._people_file is None:
            self.start()

        step = _strict_int(
            _required(snapshot, "step", "snapshot"),
            field="snapshot.step",
            minimum=0,
        )
        if self._last_step is None and step != 0:
            raise CsvLogError("the first snapshot must use step=0")
        if self._last_step is not None and step != self._last_step + 1:
            raise CsvLogError("snapshot.step must increase by exactly one")

        snapshot_run_id = _required(snapshot, "run_id", "snapshot")
        snapshot_scenario = _required(snapshot, "scenario_id", "snapshot")
        snapshot_schema = _required(snapshot, "schema_version", "snapshot")
        if snapshot_run_id != self.run_id:
            raise CsvLogError("snapshot.run_id does not match logger run_id")
        if snapshot_scenario != self.scenario_id:
            raise CsvLogError(
                "snapshot.scenario_id does not match logger scenario_id"
            )
        if snapshot_schema != self.schema_version:
            raise CsvLogError(
                "snapshot.schema_version does not match logger schema_version"
            )
        if (
            "random_seed" in snapshot
            and snapshot.get("random_seed") != self.random_seed
        ):
            raise CsvLogError(
                "snapshot.random_seed does not match logger random_seed"
            )

        snapshot_time_step = _finite_float(
            _required(snapshot, "time_step", "snapshot"),
            field="snapshot.time_step",
        )
        if not math.isclose(
            snapshot_time_step, self.time_step_s, rel_tol=0.0, abs_tol=1e-12
        ):
            raise CsvLogError(
                "snapshot.time_step does not match logger time_step_s"
            )
        time_s = _finite_float(
            _required(snapshot, "time_s", "snapshot"),
            field="snapshot.time_s",
        )
        expected_time_s = step * self.time_step_s
        if not math.isclose(time_s, expected_time_s, rel_tol=0.0, abs_tol=1e-9):
            raise CsvLogError("snapshot.time_s must equal step * time_step")

        raw_people = _required(snapshot, "people", "snapshot")
        if not isinstance(raw_people, Sequence) or isinstance(
            raw_people, (str, bytes)
        ):
            raise CsvLogError("snapshot.people must be a sequence")

        current_evacuation: dict[int, bool] = {}
        current_positions: dict[int, tuple[int, int]] = {}
        current_people: dict[int, Mapping[str, Any]] = {}
        people_rows: list[dict[str, Any]] = []
        common = {
            "run_id": self.run_id,
            "schema_version": self.schema_version,
            "scenario_id": self.scenario_id,
            "random_seed": self.random_seed,
            "step": step,
            "time_step_s": self.time_step_s,
            "time_s": time_s,
        }

        for index, raw_person in enumerate(raw_people):
            if not isinstance(raw_person, Mapping):
                raise CsvLogError(f"snapshot.people[{index}] must be a mapping")
            person_id = _strict_int(
                _required(raw_person, "person_id", f"snapshot.people[{index}]"),
                field=f"snapshot.people[{index}].person_id",
                minimum=1,
            )
            if person_id in current_people:
                raise CsvLogError(f"duplicate person_id in snapshot: {person_id!r}")
            x = _strict_int(
                _required(raw_person, "x", f"snapshot.people[{index}]"),
                field=f"snapshot.people[{index}].x",
                minimum=0,
            )
            y = _strict_int(
                _required(raw_person, "y", f"snapshot.people[{index}]"),
                field=f"snapshot.people[{index}].y",
                minimum=0,
            )
            raw_evacuated = _required(
                raw_person, "evacuated", f"snapshot.people[{index}]"
            )
            if not isinstance(raw_evacuated, bool):
                raise CsvLogError(
                    f"snapshot.people[{index}].evacuated must be boolean"
                )
            status = raw_person.get("status")
            if raw_evacuated and status not in (None, "", "EVACUATED"):
                raise CsvLogError(
                    f"snapshot.people[{index}].status conflicts with evacuated=true"
                )
            if not raw_evacuated and status == "EVACUATED":
                raise CsvLogError(
                    f"snapshot.people[{index}].status conflicts with evacuated=false"
                )
            actual_exit = raw_person.get("actual_exit")
            if not raw_evacuated and actual_exit not in (None, ""):
                raise CsvLogError(
                    f"snapshot.people[{index}].actual_exit requires evacuated=true"
                )

            smoke_concentration = raw_person.get("smoke_concentration")
            if smoke_concentration is not None:
                smoke_concentration = _finite_float(
                    smoke_concentration,
                    field=f"snapshot.people[{index}].smoke_concentration",
                )
            risk = raw_person.get("risk")
            if risk is not None:
                risk = _finite_float(
                    risk, field=f"snapshot.people[{index}].risk"
                )
            dose = raw_person.get("dose")
            if dose is not None:
                dose = _finite_float(
                    dose, field=f"snapshot.people[{index}].dose"
                )
            receive_time = raw_person.get("receive_time")
            if receive_time is not None:
                receive_time = _strict_int(
                    receive_time,
                    field=f"snapshot.people[{index}].receive_time",
                    minimum=0,
                )
            follow_target = raw_person.get("follow_target")
            if follow_target is not None:
                follow_target = _strict_int(
                    follow_target,
                    field=f"snapshot.people[{index}].follow_target",
                    minimum=1,
                )

            current_people[person_id] = raw_person
            current_evacuation[person_id] = raw_evacuated
            current_positions[person_id] = (x, y)
            row = {
                **common,
                "person_id": person_id,
                "x": x,
                "y": y,
                "heading": raw_person.get("heading"),
                "status": status,
                "target_exit": raw_person.get("target_exit"),
                "actual_exit": actual_exit,
                "evacuated": raw_evacuated,
                "smoke_concentration": smoke_concentration,
                "risk": risk,
                "dose": dose,
                "info_state": raw_person.get("info_state"),
                "info_source": raw_person.get("info_source"),
                "receive_time": receive_time,
                "follow_target": follow_target,
            }
            people_rows.append(row)

        current_person_ids = set(current_people)
        if self._person_ids is not None and current_person_ids != self._person_ids:
            missing = sorted(self._person_ids - current_person_ids)
            added = sorted(current_person_ids - self._person_ids)
            raise CsvLogError(
                "snapshot.people IDs must remain fixed; "
                f"missing={missing}, added={added}"
            )
        for person_id, was_evacuated in self._previous_evacuation.items():
            if was_evacuated and not current_evacuation.get(person_id, False):
                raise CsvLogError(
                    f"person {person_id} cannot change evacuated true -> false"
                )
            if (
                was_evacuated
                and current_positions.get(person_id)
                != self._previous_positions.get(person_id)
            ):
                raise CsvLogError(
                    f"evacuated person {person_id} position must remain fixed"
                )

        event_rows, explicit_evacuation_ids, pending_evacuation_ids = (
            self._prepare_explicit_events(
                snapshot=snapshot,
                common=common,
                current_people=current_people,
                current_evacuation=current_evacuation,
            )
        )
        if self.derive_evac_success and self._last_step is not None:
            for person_id, evacuated in current_evacuation.items():
                newly_evacuated = (
                    evacuated
                    and not self._previous_evacuation.get(person_id, False)
                )
                if (
                    newly_evacuated
                    and person_id not in explicit_evacuation_ids
                    and person_id not in self._logged_evacuation_ids
                ):
                    person = current_people[person_id]
                    event_rows.append(
                        self._event_row(
                            common=common,
                            event_type="evac_success",
                            person_id=person_id,
                            x=current_positions[person_id][0],
                            y=current_positions[person_id][1],
                            source=None,
                            target=person.get("actual_exit")
                            or person.get("target_exit"),
                            details={
                                "derived_by": "D evacuation state transition"
                            },
                        )
                    )
                    pending_evacuation_ids.add(person_id)

        try:
            serialized_people_rows = [
                {key: _csv_value(value) for key, value in row.items()}
                for row in people_rows
            ]
            serialized_event_rows = [
                {key: _csv_value(value) for key, value in row.items()}
                for row in event_rows
            ]
        except (TypeError, ValueError) as exc:
            raise CsvLogError(
                "snapshot contains a value that cannot be serialized to CSV"
            ) from exc

        assert self._people_writer is not None
        assert self._event_writer is not None
        try:
            for row in serialized_people_rows:
                self._people_writer.writerow(row)
            for row in serialized_event_rows:
                self._event_writer.writerow(row)
            self._flush()
        except Exception:
            self._failed = True
            self.close()
            raise

        self._person_ids = current_person_ids
        self._previous_evacuation = current_evacuation
        self._previous_positions = current_positions
        self._logged_evacuation_ids.update(pending_evacuation_ids)
        self._last_step = step

    def _prepare_explicit_events(
        self,
        *,
        snapshot: Mapping[str, Any],
        common: Mapping[str, Any],
        current_people: Mapping[int, Mapping[str, Any]],
        current_evacuation: Mapping[int, bool],
    ) -> tuple[list[dict[str, Any]], set[int], set[int]]:
        raw_events = snapshot.get("events", [])
        if raw_events is None:
            return [], set(), set()
        if not isinstance(raw_events, Sequence) or isinstance(
            raw_events, (str, bytes)
        ):
            raise CsvLogError("snapshot.events must be a sequence")

        rows: list[dict[str, Any]] = []
        explicit_evacuation_ids: set[int] = set()
        pending_evacuation_ids: set[int] = set()
        for index, raw_event in enumerate(raw_events):
            if not isinstance(raw_event, Mapping):
                raise CsvLogError(f"snapshot.events[{index}] must be a mapping")
            event_type = raw_event.get("type", raw_event.get("event_type"))
            if not event_type:
                raise CsvLogError(f"snapshot.events[{index}].type is required")
            normalized_type = str(event_type).strip().lower()
            if normalized_type not in ALLOWED_EVENT_TYPES:
                raise CsvLogError(
                    f"unsupported event type: {normalized_type!r}"
                )

            raw_person_id = raw_event.get("person_id")
            person_id = (
                None
                if raw_person_id is None
                else _strict_int(
                    raw_person_id,
                    field=f"snapshot.events[{index}].person_id",
                    minimum=1,
                )
            )
            if person_id is not None and person_id not in current_people:
                raise CsvLogError(
                    f"event person_id {person_id} is not in snapshot.people"
                )
            if normalized_type == "evac_success":
                if person_id is None:
                    raise CsvLogError("evac_success requires person_id")
                if not current_evacuation[person_id]:
                    raise CsvLogError(
                        "evac_success person must have evacuated=true"
                    )
                if (
                    person_id in self._logged_evacuation_ids
                    or person_id in pending_evacuation_ids
                ):
                    continue
                explicit_evacuation_ids.add(person_id)
                pending_evacuation_ids.add(person_id)

            x = raw_event.get("x")
            y = raw_event.get("y")
            if x is not None:
                x = _strict_int(
                    x,
                    field=f"snapshot.events[{index}].x",
                    minimum=0,
                )
            if y is not None:
                y = _strict_int(
                    y,
                    field=f"snapshot.events[{index}].y",
                    minimum=0,
                )
            rows.append(
                self._event_row(
                    common=common,
                    event_type=normalized_type,
                    person_id=person_id,
                    x=x,
                    y=y,
                    source=raw_event.get("source"),
                    target=raw_event.get("target"),
                    details=raw_event.get("details"),
                )
            )
        return rows, explicit_evacuation_ids, pending_evacuation_ids

    @staticmethod
    def _event_row(
        *,
        common: Mapping[str, Any],
        event_type: str,
        person_id: Any,
        x: Any,
        y: Any,
        source: Any,
        target: Any,
        details: Any,
    ) -> dict[str, Any]:
        return {
            **common,
            "event_type": event_type,
            "person_id": person_id,
            "x": x,
            "y": y,
            "source": source,
            "target": target,
            "details": {} if details is None else details,
        }

    def _flush(self) -> None:
        if self._people_file is not None:
            self._people_file.flush()
        if self._event_file is not None:
            self._event_file.flush()

    def close(self) -> None:
        if self._people_file is not None:
            self._people_file.close()
            self._people_file = None
        if self._event_file is not None:
            self._event_file.close()
            self._event_file = None
        self._people_writer = None
        self._event_writer = None

    def __enter__(self) -> "CsvExperimentLogger":
        return self.start()

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()
