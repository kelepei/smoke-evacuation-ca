# B → D Web Runtime integration contract

This document records the current, code-backed B interface consumed by D's
Enhanced Web Runtime. It describes the `origin/main` integration state; it
does not introduce a parallel smoke or avoidance implementation.

| Feature | B implementation | D call / display path | Inputs | Real output | Trigger | Status |
| --- | --- | --- | --- | --- | --- | --- |
| Smoke field and wall blocking | `simulation/smoke_model.py`: `SmokeDiffusionModel.update_smoke()` / `_laplacian()` | `EvacEngine.run_one_step()` → `EvacEngineRuntimeAdapter.smoke_matrix` → `CaSnapshotAdapter.fields.smoke_field` → Enhanced canvas `drawField()` | `Grid` with dense row-major cells; `SmokeSource(x, y, intensity)` from `smoke_source` cells | `float[height][width]`, normalized dimensionless concentration in `[0, 1]`; `smoke_matrix[y][x]` | Every B step when at least one source is present | D connected; Enhanced renderer uses the real `[0,1]` scale |
| Smoke avoidance in movement | `simulation/ca_model.py`: `calc_next_position()` | `EvacEngine.run_one_step(c_step_data)` calls it once for each eligible person | Current person, `smoke_matrix`, occupancy, exits, C step behavior, `scene.parameters["ca_weights"]` | Next grid coordinate; D never fabricates a trajectory | Person must be informed / not waiting / not dead / not evacuated | D connected |
| Hard smoke avoidance | `calc_next_position()`: candidate is rejected when `next_smoke > 0.9` | Same as above | Candidate-cell concentration | Candidate excluded from B movement decision | Every movement step | D connected |
| Soft smoke and smoke-gradient cost | `calc_next_position()`: `w_s_eff * next_smoke**1.5` and positive `dS` penalty | Same as above | Candidate / current smoke and `risk_sensitivity` | Lower movement utility for smoky cells | Every movement step | D connected |
| Risk / dose | `simulation/risk_perception.py`, `simulation/risk_metrics.py` | B calls both in `EvacEngine.run_one_step()`; D exposes `person.dose` when B writes it | People, smoke field, `time_step_s` | Dose is in snapshot `people[].dose` | Every B step | Dose observable; per-person risk is not currently published by B |
| Alarm | `EvacEngine.run_one_step()`: native `is_alarm_triggered` | Adapter `alarm_triggered` → snapshot `fields.alarm_triggered` → UI / guidance | Optional map alarm points; smoke matrix; B threshold `0.45` | Boolean alarm state | B step | D connected |

## Exact runtime flow

1. D loads the edited A map with `visualization.scene_input_adapter.load_map_grid()` through `experiments.integrated_runner.build_integrated_scenario()`.
2. D creates B `ScenarioConfig` from that `Grid`, A/C-positioned people, exits and map `smoke_source` cells.
3. `experiments.web_runtime_server._control_runtime_config()` explicitly sets `web_mode=true`; `experiments.integrated_runner.build_integrated_scenario()` carries that documented C setting into `d_c_runtime`.
4. `experiments.b_runtime_adapter.EvacEngineRuntimeAdapter.step()` gets C's real behavior mapping from `experiments.c_runtime_bridge.CWebRuntimeBridge`, then calls `EvacEngine.run_one_step(c_step_data)`.
5. B updates its smoke engine before risk and movement; the movement function receives that same current `smoke_matrix`.
6. `visualization.ca_snapshot_adapter.CaSnapshotAdapter.capture()` serializes B's matrix and people positions without inferring values. The page renders `snapshot.fields.smoke_field` and `snapshot.people`.

## Required B inputs

- Dense `Grid` (`width`, `height`, row-major `cells`) with `wall`, `obstacle`, `free`, `exit`, and optional `smoke_source` cell types.
- `ScenarioConfig.smoke_sources`: D creates these from map `smoke_source` cells.
- `ScenarioConfig.persons`: C attributes plus A-assigned `x/y`; D preserves C fields when assembling B persons.
- Optional C per-step mapping keyed by runtime person id. D passes it directly to `EvacEngine.run_one_step()`.

## What D receives and should render

- Current smoke concentration: `snapshot.fields.smoke_field[y][x]`.
- Current source list: `snapshot.fields.smoke_sources[]`.
- Current B movement result: `snapshot.people[].x/y` after a step.
- Person smoke at position: `snapshot.people[].smoke_concentration`.
- Dose / state where B provides it: `people[].dose`, `is_dead`, `evacuated`, `info_state`, `is_waiting`, panic fields.
- Alarm: `snapshot.fields.alarm_triggered` and `snapshot.fields.max_smoke_concentration`.

## Preconditions that otherwise look like a Web failure

1. A map with **no `smoke_source` cell cannot produce smoke or smoke avoidance**. D reports this as “当前场景无烟源”.
2. Movement avoidance only applies after a person is informed. C's Web bridge provides this state from source proximity, smoke exposure, alarm/relationship/guide propagation.
3. The Enhanced endpoint must set C's `web_mode=true`, which D now does explicitly. Previously an absent setting defaulted to non-Web behavior, so the source/smoke information chain could fail to make avoidance observable even though B's movement function was present.
4. Current B smoke values are normalized `[0,1]`. The Enhanced renderer must not apply the former `[0,10]` display scale.

## Observed wall-barrier behavior

The current model makes wall and obstacle cells zero and applies zero Laplacian there. In D's full-barrier synthetic test, after five updates the far-side maximum is below `1e-6`, while the identical open map is above `1e-3`; this is below the Web/C smoke exposure threshold `0.02`.

The Gaussian source term has a very small nonzero numerical tail on the far side of a wall. If B requires mathematically exact zero beyond a sealed wall—not merely no practically observable transfer—B must clip/mask its source term by connected component. D should not change that model rule.

## B follow-up only if needed

- Publish a documented per-person risk value or a `risk_field` if B wants D to visualize it; B currently computes an internal risk dictionary but does not publish it to `person.risk` or a field.
- Expose smoke-model parameters through `ScenarioConfig.parameters` if B expects Web users to tune them. D currently uses B's constructor defaults and does not invent parameter values.
- Keep the normalized `[0,1]` smoke-field contract and native `is_alarm_triggered` contract stable.
