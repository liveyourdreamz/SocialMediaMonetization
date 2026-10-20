#!/usr/bin/env bash
# One command on a Mac or Linux laptop: installs uv if missing, syncs the project,
# starts the app with demo data and opens the browser. The app asks for the Anthropic
# API key on first run (`uv run cap key` changes it). Records live in ./cap-data and
# survive restarts. `./run.sh --reset` starts clean.
set -euo pipefail
cd "$(dirname "$0")"

if ! command -v uv >/dev/null 2>&1; then
  echo "Installing uv (the Python project tool)..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

uv sync --quiet
if [ "${1:-}" = "--smoke" ]; then exec uv run cap smoke; fi   # CI: the loop, no browser
exec uv run cap serve --demo --host 0.0.0.0 --open "$@"
