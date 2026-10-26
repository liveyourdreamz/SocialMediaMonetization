"""Best-effort code update for the laptop install (review H7).

install.bat runs this file straight out of the newest fetched commit (so the update
logic is always the latest), with any Python 3.12+, before the app starts. It is also
`uv run cap update`. Standard library only, and it imports nothing from `cap`.

The rule: an update never stops the app from starting. Whatever goes wrong (no
network, GitHub down, a sign-in GitHub wants, a stale .git/index.lock, files Hemant
saved himself in the folder) it says so in one warning and the launcher starts the
code already on disk. Files of his that are in the way are copied to a dated folder
under the data dir first, never thrown away; commits that only exist on the laptop
are kept on a backup branch.

Exit codes: 0 updated or already current, 2 not updated (the warning is printed),
3 the app is already running (the launcher opens it instead of updating under it).
"""

from __future__ import annotations

import argparse
import os
import shutil
import socket
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

OK, NOT_UPDATED, RUNNING = 0, 2, 3
STALE_LOCK_S = 600  # an index.lock older than this belongs to a git that died
FETCH_TIMEOUT_S = 180
GIT_TIMEOUT_S = 60
DEFAULT_PORT = 8000
LOCK_FILE = "serve.lock"  # written by `cap serve` (cap.instance); holds "port=N"
BACKUPS = "update-backups"

# Never wait for a person: no terminal prompt, no Git Credential Manager window
# (a hidden one is what made the old launcher hang), literal paths only.
GIT_ENV = {
    "GIT_TERMINAL_PROMPT": "0",
    "GCM_INTERACTIVE": "never",
    "GIT_LITERAL_PATHSPECS": "1",
    "LC_ALL": "C",
}
GIT_CONFIG = (
    "-c",
    "credential.interactive=never",
    "-c",
    "http.lowSpeedLimit=1000",
    "-c",
    "http.lowSpeedTime=30",
    "-c",
    "core.quotepath=off",
)


class UpdateError(Exception):
    """The update did not happen; the message is the one-line reason for Hemant."""


@dataclass
class Result:
    before: str
    after: str
    moved: list[str] = field(default_factory=list)
    backup_dir: Path | None = None
    backup_branches: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return self.before != self.after


