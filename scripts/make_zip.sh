#!/usr/bin/env bash
# Build the submission archive.
#
# Contents come from `git ls-files`, not from a find over the working tree: that
# way the archive can only ever contain what is committed, so an untracked .env,
# a stray run under out/ or a .venv cannot leak into something that gets mailed.
# The script refuses to build if the secret scan fails.
set -euo pipefail

cd "$(dirname "$0")/.."
root="$(pwd)"
name="content-forge"
stamp="$(date +%Y-%m-%d)"
out="dist/${name}_${stamp}.zip"

echo "==> secret scan"
./scripts/check_secrets.sh --all

echo "==> checking the working tree is committed"
if [[ -n "$(git status --porcelain)" ]]; then
  echo "    warning: uncommitted changes exist and will NOT be in the zip:" >&2
  git status --short | sed 's/^/      /' >&2
fi

echo "==> staging"
stage="$(mktemp -d)"
trap 'rm -rf "$stage"' EXIT
dest="$stage/$name"
mkdir -p "$dest"

# -z/-d handles paths with spaces; samples/*.png are binary so cp, not cat.
git ls-files -z | while IFS= read -r -d '' f; do
  mkdir -p "$dest/$(dirname "$f")"
  cp "$f" "$dest/$f"
done

# The evaluator gets a map of the five deliverables at the top level.
cat > "$dest/SUBMISSION.md" <<'INNER'
# Submission - Case 2: AI-Powered Content Generation Workflow

| Deliverable | Where |
|---|---|
| 1. Workflow design, explanation, assumptions | `docs/DESIGN.md` |
| 2. Source code | `forge/` (pipeline, providers, sinks, API, worker), `tests/` |
| 3. Setup and execution instructions | `README.md` quickstart, then `docs/RUNBOOK.md` |
| 4. Sample outputs (4 topics) | `samples/` - see `samples/README.md` |
| 5. Demo video | linked in the covering mail; script in `docs/DEMO_SCRIPT.md` |

**Runs with no API key.** `FAKE_PROVIDERS=1` exercises the whole pipeline with
no network and no credentials, which is also how the test suite runs:

```bash
python3.13 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest -q
FAKE_PROVIDERS=1 .venv/bin/uvicorn forge.api.app:app --port 8000
```

For real generation, copy `.env.example` to `.env` and add a free Gemini API key
(aistudio.google.com). Images need no key at all. See `README.md`.

No credentials are included in this archive.
INNER

echo "==> zipping"
mkdir -p "$root/dist"
rm -f "$root/$out"
( cd "$stage" && zip -q -r -X "$root/$out" "$name" )

echo
echo "    $out  ($(du -h "$root/$out" | cut -f1), $(git ls-files | wc -l | tr -d ' ') tracked files + SUBMISSION.md)"
echo "==> verifying the archive carries no credentials"
if unzip -p "$root/$out" '*' 2>/dev/null \
   | grep -aqE 'sk-ant-[A-Za-z0-9_-]{10}|AIza[A-Za-z0-9_-]{20}|AQ\.[A-Za-z0-9_-]{20}'; then
  echo "    FAIL: key-shaped material inside the zip" >&2
  rm -f "$root/$out"
  exit 1
fi
echo "    clean"
