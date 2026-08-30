"""Read-only discovery of real D experiment outputs."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Iterable, Mapping

from experiments.result_package import ResultPackageError, build_runtime_analysis
from experiments.week6_analysis import analyze_run


class ExperimentHistoryError(ValueError):
    """Raised when an experiment result cannot be read safely."""


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return dict(payload) if isinstance(payload, Mapping) else {}


def _run_directories(root: Path) -> Iterable[Path]:
    if not root.is_dir():
        return
    for directory, names, files in os.walk(root, onerror=lambda _error: None):
        names[:] = [name for name in names if not name.startswith(".")]
        if {"people_log.csv", "config_used.json", "batch_run.json", "batch_failure.json"}.intersection(files):
            yield Path(directory)


def _relative_id(run_dir: Path, roots: Iterable[Path]) -> str:
    for root in roots:
        try:
            relative = run_dir.resolve().relative_to(root.resolve())
        except ValueError:
            continue
        return f"{root.name}/{relative.as_posix()}"
    return run_dir.name


def _package_info(run_dir: Path) -> dict[str, Any]:
    packages = sorted(run_dir.glob("*.zip"))
    if packages:
        package = packages[-1]
        return {"status": "present", "path": str(package), "filename": package.name}
    required = all((run_dir / name).is_file() for name in ("people_log.csv", "event_log.csv"))
    return {
        "status": "buildable" if required else "missing",
        "path": None,
        "filename": None,
    }


def _summary(run_dir: Path, roots: list[Path]) -> dict[str, Any]:
    failure = _read_json(run_dir / "batch_failure.json")
    try:
        metrics = analyze_run(run_dir)
        error = ""
    except (OSError, ValueError) as exc:
        metrics = {}
        error = str(failure.get("error") or exc)
    config = _read_json(run_dir / "config_used.json")
    batch = _read_json(run_dir / "batch_run.json")
    input_files = config.get("input_files") if isinstance(config.get("input_files"), Mapping) else {}
    return {
        "id": _relative_id(run_dir, roots),
        "experiment_id": str(batch.get("batch_id") or metrics.get("run_id") or run_dir.name),
        "scene": metrics.get("scenario_id", "NA"),
        "config": input_files.get("yaml") or "NA",
        "random_seed": metrics.get("random_seed", "NA"),
        "total_persons": metrics.get("total_persons", "NA"),
        "status": metrics.get("status", failure.get("status", "invalid")),
        "error": error,
        "total_evacuation_time_s": metrics.get("total_evacuation_time_s", "NA"),
        "mean_evacuation_time_s": metrics.get("mean_evacuation_time_s", "NA"),
        "t90_time_s": metrics.get("t90_time_s", "NA"),
        "evacuation_rate": metrics.get("evacuation_rate", "NA"),
        "remaining_count": metrics.get("remaining_count", "NA"),
        "simulation_steps": metrics.get("simulation_steps", "NA"),
        "simulation_time_s": metrics.get("simulation_time_s", "NA"),
        "output_dir": str(run_dir),
        "result_package": _package_info(run_dir),
        "timestamp_utc": batch.get("created_at_utc") or failure.get("recorded_at_utc") or "NA",
        "updated_at": run_dir.stat().st_mtime,
    }


def discover_experiments(
    roots: Iterable[str | Path], *, limit: int = 100
) -> list[dict[str, Any]]:
    """Return newest real runs from one or more formal output roots."""

    normalized = [Path(root).resolve() for root in roots]
    candidates: list[Path] = []
    for root in normalized:
        candidates.extend(_run_directories(root))
    candidates.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    results: list[dict[str, Any]] = []
    for run_dir in candidates[: max(0, limit)]:
        try:
            results.append(_summary(run_dir, normalized))
        except OSError:
            continue
    return results


def _resolve_experiment(experiment_id: str, roots: Iterable[Path]) -> Path:
    normalized_id = str(experiment_id or "").replace("\\", "/").strip("/")
    if not normalized_id or ".." in normalized_id.split("/"):
        raise ExperimentHistoryError("invalid experiment id")
    normalized_roots = [root.resolve() for root in roots]
    for root in normalized_roots:
        prefix = root.name + "/"
        if not normalized_id.startswith(prefix):
            continue
        candidate = (root / normalized_id[len(prefix) :]).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            continue
        if candidate.is_dir():
            return candidate
    raise ExperimentHistoryError("experiment does not exist")


def load_experiment_detail(
    experiment_id: str, roots: Iterable[str | Path]
) -> dict[str, Any]:
    """Load metrics, metadata, and real log-derived figures for one run."""

    normalized_roots = [Path(root).resolve() for root in roots]
    run_dir = _resolve_experiment(experiment_id, normalized_roots)
    config = _read_json(run_dir / "config_used.json")
    grid = config.get("grid") if isinstance(config.get("grid"), Mapping) else None
    snapshot = {"grid": grid} if grid else None
    try:
        analysis = build_runtime_analysis(
            output_dir=run_dir,
            final_snapshot=snapshot,
            include_figures=True,
            include_layers=False,
        )
    except ResultPackageError as exc:
        raise ExperimentHistoryError(str(exc)) from exc
    return {
        "id": experiment_id,
        "summary": _summary(run_dir, normalized_roots),
        "metrics": analysis["week6_metrics"],
        "metric_rows": analysis["metrics"],
        "metadata": {
            "config_used": config,
            "acceptance_report": _read_json(run_dir / "acceptance_report.json"),
            "batch_run": _read_json(run_dir / "batch_run.json"),
            "figure_grid": (
                "config_used.grid"
                if grid
                else "inferred from observed people_log coordinates"
            ),
        },
        "evacuation_curve_svg": analysis["evacuation_curve_svg"],
        "occupancy_heatmap_svg": analysis["occupancy_heatmap_svg"],
    }
