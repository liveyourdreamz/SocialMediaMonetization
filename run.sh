#!/usr/bin/env bash
# One command on a Mac or Linux laptop: installs uv if missing, syncs the project,
# starts the app with demo data and opens the browser. Re-run any time; records live
# in ./cap-data and survive restarts. `./run.sh --reset` starts clean.
set -euo pipefail
cd "$(dirname "$0")"

if ! command -v uv >/dev/null 2>&1; then
  echo "Installing uv (the Python project tool)..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

[ -f .env ] || cp .env.example .env

if ! grep -q '^CAP_ANTHROPIC_API_KEY=sk-' .env; then
  echo "Anthropic API key (typed hidden, saved only to $(pwd)/.env; leave blank for mock mode):"
  read -r -s KEY; echo
  if [ -n "$KEY" ]; then
    grep -v -E '^#?\s*CAP_ANTHROPIC_API_KEY=|^CAP_MODEL_BACKEND=' .env > .env.tmp
    { cat .env.tmp; echo "CAP_MODEL_BACKEND=anthropic"; echo "CAP_ANTHROPIC_API_KEY=$KEY"; } > .env
    rm -f .env.tmp; unset KEY
    echo "Saved. Drafts and judges will run on the real models."
  else
    echo "No key: mock mode. Run ./run.sh again any time to add one."
  fi
fi

uv sync --quiet
exec uv run cap serve --demo --host 0.0.0.0 --open "$@"
