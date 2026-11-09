"""Selection with memory.

The brief asks for "logic to ensure content and visual diversity across
executions". Asking the model to "be varied" does not survive contact with
reality: an LLM given the same topic twice will converge on the same framing and
the same picture. So the choice is made *before* any model call, by an algorithm
that can see what the previous runs chose.

Three mechanisms, in order of strictness:

1. Hard cooldown - an option used within the last `cooldown` runs has weight 0
   and cannot be chosen at all. This is what makes "no repeats" a guarantee
   rather than a probability.
2. Recency-weighted sampling - among the survivors, an option unused for longer
   carries more weight, so the rotation spreads out instead of ping-ponging
   between two options.
3. Combo rejection - the (style, angle, art_direction) triple is additionally
   checked against recent history and re-rolled if it collides, because three
   individually-fresh choices can still reproduce a recent *pairing*.

Every decision is driven by a seeded RNG, so a run_id reproduces its own
selection exactly - which is what makes the behaviour unit-testable and a
production incident replayable.
"""
from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass, field
from typing import Any

from forge.diversity.taxonomy import (
    ANGLES,
    ART_DIRECTIONS,
    AUDIENCES,
    COMBO_KEY,
    COMPOSITIONS,
    DIMENSIONS,
    PALETTES,
    STYLES,
)

MAX_COMBO_REROLLS = 8


@dataclass
class Selection:
    """One run's creative coordinates, plus why they were chosen."""
    style: str
    angle: str
    audience: str
    art_direction: str
    palette: str
    composition: str
    rationale: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, str]:
        return {
            "style": self.style,
            "angle": self.angle,
            "audience": self.audience,
            "art_direction": self.art_direction,
            "palette": self.palette,
            "composition": self.composition,
        }

    def describe(self) -> dict[str, str]:
        """Human-readable expansion, for prompts and for the run record."""
        return {
            "style": STYLES[self.style].label,
            "style_voice": STYLES[self.style].voice,
            "style_guidance": STYLES[self.style].guidance,
            "style_structure": ", ".join(STYLES[self.style].structure),
            "angle": ANGLES[self.angle],
            "audience": AUDIENCES[self.audience],
            "art_direction": ART_DIRECTIONS[self.art_direction],
            "palette": PALETTES[self.palette],
            "composition": COMPOSITIONS[self.composition],
        }


def _age_map(history: list[dict[str, Any]], dimension: str) -> dict[str, int]:
    """How many runs ago each option was last used. Absent -> never used."""
    ages: dict[str, int] = {}
    for position, row in enumerate(history):  # history[0] is the newest run
        value = row.get(dimension)
        if value and value not in ages:
            ages[value] = position
    return ages


def _weights(pool: list[str], ages: dict[str, int], cooldown: int) -> dict[str, float]:
    """Hard cooldown to zero, then linear recency bonus on the survivors."""
    weights: dict[str, float] = {}
    for option in pool:
        age = ages.get(option)
        if age is None:
            weights[option] = float(len(pool))  # never used: strongly preferred
        elif age < cooldown:
            weights[option] = 0.0               # inside cooldown: excluded
        else:
            weights[option] = 1.0 + 0.5 * (age - cooldown)
    return weights


def _pick(rng: random.Random, pool: list[str], ages: dict[str, int],
          cooldown: int) -> tuple[str, str]:
    """Return (choice, reason). Falls back to least-recently-used if all excluded."""
    # A cooldown wider than the pool would exclude everything; relax it until
    # at least one option survives rather than failing the run.
    effective = min(cooldown, max(0, len(pool) - 1))
    weights = _weights(pool, ages, effective)
    live = [(o, w) for o, w in weights.items() if w > 0]
    if not live:
        lru = max(pool, key=lambda o: ages.get(o, len(pool) + 1))
        return lru, "all options within cooldown; fell back to least-recently-used"
    options, ws = zip(*live)
    choice = rng.choices(list(options), weights=list(ws), k=1)[0]
    age = ages.get(choice)
    if age is None:
        reason = "never used in recent history"
    else:
        reason = f"last used {age} run(s) ago (cooldown {effective})"
    return choice, reason


class DiversityEngine:
    def __init__(self, cooldown: int = 3, history_size: int = 50):
        self.cooldown = cooldown
        self.history_size = history_size

    def select(
        self,
        run_id: str,
        history: list[dict[str, Any]],
        overrides: dict[str, str] | None = None,
    ) -> Selection:
        """Choose this run's coordinates. `overrides` wins over the algorithm."""
        overrides = {k: v for k, v in (overrides or {}).items() if v}
        rng = random.Random(hashlib.sha256(run_id.encode()).hexdigest())
        history = history[: self.history_size]

        chosen: dict[str, str] = {}
        rationale: dict[str, Any] = {}

        for dimension, pool in DIMENSIONS.items():
            if dimension in overrides:
                value = overrides[dimension]
                if value not in pool:
                    raise ValueError(
                        f"{dimension}={value!r} is not a valid option; choose from {pool}"
                    )
                chosen[dimension] = value
                rationale[dimension] = "explicitly requested by the caller"
                continue
            ages = _age_map(history, dimension)
            value, reason = _pick(rng, pool, ages, self.cooldown)
            chosen[dimension] = value
            rationale[dimension] = reason

        # --- combo rejection -------------------------------------------------
        recent_combos = {
            tuple(row.get(k) for k in COMBO_KEY) for row in history[: self.cooldown * 2]
        }
        rerolls = 0
        rerollable = [d for d in COMBO_KEY if d not in overrides]
        while rerollable and tuple(chosen[k] for k in COMBO_KEY) in recent_combos:
            if rerolls >= MAX_COMBO_REROLLS:
                # Force a change on the last rerollable dimension via LRU.
                dimension = rerollable[-1]
                ages = _age_map(history, dimension)
                lru = max(DIMENSIONS[dimension],
                          key=lambda o: ages.get(o, len(DIMENSIONS[dimension]) + 1))
                chosen[dimension] = lru
                rationale[dimension] = "forced to least-recently-used to break a combo collision"
                break
            dimension = rerollable[rerolls % len(rerollable)]
            ages = _age_map(history, dimension)
            value, reason = _pick(rng, DIMENSIONS[dimension], ages, self.cooldown)
            chosen[dimension] = value
            rationale[dimension] = f"{reason}; re-rolled to avoid repeating a recent combination"
            rerolls += 1

        rationale["_meta"] = {
            "history_depth": len(history),
            "cooldown": self.cooldown,
            "combo_rerolls": rerolls,
            "combo_key": list(COMBO_KEY),
        }
        return Selection(**chosen, rationale=rationale)
