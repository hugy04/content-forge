#!/usr/bin/env bash
# Refuse to let key-shaped strings reach git history.
#
# Run standalone, or install as a pre-commit hook:
#   ln -sf ../../scripts/check_secrets.sh .git/hooks/pre-commit
#
# With no argument it scans whatever is staged, falling back to every tracked
# file when nothing is staged. `--all` forces the full tracked-file scan, which
# is what the release script wants: a clean commit still ships a bad file.
#
# Patterns cover the formats this project touches. Note that Google AI Studio
# issues BOTH the legacy `AIza...` form and a newer `AQ.`-prefixed form; a scan
# that only knows the old one gives false confidence.
set -uo pipefail

PATTERNS=(
  'sk-ant-[A-Za-z0-9_-]{20,}'      # Anthropic
  'AIza[A-Za-z0-9_-]{30,}'         # Google, legacy format
  'AQ\.[A-Za-z0-9_-]{30,}'         # Google, current AI Studio format
  'sk-[A-Za-z0-9]{32,}'            # OpenAI
  'AKIA[0-9A-Z]{16}'               # AWS access key id
  'ghp_[A-Za-z0-9]{36}'            # GitHub PAT
)

# Scan what is staged; fall back to the whole tree when run by hand.
if [ "${1:-}" = "--all" ]; then
  TARGET="all tracked files"
  get_content() { git ls-files -z | xargs -0 cat 2>/dev/null; }
elif git diff --cached --quiet 2>/dev/null; then
  TARGET="tracked files"
  get_content() { git ls-files -z | xargs -0 cat 2>/dev/null; }
else
  TARGET="staged changes"
  get_content() { git diff --cached; }
fi

found=0
for pattern in "${PATTERNS[@]}"; do
  if get_content | grep -aqE "$pattern"; then
    echo "BLOCKED: a string matching /$pattern/ is present in $TARGET."
    found=1
  fi
done

# .env must never be tracked, empty or not.
if git ls-files --error-unmatch .env >/dev/null 2>&1; then
  echo "BLOCKED: .env is tracked by git. Run: git rm --cached .env"
  found=1
fi

if [ "$found" -ne 0 ]; then
  echo
  echo "Nothing was committed. Move the secret into .env (gitignored) and retry."
  exit 1
fi

echo "Secret scan clean ($TARGET)."
