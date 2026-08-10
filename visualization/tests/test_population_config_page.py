"""Static contract checks for D's user-defined C population config page."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PAGE = ROOT / "visualization" / "prototype" / "population_config.html"


def test_page_lists_all_nine_c_scene_semantics():
    page = PAGE.read_text(encoding="utf-8")
    for scene in (
        "classroom", "corridor", "stair", "shop", "hall",
        "canteen", "dorm", "library", "hospital",
    ):
        assert f"{scene}:" in page


def test_page_exports_only_c_scene_config_inputs():
    page = PAGE.read_text(encoding="utf-8")
    for field in (
        "scene_name", "total_persons", "profile_ratios",
        "relation_intensity", "random_seed", "group_config",
    ):
        assert field in page
    assert "不生成 <code>persons</code>" in page
    assert "初始位置由 A" in page


def test_page_enforces_exact_ratio_sum_before_export():
    page = PAGE.read_text(encoding="utf-8")
    assert "Math.abs(Object.values(state.ratios)" in page
    assert "- 1) < 1e-9" in page
    assert "downloadJson').disabled = !isValid()" in page
