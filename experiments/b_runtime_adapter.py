"""D-side read-only control adapter for B's current ``EvacEngine``.

The current B runtime exposes ``EvacEngine(scene)``, ``run_one_step(data)``
and ``is_all_evacuated()``. D's runner needs a small stable control surface
for snapshots and CSV logging. This adapter never changes B source or rules.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
import math
from typing import Any


class BRuntimeAdapterError(ValueError):
    """Raised when a D caller supplies invalid optional B behavior input."""


BehaviorProvider = Callable[[Any], Mapping[int, Mapping[str, Any]]]


# B06 reports this global threshold, but the current B ``EvacEngine`` does not
# expose it as a runtime field.  D uses it only to project B06's real smoke
# matrix into a documented alert context for recommendation-only consumers.
B06_SMOKE_ALARM_THRESHOLD = 0.45


def _prepare_b_exit_tuples(engine: Any) -> bool:
    """Keep shared-schema exits object-shaped for current B runtime."""

    # B builds the cell tuples passed to calc_next_position itself; it still
    # reads engine.exits object attributes for logging and actual_exit.
    return False


def _install_indexed_grid_lookup(grid: Any) -> bool:
    """Use D's validated row-major Grid layout for constant-time lookup."""

    try:
        width = int(grid.width)
        height = int(grid.height)
        cells = grid.cells
    except (AttributeError, TypeError, ValueError):
        return False
    if width <= 0 or height <= 0 or len(cells) != width * height:
        return False
    for index, cell in enumerate(cells):
        if int(getattr(cell, "x", -1)) != index % width or int(
            getattr(cell, "y", -1)
        ) != index // width:
            return False

    def get_cell(x: int, y: int) -> Any:
        try:
            ix = int(x)
            iy = int(y)
        except (TypeError, ValueError, OverflowError):
            return None
        if ix != x or iy != y or not (0 <= ix < width and 0 <= iy < height):
            return None
        return cells[iy * width + ix]

    setattr(grid, "get_cell", get_cell)
    return True


