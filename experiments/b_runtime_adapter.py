"""D-side read-only control adapter for B's current ``EvacEngine``.

The current B runtime exposes ``EvacEngine(scene)``, ``run_one_step(data)``
and ``is_all_evacuated()``. D's runner needs a small stable control surface
for snapshots and CSV logging. This adapter never changes B source or rules.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any


class BRuntimeAdapterError(ValueError):
    """Raised when a D caller supplies invalid optional B behavior input."""


BehaviorProvider = Callable[[Any], Mapping[int, Mapping[str, Any]]]


def _prepare_b_exit_tuples(engine: Any) -> bool:
    """Adapt shared-schema exits to B's current runtime-only tuple contract."""

    raw_exits = getattr(engine, "exits", None)
    if not isinstance(raw_exits, list) or not raw_exits:
        return False
    if all(isinstance(item, tuple) and len(item) == 3 for item in raw_exits):
        return False

    grid = getattr(engine, "grid", None)
    cells = getattr(grid, "cells", [])
    exit_cells = [
        (int(cell.x), int(cell.y))
        for cell in cells
        if getattr(getattr(cell, "cell_type", None), "value", getattr(cell, "cell_type", None))
        == "exit"
    ]
    if len(exit_cells) != len(raw_exits):
        return False

    tuples: list[tuple[int, int, str]] = []
    for index, exit_obj in enumerate(raw_exits):
        exit_id = getattr(exit_obj, "id", getattr(exit_obj, "exit_id", None))
        if exit_id in (None, ""):
            return False
        x = getattr(exit_obj, "x", exit_cells[index][0])
        y = getattr(exit_obj, "y", exit_cells[index][1])
        tuples.append((int(x), int(y), str(exit_id)))
    engine.exits = tuples
    return True


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