def _git(
    repo: Path, *args: str, timeout: float = GIT_TIMEOUT_S, check: bool = True
) -> subprocess.CompletedProcess[str]:
    try:
        done = subprocess.run(
            ["git", *GIT_CONFIG, "-C", str(repo), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env={**os.environ, **GIT_ENV},
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired as e:
        raise UpdateError(f"git {args[0]} took longer than {timeout:.0f} seconds") from e
    except FileNotFoundError as e:
        raise UpdateError("git is not installed") from e
    if check and done.returncode:
        raise UpdateError(_reason(args[0], done.stderr))
    return done


def _reason(cmd: str, stderr: str) -> str:
    text = stderr.lower()
    if any(s in text for s in ("could not read username", "authentication", "terminal prompts")):
        return (
            "GitHub wants you to sign in again. Open a terminal in the app folder, run "
            "`git fetch` (a sign-in window opens), then run install.bat again"
        )
    if any(s in text for s in ("could not resolve host", "unable to access", "timed out")):
        return "could not reach GitHub (no internet, or GitHub is down)"
    lines = [ln.strip() for ln in stderr.strip().splitlines() if ln.strip()]
    return f"git {cmd} failed: {lines[-1] if lines else 'no message'}"


def _names(out: str) -> set[str]:
    return {p for p in out.split("\0") if p}


def _rev(repo: Path, ref: str) -> str | None:
    done = _git(repo, "rev-parse", "-q", "--verify", f"{ref}^{{commit}}", check=False)
    return done.stdout.strip() or None


def data_dir(repo: Path) -> Path:
    """Where the app keeps its records: CAP_DATA_DIR from the environment or .env."""
    value = os.environ.get("CAP_DATA_DIR", "")
    env = repo / ".env"
    if not value and env.exists():
        for line in env.read_text(encoding="utf-8-sig", errors="replace").splitlines():
            if line.startswith("CAP_DATA_DIR="):
                value = line.split("=", 1)[1].strip().strip('"').strip("'")
    path = Path(value or "cap-data")
    return path if path.is_absolute() else repo / path


def app_port(repo: Path) -> int | None:
    """The port of an app already serving from this folder, or None."""
    port = DEFAULT_PORT
    lock = data_dir(repo) / LOCK_FILE
    try:
        for line in lock.read_text(encoding="utf-8").splitlines():
            if line.startswith("port="):
                port = int(line.split("=", 1)[1])
    except (OSError, ValueError):
        pass
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return port if s.connect_ex(("127.0.0.1", port)) == 0 else None


def clear_stale_lock(repo: Path, now: Callable[[], float] = time.time) -> str | None:
    """Remove .git/index.lock left by a git that died (older than 10 minutes). A fresh
    one means another git is working here, so the update waits for next time."""
    git_dir = Path(_git(repo, "rev-parse", "--absolute-git-dir").stdout.strip())
    lock = git_dir / "index.lock"
    if not lock.exists():
        return None
    age = now() - lock.stat().st_mtime
    if age < STALE_LOCK_S:
        raise UpdateError(
            f"another git program is working in the folder (.git/index.lock is {age:.0f} s old)"
        )
    lock.unlink()
    return f"removed a stale .git/index.lock ({age / 60:.0f} minutes old)"


def blocking_paths(repo: Path, head: str, target: str) -> tuple[list[str], list[str]]:
    """The local files the update would overwrite: tracked files changed here that the
    update also changes, and untracked files where the update brings a tracked one."""
    incoming_changes = _names(_git(repo, "diff", "--name-only", "-z", head, target).stdout)
    changed_here = _names(_git(repo, "diff", "--name-only", "-z", "HEAD").stdout)
    untracked = _names(_git(repo, "ls-files", "-z", "--others", "--exclude-standard").stdout)
    in_target = _names(_git(repo, "ls-tree", "-r", "-z", "--name-only", target).stdout)
    return sorted(changed_here & incoming_changes), sorted(untracked & in_target)


def back_up(repo: Path, paths: Sequence[str], dest: Path) -> list[str]:
    saved = []
    for rel in paths:
        src = repo / rel
        if src.is_file():
            (dest / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest / rel)
            saved.append(rel)
    return saved


def _restore(repo: Path, tracked: Sequence[str], untracked: Sequence[str]) -> None:
    """Put the backed-up paths back to the committed version (or remove them)."""
    if tracked:
        _git(repo, "reset", "-q", "--", *tracked)
        in_head = _names(_git(repo, "ls-tree", "-z", "--name-only", "HEAD", "--", *tracked).stdout)
        if in_head:
            _git(repo, "checkout", "-q", "--", *sorted(in_head))
        for rel in set(tracked) - in_head:
            (repo / rel).unlink(missing_ok=True)
    for rel in untracked:
        (repo / rel).unlink(missing_ok=True)


def _stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d_%H%M%S")


def update(repo: Path, branch: str) -> Result:
    """Bring `repo` to origin/<branch>. Raises UpdateError when it could not."""
    if not (repo / ".git").exists():
        raise UpdateError(f"{repo} is not a git checkout")
    notes = [n for n in [clear_stale_lock(repo)] if n]
    remote_ref = f"refs/remotes/origin/{branch}"
    _git(
        repo, "fetch", "-q", "origin", f"+refs/heads/{branch}:{remote_ref}", timeout=FETCH_TIMEOUT_S
    )
    target = _rev(repo, remote_ref)
    head = _rev(repo, "HEAD")
    if target is None or head is None:
        raise UpdateError(f"branch {branch} not found on GitHub")
    on_branch = _git(repo, "symbolic-ref", "-q", "--short", "HEAD", check=False).stdout.strip()
    result = Result(before=head, after=head, notes=notes)
    if head == target and on_branch == branch:
        return result

    # commits that exist only on this laptop are kept on a branch, never dropped
    stamp = _stamp()
    for ref in sorted({head, _rev(repo, f"refs/heads/{branch}") or head}):
        ancestor = _git(repo, "merge-base", "--is-ancestor", ref, target, check=False)
        if ancestor.returncode != 0:
            name = f"backup/{branch}-{stamp}-{ref[:7]}"
            _git(repo, "branch", "-f", name, ref)
            result.backup_branches.append(name)

    first = _git(repo, "checkout", "-q", "-B", branch, target, check=False)
    if first.returncode:
        tracked, untracked = blocking_paths(repo, head, target)
        if not tracked and not untracked:
            raise UpdateError(_reason("checkout", first.stderr))
        dest = data_dir(repo) / BACKUPS / stamp
        result.moved = back_up(repo, [*tracked, *untracked], dest)
        result.backup_dir = dest
        _restore(repo, tracked, untracked)
        _git(repo, "checkout", "-q", "-B", branch, target)
    result.after = target
    return result


def report(result: Result, out: Callable[[str], None] = print) -> None:
    for note in result.notes:
        out(f"  {note}")
    if result.changed:
        out(f"  Code updated: {result.before[:7]} -> {result.after[:7]}")
    else:
        out(f"  Code is up to date ({result.after[:7]})")
    if result.moved:
        out("  Files you changed in the app folder were in the way of the update.")
        out(f"  Your copies are saved in {result.backup_dir}:")
        for rel in result.moved:
            out(f"    {rel}")
        out("  The app folder now has the new versions.")
    for name in result.backup_branches:
        out(f"  Commits made on this laptop are kept on the git branch {name}")


def main(argv: Sequence[str] | None = None, out: Callable[[str], None] = print) -> int:
    p = argparse.ArgumentParser(prog="cap update", description="Best-effort code update.")
    p.add_argument("--dir", default=".", help="the app folder (a git checkout)")
    p.add_argument("--branch", default="bootstrap")
    args = p.parse_args(argv)
    repo = Path(args.dir).resolve()
    try:
        port = app_port(repo)
        if port is not None:
            out(f"  The app is already running (port {port}); not updating it while it runs.")
            return RUNNING
        report(update(repo, args.branch), out)
    except UpdateError as e:
        out(f"  WARNING: the code was not updated: {e}.")
        out("  Starting the version already on this laptop.")
        return NOT_UPDATED
    except Exception as e:  # noqa: BLE001 - an update must never stop the app starting
        out(f"  WARNING: the code was not updated ({type(e).__name__}: {e}).")
        out("  Starting the version already on this laptop.")
        return NOT_UPDATED
    return OK


if __name__ == "__main__":
    sys.exit(main())