class EvacEngineRuntimeAdapter:
    """Expose B's current public runtime through D's runner contract.

    With no ``behavior_provider``, D passes an empty behavior dictionary. This
    is not a synthetic policy: B documents a dictionary argument and C has not
    supplied per-step behavior output.
    """

    def __init__(
        self,
        engine: Any,
        *,
        behavior_provider: BehaviorProvider | None = None,
        render_upstream_animation: bool = False,
        adapter_meta: Mapping[str, Any] | None = None,
        exit_entities: Any = None,
        exit_entity_by_cell_id: Mapping[str, str] | None = None,
    ) -> None:
        for name in ("scene", "grid", "person_map", "smoke_matrix"):
            if not hasattr(engine, name):
                raise TypeError(f"B EvacEngine is missing {name}")
        if not callable(getattr(engine, "run_one_step", None)) and not callable(
            getattr(engine, "step", None)
        ):
            raise TypeError("B EvacEngine must expose run_one_step() or step()")
        if not callable(getattr(engine, "is_all_evacuated", None)) and not callable(
            getattr(engine, "all_done", None)
        ):
            raise TypeError(
                "B EvacEngine must expose is_all_evacuated() or all_done()"
            )
        self._engine = engine
        self._behavior_provider = behavior_provider
        self._exit_entities = self._normalize_exit_entities(exit_entities)
        self._exit_entity_by_cell_id = {
            str(cell_id): str(entity_id)
            for cell_id, entity_id in (exit_entity_by_cell_id or {}).items()
            if cell_id not in (None, "") and entity_id not in (None, "")
        }
        if not isinstance(render_upstream_animation, bool):
            raise TypeError("render_upstream_animation must be boolean")
        self._render_upstream_animation = render_upstream_animation
        initialized_fields: list[str] = []
        if _prepare_b_exit_tuples(self._engine):
            initialized_fields.append("B runtime exits=(x,y,exit_id) from scene grid")
        grid = self._engine.grid
        if _install_indexed_grid_lookup(grid):
            initialized_fields.append(
                "grid.get_cell(x,y) indexed from validated row-major cells"
            )
        elif not callable(getattr(grid, "get_cell", None)):
            def get_cell(x: int, y: int) -> Any:
                for cell in grid.cells:
                    if int(cell.x) == int(x) and int(cell.y) == int(y):
                        return cell
                return None

            setattr(grid, "get_cell", get_cell)
            initialized_fields.append("grid.get_cell(x,y) from existing cells")
        for person in self._engine.person_map.values():
            # B's current EvacEngine reads these fields but the current shared
            # Person constructor does not create them.  Add only the explicit
            # neutral defaults that B itself expects; no movement or behavior
            # value is inferred by D.
            if not hasattr(person, "evacuated"):
                person.evacuated = False
                initialized_fields.append("person.evacuated=False")
            if not hasattr(person, "dose"):
                person.dose = 0.0
                initialized_fields.append("person.dose=0.0")
        self.d_adapter_meta = {
            "input_mode": "A map + C population + B EvacEngine",
            "b_runtime_api": (
                "EvacEngine.run_one_step(c_step_data)"
                if callable(getattr(engine, "run_one_step", None))
                else "EvacEngine.step()"
            ),
            "behavior_input": "empty mapping; C behavior output not provided",
            # B currently calculates a local risk dictionary for movement but
            # does not publish that dictionary or write it to ``person.risk``.
            # Tell D views not to present the schema's default ``risk`` value
            # as a measured risk result.
            "risk_source": "B risk_engine result is not exposed per person",
            "dose_source": "B SmokeDoseRecorder person.dose",
            "upstream_animation": (
                "enabled"
                if render_upstream_animation
                else "suppressed; D browser/visualizer owns rendering"
            ),
            "runtime_instance_defaults": sorted(set(initialized_fields)),
            "missing_fields_are_null": True,
            "exit_topology": {
                "entity_count": len(self._exit_entities),
                "cell_to_entity": dict(self._exit_entity_by_cell_id),
            },
            "smoke_alarm_projection": {
                "threshold": B06_SMOKE_ALARM_THRESHOLD,
                "threshold_source": "B06 reported contract; not configurable in current B EvacEngine",
                "max_source": "B smoke_engine.get_max_smoke() when available",
                "alarm_source": "B field when published; otherwise D projection from B06 max smoke",
            },
        }
        if adapter_meta:
            self.d_adapter_meta.update(dict(adapter_meta))

    @property
    def config(self) -> Any:
        return self._engine.scene

    @property
    def grid(self) -> Any:
        return self._engine.grid

    @property
    def persons(self) -> Any:
        return self._engine.person_map

    @property
    def smoke_matrix(self) -> Any:
        return self._engine.smoke_matrix

    @property
    def max_smoke_concentration(self) -> float | None:
        """Read B06's global smoke maximum without changing B state."""

        raw_value = getattr(self._engine, "max_smoke_concentration", None)
        if isinstance(raw_value, (int, float)) and not isinstance(raw_value, bool):
            value = float(raw_value)
            return value if math.isfinite(value) else None
        smoke_engine = getattr(self._engine, "smoke_engine", None)
        getter = getattr(smoke_engine, "get_max_smoke", None)
        if callable(getter):
            value = float(getter())
            return value if math.isfinite(value) else None
        return None

    @property
    def alarm_triggered(self) -> bool | None:
        """Expose B's alert when available, otherwise project B06's field.

        The fallback is explicitly D-derived from B06 raw smoke; it must not
        be presented as a B-published state field.
        """

        raw_value = getattr(self._engine, "alarm_triggered", getattr(self._engine, "is_alarm_triggered", None))
        if isinstance(raw_value, bool):
            return raw_value
        maximum = self.max_smoke_concentration
        if maximum is None:
            return None
        return maximum >= B06_SMOKE_ALARM_THRESHOLD

    @property
    def alarm_source(self) -> str | None:
        """Report one authoritative source for the exposed alarm value."""

        if isinstance(getattr(self._engine, "alarm_triggered", getattr(self._engine, "is_alarm_triggered", None)), bool):
            return "b_native_runtime_field"
        return (
            "d_projection_from_b06_smoke"
            if self.max_smoke_concentration is not None
            else None
        )

    @property
    def max_smoke_source(self) -> str | None:
        raw_value = getattr(self._engine, "max_smoke_concentration", None)
        if isinstance(raw_value, (int, float)) and not isinstance(raw_value, bool):
            return "b_native_runtime_field"
        return (
            "b06_get_max_smoke"
            if self.max_smoke_concentration is not None
            else None
        )

    @property
    def smoke_sources(self) -> Any:
        """Expose B's runtime smoke sources without changing its model state.

        B's source generator may keep its collection on the engine, its smoke
        engine, or the scene depending on the integration revision.  D only
        reads the first public collection that is present.
        """

        sources = getattr(self._engine, "smoke_sources", None)
        if sources is not None:
            return sources
        smoke_engine = getattr(self._engine, "smoke_engine", None)
        sources = getattr(smoke_engine, "smoke_sources", None)
        if sources is not None:
            return sources
        return getattr(self._engine.scene, "smoke_sources", [])

    @property
    def current_step(self) -> int:
        return int(self._engine.current_step)

    @property
    def exit_entities(self) -> list[dict[str, Any]]:
        """D-only physical-exit metadata; B still uses individual exit cells."""

        return [dict(entity) for entity in self._exit_entities]

    @staticmethod
    def _normalize_exit_entities(raw_entities: Any) -> list[dict[str, Any]]:
        if raw_entities is None:
            return []
        result: list[dict[str, Any]] = []
        for entity in raw_entities:
            if hasattr(entity, "as_dict") and callable(entity.as_dict):
                entity = entity.as_dict()
            if not isinstance(entity, Mapping):
                raise TypeError("exit_entities entries must be mappings or expose as_dict()")
            entity_id = entity.get("exit_entity_id")
            member_cells = entity.get("member_cells")
            if entity_id in (None, "") or not isinstance(member_cells, (list, tuple)):
                raise ValueError("exit entity requires exit_entity_id and member_cells")
            result.append(dict(entity))
        return result

    def init_simulation(self) -> None:
        """B initializes state in ``EvacEngine.__init__``."""

        return None

    def all_done(self) -> bool:
        if callable(getattr(self._engine, "is_all_evacuated", None)):
            return bool(self._engine.is_all_evacuated())
        return bool(self._engine.all_done())

    def step(self) -> None:
        behavior: Mapping[int, Mapping[str, Any]] = {}
        if self._behavior_provider is not None:
            supplied = self._behavior_provider(self._engine)
            if not isinstance(supplied, Mapping):
                raise BRuntimeAdapterError("behavior_provider must return a mapping")
            behavior = supplied
        if callable(getattr(self._engine, "run_one_step", None)):
            self._run_one_step(dict(behavior))
        else:
            self._engine.step()
        after_step = getattr(self._behavior_provider, "after_b_step", None)
        if callable(after_step):
            after_step(self._engine)
        self._record_exit_entity_ids()

    @property
    def c_runtime_state(self) -> Any:
        """Read C's published bridge state when a real provider is installed."""

        return getattr(self._engine, "c_runtime_state", None)

    @property
    def guides(self) -> Any:
        state = self.c_runtime_state
        if isinstance(state, Mapping):
            return state.get("guides", [])
        return []

    def _record_exit_entity_ids(self) -> None:
        """Attach D aliases after B has recorded its original cell-level ID."""

        if not self._exit_entity_by_cell_id:
            return
        for person in self._engine.person_map.values():
            actual_exit = getattr(person, "actual_exit", None)
            if actual_exit in (None, ""):
                continue
            actual_exit_cell = str(actual_exit)
            setattr(person, "actual_exit_cell", actual_exit_cell)
            entity_id = self._exit_entity_by_cell_id.get(actual_exit_cell)
            if entity_id is not None:
                setattr(person, "actual_exit_entity", entity_id)

    def _run_one_step(self, behavior: dict[int, Mapping[str, Any]]) -> None:
        """Call B once while avoiding its duplicate Matplotlib renderer."""

        if self._render_upstream_animation or not callable(
            getattr(self._engine, "draw_animation", None)
        ):
            self._engine.run_one_step(behavior)
            return

        instance_state = getattr(self._engine, "__dict__", None)
        if not isinstance(instance_state, dict):
            self._engine.run_one_step(behavior)
            return
        had_override = "draw_animation" in instance_state
        original_override = instance_state.get("draw_animation")
        try:
            setattr(self._engine, "draw_animation", lambda *_args, **_kwargs: None)
        except (AttributeError, TypeError):
            self._engine.run_one_step(behavior)
            return
        try:
            self._engine.run_one_step(behavior)
        finally:
            if had_override:
                setattr(self._engine, "draw_animation", original_override)
            else:
                delattr(self._engine, "draw_animation")
