"""cap youtube test: a private 5-second clip, a link on success, a plain reason on failure."""

import json

from cap.publisher import youtube


class _Req:
    def __init__(self, resp):
        self.resp = resp

    def next_chunk(self):
        return None, self.resp

    def execute(self):
        return self.resp


class _YT:
    def __init__(self):
        self.body = None

    def channels(self):
        class C:
            def list(self, **k):
                return _Req({"items": [{"snippet": {"title": "Falak Explains"}}]})

        return C()

    def videos(self):
        yt = self

        class V:
            def insert(self, part, body, media_body):
                yt.body = body
                return _Req({"id": "abc123"})

        return V()


def test_the_test_upload_is_private_and_prints_the_link(monkeypatch):
    fake = _YT()
    monkeypatch.setattr(youtube, "_service", lambda: fake)
    lines: list[str] = []
    assert youtube.test_upload(out=lines.append) == 0
    assert fake.body["status"]["privacyStatus"] == "private"
    assert fake.body["status"]["containsSyntheticMedia"] is True
    assert any("Falak Explains" in ln for ln in lines)
    assert any("https://www.youtube.com/watch?v=abc123" in ln for ln in lines)


def test_not_connected_says_where_to_connect(monkeypatch):
    def boom():
        raise RuntimeError("YouTube is not connected; open Connections in the app")

    monkeypatch.setattr(youtube, "_service", boom)
    lines: list[str] = []
    assert youtube.test_upload(out=lines.append) == 1
    assert "open Connections" in lines[-1]


def test_youtube_errors_become_advice():
    class HttpError(Exception):
        def __init__(self, reason, msg):
            self.content = json.dumps(
                {"error": {"message": msg, "errors": [{"reason": reason}]}}
            ).encode()
            self.resp = type("R", (), {"status": 403})()

    assert "youtube.com/create_channel" in youtube.explain(
        HttpError("youtubeSignupRequired", "Unauthorized")
    )
    assert "quota" in youtube.explain(HttpError("quotaExceeded", "quota"))
    assert "Data API v3" in youtube.explain(HttpError("accessNotConfigured", "not enabled"))
    plain = youtube.explain(HttpError("somethingElse", "odd"))
    assert "403" in plain and "odd" in plain
