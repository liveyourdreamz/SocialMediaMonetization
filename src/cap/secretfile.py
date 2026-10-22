"""Files that hold secrets (.env, cap-data/connections) readable by their owner only.

POSIX: mode 0600 for a file, 0700 for a directory. Windows ignores os.chmod for
everything but the read-only bit, so there the file's access list is replaced with one
entry for the signed-in user (icacls, no shell). Best effort: a failure warns and the
write still stands, because refusing to save a key the owner just typed helps nobody.
"""

from __future__ import annotations

import os
import stat
import subprocess
import warnings
from pathlib import Path

WINDOWS = os.name == "nt"


def _windows_user() -> str:
    user = os.environ.get("USERNAME", "")
    domain = os.environ.get("USERDOMAIN", "")
    return f"{domain}\\{user}" if domain and user else user


def restrict_to_owner(path: Path) -> None:
    """Only the current user may read or write `path` from now on."""
    if WINDOWS:
        user = _windows_user()
        if not user:
            warnings.warn(f"could not restrict {path}: USERNAME is not set", stacklevel=2)
            return
        grant = f"{user}:(OI)(CI)F" if path.is_dir() else f"{user}:F"
        try:
            done = subprocess.run(  # noqa: S603 - fixed argv, no shell
                ["icacls", str(path), "/inheritance:r", "/grant:r", grant],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as e:
            warnings.warn(f"could not restrict {path} to {user}: {e}", stacklevel=2)
            return
        if done.returncode != 0:
            why = (done.stderr or done.stdout).strip()[:200]
            warnings.warn(f"could not restrict {path} to {user}: {why}", stacklevel=2)
        return
    mode = stat.S_IRWXU if path.is_dir() else stat.S_IRUSR | stat.S_IWUSR
    try:
        os.chmod(path, mode)
    except OSError as e:
        warnings.warn(f"could not restrict {path}: {e}", stacklevel=2)
