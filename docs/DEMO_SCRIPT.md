# Demo video script

> Deliverable 5. Target **4 minutes**. Record with QuickTime (File → New Screen
> Recording) or Loom. Screen + voice; your face is not needed.

**Before you hit record**

```bash
cd ~/content-forge
.venv/bin/uvicorn forge.api.app:app --port 8000      # leave running
```

- Browser tabs, in order: `localhost:8000` · `samples/README.md` on GitHub ·
  `docs/DESIGN.md` on GitHub · the Google Sheet (if the sink is wired)
- A terminal, font size up to ~16pt
- Close Slack/mail notifications

Timings are a guide. **Do not read this aloud verbatim** — it is written as
talking points so it sounds like you.

---

### 0:00 – 0:25 · What it is

> "This is content-forge. The brief was: take a topic string, and automatically
> produce a 500 to 800 word content piece plus a relevant AI-generated image —
> with variety across runs, inside five minutes, with proper error handling.
>
> I built it as a Python service: FastAPI, Claude for the writing, a chain of
> image providers, and a diversity engine that decides the creative direction
> before any model is called. Let me show you a run, and then the three
> decisions I think actually mattered."

---

### 0:25 – 1:30 · A live run

Type a topic you have **not** run before. Suggested:
*"Why used-car pricing models break in a volatile market"*

Leave the format on **Auto**.

While it runs, talk — don't narrate the spinner:

> "Notice I didn't pick a format. The engine picks one, and it picks one that
> recent runs haven't used. Behind this there are three Claude calls: an outline,
> the draft, and a separate small call that turns the editorial image brief into
> an image prompt. The image generation runs *in parallel* with the draft — it
> depends on the outline, not the finished text — so the image is essentially
> free in wall-clock terms."

When it lands, point at the chips:

> "Seven hundred and ten words, inside the contract. Format is a trend analysis,
> angle is economics, art direction is isometric 3D — and that whole combination
> was chosen, not prompted. Thirty-eight seconds, well inside the five-minute
> budget."

---

### 1:30 – 2:15 · Variety is an algorithm, not a prompt

Switch to `samples/README.md`.

> "Here are four topics run through the same pipeline. Different format,
> different angle, different art direction on each — and I specified none of
> them.
>
> This is the part I'd defend hardest. The naive approach is to put 'be creative
> and varied' in the prompt. That doesn't survive repetition — run the same topic
> twice and you get the same framing and nearly the same picture.
>
> So the choice happens *before* the model. Six independent dimensions —
> format, angle, audience, art direction, palette, composition. Anything used in
> the last three runs is excluded outright; among what's left, options unused
> for longer get more weight; and the format-angle-art triple gets re-rolled if
> it matches a recent run. It's seeded from the run ID, so any run reproduces
> its own choices exactly — which means I can unit-test it."

Optional, if you want one terminal moment — run:

```bash
.venv/bin/python -m pytest tests/test_diversity.py -q
```

> "Ten tests asserting exactly that: across sixty simulated runs, nothing
> repeats inside its cooldown window."

---

### 2:15 – 3:00 · What happens when things fail

> "Second decision: the pipeline assumes providers fail, because they do."

Show `docs/DESIGN.md` §5, or just talk over the UI:

> "Three things. One — every call retries with exponential backoff, and a
> circuit breaker trips a provider that's genuinely down so we stop wasting the
> latency budget on it.
>
> Two — images go through an ordered chain: Gemini, then Pollinations, then a
> card rendered locally with Pillow. That last one can't fail, so a run always
> produces a visual asset. But if it lands there, the run is marked `partial`
> and flagged — a synthesised card never gets passed off as a generated image.
>
> Three — the word count isn't a hope, it's a gate. Nine deterministic checks,
> no model involved. If the draft comes back at 430 words, the pipeline tells
> Claude to expand by a specific number of words in the thinnest sections, and
> re-checks. Bounded at two attempts, and it keeps the revision only if it
> actually improved the gate."

If you have a run in `samples/` with `revisions: 1`, point at it.

---

### 3:00 – 3:30 · Where the outputs go, and scale

Show the Google Doc + Sheet (skip if not wired, and say the local artifacts are
the sink instead).

> "Outputs go to disk always, and optionally to Google — a formatted Doc, the
> image in Drive, and one row per run in a content log with format, word count,
> cost and latency. Sinks are pluggable and a sink failure never fails a run.
>
> On scale: the same pipeline runs behind Celery and Redis by flipping one env
> var. Four workers at four slots is roughly six hundred assets an hour — but
> the real ceiling is the provider rate limit, not compute, which is why the
> batch endpoint is concurrency-capped. And model and effort are configured per
> stage, so the cheap formatting call can go to Haiku without touching code."

---

### 3:30 – 4:00 · Close

> "Everything's in the repo: the design doc covers the architecture, the
> error-handling matrix, the scale analysis and an explicit assumptions section.
> There's a runbook, and the whole thing runs with no API key at all in fixture
> mode — thirty-seven tests, no network — so it's reproducible on any machine.
>
> The honest gap: there's no retrieval, so the model writes from parametric
> memory. The prompt forbids inventing named studies or false precision, but for
> anything factual I'd add a search tool between the outline and the draft. That's
> first on the list in the design doc."

---

## Checklist before you send it

- [ ] Audio is audible and there is no background noise
- [ ] The live run actually completed on camera (re-record if it errored —
      or better, keep it and explain the fallback, which demonstrates the point)
- [ ] No API keys visible on screen — **check the terminal scrollback and `.env`
      is never opened**
- [ ] Under 5 minutes
- [ ] Exported as MP4 and either under the mail size limit or on a Drive link
      set to "anyone with the link can view"
