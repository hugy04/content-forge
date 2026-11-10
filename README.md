# content-forge

**A topic string goes in. A 500–800 word piece and a matching AI-generated
image come out — in a different format and visual style every time.**

Built for the Case 2 assignment: an AI-powered content generation workflow.

```
┌ trigger ─────────┐   ┌ plan ────────┐   ┌ generate ──────────┐   ┌ publish ─┐
│ API · form       │ → │ diversity    │ → │ outline → draft    │ → │ disk     │
│ webhook · CLI    │   │ engine       │   │   → QC → revise    │   │ Drive    │
│ batch            │   │ (no LLM)     │   │ ∥ image prompt→img │   │ Sheets   │
└──────────────────┘   └──────────────┘   └────────────────────┘   └──────────┘
```

| | |
|---|---|
| **Text** | Pluggable: Gemini Flash (default, free tier) or Claude — three structured-output calls |
| **Images** | Gemini → Pollinations → locally-rendered card (ordered fallback) |
| **Variety** | 115,200 combinations across 6 dimensions, with cooldown + recency weighting |
| **Quality** | 9 deterministic checks, bounded self-repair loop |
| **Runs as** | FastAPI inline (zero infra) or Celery + Redis (scale) |
| **Tests** | 111, all offline — no API key needed |

---

## Quickstart

Runs end to end with **no API key and no network**, using fixtures:

```bash
python3.13 -m venv .venv
.venv/bin/pip install -r requirements.txt
FAKE_PROVIDERS=1 IMAGE_PROVIDERS=card .venv/bin/uvicorn forge.api.app:app --reload
```

Open <http://localhost:8000>, type a topic, press Generate.

### With real providers

```bash
cp .env.example .env     # then add GEMINI_API_KEY
.venv/bin/python scripts/gemini_doctor.py   # confirms the key and the model ids
.venv/bin/uvicorn forge.api.app:app
```

**`GEMINI_API_KEY` is the only required secret**, and it is free — no card, no
billing account: <https://aistudio.google.com/apikey>. One key serves both the
text and the image stage.

Run `scripts/gemini_doctor.py` first. Google retires Flash model ids on roughly
a quarterly cadence, and the doctor prints the ids your key can actually call
rather than leaving you to discover a 404 mid-run.

To run on Claude instead — better prose, metered cost — set `TEXT_PROVIDER=claude`
and `ANTHROPIC_API_KEY`. Nothing else changes; that swap is the whole point of
keeping the text layer behind an interface. Note that an Anthropic **API** key
needs prepaid credits and is billed separately from a Claude Pro subscription.

### Command line

```bash
.venv/bin/python -m forge.cli "EV battery recycling in India"
.venv/bin/python -m forge.cli "The case against micro-SaaS" --style opinion
.venv/bin/python -m forge.cli --file samples/topics.txt --concurrency 2
```

### Tests

```bash
.venv/bin/python -m pytest -q      # 37 tests, no network, no key
```

---

## API

| Endpoint | Purpose |
|---|---|
| `POST /v1/generate` | Main entry. `{"topic": "...", "style": "opinion"}` — `style` optional |
| `POST /v1/batch` | `{"topics": [...], "concurrency": 3}`, isolated per topic |
| `POST /webhooks/topic` | Permissive trigger: accepts `{topic}`, `{text}`, `{subject}` or bare text |
| `GET /v1/runs/{run_id}` | Full run record: selection, timings, tokens, cost, QC verdict |
| `GET /v1/runs?limit=20` | Recent runs |
| `GET /v1/stats` | Aggregates, style/art distribution, circuit-breaker state |
| `GET /v1/taxonomy` | Every option the diversity engine can choose |
| `GET /healthz` | Liveness, for an uptime monitor |
| `GET /docs` | Generated OpenAPI docs |

```bash
curl -s localhost:8000/v1/generate \
  -H 'content-type: application/json' \
  -d '{"topic":"What the ONDC rollout means for small retailers"}' | jq '.status, .word_count'
```

