# D Platform MVP

This stage extends the established E0 pipeline without changing A/B/C core
algorithms. Every displayed value comes from an input file, a normalized
runtime snapshot, or a D CSV log. Missing upstream fields remain `NA`.

## Effective Runtime Inputs

- Map JSON/CSV/PNG through A's loader. A cell with `type: smoke_source` is the
  only supported smoke-source input.
- A-positioned C population JSON. Its person count is authoritative.
- Optional C YAML for scene identity and static population metadata.
- D runtime overrides: `random_seed`, `time_step_s`, and `max_steps`.

`relation_intensity`, group configuration, and profile ratios describe C's
static population input. They do not alter an already generated population or
B's per-step movement, so the final platform does not present them as active
strategy sliders.

## Real Data Layers

- People and exits: normalized runtime snapshot.
- Smoke: B's `smoke_matrix`/`smoke_field` and runtime smoke sources.
- Cumulative occupancy: count of non-evacuated person appearances per cell in
  `people_log.csv`.
- Trajectories: recorded person coordinates in `people_log.csv`.

## Metrics

`experiments/metrics_registry.py` defines metric names, labels, units, sources,
and NA conditions. `experiments/week6_analysis.py` is the canonical log-side
calculator consumed by artifacts, result packages, history, and batch output.

The formal metric table contains total persons, evacuated/remaining counts and
rate, first/mean/total evacuation time, T90, simulation steps/time, latest and
process-wide overlap diagnostics, and logged smoke/dose/risk fields. Total evacuation time is available
only after everyone evacuates. Exit utilization is `NA` until B emits
`actual_exit`; no information-propagation metric is currently reported.

## Batch and History Backends

`experiments.batch_run` executes seeds sequentially through
`create_integrated_runner`. Each seed has its own output directory and real
CSV logs; `batch_summary.csv` retains incomplete and failed rows with their
error text. Statistics use only available log-backed values. A distribution
figure is produced only when a metric has at least three real observations.

`experiments.experiment_history` is a read-only scanner of those directories.
It reports incomplete or failed artifacts as such, without reconstructing
missing metrics or timestamps.

## Start and Run

Start the local platform from the repository root with
`python -m experiments.web_runtime_server`, then open
`http://127.0.0.1:8765/visualization/prototype/final_platform.html`.

For one headless E0 run, use `python -m experiments.integrated_runner --map
maps/edited_map.json --population control/output_people_position.json --yaml
control/config_template.yaml --headless`. The browser supports initialization,
single-step, start/pause, reset, real layers, analysis refresh, result-package
export, and a collapsed read-only history panel.

Batch runs accept the same map/population/YAML plus `--seeds`, `--time-step`,
and `--max-steps`. Per-run outputs are `people_log.csv`, `event_log.csv`,
`config_used.json`, metrics, the evacuation curve, cumulative occupancy
heatmap, final frame, and result package. Batch roots add summary, statistics,
metadata, and (for at least three observations) a distribution figure.

## Seed Check (2026-08-30)

D forwards `random_seed` to both Python and NumPy before constructing B's
runtime. The current fixed-population E0 sample produced byte-equivalent
normalized trajectories for seeds 40, 41, and 42. B's current
`run_one_step` uses deterministic utility ordering; its separate random
conflict-solver helper is not called by that path. Therefore the seed sweep is
currently repeatability/reproducibility infrastructure, not evidence of
statistical stability or stochastic variation. D does not add artificial
noise.

## Current Upstream Limits

- The current B version still produces real active-person overlaps.
- B has not yet supplied `actual_exit`, so exit utilization is `NA`.
- B has not yet supplied a logged risk field or integrated its conflict fix.
- C per-step behavior is not yet merged by `person_id` into B movement input.
- This is a D-stage MVP, not the completed project or a complete ABCD social
  behavior coupling.
