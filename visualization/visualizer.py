"""Matplotlib viewer for D's third-week real-step animation.

The viewer consumes normalized D snapshots through ``SimulationRunner``.  It
never changes upstream map, movement, smoke, or social-model state directly.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import ListedColormap
from matplotlib.patches import Patch
from matplotlib.widgets import Button, Slider


CELL_CODES = {
    "free": 0,
    "wall": 1,
    "obstacle": 2,
    "exit": 3,
    "smoke_source": 4,
    "sign": 5,
    "guide_zone": 6,
}

CELL_COLORS = ListedColormap(
    [
        "#f7f7f7",
        "#30343b",
        "#777c84",
        "#33a65c",
        "#bb4d00",
        "#ffcc33",
        "#6baed6",
    ]
)


class MatplotlibSimulationViewer:
    """Interactive map, smoke, and people viewer with four run controls."""

    def __init__(
        self,
        runner: Any,
        *,
        interval_ms: int = 300,
    ) -> None:
        if interval_ms <= 0:
            raise ValueError("interval_ms must be greater than zero")
        if not runner.initialized:
            raise RuntimeError("runner must be initialized before opening viewer")

        self.runner = runner
        self.base_interval_ms = int(interval_ms)
        self._running = False
        self._closed = False
        self._last_error: str | None = None

        self.figure, self.map_axes = plt.subplots(figsize=(11, 7))
        self.figure.subplots_adjust(bottom=0.20, left=0.08, right=0.97, top=0.91)
        self.figure.canvas.manager.set_window_title(
            "Pedestrian evacuation simulation"
        )

        self._status_text = self.figure.text(
            0.08,
            0.135,
            "",
            fontsize=9,
            color="#444444",
        )
        self._create_controls()
        self._timer = self.figure.canvas.new_timer(interval=self.base_interval_ms)
        self._timer.add_callback(self._on_timer)
        self.figure.canvas.mpl_connect("close_event", self._on_close)

        assert runner.current_snapshot is not None
        self.draw_snapshot(runner.current_snapshot)

    @property
    def running(self) -> bool:
        return self._running

    def _create_controls(self) -> None:
        start_axes = self.figure.add_axes([0.08, 0.055, 0.10, 0.05])
        pause_axes = self.figure.add_axes([0.19, 0.055, 0.10, 0.05])
        step_axes = self.figure.add_axes([0.30, 0.055, 0.10, 0.05])
        reset_axes = self.figure.add_axes([0.41, 0.055, 0.10, 0.05])
        speed_axes = self.figure.add_axes([0.61, 0.064, 0.28, 0.035])

        self.start_button = Button(start_axes, "Start")
        self.pause_button = Button(pause_axes, "Pause")
        self.step_button = Button(step_axes, "Single step")
        self.reset_button = Button(reset_axes, "Reset")
        self.speed_slider = Slider(
            speed_axes,
            "Speed",
            valmin=0.25,
            valmax=4.0,
            valinit=1.0,
            valstep=0.25,
        )

        self.start_button.on_clicked(self.start)
        self.pause_button.on_clicked(self.pause)
        self.step_button.on_clicked(self.single_step)
        self.reset_button.on_clicked(self.reset)
        self.speed_slider.on_changed(self._change_speed)

    def _change_speed(self, value: float) -> None:
        interval = max(1, int(round(self.base_interval_ms / float(value))))
        self._timer.interval = interval

    def start(self, _event: Any = None) -> None:
        if self._closed or self.runner.finished:
            return
        self._last_error = None
        self._running = True
        self._timer.start()
        self._update_status_text()
        self.figure.canvas.draw_idle()

    def pause(self, _event: Any = None) -> None:
        self._timer.stop()
        self._running = False
        self._update_status_text()
        self.figure.canvas.draw_idle()

    def single_step(self, _event: Any = None) -> None:
        self.pause()
        if self.runner.finished:
            return
        self._advance_once()

    def reset(self, _event: Any = None) -> None:
        self.pause()
        self._last_error = None
        snapshot = self.runner.reset()
        self.draw_snapshot(snapshot)

    def _on_timer(self) -> None:
        if not self._running:
            return
        if self.runner.finished:
            self.pause()
            return
        self._advance_once()
        if self.runner.finished:
            self.pause()

    def _advance_once(self) -> None:
        try:
            snapshot = self.runner.step()
        except Exception as exc:  # stop a GUI timer safely and surface the error
            self._last_error = f"{type(exc).__name__}: {exc}"
            self.pause()
            return
        self.draw_snapshot(snapshot)

    def draw_snapshot(self, snapshot: dict[str, Any]) -> None:
        grid = snapshot["grid"]
        width = int(grid["width"])
        height = int(grid["height"])
        raw_cell_type = grid["cell_type"]
        normalized_cell_types = [
            [str(value).lower() for value in row] for row in raw_cell_type
        ]
        unknown_cell_types = sorted(
            {
                value
                for row in normalized_cell_types
                for value in row
                if value not in CELL_CODES
            }
        )
        if unknown_cell_types:
            raise ValueError(
                "unknown cell_type value(s): "
                + ", ".join(unknown_cell_types)
            )
        cell_values = np.array(
            [
                [CELL_CODES[value] for value in row]
                for row in normalized_cell_types
            ],
            dtype=float,
        )

        self.map_axes.clear()
        self.map_axes.imshow(
            cell_values,
            origin="upper",
            interpolation="nearest",
            cmap=CELL_COLORS,
            vmin=0,
            vmax=len(CELL_CODES) - 1,
        )

        smoke_field = snapshot.get("fields", {}).get("smoke_field", [])
        if smoke_field:
            smoke = np.asarray(smoke_field, dtype=float)
            masked_smoke = np.ma.masked_less_equal(smoke, 0.0)
            self.map_axes.imshow(
                masked_smoke,
                origin="upper",
                interpolation="nearest",
                cmap="Reds",
                vmin=0,
                vmax=1,
                alpha=0.48,
            )

        people = snapshot["people"]
        active = [person for person in people if not person["evacuated"]]
        evacuated = [person for person in people if person["evacuated"]]
        if active:
            self.map_axes.scatter(
                [person["x"] for person in active],
                [person["y"] for person in active],
                s=72,
                c="#2166ac",
                edgecolors="white",
                linewidths=0.8,
                label="evacuating",
                zorder=5,
            )
        if evacuated:
            self.map_axes.scatter(
                [person["x"] for person in evacuated],
                [person["y"] for person in evacuated],
                s=70,
                c="#33a65c",
                marker="x",
                linewidths=2.0,
                label="evacuated",
                zorder=6,
            )

        self.map_axes.set_xlim(-0.5, width - 0.5)
        self.map_axes.set_ylim(height - 0.5, -0.5)
        self.map_axes.set_aspect("equal")
        self.map_axes.set_xticks(np.arange(-0.5, width, 1), minor=True)
        self.map_axes.set_yticks(np.arange(-0.5, height, 1), minor=True)
        self.map_axes.grid(which="minor", color="#d9d9d9", linewidth=0.35)
        self.map_axes.tick_params(which="minor", bottom=False, left=False)
        self.map_axes.set_xlabel("x / cell")
        self.map_axes.set_ylabel("y / cell")
        self.map_axes.set_title(
            "Real CA step animation"
            f"  |  run={snapshot['run_id']}"
            f"  |  step={snapshot['step']}"
            f"  |  time={snapshot['time_s']:.1f}s"
            f"  |  evacuated={len(evacuated)}/{len(people)}"
        )

        legend_items = [
            Patch(facecolor="#30343b", label="wall"),
            Patch(facecolor="#777c84", label="obstacle"),
            Patch(facecolor="#33a65c", label="exit"),
            Patch(facecolor="#bb4d00", label="smoke source"),
            Patch(facecolor="#ef3b2c", alpha=0.48, label="smoke (0-1)"),
        ]
        self.map_axes.legend(
            handles=legend_items,
            loc="upper left",
            bbox_to_anchor=(1.005, 1.0),
            borderaxespad=0,
            fontsize=8,
        )
        self._update_status_text()
        self.figure.canvas.draw_idle()

    def _update_status_text(self) -> None:
        if self._last_error:
            status = f"Stopped with error: {self._last_error}"
        elif self.runner.finished:
            status = "Finished"
        elif self._running:
            status = "Running"
        else:
            status = "Paused"
        snapshot = self.runner.current_snapshot or {}
        adapter_meta = snapshot.get("adapter_meta", {})
        input_mode = adapter_meta.get("input_mode")
        source_text = (
            str(input_mode)
            if input_mode
            else "real B mock step() | A/C input not connected"
        )
        self._status_text.set_text(f"Status: {status}  |  {source_text}")

    def save_screenshot(self, path: str | Path, *, dpi: int = 150) -> Path:
        output_path = Path(path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        self.figure.savefig(output_path, dpi=dpi, bbox_inches="tight")
        return output_path

    def show(self) -> None:
        plt.show()

    def close(self) -> None:
        if self._closed:
            return
        self.pause()
        self.runner.close()
        self._closed = True
        plt.close(self.figure)

    def _on_close(self, _event: Any) -> None:
        if not self._closed:
            self._timer.stop()
            self._running = False
            self.runner.close()
            self._closed = True
