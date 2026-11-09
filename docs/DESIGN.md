# content-forge — design, decisions and assumptions

> Deliverable 1 of the Case 2 assignment: workflow design, an explanation of it,
> and the assumptions it rests on.

---

## 1. The problem, restated

A content team wants to go from a bare topic string to a publishable asset — a
500–800 word piece plus a matching image — without a human in the loop, and
without every output looking like the last one.

That last clause is the whole problem. Calling a language model and an image
model is a weekend exercise. Calling them a hundred times and getting a hundred
assets a content lead would actually publish is an engineering problem, and it
decomposes into four:

| Problem | Why the naive version fails |
|---|---|
| **Sameness** | Given the same topic twice, a model converges on the same framing and the same picture. Telling it to "be creative" does not survive repetition. |
| **Length drift** | "Write 500–800 words" produces 420 or 1,100 often enough to matter. Nothing downstream catches it. |
| **Partial failure** | Image providers rate-limit and time out. A pipeline that treats that as fatal throws away a perfectly good article. |
| **Unbounded latency** | Chained model calls have no natural ceiling, and the brief sets one at five minutes. |

The design is organised around those four, not around the API calls.

---

## 2. Architecture

```
                         POST /v1/generate ── JSON API
  trigger ──────────────  GET  /            ── browser form
                         POST /webhooks/topic ── generic webhook (Zapier/Make/CMS)
                         POST /v1/batch     ── bulk
                         python -m forge.cli ── CLI / cron
                                    │
                                    ▼
                        ┌───────────────────────┐
                        │ intake                │  validate · run_id · idempotency key
                        └───────────┬───────────┘
                                    ▼
                        ┌───────────────────────┐
                        │ plan  (no LLM)        │  diversity engine reads history,
                        └───────────┬───────────┘  picks 6 dimensions, records them
                                    ▼
                        ┌───────────────────────┐
                        │ outline   (Claude 1)  │  structured JSON plan
                        └───────────┬───────────┘
                                    │
                 ┌──────────────────┴──────────────────┐
                 ▼            asyncio.gather           ▼
      ┌─────────────────────┐              ┌──────────────────────────┐
      │ draft   (Claude 2)  │              │ image_prompt (Claude 3)  │
      ├─────────────────────┤              ├──────────────────────────┤
      │ qc  (deterministic) │              │ image                    │
      │  └ revise (≤2, LLM) │              │  gemini → pollinations   │
      └──────────┬──────────┘              │        → card            │
                 │                         └────────────┬─────────────┘
                 └──────────────────┬───────────────────┘
                                    ▼
                        ┌───────────────────────┐
                        │ publish               │  local (always) + gdrive (best effort)
                        └───────────┬───────────┘
                                    ▼
                        ┌───────────────────────┐
                        │ notify (optional)     │  callback webhook
                        └───────────────────────┘
```

Every stage is a module under `forge/pipeline/stages/` exposing one coroutine
`run(ctx, deps)`. They communicate only through `RunContext`. That is what makes
the pipeline modular in a way that is more than a claim: a stage can be
reordered, replaced or tested in isolation because it has no other coupling.

**The fork after `outline` is the main latency decision.** Once the outline
exists, the image branch depends on nothing the draft produces, so the two run
concurrently and image generation hides entirely behind the draft and revision
calls. Serially these stages add up; in parallel the run is bounded by the
slower branch, which is almost always the text.

### Why custom code rather than n8n/Zapier/Make

A visual tool would have been faster to assemble, and for a linear
trigger→call→call→store flow I would recommend one. This pipeline needs three
things those tools make awkward:

1. **A stateful selection algorithm.** The diversity engine reads a rolling
   history, applies recency weighting and rejects collisions. In a node editor
   that becomes a code node anyway — but an untestable one.
2. **A bounded repair loop.** Conditional re-entry with an attempt counter and a
   deadline check is a native `while` and an awkward node graph.
