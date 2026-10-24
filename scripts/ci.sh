#!/usr/bin/env bash
# The CI checks job, step for step, on this machine. Set CAP_TEST_DATABASE_URL to a
# Postgres server the tests may create databases on (CI sets it; CAP_REQUIRE_PG=1 there).
set -euo pipefail
cd "$(dirname "$0")/.."
uv run ruff check src tests scripts
uv run ruff format --check src tests scripts
uv run mypy
uv run lint-imports
if [ "$(id -u)" = "0" ]; then
  # initdb refuses root: the laptop-Postgres tests run in CI and as a normal user
  echo "ci.sh: running as root, skipping tests/test_localpg.py"
  uv run pytest -q --deselect tests/test_localpg.py
else
  uv run pytest -q
fi
uv run cap prompts check
uv run cap tokens check
uv run cap schema export >/dev/null && git diff --exit-code -- src/cap/schema/post_record.schema.json
echo "ci.sh: all checks passed"
