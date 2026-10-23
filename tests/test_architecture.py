"""The layer contracts (M1, ADR-001) hold, and their exception list only shrinks.

Each exception in pyproject.toml names the M1 step that removes it. A new exception is
a new tangle; this test makes adding one a visible, reviewed change.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).parents[1]
MAX_EXCEPTIONS = 0  # the list is empty; keep it so


def _contracts() -> list[dict]:
    return tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["importlinter"]["contracts"]


def test_the_exception_list_only_shrinks():
    total = sum(len(c.get("ignore_imports", [])) for c in _contracts())
    assert total <= MAX_EXCEPTIONS, f"{total} layer exceptions; the limit is {MAX_EXCEPTIONS}"


def test_the_layer_contracts_hold():
    exe = shutil.which("lint-imports", path=str(Path(sys.executable).parent))  # .exe on Windows
    assert exe, "import-linter is not installed (uv sync --all-extras)"
    out = subprocess.run([exe], cwd=ROOT, capture_output=True, text=True)
    assert out.returncode == 0, out.stdout[-3000:]


def test_the_domain_imports_nothing_above_it():
    allowed = ("cap.domain", "cap.schema", "cap.policy", "cap.config", "cap.version")
    for f in (ROOT / "src" / "cap" / "domain").glob("*.py"):
        for line in f.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if s.startswith(("from cap", "import cap")):
                mod = s.split()[1]
                assert mod.startswith(allowed), f"{f.name}: {s}"


# ---- size and complexity (Build Plan engineering standards) ---------------------------
# A file over MAX_LINES may only be one listed here, and may not grow past its listed
# length; a function over complexity 10 must carry the C901 marker, and the markers may
# only become fewer. M1 step 4 splits these; lower each number as it does.
MAX_LINES = 400
LONG_FILES = {
    "src/cap/render/scenes.py": 1053,
    "src/cap/render/__init__.py": 585,
    "src/cap/connections.py": 462,
    "src/cap/schema/post_record.py": 451,
    "src/cap/models/__init__.py": 419,
}
MAX_COMPLEX = 14  # functions marked "noqa: C901"; never raise it


def test_no_file_grows_past_the_limit():
    over = {}
    for f in sorted((ROOT / "src").rglob("*.py")):
        n = len(f.read_text(encoding="utf-8").splitlines())
        rel = f.relative_to(ROOT).as_posix()
        if n > LONG_FILES.get(rel, MAX_LINES):
            over[rel] = n
    assert not over, f"over the size limit (split, do not add to the list): {over}"


def test_the_list_of_long_files_only_shrinks():
    for rel, limit in LONG_FILES.items():
        f = ROOT / rel
        if f.exists():
            n = len(f.read_text(encoding="utf-8").splitlines())
            assert n > MAX_LINES, f"{rel} is now {n} lines: remove it from LONG_FILES"


def test_complex_functions_only_become_fewer():
    marked = sum(
        f.read_text(encoding="utf-8").count("noqa: C901")
        for d in ("src", "tests")
        for f in (ROOT / d).rglob("*.py")
        if f.name != "test_architecture.py"
    )
    assert marked <= MAX_COMPLEX, (
        f"{marked} functions over complexity 10; the limit is {MAX_COMPLEX}"
    )
