"""Run reproducible multi-seed D experiments through the formal runtime."""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from experiments.integrated_runner import create_integrated_runner
from experiments.metrics_registry import batch_metric_keys
from experiments.result_package import build_result_package, build_runtime_analysis
from experiments.run_artifacts import write_run_artifacts
from experiments.week6_analysis import analyze_run, write_analysis


class BatchRunError(ValueError):
    """Raised when a batch cannot be run without overwriting real output."""


SUMMARY_FIELDS = (
    "experiment_id",
    "seed",
    "run_id",
    "status",
    "error",
    "total_persons",
    "evacuated_count",
    "evacuation_rate",
    "remaining_count",
    "total_evacuation_time_s",
    "mean_evacuation_time_s",
    "t90_time_s",
    "simulation_steps",
    "simulation_time_s",
    "output_dir",
)


def _safe_id(value: str, *, field: str) -> str:
    if (
        not value
        or Path(value).name != value
        or value in {".", ".."}
        or "/" in value
        or "\\" in value
    ):
        raise BatchRunError(f"{field} must be a single safe path component")
    return value


def _numeric(value: Any) -> float | None:
    if value in (None, "", "NA") or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    index = (len(ordered) - 1) * fraction
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (index - lower)


def calculate_batch_statistics(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Calculate sample mean/std only from available real metric values."""

    materialized = list(rows)
    statistics_rows: list[dict[str, Any]] = []
    for metric in batch_metric_keys():
        values = [number for row in materialized if (number := _numeric(row.get(metric))) is not None]
        statistics_rows.append(
            {
                "metric_name": metric,
                "n": len(values),
                "mean": statistics.mean(values) if values else "NA",
                "std": statistics.stdev(values) if len(values) >= 2 else "NA",
                "min": min(values) if values else "NA",
                "q1": _percentile(values, 0.25) if values else "NA",
                "median": statistics.median(values) if values else "NA",
                "q3": _percentile(values, 0.75) if values else "NA",
                "max": max(values) if values else "NA",
            }
        )
    return statistics_rows


def batch_boxplot_svg(stat_rows: Iterable[Mapping[str, Any]]) -> str | None:
    """Draw registry metrics only when at least three real seeds exist."""

    rows = [row for row in stat_rows if int(row.get("n", 0)) >= 3]
    if not rows:
        return None
    width = 900
    row_height = 68
    left, right, top = 230, 50, 56
    chart_width = width - left - right
    height = top + row_height * len(rows) + 35
    elements = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        '<text x="24" y="30" font-family="Arial, Microsoft YaHei" font-size="18" fill="#172033">Batch 指标箱线图（真实运行）</text>',
    ]
    for index, row in enumerate(rows):
        values = [float(row[key]) for key in ("min", "q1", "median", "q3", "max")]
        minimum, q1, median, q3, maximum = values
        span = maximum - minimum
        scale = chart_width / span if span > 0 else 1.0
        x = lambda value: left + (value - minimum) * scale if span > 0 else left + chart_width / 2
        y = top + index * row_height + 24
        elements.extend(
            [
                f'<text x="20" y="{y + 5}" font-family="Arial" font-size="13" fill="#334155">{row["metric_name"]}</text>',
                f'<line x1="{x(minimum):.2f}" y1="{y}" x2="{x(maximum):.2f}" y2="{y}" stroke="#64748b" stroke-width="2"/>',
                f'<line x1="{x(minimum):.2f}" y1="{y - 8}" x2="{x(minimum):.2f}" y2="{y + 8}" stroke="#64748b"/>',
                f'<line x1="{x(maximum):.2f}" y1="{y - 8}" x2="{x(maximum):.2f}" y2="{y + 8}" stroke="#64748b"/>',
                f'<rect x="{x(q1):.2f}" y="{y - 13}" width="{max(2.0, x(q3) - x(q1)):.2f}" height="26" fill="#dbeafe" stroke="#2563eb"/>',
                f'<line x1="{x(median):.2f}" y1="{y - 13}" x2="{x(median):.2f}" y2="{y + 13}" stroke="#dc2626" stroke-width="2"/>',
                f'<text x="{left}" y="{y + 30}" font-family="Arial" font-size="11" fill="#64748b">min {minimum:g} / median {median:g} / max {maximum:g} / n={row["n"]}</text>',
            ]
        )
    elements.append("</svg>")
    return "".join(elements)


def _write_csv(path: Path, rows: list[Mapping[str, Any]], fields: Iterable[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(fields), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _summary_row(
    *,
    batch_id: str,
    seed: int,
    run_id: str,
    run_dir: Path,
    metrics: Mapping[str, Any] | None = None,
    error: str = "",
) -> dict[str, Any]:
    metrics = metrics or {}
    return {
        "experiment_id": batch_id,
        "seed": seed,
        "run_id": run_id,
        "status": metrics.get("status", "failed" if error else "incomplete"),
        "error": error,
        "total_persons": metrics.get("total_persons", "NA"),
        "evacuated_count": metrics.get("evacuated_count", "NA"),
        "evacuation_rate": metrics.get("evacuation_rate", "NA"),
        "remaining_count": metrics.get("remaining_count", "NA"),
        "total_evacuation_time_s": metrics.get("total_evacuation_time_s", "NA"),
        "mean_evacuation_time_s": metrics.get("mean_evacuation_time_s", "NA"),
        "t90_time_s": metrics.get("t90_time_s", "NA"),
        "simulation_steps": metrics.get("simulation_steps", "NA"),
        "simulation_time_s": metrics.get("simulation_time_s", "NA"),
        "output_dir": str(run_dir),
    }


def run_batch(
    *,
    map_path: str | Path,
    population_path: str | Path,
    yaml_path: str | Path | None,
    seeds: Iterable[int],
    output_root: str | Path,
    batch_id: str,
    time_step_s: float = 0.5,
    max_steps: int = 500,
) -> dict[str, Any]:
    """Run one formal scenario sequentially for each unique seed."""

    normalized_batch_id = _safe_id(batch_id, field="batch_id")
    normalized_seeds = list(seeds)
    if not normalized_seeds or any(isinstance(seed, bool) or not isinstance(seed, int) for seed in normalized_seeds):
        raise BatchRunError("seeds must contain at least one integer")
    if len(set(normalized_seeds)) != len(normalized_seeds):
        raise BatchRunError("seeds must be unique")
    root = Path(output_root).resolve() / normalized_batch_id
    if root.exists():
        raise BatchRunError(f"refusing to overwrite existing batch directory: {root}")
    root.mkdir(parents=True)
    inputs = {
        "map": Path(map_path).resolve(),
        "population": Path(population_path).resolve(),
    }
    if yaml_path is not None:
        inputs["yaml"] = Path(yaml_path).resolve()

    summaries: list[dict[str, Any]] = []
    for seed in normalized_seeds:
        run_id = f"{normalized_batch_id}_seed_{seed}"
        run_dir = root / run_id
        runner = None
        try:
            runner = create_integrated_runner(
                map_path=inputs["map"],
                population_path=inputs["population"],
                yaml_path=inputs.get("yaml"),
                output_root=root,
                run_id=run_id,
                random_seed=seed,
                time_step_s=time_step_s,
                max_steps=max_steps,
            )
            runner.initialize()
            snapshot = runner.run_until_finished()
            write_run_artifacts(snapshot, run_dir, input_files=inputs, save_frame=True)
            metrics = analyze_run(run_dir)
            write_analysis(metrics, run_dir)
            analysis = build_runtime_analysis(
                output_dir=run_dir,
                final_snapshot=snapshot,
                include_figures=True,
            )
            (run_dir / "evacuation_curve.svg").write_text(
                analysis["evacuation_curve_svg"], encoding="utf-8"
            )
            (run_dir / "occupancy_heatmap.svg").write_text(
                analysis["occupancy_heatmap_svg"], encoding="utf-8"
            )
            package = build_result_package(
                output_dir=run_dir,
                final_snapshot=snapshot,
                input_files=inputs,
                max_steps=max_steps,
            )
            (run_dir / package.filename).write_bytes(package.content)
            (run_dir / "batch_run.json").write_text(
                json.dumps(
                    {
                        "batch_id": normalized_batch_id,
                        "seed": seed,
                        "created_at_utc": datetime.now(timezone.utc).isoformat(),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            summaries.append(_summary_row(batch_id=normalized_batch_id, seed=seed, run_id=run_id, run_dir=run_dir, metrics=metrics))
        except Exception as exc:  # Preserve this seed's actual failure and continue.
            run_dir.mkdir(parents=True, exist_ok=True)
            error = f"{type(exc).__name__}: {exc}"
            (run_dir / "batch_failure.json").write_text(
                json.dumps({"batch_id": normalized_batch_id, "seed": seed, "run_id": run_id, "status": "failed", "error": error, "recorded_at_utc": datetime.now(timezone.utc).isoformat()}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            summaries.append(_summary_row(batch_id=normalized_batch_id, seed=seed, run_id=run_id, run_dir=run_dir, error=error))
        finally:
            if runner is not None:
                runner.close()

    _write_csv(root / "batch_summary.csv", summaries, SUMMARY_FIELDS)
    stat_rows = calculate_batch_statistics(summaries)
    _write_csv(
        root / "batch_statistics.csv",
        stat_rows,
        ("metric_name", "n", "mean", "std", "min", "q1", "median", "q3", "max"),
    )
    boxplot = batch_boxplot_svg(stat_rows)
    if boxplot is not None:
        (root / "batch_distribution.svg").write_text(boxplot, encoding="utf-8")
    metadata = {
        "batch_id": normalized_batch_id,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "seeds": normalized_seeds,
        "time_step_s": time_step_s,
        "max_steps": max_steps,
        "input_files": {key: str(path) for key, path in inputs.items()},
        "run_count": len(summaries),
        "data_rule": "all rows and figures derive from formal runtime CSV logs",
    }
    (root / "batch_metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {
        "batch_id": normalized_batch_id,
        "output_dir": str(root),
        "runs": summaries,
        "statistics": stat_rows,
        "distribution_figure": str(root / "batch_distribution.svg") if boxplot else None,
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a formal D multi-seed experiment batch.")
    parser.add_argument("--map", required=True, type=Path)
    parser.add_argument("--population", required=True, type=Path)
    parser.add_argument("--yaml", type=Path)
    parser.add_argument("--seeds", required=True, type=int, nargs="+")
    parser.add_argument("--batch-id", required=True)
    parser.add_argument("--output-root", type=Path, default=Path("outputs") / "experiments")
    parser.add_argument("--time-step", type=float, default=0.5)
    parser.add_argument("--max-steps", type=int, default=500)
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    result = run_batch(
        map_path=args.map,
        population_path=args.population,
        yaml_path=args.yaml,
        seeds=args.seeds,
        output_root=args.output_root,
        batch_id=args.batch_id,
        time_step_s=args.time_step,
        max_steps=args.max_steps,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