3. **Partial success.** "The image failed, publish anyway, mark it `partial`"
   is a branch-and-merge in a visual tool and three lines here.

The trade-off is honest: this needs a Python runtime and someone who reads
Python, where n8n would let a non-engineer edit the flow. For a content
engineering team shipping this as a service, that is the right trade. For a
marketing team self-serving, it is not.

---

## 3. The diversity engine

`forge/diversity/` — the direct answer to *"include logic to ensure content and
visual diversity across executions"*.

Six dimensions are sampled **independently**, before any model call:

| Dimension | Options | Governs |
|---|---|---|
| `style` | 6 — article, guide, opinion, trend_analysis, listicle, case_study | structure and voice |
| `angle` | 10 — economics, contrarian, risk, data_led, historical … | the interpretive lens |
| `audience` | 5 — executives, practitioners, newcomers, investors, builders | register and assumed knowledge |
| `art_direction` | 8 — editorial_photography, flat_vector, isometric_3d, risograph … | the image's medium |
| `palette` | 8 | the image's colour story |
| `composition` | 6 | the image's framing |

115,200 combinations, and crucially the visual dimensions are independent of the
textual ones — so an opinion piece is not locked to one look.

Selection runs three mechanisms in increasing strictness:

1. **Hard cooldown.** An option used within the last *N* runs gets weight 0 and
   cannot be selected. This is what turns "probably varied" into a guarantee.
2. **Recency-weighted sampling.** Among survivors, weight rises with time since
   last use (`1.0 + 0.5 × (age − cooldown)`; never-used options get the pool
   size as their weight). This spreads the rotation instead of ping-ponging.
3. **Combo rejection.** Three individually-fresh choices can still reproduce a
   recent *pairing*, so the `(style, angle, art_direction)` triple is checked
   against the last `2 × cooldown` runs and re-rolled on collision, with an
   LRU forced change as the backstop.

Two properties worth calling out:

- **Seeded and reproducible.** The RNG is seeded from `run_id`, so a run
  reproduces its own selection exactly. That is what makes the behaviour
  unit-testable, and an incident replayable.
- **Degrades instead of deadlocking.** A cooldown wider than a pool would
  exclude every option; the engine relaxes to least-recently-used rather than
  raising. Covered by `test_engine_survives_a_cooldown_wider_than_the_pool`.

`tests/test_diversity.py` asserts the guarantee directly: across 60 simulated
runs, no dimension repeats inside the cooldown window; across 100 runs at least
90 distinct `(style, angle, art_direction)` triples; and no style is starved.

---

## 4. The quality gate

`forge/pipeline/validators.py` — deterministic checks, no model involved.
Repeatable, free, and unit-tested, which an "LLM as judge" call is none of.

| Check | Level | Rule |
|---|---|---|
| `word_count` | error | 500–800 words, measured on prose with code fences and markdown syntax stripped |
| `has_sections` | error | at least two `##` headings |
| `no_h1_in_body` | error | the title is metadata, not body content |
| `has_title` | error | 10–110 characters |
| `no_placeholders` | error | no `TODO`, `lorem ipsum`, `[insert …]` |
| `no_empty_sections` | error | every heading is followed by prose |
| `ai_tells` | warn | "in today's fast-paced", "it's important to note", … |
| `meta_description` | warn | 50–180 characters |
| `follows_outline` | warn | section count within 1 of the plan |

A failing **error** produces a *specific* repair instruction — "Cut about 140
words. Remove the weakest sentences and any restatement. Keep every specific,
number and named example." — not "try again". The revise call is bounded at
`MAX_REVISIONS` (default 2) and additionally gated on the remaining time budget.

Two deliberate subtleties:

- **A revision is kept only if it improves the gate.** If the repair makes
  things worse or no better, the previous draft is restored and the loop stops.
  Without that check a repair loop can walk a good draft downhill.
- **Warnings never trigger a revision.** Style is a judgement call; the evidence
  ships with the run and a human decides.

---

## 5. Error handling

