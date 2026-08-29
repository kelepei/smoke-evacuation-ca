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
3. Select C's YAML configuration, or apply the C configuration form.
4. Click `启动 A+B+C 联调`.
5. Use `单步`, `开始`, `暂停`, and `重置` to control the existing B
   `run_one_step(c_step_data)` runtime.
6. Click `导出结果包` after a run to download config, people/event CSV,
   metrics, and a final PNG.

## Data Rules

- The map, people, smoke field, smoke sources, and evacuation status are
  drawn from actual runtime snapshots.
- The evacuation curve is calculated from received snapshots only.
- Exit utilization is shown only after actual evacuations with a supplied
  `target_exit`; otherwise it is shown as unavailable.
- Risk and dose are shown only when supplied by upstream people records;
  otherwise they are `NA`.
- Current repository sample has no map smoke source. B must provide runtime
  `smoke_sources` and `smoke_matrix` for smoke to appear.
