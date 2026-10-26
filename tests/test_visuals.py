"""Real photographs in the video (25 Oct, "the visuals are appalling"): the licence rule,
the Commons answer, the safe download, the photo scene and the renderer's wiring."""

from __future__ import annotations

import io
from pathlib import Path

import httpx
import pytest
from PIL import Image

from cap.render.scenes import Scene, layout
from cap.render.scenes.photo import photo_frame
from cap.visuals import Chooser, commons
from cap.visuals.commons import Candidate, Picture, licence_ok, parse


@pytest.mark.parametrize(
    ("name", "ok"),
    [
        ("Public domain", True), ("PD-USGov-NASA", True), ("CC0", True),
        ("CC BY 4.0", True), ("CC BY 2.0", True), ("CC-BY 3.0", True),
        ("CC BY-SA 4.0", False), ("CC BY-NC 2.0", False), ("CC BY-ND 4.0", False),
        ("CC BY-NC-SA 3.0", False), ("GFDL", False), ("", False), ("Copyrighted", False),
    ],
)  # fmt: skip
def test_only_licences_a_monetised_video_may_use(name, ok):
    assert licence_ok(name) is ok


def _page(i, licence, w=3000, h=2000, mime="image/jpeg"):  # type: ignore[no-untyped-def]
    return {
        "pageid": 100 + i, "index": i, "title": f"File:Rainbow {i}.jpg",
        "imageinfo": [{
            "thumburl": f"https://upload.wikimedia.org/x/{i}.jpg", "width": w, "height": h,
            "mime": mime, "descriptionurl": f"https://commons.wikimedia.org/wiki/File:R{i}",
            "extmetadata": {"LicenseShortName": {"value": licence},
                            "Artist": {"value": '<a href="//x">Jane &amp; Co</a>'}},
        }],
    }  # fmt: skip


def test_the_search_keeps_order_and_drops_what_cannot_be_used():
    reply = {"query": {"pages": {
        "a": _page(3, "CC BY 4.0"), "b": _page(1, "CC BY-SA 4.0"),
        "c": _page(2, "Public domain"), "d": _page(4, "CC0", w=800, h=600),
        "e": _page(5, "CC0", mime="image/svg+xml"),
    }}}  # fmt: skip
    got = parse(reply)
    assert [c.page_id for c in got] == [102, 103]
    assert got[0].author == "Jane & Co" and got[1].licence == "CC BY 4.0"


def _jpeg(w=2400, h=1600):  # type: ignore[no-untyped-def]
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (90, 140, 200)).save(buf, "JPEG")
    return buf.getvalue()


def _cand(url="https://upload.wikimedia.org/x/1.jpg"):  # type: ignore[no-untyped-def]
    return Candidate(1, "File:Rainbow.jpg", url, "https://commons.wikimedia.org/wiki/F",
                     "Jane", "CC BY 4.0")  # fmt: skip


@pytest.fixture
def offline(monkeypatch):
    monkeypatch.setattr(commons, "check_public", lambda url: None)


def test_the_download_is_proven_to_be_a_picture(tmp_path, offline):
    c = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=_jpeg())))
    pic = commons.download(c, _cand(), tmp_path / "p.jpg")
    assert Image.open(pic.path).size == (2400, 1600) and "CC BY 4.0" in pic.credit
    html = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, text="<x>")))
    with pytest.raises(commons.PictureRefusedError, match="not a readable picture"):
        commons.download(html, _cand(), tmp_path / "q.jpg")


def test_only_the_commons_image_server_is_contacted(tmp_path, offline):
    c = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=_jpeg())))
    with pytest.raises(commons.PictureRefusedError, match="image server"):
        commons.download(c, _cand("https://attacker.example/1.jpg"), tmp_path / "p.jpg")


def test_the_chooser_never_uses_a_photo_twice_and_remembers(tmp_path, monkeypatch, offline):
    found = [_cand(), Candidate(2, "File:Glacier.jpg", "https://upload.wikimedia.org/x/2.jpg",
                                "p", "Ann", "Public domain")]  # fmt: skip
    asked: list[str] = []
    monkeypatch.setattr(commons, "search", lambda c, q, limit=12: asked.append(q) or found)
    monkeypatch.setattr(commons, "client", lambda: httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, content=_jpeg()))))  # fmt: skip
    ch = Chooser("How glaciers move", tmp_path, say=lambda s: None)
    a, b = ch(["The ice flows.", "It carves valleys."])
    assert a is not None and b is not None and a.path != b.path
    n = len(asked)
    assert ch(["The ice flows."])[0] == a and len(asked) == n  # asked no one again


def _photo_scene(tmp_path: Path) -> Scene:
    p = tmp_path / "photo.jpg"
    Image.new("RGB", (2400, 1600), (40, 90, 160)).save(p)
    return Scene(kind="photo", text="Light bends.", sentences=["Light bends in the raindrop."],
                 photo=p, credit="Rainbow · Jane · CC BY 4.0 · Wikimedia Commons")  # fmt: skip


@pytest.mark.parametrize("size", [(1920, 1080), (1080, 1920)])
def test_the_photo_scene_moves_and_keeps_its_labels(tmp_path, size):
    sc = _photo_scene(tmp_path)
    lay = layout(size)
    first = photo_frame(sc, 0, 30, 0.0, lay, character="Falak", channel="B", level=0.3,
                        arabic=False)  # fmt: skip
    last = photo_frame(sc, 29, 30, 2.0, lay, character="Falak", channel="B", level=0.3,
                       arabic=False)  # fmt: skip
    assert first.size == size and first.tobytes() != last.tobytes()


def test_the_renderer_puts_photos_on_the_spoken_scenes_only(tmp_path, monkeypatch):
    from cap.render import silent_voice
    from cap.render.video import render
    from cap.schema.post_record import Channel, PostRecord

    rec = PostRecord(channel_id=Channel.B)
    rec.brief.topic, rec.draft.title = "How rainbows work", "How rainbows work"
    rec.draft.script = "Sunlight enters a raindrop.\n\nIt bends and splits into colours."
    sc = _photo_scene(tmp_path)
    seen: list[list[str]] = []

    def pictures(texts):  # type: ignore[no-untyped-def]
        seen.append(texts)
        return [Picture(sc.photo, "File:R.jpg", "p", "Jane", "CC BY 4.0") for _ in texts]

    import cap.render.scenes as scenes

    kinds: list[str] = []
    real = scenes.frame

    def spy(s, *a, **k):  # type: ignore[no-untyped-def]
        kinds.append(s.kind)
        return real(s, *a, **k)

    monkeypatch.setattr(scenes, "frame", spy)
    out = render(rec, tmp_path / "r", voice_fn=silent_voice, pictures=pictures, with_short=False)
    assert out.video.is_file() and out.short is None
    assert set(kinds) == {"photo", "sources"}  # the sources card keeps its own look
    assert seen and all(t.strip() for t in seen[0])  # only spoken scenes are asked about
