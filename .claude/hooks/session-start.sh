#!/bin/bash
# SessionStart hook — install the Secure Context Pipeline's dependencies so tests and the demo
# run in Claude Code on the web. Synchronous (blocks session start until deps are ready).
set -euo pipefail

# Only run in the remote (web) environment; local sessions manage their own venv.
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "$CLAUDE_PROJECT_DIR"

# Core package + test/property deps (idempotent; editable install picks up local changes).
pip install -e ".[test]"

# Presidio is the intended detection substrate. It's heavier (spaCy model download), so it's
# best-effort: if it can't be installed, the pipeline falls back to the native-regex detector
# and the Presidio-only tests skip themselves — CI stays green either way.
if pip install "presidio-analyzer>=2.2"; then
  python -m spacy download en_core_web_sm || echo "spaCy model download failed; native fallback will be used"
else
  echo "presidio-analyzer unavailable; native-regex detector will be used"
fi
