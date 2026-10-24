"""The voice: Microsoft neural voices through edge-tts (free, no key; English and
Arabic). The PRD's ElevenLabs private voices (D21) replace `speak` behind the same
signature; `silent_voice` is the offline stand-in the tests and dry runs use."""

from __future__ import annotations

import asyncio
from pathlib import Path

from cap.render.media import run_ffmpeg

VOICES = {  # character -> edge-tts voice; ElevenLabs ids replace these (D21)
    ("Patch", "en"): "en-GB-RyanNeural",
    ("Falak", "en"): "en-GB-SoniaNeural",
    ("Falak", "ar"): "ar-AE-FatimaNeural",
}


async def _tts(text: str, voice: str, out: Path) -> None:
    import edge_tts

    await edge_tts.Communicate(text, voice).save(str(out))


def speak(text: str, voice: str, out: Path) -> None:
    """One paragraph -> one mp3. Swappable: ElevenLabs lands here."""
    asyncio.run(_tts(text, voice, out))


def silent_voice(text: str, voice: str, out: Path) -> None:
    """Offline stand-in: 0.4 s of silence per word, so timings stay plausible."""
    silence(max(1.0, 0.4 * len(text.split())), out)


def silence(seconds: float, out: Path) -> None:
    run_ffmpeg(
        [
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=24000:cl=mono",
            "-t",
            f"{seconds:.2f}",
            "-q:a",
            "9",
            "-acodec",
            "libmp3lame",
            str(out),
        ]
    )
