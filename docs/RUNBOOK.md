# Runbook — setup and execution

> Deliverable 3. Three paths: **run it in two minutes with no credentials**,
> run it properly with real providers, or deploy it.

---

## 0. Prerequisites

- Python 3.13 (3.11+ works; 3.13 is what this was built and tested on)
- Optional: Redis, only for queue mode — `brew install redis && brew services start redis`

```bash
git clone <repo-url> content-forge && cd content-forge
python3.13 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

---

## 1. Run it with no credentials (2 minutes)

The whole pipeline runs against fixtures. Nothing leaves the machine.

```bash
FAKE_PROVIDERS=1 IMAGE_PROVIDERS=card .venv/bin/uvicorn forge.api.app:app --port 8000
```

Open <http://localhost:8000> and generate. You will see the real stage
sequence, the quality gate firing a revision on a deliberately short fixture
draft, and artifacts written to `out/`.

```bash
.venv/bin/python -m pytest -q      # 37 tests, same fixtures, no network
```

---

## 2. Run it with real providers

```bash
cp .env.example .env
```

Edit `.env`:

| Key | Where to get it | Required? |
|---|---|---|
| `GEMINI_API_KEY` | <https://aistudio.google.com/apikey> → Create API key — free, no card, no billing account | **yes** |
| `ANTHROPIC_API_KEY` | <https://platform.claude.com> → API keys | only if `TEXT_PROVIDER=claude` |

One free Gemini key drives both the text stage and the image stage. An
Anthropic API key is **not** included in a Claude Pro/Max subscription — the
API meter is prepaid and separate — so the default path deliberately avoids it.

Then confirm the key and the model ids before your first real run:

```bash
.venv/bin/python scripts/gemini_doctor.py
```

It lists every model the key can call, resolves the text model, runs a
structured-output smoke test and prints the exact lines to paste into `.env`.
Run it whenever a model id starts 404ing: Google retires Flash ids roughly
quarterly (`gemini-2.5-flash` was deprecated in October 2026), and free-tier
daily quotas have been cut repeatedly, so neither is safe to assume.

### Free-tier quotas are the real constraint

| Provider | Observed free limit | How the pipeline copes |
|---|---|---|
| Gemini text | quotas are enforced **per model**, so a spent model is not a spent key. Newest ids are also the most capacity-limited: `gemini-3.8-flash` and `gemini-flash-latest` returned 503 "high demand" on a fresh free key, `gemini-3.6-flash` served normally | three calls per run; the provider retries, then **falls forward to the next model** |
| Gemini image | **no free quota at all** — every image model returned 429 immediately | left out of the default chain; the code stays for paid keys |
| Retired model ids | `gemini-2.5-flash`, `-flash-lite` and `-pro` all return 404 "no longer available to new users" | resolved against ListModels at startup |
| Pollinations | ~1 image / 45s per IP, watermarked | paced by `POLLINATIONS_MIN_INTERVAL_SECONDS`, and the server's own `Retry after Ns` hint overrides it |
| Card fallback | none — local | always last; guarantees every run ships a visual |

If a daily cap is exhausted mid-demo, `FAKE_PROVIDERS=1` reproduces the full
pipeline offline, and the already-generated `samples/` are unaffected.

Then:

```bash
.venv/bin/uvicorn forge.api.app:app --port 8000
```

Verify:

```bash
curl -s localhost:8000/healthz | jq
curl -s localhost:8000/v1/generate -H 'content-type: application/json' \
     -d '{"topic":"EV battery recycling in India"}' | jq '.status, .word_count, .image.provider'
```

Without `GEMINI_API_KEY` the chain falls to Pollinations (keyless, watermarked).
With neither reachable it falls to the locally rendered card and marks the run
`partial` — by design, never a hard failure.

### Generate the sample set

```bash
./scripts/run_samples.sh        # 4 topics, then collects them into samples/
```

---

## 3. Queue mode (Celery + Redis)

Inline mode runs the pipeline in the web process — simplest, and what the
hosted demo uses. Queue mode is the same pipeline behind a broker, which is what
scales.

```bash
brew services start redis      # or: redis-server
redis-cli ping                 # expect PONG
```

Terminal 1 — worker:
```bash
RUN_MODE=queue .venv/bin/celery -A forge.worker.celery_app worker \
    --loglevel=info --concurrency=4 -Q forge
```

Terminal 2 — API:
```bash
RUN_MODE=queue .venv/bin/uvicorn forge.api.app:app --port 8000
```

Now `POST /v1/generate` returns `202` with a `task_id` instead of blocking, and
`POST /v1/batch` fans the whole list onto the queue.

```bash
curl -s localhost:8000/v1/batch -H 'content-type: application/json' \
     -d '{"topics":["Topic A","Topic B","Topic C"],"concurrency":3}' | jq
