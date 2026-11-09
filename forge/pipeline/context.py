"""The object every stage reads from and writes to."""
from __future__ import annotations

import hashlib
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from forge.diversity.engine import Selection
from forge.providers.text.usage import Usage


class DeadlineExceeded(RuntimeError):
    pass


@dataclass
class Deadline:
    """A wall-clock budget threaded through the run.

    The brief requires end-to-end completion inside five minutes. Rather than
    hoping, each stage is handed the remaining time and the expensive stages
    check it before starting work they cannot finish.
    """
    budget_seconds: float
    started: float = field(default_factory=time.monotonic)
    finished: float | None = None

    def stop(self) -> float:
        """Freeze the clock at the end of the run.

        `elapsed` is live by design, because the stages need the *current*
        remaining budget. That makes it the wrong thing to read afterwards: a
        batch runner that printed `elapsed` once every run had finished
        reported 437s for a run that actually took 67s. Freezing it here keeps
        one number that means "how long this run took".
        """
        if self.finished is None:
            self.finished = time.monotonic()
        return self.finished - self.started

    @property
    def elapsed(self) -> float:
        end = self.finished if self.finished is not None else time.monotonic()
        return end - self.started

    @property
    def remaining(self) -> float:
        return max(0.0, self.budget_seconds - self.elapsed)

    def expired(self) -> bool:
        return self.remaining <= 0

    def require(self, stage: str, needs: float = 0.0) -> None:
        if self.remaining <= needs:
            raise DeadlineExceeded(
                f"{stage} needs ~{needs:.0f}s but only {self.remaining:.0f}s of the "
                f"{self.budget_seconds:.0f}s budget remains"
            )


WORD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9'’\-]*")
_CODE_FENCE = re.compile(r"```.*?```", re.S)
_MD_SYNTAX = re.compile(r"[#*_>`\[\]()|]")


def count_words(markdown: str) -> int:
    """Prose words in the body.

    Code fences are removed and markdown punctuation stripped before counting,
    so the 500-800 contract is measured on what a reader actually reads. Headings
    count: they are part of the piece.
    """
    text = _CODE_FENCE.sub(" ", markdown or "")
    text = _MD_SYNTAX.sub(" ", text)
    return len(WORD_RE.findall(text))


def slugify(value: str, max_len: int = 60) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (value or "").lower()).strip("-")
    return (slug[:max_len].rstrip("-")) or "untitled"


@dataclass
class RunContext:
    topic: str
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    overrides: dict[str, str] = field(default_factory=dict)
    notify_url: str = ""

    deadline: Deadline | None = None
    selection: Selection | None = None
    outline: dict[str, Any] = field(default_factory=dict)
    draft: dict[str, Any] = field(default_factory=dict)
    image_prompt: dict[str, Any] = field(default_factory=dict)
    image: Any = None                       # ImageResult
    image_attempts: list[dict[str, Any]] = field(default_factory=list)

    qc: dict[str, Any] = field(default_factory=dict)
    revisions: int = 0
    timings: dict[str, float] = field(default_factory=dict)
    usage: Usage = field(default_factory=Usage)
    artifacts: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    status: str = "pending"                 # pending|completed|partial|failed

    @property
    def idempotency_key(self) -> str:
        basis = "|".join([
            self.topic.strip().lower(),
            self.overrides.get("style", ""),
            self.overrides.get("angle", ""),
            self.created_at[:10],
        ])
        return hashlib.sha256(basis.encode()).hexdigest()[:32]

    @property
    def topic_hash(self) -> str:
        return hashlib.sha256(self.topic.strip().lower().encode()).hexdigest()[:16]

    @property
    def title(self) -> str:
        return self.draft.get("title") or self.outline.get("title") or self.topic

    @property
    def slug(self) -> str:
        return slugify(self.title)

    @property
    def word_count(self) -> int:
        return count_words(self.draft.get("body_markdown", ""))

    def mark(self, stage: str, seconds: float) -> None:
        self.timings[stage] = round(seconds, 3)

    def summary(self) -> dict[str, Any]:
        sel = self.selection.as_dict() if self.selection else {}
        return {
            "run_id": self.run_id,
            "status": self.status,
            "topic": self.topic,
            "title": self.title,
            "subtitle": self.draft.get("subtitle", ""),
            "meta_description": self.draft.get("meta_description", ""),
            # The full piece travels with the record so run.json is a complete,
            # self-contained artifact and the UI needs no second request.
            "body_markdown": self.draft.get("body_markdown", ""),
            "outline": self.outline,
            "created_at": self.created_at,
            "selection": sel,
            "selection_rationale": self.selection.rationale if self.selection else {},
            "word_count": self.word_count,
            "revisions": self.revisions,
            "qc": self.qc,
            "image": {
                "provider": getattr(self.image, "provider", None),
                "generated": getattr(self.image, "generated", None),
                "width": getattr(self.image, "width", None),
                "height": getattr(self.image, "height", None),
                "prompt": self.image_prompt.get("prompt"),
                "alt_text": self.image_prompt.get("alt_text"),
                "treatment": getattr(self.image, "meta", {}).get("treatment"),
                "meta": getattr(self.image, "meta", {}),
                "attempts": self.image_attempts,
            },
            "timings_seconds": self.timings,
            "total_seconds": round(self.deadline.elapsed, 2) if self.deadline else None,
            "usage": self.usage.as_dict(),
            "artifacts": self.artifacts,
            "warnings": self.warnings,
            "errors": self.errors,
        }