Every outbound call goes through `forge/providers/retry.py`: tenacity with
exponential backoff and jitter, plus a per-provider circuit breaker that trips
after 3 consecutive failures and half-opens after 60s. The breaker matters
because retrying a provider that is genuinely down burns the latency budget
before the fallback is ever tried.

| Failure | Detected by | Response |
|---|---|---|
| Text provider 429 / 5xx / timeout | status code or typed SDK exception | 3 retries, exponential + jitter; breaker on repeats |
| Text provider 4xx | status code | fail fast — a bug in our request, not transient |
| Refusal / safety block | `stop_reason` or `finishReason` | fail the run with the refusal category recorded |
| Truncation | `max_tokens` / `MAX_TOKENS` finish reason | treated as failure, not silently shipped |
| Malformed JSON | `json.loads` | retried; structured outputs make it near-impossible |
| Image provider down | exception / non-200 / bad content-type | retry, then next provider in the chain |
| Published rate limit (402/429 with a cool-off) | `RateLimited` carrying `retry_after` | **paced, not retried** — see below |
| Model id retired (404) | ListModels resolution | falls forward to the newest available model, once per process |
| Image provider returns an HTML error page with HTTP 200 | Pillow decode check | treated as a failure, not written to disk |
| Every image provider down | chain exhausted | locally rendered card; run marked `partial` |
| Draft fails the gate after 2 repairs | validators | published anyway, `partial`, failing checks attached |
| Remote sink down | exception in `publish` | logged, non-fatal; local sink already has the artifacts |
| Budget exhausted | `Deadline.require()` | run fails cleanly with elapsed timings recorded |
| Duplicate request | idempotency key | returns the existing asset instead of paying twice |
| Worker killed mid-run | `task_acks_late` | Celery redelivers; idempotency key prevents a duplicate |

### Rate limits are paced, not retried

Retry-with-backoff is the wrong instrument for a *published* limit. When
Pollinations' anonymous tier says "one image every 45s per IP", three retries
inside that window spend 20s of the run's budget and still fail. So a documented
limit is handled by `forge/providers/throttle.py` instead: a process-wide
limiter holds a next-allowed timestamp per provider, built from a configured
floor and — authoritatively — from the server's own `Retry-After` or the
seconds quoted in the error body.

Two properties make this safe rather than merely slow:

* **The limiter is deadline-aware.** `acquire()` takes the budget the run can
  still spend. If pacing needs longer, it raises `TooLongToWait`, which is
  deliberately *not* a `ProviderError`, so it escapes the retry loop and moves
  the chain to the next provider rather than parking the run.
* **The lock is held across the sleep**, so concurrent runs queue in turn
  instead of all waking into the same window and re-tripping the limit.

Measured on the free tier: two back-to-back runs both fell through to the
rendered card before pacing existed; with pacing, both returned real generated
images, at 101.1s and 93.1s end-to-end against a 300s budget.

Across replicas this belongs in a shared Redis token bucket rather than process
memory — noted in §7 and in the module docstring.

**Status is three-valued on purpose.** `completed` means the gate passed *and*
the image came from a generative provider. `partial` means a usable asset exists
with a named caveat. `failed` means no asset. Collapsing `partial` into either
neighbour would be a lie to whoever consumes the log.

---

## 6. Latency

Budget: 300s, enforced by a `Deadline` threaded through the context. Expensive
stages call `deadline.require(stage, needs=N)` before starting work they cannot
finish, and the revise loop stops when under 30s remain.

| Stage | Typical | Notes |
|---|---|---|
| plan | <10ms | pure Python, one SQLite read |
| outline | 10–30s | text provider, effort `medium` |
| draft | 30–70s | text provider, longest single call |
| qc | <10ms | deterministic |
| revise | 25–60s | only when the gate fails |
| image_prompt | 5–15s | text provider, effort `low` — **runs parallel to draft** |
| image | 8–25s, plus up to 47s if paced | **runs parallel to draft/revise**, so pacing is usually absorbed |
| publish | 0.1–2s | disk, plus one HTTPS POST if gdrive is on |

