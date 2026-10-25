"""Real photographs from Wikimedia Commons: search, the licence rule, a safe download.

Licence rule (D50): public domain, CC0 and CC BY only. CC BY-SA is left out: a video
built on a share-alike photo may have to carry the same licence. NC (no commercial
use) and ND (no changes; a pan and zoom is a change) are left out because monetised
channels need both. An image whose licence cannot be read is never used. Every picture
keeps its author, licence and page, for the credit on screen and in the description.

Only two hosts are ever contacted: the Commons API and its image server.
"""

from __future__ import annotations

import html
import io
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from PIL import Image

from cap.fetch import UA, check_public
from cap.safehttp import PinnedTransport

API = "https://commons.wikimedia.org/w/api.php"
IMAGE_HOST = "upload.wikimedia.org"
MAX_IMAGE_BYTES = 15_000_000
MIN_WIDTH, MIN_HEIGHT = 1600, 900
FETCH_WIDTH = 2400  # Commons scales the file to this width for us
_MIMES = ("image/jpeg", "image/png")


@dataclass(frozen=True)
class Candidate:
    """One search result that passed the licence and size rules."""

    page_id: int
    title: str  # "File:Double rainbow.jpg"
    url: str  # the scaled image on upload.wikimedia.org
    page: str  # its Commons page, for the credit
    author: str
    licence: str


@dataclass(frozen=True)
class Picture:
    """A downloaded candidate, on disk."""

    path: Path
    title: str
    page: str
    author: str
    licence: str

    @property
    def credit(self) -> str:
        name = re.sub(r"^File:|\.\w+$", "", self.title)
        return f"{name} · {self.author} · {self.licence} · Wikimedia Commons"


def licence_ok(name: str) -> bool:
    """Public domain, CC0 or CC BY (any version); nothing share-alike, non-commercial,
    no-derivatives or unknown."""
    n = name.strip().lower()
    if not n:
        return False
    if n.startswith(("public domain", "pd")) or n == "cc0" or n.startswith("cc0 "):
        return True
    return bool(re.fullmatch(r"cc[ -]by \d(\.\d)?( [a-z]+)?", n))


def _plain(value: str) -> str:
    """Commons gives authors as HTML; the credit wants the name."""
    text = html.unescape(re.sub(r"<[^>]+>", "", value or ""))
    return re.sub(r"\s+", " ", text).strip()[:80] or "unknown author"


def _meta(info: dict[str, Any], key: str) -> str:
    return str((info.get("extmetadata") or {}).get(key, {}).get("value", ""))


def parse(reply: dict[str, Any]) -> list[Candidate]:
    """The API's answer, in search order, keeping only what may be used."""
    pages = sorted(
        (reply.get("query") or {}).get("pages", {}).values(), key=lambda p: p.get("index", 0)
    )
    out = []
    for p in pages:
        info = (p.get("imageinfo") or [{}])[0]
        licence = _meta(info, "LicenseShortName")
        url = str(info.get("thumburl") or "")
        if not (licence_ok(licence) and url and info.get("mime") in _MIMES):
            continue
        if int(info.get("width", 0)) < MIN_WIDTH or int(info.get("height", 0)) < MIN_HEIGHT:
            continue
        out.append(
            Candidate(
                page_id=int(p.get("pageid", 0)),
                title=str(p.get("title", "")),
                url=url,
                page=str(info.get("descriptionurl", "")),
                author=_plain(_meta(info, "Artist")),
                licence=licence.strip(),
            )
        )
    return out


def client(timeout: float = 30.0) -> httpx.Client:
    """No proxy from the environment, every connection pinned to a public address."""
    return httpx.Client(
        transport=PinnedTransport(),
        follow_redirects=False,
        timeout=timeout,
        trust_env=False,
        headers={"User-Agent": UA},
    )


def search(c: httpx.Client, query: str, limit: int = 12) -> list[Candidate]:
    params: dict[str, str | int] = {
        "action": "query",
        "format": "json",
        "generator": "search",
        "gsrsearch": f"{query} filetype:bitmap",
        "gsrnamespace": 6,
        "gsrlimit": limit,
        "prop": "imageinfo",
        "iiprop": "url|size|mime|extmetadata",
        "iiurlwidth": FETCH_WIDTH,
        "iiextmetadatafilter": "LicenseShortName|Artist",
    }
    check_public(API)
    r = c.get(API, params=params)
    r.raise_for_status()
    return parse(r.json())


class PictureRefusedError(ValueError):
    pass


def download(c: httpx.Client, cand: Candidate, dest: Path) -> Picture:
    """The scaled image, from the image server only, size-capped, proven to be a
    picture and saved as a plain JPEG (nothing else in the file survives)."""
    if urlparse(cand.url).hostname != IMAGE_HOST or not cand.url.startswith("https://"):
        raise PictureRefusedError(f"not the Commons image server: {cand.url[:80]}")
    check_public(cand.url)
    body = bytearray()
    with c.stream("GET", cand.url) as r:
        r.raise_for_status()
        for chunk in r.iter_bytes():
            body.extend(chunk)
            if len(body) > MAX_IMAGE_BYTES:
                raise PictureRefusedError("image larger than 15 MB")
    try:
        im = Image.open(io.BytesIO(bytes(body)))
        im.load()
    except Exception as e:  # noqa: BLE001 - anything that is not a picture is refused
        raise PictureRefusedError(f"not a readable picture: {type(e).__name__}") from e
    dest.parent.mkdir(parents=True, exist_ok=True)
    im.convert("RGB").save(dest, "JPEG", quality=92)
    return Picture(
        path=dest, title=cand.title, page=cand.page, author=cand.author, licence=cand.licence
    )
