"""Prompt construction and the JSON schemas that constrain each model call.

Two deliberate choices:

* Outline and draft are separate calls. A single "write me 700 words" call
  produces competent mush; planning the structure first, then writing against
  that plan, produces a piece with an argument. It also gives the QC stage
  something to check the draft against.
* Every call uses structured outputs (`output_config.format`). Assistant prefill
  was removed from the API, and asking for JSON in prose then regex-ing it back
  out is the single most common source of pipeline flakiness.
"""
from __future__ import annotations

from typing import Any

from forge.diversity.engine import Selection

OUTLINE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "subtitle": {"type": "string"},
        "hook": {"type": "string"},
        "meta_description": {"type": "string"},
        "keywords": {"type": "array", "items": {"type": "string"}},
        "sections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "heading": {"type": "string"},
                    "word_budget": {"type": "integer"},
                    "points": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["heading", "word_budget", "points"],
                "additionalProperties": False,
            },
        },
        "key_takeaways": {"type": "array", "items": {"type": "string"}},
        "image_brief": {"type": "string"},
    },
    "required": ["title", "subtitle", "hook", "meta_description", "keywords",
                 "sections", "key_takeaways", "image_brief"],
    "additionalProperties": False,
}

DRAFT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "subtitle": {"type": "string"},
        "body_markdown": {"type": "string"},
        "meta_description": {"type": "string"},
    },
    "required": ["title", "subtitle", "body_markdown", "meta_description"],
    "additionalProperties": False,
}

IMAGE_PROMPT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "prompt": {"type": "string"},
        "alt_text": {"type": "string"},
    },
    "required": ["prompt", "alt_text"],
    "additionalProperties": False,
}


def _selection_block(selection: Selection) -> str:
    described = selection.describe()
    return "\n".join([
        f"style: {selection.style}",
        f"angle: {selection.angle}",
        f"audience: {selection.audience}",
        f"art_direction: {selection.art_direction}",
        f"palette: {selection.palette}",
        f"composition: {selection.composition}",
        "",
        f"Format: {described['style']}",
        f"Voice: {described['style_voice']}",
        f"Structural shape: {described['style_structure']}",
        f"Format rule: {described['style_guidance']}",
        f"Interpretive angle: {described['angle']}",
        f"Written for: {described['audience']}",
    ])


OUTLINE_SYSTEM = """You are a commissioning editor who plans content assets before anyone writes them.

You produce a structure with an argument, not a list of topics. Specifically:
- The title must make a claim or promise something concrete. Never "A Guide to X" \
or "Understanding X".
- The hook must earn the second sentence. Lead with a specific fact, tension or \
number, never with "In today's fast-paced world".
- Section word budgets must sum to within 20 words of the requested target.
- Each section's points must be assertions the writer can defend, not headings in \
disguise.
- The image_brief describes a single concrete scene or object that carries the \
piece's meaning. It never describes charts, text, or a collage of concepts.

You respect the requested format, voice and angle exactly. They are not suggestions."""


def outline_user(topic: str, selection: Selection, target_words: int) -> str:
    return f"""Topic: {topic}

{_selection_block(selection)}

Target length: {target_words} words total across all sections.

Plan this piece. Return the outline as JSON matching the schema."""


DRAFT_SYSTEM = """You are a senior staff writer. You write the piece you were given a plan for.

Hard rules:
- Follow the outline's sections and their word budgets. Hit the total word target.
- Write in markdown. Use `##` for section headings. No `#` H1: the title is \
separate metadata.
- Open with the hook. Do not restate the title as your first line.
- Every claim gets a specific: a number, a named example, a mechanism. If you \
cannot be specific, cut the claim.
- No filler openers ("In today's rapidly evolving landscape"), no throat-clearing \
("It's important to note that"), no summary paragraph that merely repeats what \
you just said.
- Do not invent statistics, studies or quotes attributed to real named people or \
organisations. Where a figure is illustrative, phrase it as a range or an order of \
magnitude rather than a false precision.
- Match the requested voice exactly. An opinion piece argues; a guide instructs; \
a trend analysis separates observation from forecast."""


def draft_user(topic: str, selection: Selection, outline: dict[str, Any],
               target_min: int, target_max: int) -> str:
    sections = "\n".join(
        f"  {i+1}. {s['heading']} (~{s['word_budget']} words)\n"
        + "\n".join(f"     - {p}" for p in s.get("points", []))
        for i, s in enumerate(outline.get("sections", []))
    )
    takeaways = "\n".join(f"  - {t}" for t in outline.get("key_takeaways", []))
    return f"""Topic: {topic}

{_selection_block(selection)}

Approved plan
Title: {outline.get('title')}
Subtitle: {outline.get('subtitle')}
Hook: {outline.get('hook')}
Sections:
{sections}
Key takeaways to land:
{takeaways}

Length requirement: the body must be between {target_min} and {target_max} words. \
Aim for {(target_min + target_max) // 2}.

Write the piece. Return JSON matching the schema, with the full piece in \
body_markdown."""


REVISE_SYSTEM = """You are an editor making a targeted revision. You change only what \
the instruction asks for.

You preserve the piece's argument, voice, structure and section headings. You do \
not rewrite from scratch, do not add new sections, and do not drop the specifics \
that make the piece worth reading. When asked to cut, you cut the weakest \
sentences, not the evidence. When asked to expand, you add substance, not \
adjectives."""


def revise_user(draft: dict[str, Any], instruction: str, target_min: int,
                target_max: int, current_words: int) -> str:
    return f"""The piece below is {current_words} words. It must be between \
{target_min} and {target_max}.

Required change: {instruction}

Return the full revised piece as JSON matching the schema. Keep the title and \
subtitle unless the instruction says otherwise.

--- CURRENT PIECE ---
{draft.get('body_markdown', '')}"""


IMAGE_PROMPT_SYSTEM = """You turn an editorial image brief into a single text-to-image \
prompt.

Rules:
- One scene. One subject. A generative model cannot render three ideas at once.
- Lead with the subject and what it is doing, then the setting, then the light, \
then the medium and style tokens you were given.
- Use concrete nouns. "A technician's hands on a diagnostic tablet beside a lifted \
car" beats "automotive innovation".
- Never ask for text, words, numbers, labels, logos or UI in the image. Models \
render them as garbage.
- Keep the prompt under 90 words.
- alt_text is a plain, literal description of the resulting image for a screen \
reader. One sentence, no style jargon."""


def image_prompt_user(topic: str, selection: Selection, outline: dict[str, Any]) -> str:
    described = selection.describe()
    return f"""Topic: {topic}
Piece title: {outline.get('title')}
Image brief: {outline.get('image_brief')}

Apply these visual directions exactly:
- Medium and style: {described['art_direction']}
- Colour palette: {described['palette']}
- Composition: {described['composition']}

Return JSON matching the schema."""
