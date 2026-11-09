"""The diversity requirement, asserted rather than asserted-to."""
from __future__ import annotations

import pytest

from forge.diversity.engine import DiversityEngine
from forge.diversity.taxonomy import COMBO_KEY, DIMENSIONS


def simulate(runs: int, cooldown: int = 3) -> list[dict[str, str]]:
    engine = DiversityEngine(cooldown=cooldown, history_size=50)
    history: list[dict[str, str]] = []
    for i in range(runs):
        selection = engine.select(f"run-{i:04d}", history)
        history.insert(0, selection.as_dict())
    return list(reversed(history))   # oldest first


@pytest.mark.parametrize("dimension", list(DIMENSIONS))
def test_no_option_repeats_inside_the_cooldown_window(dimension):
    cooldown = 3
    runs = simulate(60, cooldown=cooldown)
    for i, run in enumerate(runs):
        window = runs[max(0, i - cooldown):i]
        assert run[dimension] not in [w[dimension] for w in window], (
            f"{dimension}={run[dimension]} repeated within {cooldown} runs at index {i}"
        )


def test_style_angle_art_triples_do_not_repeat_inside_the_combo_window():
    """The engine guarantees freshness within a window, not globally forever.

    Combo rejection looks back `cooldown * 2` runs, so that is exactly what is
    asserted here - claiming global uniqueness would be asserting a property the
    implementation does not provide.
    """
    cooldown = 3
    window = cooldown * 2
    runs = simulate(80, cooldown=cooldown)
    combos = [tuple(r[k] for k in COMBO_KEY) for r in runs]
    for i, combo in enumerate(combos):
        assert combo not in combos[max(0, i - window):i], (
            f"combo {combo} repeated within {window} runs at index {i}"
        )


def test_combo_variety_stays_high_over_a_long_sequence():
    """Even without a global guarantee, repeats should be rare."""
    combos = [tuple(r[k] for k in COMBO_KEY) for r in simulate(100)]
    assert len(set(combos)) >= 90, f"only {len(set(combos))} distinct combos in 100 runs"


def test_distribution_is_broadly_even_not_lopsided():
    runs = simulate(120)
    counts: dict[str, int] = {}
    for run in runs:
        counts[run["style"]] = counts.get(run["style"], 0) + 1
    # 120 runs over 6 styles: perfectly even is 20 each. Allow generous slack but
    # catch a style being starved or dominating.
    assert min(counts.values()) >= 10, counts
    assert max(counts.values()) <= 32, counts
    assert len(counts) == len(DIMENSIONS["style"]), "a style was never chosen"


def test_selection_is_reproducible_from_the_run_id():
    engine = DiversityEngine()
    assert engine.select("stable-id", []).as_dict() == engine.select("stable-id", []).as_dict()


def test_different_run_ids_diverge():
    engine = DiversityEngine()
    a = engine.select("id-a", []).as_dict()
    b = engine.select("id-b", []).as_dict()
    assert a != b


def test_explicit_override_always_wins():
    engine = DiversityEngine()
    history = [{"style": "opinion"} for _ in range(5)]
    selection = engine.select("any", history, overrides={"style": "opinion"})
    assert selection.style == "opinion"
    assert "requested by the caller" in selection.rationale["style"]


def test_unknown_override_is_rejected():
    with pytest.raises(ValueError, match="not a valid option"):
        DiversityEngine().select("any", [], overrides={"style": "haiku"})


def test_engine_survives_a_cooldown_wider_than_the_pool():
    """Must degrade to least-recently-used, not raise or deadlock."""
    engine = DiversityEngine(cooldown=99, history_size=50)
    history: list[dict[str, str]] = []
    for i in range(10):
        selection = engine.select(f"r{i}", history)
        history.insert(0, selection.as_dict())
    assert len(history) == 10
