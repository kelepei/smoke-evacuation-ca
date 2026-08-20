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
        grid = self._engine.grid
        if not callable(getattr(grid, "get_cell", None)):
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
