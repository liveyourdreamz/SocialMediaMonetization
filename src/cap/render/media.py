"""ffmpeg: the static binary imageio-ffmpeg ships. Durations, concatenation and the
raw-frame encoder the scenes are piped into."""

from __future__ import annotations

import re
import shutil
import subprocess
from collections.abc import Iterable
from pathlib import Path

from PIL import Image

from cap.render.base import RenderError


def ffmpeg_exe() -> str:
    import imageio_ffmpeg

    return str(imageio_ffmpeg.get_ffmpeg_exe())


def _ffprobe() -> str | None:
    return shutil.which("ffprobe")


def run_ffmpeg(args: list[str]) -> None:
    r = subprocess.run(
        [ffmpeg_exe(), "-y", "-loglevel", "error", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if r.returncode != 0:
        raise RenderError(r.stderr.strip()[-800:])


def duration(path: Path) -> float:
    exe = _ffprobe()
    if exe:
        r = subprocess.run(
            [exe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
            capture_output=True,
            text=True,
        )
        try:
            return float(r.stdout.strip())
        except ValueError:
            pass
    # fall back to ffmpeg's own report
    exe = ffmpeg_exe()
    r = subprocess.run(
        [exe, "-i", str(path)], capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", r.stderr)
    if not m:
        raise RenderError(f"cannot read duration of {path.name}")
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))


def concat(parts: list[Path], out: Path) -> None:
    lst = out.with_suffix(".txt")
    lst.write_text("".join(f"file '{p.name}'\n" for p in parts), encoding="utf-8")
    run_ffmpeg(
        [
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(lst),
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            str(out),
        ]
    )
    lst.unlink(missing_ok=True)


def encode_scene(
    frames_iter: Iterable[Image.Image], fps: int, size: tuple[int, int], mp3: Path, out: Path
) -> None:
    """Pipe raw frames into ffmpeg with the scene's audio -> one mp4 segment."""
    cmd = [
        ffmpeg_exe(), "-y", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{size[0]}x{size[1]}", "-r", str(fps), "-i", "-",
        "-i", str(mp3),
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast", "-crf", "22",
        "-c:a", "aac", "-b:a", "128k", "-shortest", str(out),
    ]  # fmt: skip
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    assert proc.stdin is not None
    try:
        for im in frames_iter:
            proc.stdin.write(im.tobytes())
    except BrokenPipeError:
        pass
    finally:
        proc.stdin.close()
    err = proc.stderr.read().decode("utf-8", "replace") if proc.stderr else ""
    if proc.wait() != 0:
        raise RenderError(err.strip()[-800:] or "ffmpeg failed while encoding a scene")