Because the branches overlap, the end-to-end figure is roughly
`outline + draft + (revise?) + publish`. Measured numbers from the committed
sample runs are in [`samples/README.md`](../samples/README.md).

---

## 7. Scale

The brief asks how this supports large-scale generation.

**Horizontal.** `RUN_MODE=queue` puts Celery on Redis in front of the identical
pipeline. Throughput is `workers × concurrency / mean_runtime`. At a 90s mean
and 4 workers × 4 slots, ≈ 640 assets/hour, and the ceiling moves by starting
more workers — the pipeline holds no process-local state.

**The real ceiling is provider rate limits, not compute.** `worker_prefetch_multiplier=1`
and the `/v1/batch` concurrency cap exist to keep the queue from stampeding the
Anthropic account into 429s. Tune concurrency to the tier, not to the hardware.

**Cost per asset.** Driven by three Claude calls plus one conditional revision.
Levers, cheapest first: route `image_prompt` to Haiku 5.5 (a formatting task —
roughly 40× cheaper per token than Opus); cache the system prompts, which are
byte-identical across runs; drop `draft` effort to `low` for high-volume
commodity content. Per-stage model and effort are already separate settings
(`OUTLINE_MODEL`, `DRAFT_MODEL`, `PROMPT_MODEL`, `*_EFFORT`) precisely so this is
configuration, not a code change. Measured per-asset cost is in
[`samples/README.md`](../samples/README.md).

**Batch economics.** For non-interactive volume, the Message Batches API halves
token cost. It does not fit the interactive path (the brief's 5-minute
requirement) but suits a nightly content calendar; the pipeline's stage split
makes that a scheduler change rather than a rewrite.

**What breaks first at scale, honestly:**

1. **SQLite.** Fine to a few writes/second; past that the run store wants
   Postgres. The `Store` class is the only thing that touches it.
2. **Local disk artifacts.** Already wrong for multi-worker deployments — the
   `gdrive` sink or an S3/R2 sink is the real answer; `Sink` is the seam.
3. **The diversity history becoming global.** At high volume you want per-brand
   or per-channel histories, otherwise unrelated campaigns compete for the same
   cooldown. `recent_selections()` would take a partition key.

---

## 8. Security

- Secrets only via environment; `.env` is gitignored and `.env.example` carries
  no values. Nothing is logged that contains a key.
- The Apps Script sink authenticates with a shared secret over HTTPS. That is
  weaker than OAuth scopes and is a deliberate trade for setup simplicity on a
  consumer Google account (see §9). Rotating it is a Script-Property edit.
- Input is length-capped and the six creative dimensions are enum-validated at
  the API boundary, so a caller cannot inject arbitrary text into those slots.
- The topic string does reach the prompt — that is the feature. Prompt injection
  can therefore influence tone and content. Mitigations in place: structured
  outputs constrain the response shape, and the deterministic gate catches
  obvious degeneration. For untrusted public input, add a classification pass
  before `outline`. **Not** currently implemented.

---

## 9. Assumptions and decisions

Explicitly required by the brief.