```

Scale out by starting more workers against the same Redis — the pipeline holds
no process-local state. Keep total concurrency under your provider's rate limit;
`worker_prefetch_multiplier=1` is set so long tasks do not hoard the queue.

---

## 4. Google Drive + Docs + Sheets sink (~5 minutes)

Produces a formatted Google Doc, the image in a Drive folder, and one row per
run in a content-log spreadsheet.

1. Go to <https://script.google.com> → **New project**.
2. Replace the contents of `Code.gs` with
   [`integrations/apps_script/Code.gs`](../integrations/apps_script/Code.gs).
3. **Project Settings** (gear icon) → **Script properties** → **Add script property**:
   - Property: `SHARED_SECRET`
   - Value: any long random string — generate one with
     `python3 -c "import secrets;print(secrets.token_urlsafe(32))"`
4. **Deploy** → **New deployment** → gear → **Web app**:
   - *Execute as*: **Me**
   - *Who has access*: **Anyone**
   - Deploy, authorise when prompted (it will warn the app is unverified — it is
     your own script; choose Advanced → Go to project).
5. Copy the `/exec` URL.
6. In `.env`:
   ```
   SINKS=local,gdrive
   GDRIVE_WEBAPP_URL=https://script.google.com/macros/s/AKfy.../exec
   GDRIVE_SHARED_SECRET=<the same string from step 3>
   ```

Verify:
```bash
curl -s "$GDRIVE_WEBAPP_URL"          # {"ok":true,"service":"content-forge sink",...}
```

The Drive folder (`content-forge`) and the spreadsheet (`content-forge log`) are
created automatically on the first successful run.

**Sink failures never fail a run** — the local sink has already written
everything to `out/`. A misconfigured secret shows up as a warning on the run
and an `ok:false` entry in `artifacts.gdrive`.

---

## 5. Deployment (Render free tier)

Chosen because it is genuinely free, needs no card, and does not depend on a
laptop being awake.

1. Push the repo to GitHub.
2. <https://dashboard.render.com> → **New** → **Blueprint** → pick the repo.
   `render.yaml` is detected automatically.
3. When prompted, set the secret env vars: `GEMINI_API_KEY`,
   `ANTHROPIC_API_KEY` (only if you switch `TEXT_PROVIDER` to `claude`).
4. Deploy. The service comes up at `https://content-forge.onrender.com`.

**Keep it warm.** Free services spin down after 15 minutes idle and take about a
minute to wake. Point an uptime monitor (UptimeRobot, free) at
`https://<your-app>.onrender.com/healthz` every 5 minutes and it never sleeps.
750 instance-hours/month covers one always-on service.

**Known limits of the free tier**, stated plainly:

| Limit | Consequence | Production answer |
|---|---|---|
| No persistent disk | `out/` and the SQLite history reset on restart | object storage + Postgres |
| No Redis | runs `inline`, not `queue` | managed Redis + worker service |
| Cold start ~60s | first request after idle is slow | uptime ping, or a paid instance |

### Alternative: a container

```bash
docker build -t content-forge .
docker run -p 8000:8000 --env-file .env -v "$PWD/data:/data" content-forge
```

---

## 6. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `GEMINI_API_KEY is not set` | no `.env`, or the placeholder is still there | add the free key, or `FAKE_PROVIDERS=1` |
| `gemini 404 ... model not found` | Google retired that model id | run `scripts/gemini_doctor.py`; the provider also self-resolves to the newest Flash id |
| `gemini 429 ... per day limit` | that **model's** daily quota is spent — quotas are per model, not per project | nothing to do: the provider excludes it for the process and falls forward. To pick a different primary, set `GEMINI_MODEL` to one `gemini_doctor.py` reports as OK |
| `gemini 429 ... per minute` | calling too fast | set `GEMINI_MIN_INTERVAL_SECONDS` (4 is comfortable) and run batches at `--concurrency 1` |
| `CircuitOpen: circuit open for gemini:<model>` | that model failed repeatedly | expected and handled — the run falls forward to a sibling model. Seeing the run *fail* on this means you are on a build before that fix |
| Image is always the fallback card | no `GEMINI_API_KEY` and Pollinations unreachable | add the key; check `.image.attempts` in the run record for the per-provider reason |
| `pollinations rate limit (402)` | anonymous tier allows ~1 image / 45s per IP | expected under load; the limiter paces the next call and the chain falls through meanwhile |
| Batch runs all show `skipped: paced` | several runs contending for one image slot | lower `--concurrency`, or raise `DEADLINE_SECONDS` so a run can afford the wait |
| Runs always come back `partial` | the gate failed, or the image is a card | `GET /v1/runs/{id}` → `qc.checks` names the failing check |
| Same style twice in a row | `DIVERSITY_COOLDOWN=0`, or an explicit `style` was passed | raise the cooldown; omit `style` to let the engine choose |
| `202` instead of a result | `RUN_MODE=queue` | expected — poll the run, or set `RUN_MODE=inline` |
| Celery connects but nothing runs | worker not listening on the right queue | include `-Q forge` |
| Port 8000 in use | something else is bound | `--port 8001` |

Useful probes:

```bash
curl -s localhost:8000/v1/stats | jq        # throughput, distributions, breaker state
curl -s localhost:8000/v1/runs | jq '.runs[0]'
tail -f out/*/run.json                      # per-run records
```
