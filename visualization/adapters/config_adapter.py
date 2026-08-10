"""Small C-config adapter used by D's week-4 demo runtime.

D consumes only presentation/runtime parameters here.  If C's official
``SceneConfig`` loader is unavailable, this module falls back to a local view
so the visual demo can still run and clearly reports the fallback in metadata.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping


@dataclass(frozen=True)
class DSceneConfigView:
    scene_name: str
    total_persons: int
    profile_ratios: Mapping[str, float]
    group_config: Mapping[str, Any]
    relation_intensity: float
    random_seed: int | None
    source_path: str | None
    status: str
    note: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def fallback_scene_config() -> DSceneConfigView:
    return DSceneConfigView(
        scene_name="d_week4_demo",
        total_persons=18,
        profile_ratios={"default": 1.0},
        group_config={},
        relation_intensity=0.0,
        random_seed=42,
        source_path=None,
        status="fallback",
        note="C SceneConfig/YAML not provided; D demo uses local fallback values.",
    )


def _normalize_profile_ratios(value: Any) -> dict[str, float]:
    if not isinstance(value, Mapping) or not value:
        return {"default": 1.0}
    ratios = {str(key): float(raw) for key, raw in value.items()}
    total = sum(ratios.values())
    if total <= 0:
        return {"default": 1.0}
    return {key: raw / total for key, raw in ratios.items() if raw > 0}


def load_d_scene_config(path: str | Path | None = None) -> DSceneConfigView:
    """Load C-style YAML when available, otherwise return a D fallback view."""

    if path is None:
        return fallback_scene_config()

    config_path = Path(path)
    if not config_path.is_file():
        view = fallback_scene_config()
        return DSceneConfigView(
            **{
                **view.to_dict(),
                "source_path": str(config_path),
                "note": f"C YAML not found: {config_path}; using D fallback.",
            }
        )

    try:
        import yaml

        with config_path.open("r", encoding="utf-8") as stream:
            payload = yaml.safe_load(stream) or {}
    except Exception as exc:
        view = fallback_scene_config()
        return DSceneConfigView(
            **{
                **view.to_dict(),
                "source_path": str(config_path),
                "note": f"C YAML load failed ({type(exc).__name__}); using D fallback.",
            }
        )

    if not isinstance(payload, Mapping):
        view = fallback_scene_config()
        return DSceneConfigView(
            **{
                **view.to_dict(),
                "source_path": str(config_path),
                "note": "C YAML root is not a mapping; using D fallback.",
            }
        )

    fallback = fallback_scene_config()
    try:
        total_persons = int(payload.get("total_persons", fallback.total_persons))
        if total_persons <= 0:
            raise ValueError("total_persons must be positive")
        random_seed = payload.get("random_seed", fallback.random_seed)
        random_seed = None if random_seed is None else int(random_seed)
        relation_intensity = float(
            payload.get("relation_intensity", fallback.relation_intensity)
        )
    except (TypeError, ValueError) as exc:
        return DSceneConfigView(
            **{
                **fallback.to_dict(),
                "source_path": str(config_path),
                "note": f"C YAML has invalid scalar fields ({exc}); using D fallback.",
            }
        )

    group_config = payload.get("group_config", {})
    if not isinstance(group_config, Mapping):
        group_config = {}

    return DSceneConfigView(
        scene_name=str(payload.get("scene_name") or payload.get("name") or config_path.stem),
        total_persons=total_persons,
        profile_ratios=_normalize_profile_ratios(
            payload.get("profile_ratios", fallback.profile_ratios)
        ),
        group_config=dict(group_config),
        relation_intensity=max(0.0, min(1.0, relation_intensity)),
        random_seed=random_seed,
        source_path=str(config_path),
        status="yaml",
        note="C YAML-compatible configuration loaded by D adapter.",
    )