Every run writes `out/<date>_<slug>_<id>/` containing `content.md` (with YAML
front matter), `image.png`, and `run.json` — the complete record including
per-stage timings and token cost.

---

## How variety actually works

Six dimensions are sampled **before any model call**, by an algorithm that can
see the previous runs:

| | options | |
|---|---|---|
| `style` | 6 | article · guide · opinion · trend_analysis · listicle · case_study |
| `angle` | 10 | economics · contrarian · risk · data_led · historical … |
| `audience` | 5 | executives · practitioners · newcomers · investors · builders |
| `art_direction` | 8 | editorial_photography · flat_vector · isometric_3d · risograph … |
| `palette` | 8 | |
| `composition` | 6 | |

An option used in the last *N* runs is **excluded outright**; among the rest,
weight grows with time since last use; and the `(style, angle, art_direction)`
triple is re-rolled if it matches a recent run. Seeded from `run_id`, so any run
reproduces its own choices.

Asking a model to "be varied" does not survive repetition. This does, and
`tests/test_diversity.py` asserts it.

---

## Configuration

Everything is env-overridable; see `.env.example` for the full list.

| Variable | Default | Notes |
|---|---|---|
| `TEXT_PROVIDER` | `gemini` | or `claude`; selects the text backend |
| `GEMINI_API_KEY` | — | required unless `FAKE_PROVIDERS=1`; free tier |
| `GEMINI_MODEL` | `gemini-3.8-flash` | auto-resolves if the id has been retired |
| `ANTHROPIC_API_KEY` | — | only when `TEXT_PROVIDER=claude` |
| `IMAGE_PROVIDERS` | `gemini,pollinations,card` | ordered fallback chain |
| `TARGET_WORDS_MIN` / `_MAX` | `500` / `800` | the enforced contract |
| `MAX_REVISIONS` | `2` | bound on the repair loop |
| `DEADLINE_SECONDS` | `300` | per-run wall-clock budget |
| `DIVERSITY_COOLDOWN` | `3` | runs an option is excluded for |
| `RUN_MODE` | `inline` | or `queue` for Celery + Redis |
| `SINKS` | `local` | add `gdrive` for Docs + Sheets |
| `FAKE_PROVIDERS` | `0` | fixtures, no network |
| `OUTLINE_MODEL` / `DRAFT_MODEL` / `PROMPT_MODEL` | provider default | per-stage, so cost can be routed |
| `POLLINATIONS_MIN_INTERVAL_SECONDS` | `47` | per-IP pacing floor for the free image tier |

---

## Documentation

| | |
|---|---|
| [`docs/DESIGN.md`](docs/DESIGN.md) | architecture, the diversity algorithm, error-handling matrix, scale analysis, **assumptions** |
| [`docs/RUNBOOK.md`](docs/RUNBOOK.md) | setup and execution, Google sink, queue mode, deployment |
| [`docs/DEMO_SCRIPT.md`](docs/DEMO_SCRIPT.md) | the walkthrough script |
| [`samples/`](samples/) | sample outputs for 4 topics, with full run records |

---

## Layout

```
forge/
  config.py              all settings, one place
  diversity/             taxonomy + the selection engine
  pipeline/
    context.py           RunContext, Deadline, word counting
    prompts.py           system/user prompts + JSON schemas
    validators.py        the deterministic quality gate
    runner.py            orchestration, the parallel fork
    stages/              one module per stage
  providers/
    text/gemini.py       default backend: model discovery, schema translation
    text/claude.py       the metered alternative, same interface
    image/               gemini · pollinations · cloudflare · openai · card
    retry.py             tenacity policies + circuit breaker
  sinks/                 local (always) · gdrive (Apps Script)
  store/db.py            SQLite: run records + diversity history
  api/                   FastAPI app, schemas, form UI
  worker/                Celery app and task
integrations/apps_script/Code.gs    paste into script.google.com
```

---

## Credits

Image generation falls back to [Pollinations](https://pollinations.ai) on its
free anonymous tier, which watermarks its output; that attribution is left
intact. Set `GEMINI_API_KEY` for unwatermarked images.
