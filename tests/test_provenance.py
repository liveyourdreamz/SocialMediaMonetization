"""D29: the final release's videos and thumbnail carry a signed C2PA manifest saying
they are AI-generated, and the token binds the signed files."""

from __future__ import annotations

import os
import subprocess

import pytest
from PIL import Image

from cap.approval import release_job
from cap.domain import token as tok
from cap.render import provenance
from cap.render.package import Rendered
from cap.schema.post_record import ReleaseStatus
from cap.store import MemoryStore
from tests.releases import approved


def _mp4(path):
    from imageio_ffmpeg import get_ffmpeg_exe

    subprocess.run(
        [get_ffmpeg_exe(), "-loglevel", "error", "-y", "-f", "lavfi", "-i",
         "testsrc=d=1:s=160x120", "-pix_fmt", "yuv420p", "-c:v", "libx264", str(path)],
        check=True,
    )  # fmt: skip
    return path


def _png(path):
    Image.new("RGB", (64, 36), "teal").save(path)
    return path


def test_a_signed_video_says_it_is_ai_generated(tmp_path):
    video = _mp4(tmp_path / "long.mp4")
    provenance.sign_in_place(video, provenance.manifest_for("How rain forms", "p1", "B"))
    m = provenance.read(video)
    assert m is not None
    assert m["validation_state"] == "Valid"  # self-signed: valid, signer untrusted (D29)
    assert m["ai_generated"] and m["title"] == "How rain forms" and m["post_id"] == "p1"
    assert not list(tmp_path.glob("*.signing"))


def test_a_thumbnail_is_signed_and_text_files_are_skipped(tmp_path):
    thumb, text = _png(tmp_path / "thumbnail.png"), tmp_path / "p.final.txt"
    text.write_text("title")
    assert provenance.sign_release("t", "p1", "A", [thumb, text]) == ["thumbnail.png"]
    assert provenance.read(thumb)["ai_generated"]
    assert provenance.read(text) is None


def test_an_edited_file_no_longer_validates(tmp_path):
    video = _mp4(tmp_path / "long.mp4")
    provenance.sign_in_place(video, provenance.manifest_for("t", "p1", "A"))
    data = bytearray(video.read_bytes())
    data[-200] ^= 0xFF  # one byte of the picture changed after signing
    video.write_bytes(bytes(data))
    m = provenance.read(video)
    assert m is not None and m["validation_state"] == "Invalid"  # the hash no longer matches


def test_the_signing_key_is_made_once_and_kept_owner_only():
    provenance._credentials()
    folder = provenance.credentials_dir()
    _, key = provenance.credential_files(folder)
    first = key.read_bytes()
    provenance._credentials()
    assert key.read_bytes() == first
    if os.name != "nt":
        assert key.stat().st_mode & 0o077 == 0


def test_a_file_that_cannot_be_signed_fails_cleanly(tmp_path):
    bad = tmp_path / "long.mp4"
    bad.write_bytes(b"not a video")
    with pytest.raises(provenance.ProvenanceError):
        provenance.sign_in_place(bad, provenance.manifest_for("t", "p1", "A"))
    assert bad.read_bytes() == b"not a video"  # the original is untouched


def test_the_token_binds_the_signed_files(tmp_path, monkeypatch):
    store = MemoryStore()
    rec = approved(tmp_path)
    rec.asset.kind = "video"
    store.put(rec)
    files = {"long": _mp4(tmp_path / "long.mp4"), "thumbnail": _png(tmp_path / "thumbnail.png")}
    monkeypatch.setattr(release_job, "render_stage", lambda *a, **k: Rendered("video", files))
    release_job.begin(store, store.get(rec.post_id))
    job = store.jobs.claim()
    release_job.run(store, job)
    out = store.get(rec.post_id)
    assert out.release.status == ReleaseStatus.READY
    assert out.release.c2pa_signed == ["long.mp4", "thumbnail.png"]
    for f in out.release.files:  # what the token binds is the signed file on disk
        assert f.sha256 == tok.sha256_file(files[f.role])
        assert provenance.read(files[f.role])["validation_state"] == "Valid"


def test_a_signing_failure_fails_the_release_with_a_retry(tmp_path, monkeypatch):
    store = MemoryStore()
    rec = approved(tmp_path)
    store.put(rec)
    bad = tmp_path / "long.mp4"
    bad.write_bytes(b"not a video")
    monkeypatch.setattr(
        release_job, "render_stage", lambda *a, **k: Rendered("video", {"long": bad})
    )
    release_job.begin(store, store.get(rec.post_id))
    release_job.run(store, store.jobs.claim())
    out = store.get(rec.post_id)
    assert out.release.status == ReleaseStatus.FAILED and "sign" in out.release.error
    assert not out.approval.token and release_job.can_start(out)


def test_two_first_time_signers_never_mix_a_key_with_another_certificate(tmp_path, monkeypatch):
    """Review 24 Oct: the key and the certificate were written separately, so two
    first-time jobs could pair one's key with the other's certificate."""
    import threading

    from cryptography import x509
    from cryptography.hazmat.primitives import serialization

    from cap.config import get_settings

    monkeypatch.setenv("CAP_C2PA_DIR", str(tmp_path / "fresh"))
    get_settings.cache_clear()
    pairs: list[tuple[bytes, bytes]] = []
    start = threading.Barrier(6)

    def sign() -> None:
        start.wait()
        pairs.append(provenance._credentials())

    threads = [threading.Thread(target=sign) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    for chain, key in pairs:
        leaf = x509.load_pem_x509_certificates(chain)[0]
        private = serialization.load_pem_private_key(key, password=None)
        assert leaf.public_key().public_numbers() == private.public_key().public_numbers()
    assert len(set(pairs)) == 1  # one pair made, everyone uses it
