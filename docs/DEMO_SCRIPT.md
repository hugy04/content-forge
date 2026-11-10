# Demo video script

> Deliverable 5. Target **4 minutes**. Record with QuickTime (File → New Screen
> Recording) or Loom. Screen + voice; your face is not needed.

**Before you hit record**

```bash
cd ~/content-forge
.venv/bin/uvicorn forge.api.app:app --port 8000      # leave running
```

- Browser tabs, in order: `localhost:8000` · `samples/README.md` ·
  `docs/DESIGN.md` · the Google Sheet (only if the sink is wired)
- A terminal, font size up to ~16pt
- Close Slack/mail notifications

Timings are a guide. **Do not read this aloud verbatim** - it is written as
talking points so it sounds like you.

---

### 0:00 – 0:25 · What it is

> "This is content-forge. The brief was: take a topic string, and automatically
> produce a 500 to 800 word content piece plus a relevant AI-generated image - > with variety across runs, inside five minutes, with proper error handling.
>
> It's a Python service - FastAPI, a pluggable text model, a chain of image
> providers, and a diversity engine that decides the creative direction before
> any model is called. Let me show you a run, then the three decisions I think
> actually mattered."

---

### 0:25 – 1:30 · A live run

Type a topic you have **not** run before. Suggested:
*"Why used-car pricing models break in a volatile market"*

Leave the format on **Auto**.

While it runs, talk - don't narrate the spinner:

> "Notice I didn't pick a format. The engine picks one, and it picks one recent
> runs haven't used. Behind this there are three model calls: an outline, the
> draft, and a small separate call that turns the editorial image brief into an
> image prompt. Image generation runs *in parallel* with the draft - it depends
> on the outline, not the finished text - so the image is close to free in
> wall-clock terms."

When it lands, point at the chips:

> "Word count inside the 500-to-800 contract. The format, the angle, the
> audience, the art direction and the palette were all chosen by the engine, not
> prompted. And it's tens of seconds against a five-minute budget - the four
> samples in the repo averaged forty-four seconds."

---

### 1:30 – 2:15 · Variety is an algorithm, not a prompt

Switch to `samples/README.md`.

> "Four topics through the same pipeline. Different format, different angle,
> different art direction on each - and I specified none of them.
>
> This is the part I'd defend hardest. The naive approach is to put 'be creative
> and varied' in the prompt. That doesn't survive repetition - run the same topic
> twice and you get the same framing and nearly the same picture.
>
> So the choice happens *before* the model. Six independent dimensions - format,
> angle, audience, art direction, palette, composition, about a hundred and
> fifteen thousand combinations. Anything used in the last three runs is excluded
> outright; among what's left, options unused for longer get more weight; and the
> format-angle-art triple is re-rolled if it matches a recent run. It's seeded
> from the run ID, so any run reproduces its own choices exactly - which is what
> makes it unit-testable."

Optional, if you want one terminal moment - run:

```bash
.venv/bin/python -m pytest tests/test_diversity.py tests/test_treatment.py -q
```

> "Thirty-one tests on diversity alone: across sixty simulated runs nothing
> repeats inside its cooldown window, and every art direction and palette pair
> produces a visibly different image."

---

### 2:15 – 2:45 · The visual-diversity problem nobody mentions

Show two sample images side by side - pick two with different `art:` lines in
`samples/README.md`.

> "Here's something I only found by running it. The free image endpoint largely
> ignores style conditioning - ask for 'isometric 3D, teal and copper' and you
> get back roughly the same photographic look every time. So *asking* for visual
> variety doesn't produce it.
>
> I moved art direction out of the prompt and into code: a deterministic
> post-process. Each art direction is a pipeline of image operations - a
> gradient-map lookup table for the palette, luminance banding before
> recolouring for the flat-vector look, edge detection for line art, seeded grain
> for risograph. Same seed, same image, every time.
>
> That turned an unverifiable prompt instruction into something I can assert in a
> test - all eight art directions across all eight palettes come out pairwise
> distinct at the pixel level. And it works on any image provider, including the
> free one."

---

### 2:45 – 3:20 · What happens when things fail

> "Third decision: the pipeline assumes providers fail, because over a night of
> real runs they all did - and each failure taught me something the retry
> textbook gets wrong."

Show `docs/DESIGN.md` §5, or talk over the UI:

> "Retry is the wrong tool for most failures. Four cases, four different
> responses.
>
> A published rate limit - one image every forty-five seconds - isn't a transient
> error, so retrying it just burns the budget. It gets *paced* instead, honouring
> the server's own retry-after, and the pacer knows the run's deadline so it
> skips rather than overrun it.
>
> A deterministic 400 will fail identically forever, so it fails fast and is
> explicitly excluded from the circuit breaker. I found that one the hard way: a
> malformed request got retried, opened a shared breaker, and denied the provider
> to three other runs in the same batch.
>
> Free-tier quota turns out to be enforced *per model*, not per project - so the
> model pool doubles as a quota pool. On a 503 or a quota 429 it falls forward to
> the next model rather than backing off against the one that's exhausted, and a
> daily cap removes that model for the whole process.
>
> And the word count isn't a hope, it's a gate - nine deterministic checks, no
> model involved. If the draft comes back at 430 words it's told to expand a
> specific number of words in the thinnest sections. Bounded at two attempts, and
> it keeps the revision only if it measurably moved closer to passing."

If a run in `samples/` shows `revisions: 1`, point at it.

---

### 3:20 – 3:45 · Pluggable models, outputs, and scale

> "The text layer is behind an interface. It ships on Gemini's free tier so an
> evaluator can run it at zero cost, and switching to Claude is one environment
> variable - same pipeline, same tests. That's the modularity claim with
> something behind it.
>
> Outputs go to disk always, and optionally to Google - a formatted Doc, the
> image in Drive, one row per run in a content log with format, word count, cost
> and latency. Sinks are pluggable and a sink failure never fails a run."

Show the Google Doc + Sheet, or say the local artifacts are the sink.

> "On scale: the same pipeline runs behind Celery and Redis by flipping one env
> var. The real ceiling is the provider rate limit, not compute, which is why the
> batch endpoint is concurrency-capped and the pacer is process-wide. Model and
> effort are configured per stage, so the cheap calls can go to a small model
> without touching code."

---

### 3:45 – 4:00 · Close

> "Everything's in the repo: the design doc has the architecture, the
> error-handling matrix, the scale analysis and an explicit assumptions section.
> There's a runbook, and the whole thing runs with no API key at all in fixture
> mode - a hundred and twenty-eight tests, no network - so it's reproducible on any
> machine.
>
> The honest gap: there's no retrieval, so the model writes from parametric
> memory. The prompt forbids inventing named studies or false precision, but for
> anything factual I'd put a search tool between the outline and the draft.
> That's first on the list in the design doc."

---

## Checklist before you send it

- [ ] Audio is audible and there is no background noise
- [ ] The live run actually completed on camera (if it errored, consider keeping
      it and explaining the fallback - it demonstrates the point)
- [ ] No API keys visible on screen - **check the terminal scrollback, and `.env`
      is never opened**
- [ ] Under 5 minutes
- [ ] Exported as MP4 and either under the mail size limit or on a Drive link
      set to "anyone with the link can view"