| # | Assumption / decision | Reasoning | If wrong |
|---|---|---|---|
| 1 | "Content piece" means a markdown article with front matter | Portable into any CMS; renders in GitHub, Docs and the UI | Add a sink; the pipeline does not change |
| 2 | 500–800 words counts prose, with code fences and markdown syntax stripped | It is what a reader reads | One constant in `count_words` |
| 3 | Factual accuracy is out of scope | No retrieval tool was specified, and a model writing from parametric memory cannot be trusted on specifics. The draft prompt forbids inventing named studies and quotes, and prefers ranges over false precision | Add a web-search tool between `outline` and `draft` |
| 4 | Gemini Flash for text, Claude as a swappable alternative | The deliverable had to be reproducible by an evaluator at zero cost, and an Anthropic **API** key is prepaid and billed separately from a Claude subscription. Gemini AI Studio issues a free key with no card. `TEXT_PROVIDER=claude` switches backends with no other change | One env var; both providers implement the same two methods |
| 5 | Gemini → Pollinations → card for images | Gemini free tier is clean and unwatermarked; Pollinations needs no key at all and keeps the quickstart honest; the card guarantees an asset | Reorder `IMAGE_PROVIDERS` |
| 6 | Google sink via Apps Script, not the Drive API | A service account has no Drive quota of its own, so uploads to a consumer My Drive fail with `storageQuotaExceeded`; full OAuth needs a GCP project and a refresh token to keep alive. "Execute as me" sidesteps both | Swap in an OAuth adapter behind `Sink` |
| 7 | One image per asset | The brief says "a relevant visual asset (image)" | The image stage would return a list |
| 8 | Diversity is measured across all runs globally | Single-tenant assumption | Partition `recent_selections()` by tenant |
| 9 | 5 minutes is wall-clock per asset, not per batch | Natural reading for an interactive trigger | `DEADLINE_SECONDS` is per run |
| 10 | Free-tier rate limits, not model capability, are the binding constraint | Pollinations allows ~1 image/45s per IP and Gemini free-tier daily caps have been cut repeatedly during 2026. Measured: two back-to-back runs both degraded to the fallback card until pacing was added, then both produced real images (101.1s and 93.1s) | Paid keys remove the pacing floor; set the interval to 0 |
| 11 | Model ids are treated as volatile, not fixed | `gemini-2.5-flash` was deprecated in Oct 2026. A hardcoded id is a scheduled outage, so the provider resolves the configured id against ListModels and falls forward to the newest available Flash | `scripts/gemini_doctor.py` reports the truth for any key |
| 12 | The hosted demo runs `inline` on Render free | No Redis and no persistent disk on that tier; artifacts live in `/tmp` and reset on restart | Queue mode is demonstrated locally; see RUNBOOK |

---

## 10. Requirements traceability

| Requirement (from the brief) | Where it lives | Verified by |
|---|---|---|
| Topic via webhook / form / API / any trigger | `forge/api/app.py`, `forge/cli.py` | 4 entry points, all in the README |
| 500–800 word structured piece | `validators.py`, `stages/qc.py` | `test_validators.py` (8 tests) |
| Relevant AI-generated image | `providers/image/`, `stages/image_prompt.py` | sample images in `samples/` |
| Multiple content styles/formats | `diversity/taxonomy.py` — 6 styles | `test_diversity.py` |
| Content **and visual** diversity across executions | `diversity/engine.py` | `test_diversity.py` (10 tests) |
| End-to-end within 5 minutes | `Deadline`, the `asyncio.gather` fork | timings in every `run.json` |
| Error handling and retry | `providers/retry.py`, `providers/throttle.py`, chain fallback | `test_resilience.py` (7), `test_throttle.py` (7) |
| Modular, maintainable, scalable | stage modules, `Sink`/text/image provider protocols, dual run mode | 45 tests |
| Assumptions documented | §9 above | — |
| Large-scale efficiency | §7 above | — |

---

## 11. What I would do next

In rough order of value per hour:

1. **Grounding.** A search tool between `outline` and `draft`, with citations in
   the front matter. The single biggest quality gain available, and the honest
   fix for assumption 3.
2. **An eval set.** 20 topics, graded on structure, specificity and format
   adherence, run on every prompt change. Prompt edits are currently unmeasured.
3. **Brand voice profiles.** A per-tenant style guide injected into the draft
   system prompt, with the diversity history partitioned per tenant.
4. **Postgres + object storage**, which is the §7 ceiling.
5. **Human-in-the-loop approval.** A `pending_review` state and an approve/reject
   endpoint, since most content teams want a gate before publication.
6. **Image QC.** A vision call checking the image matches the brief and contains
   no garbled text, feeding the same retry machinery.
