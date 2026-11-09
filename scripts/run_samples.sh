#!/usr/bin/env bash
# Produce the sample outputs committed under samples/.
# Usage: ./scripts/run_samples.sh
set -euo pipefail
cd "$(dirname "$0")/.."

VENV=".venv/bin/python"
[ -x "$VENV" ] || { echo "run: python3.13 -m venv .venv && .venv/bin/pip install -r requirements.txt"; exit 1; }

echo "Generating sample assets (4 topics, concurrency 2)…"
"$VENV" -m forge.cli --file samples/topics.txt --concurrency 2

echo
echo "Collecting the newest run per topic into samples/…"
"$VENV" scripts/collect_samples.py
echo "Done. See samples/README.md"
