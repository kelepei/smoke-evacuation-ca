# Final Platform Guide

`visualization/prototype/final_platform.html` is the D-side final display
page. It uses the same local API and runtime as `integrated_runtime.html`, but
adds real-data analysis panels. It does not contain generated people, random
movement, demo curves, or demo metrics.

## Start

From the repository root:

```powershell
python -m experiments.web_runtime_server
```

Open this URL in a browser:

```text
http://127.0.0.1:8765/visualization/prototype/final_platform.html
```

`python main.py` is currently a headless batch run. It records snapshots and
CSV results but intentionally does not open a graphical window.

## Real A+B+C Run

1. Select an A JSON, CSV, or PNG map and click `导入并显示地图`.
2. Select C's people/relations output after A has assigned valid map
   coordinates (normally `output_people_position.json`).
3. Select C's YAML configuration when needed. The page exposes only runtime
   parameters that are currently effective: `random_seed`, `time_step_s`, and
   `max_steps`. C static fields are shown as upstream configuration, not as
   movement-strategy controls.
4. Click `初始化仿真`.
5. Use `单步`, `开始`, `暂停`, and `重置` to control the existing B
   `run_one_step(c_step_data)` runtime.
6. Click `导出结果包` after a run to download config, people/event CSV,
   registry-defined metrics, figures, and input metadata.
7. Use the layer switches for people, exits, real `smoke_matrix`, cumulative
   occupancy, and trajectories. The latter two are derived from the active
   `people_log.csv`.
8. Use `实验历史` to browse existing log-backed outputs under
   `outputs/experiments` and `outputs/integrated`.

## Data Rules

- The map, people, smoke field, smoke sources, and evacuation status are
  drawn from actual runtime snapshots.
- The evacuation curve, cumulative occupancy, and trajectories are calculated
  from `people_log.csv` only.
- Exit utilization is shown only after B supplies `actual_exit`; a planned
  `target_exit` is never counted as an actual exit.
- Risk and dose are shown only when supplied by upstream people records;
  otherwise they are `NA`.
- Current repository sample has no map smoke source. B must provide runtime
  `smoke_sources` and `smoke_matrix` for smoke to appear.

## Multi-seed Batch

```powershell
python -m experiments.batch_run `
  --map maps/edited_map.json `
  --population control/output_people_position.json `
  --yaml control/config_template.yaml `
  --seeds 40 41 42 43 44 `
  --batch-id d_week8_seed_sweep
```

Each seed receives an independent directory containing CSV logs, canonical
metrics, real SVG figures, and a result ZIP. The batch directory contains
`batch_summary.csv`, `batch_statistics.csv`, and, when at least three real
observations are available, `batch_distribution.svg`. The history foldout on
the final platform scans only those real output directories.
