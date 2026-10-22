"""22 Oct: every new post's video length comes from settings.video_length_s
(CAP_VIDEO_LENGTH_S), 30 seconds while the loop is being proven."""

import pytest

from cap.config import get_settings


@pytest.fixture
def length(monkeypatch):
    def set_(n: int) -> None:
        monkeypatch.setenv("CAP_VIDEO_LENGTH_S", str(n))
        get_settings.cache_clear()

    yield set_
    get_settings.cache_clear()


def test_the_default_is_thirty_seconds():
    get_settings.cache_clear()
    assert get_settings().video_length_s == 30


@pytest.mark.parametrize("channel", ["A", "B", "C"])
def test_every_new_brief_takes_the_setting(length, channel):
    from cap.demo.seed import new_brief

    length(45)
    assert new_brief(channel, "How cocoons work").brief.length_s == 45


def test_the_drafter_is_given_the_length(fixtures, length):
    from cap.agents import draft
    from cap.demo import CHARACTER_BLOCK, TEST_LOG
    from cap.demo.seed import new_brief
    from cap.models import MockClient
    from tests.conftest import source_docs

    length(30)
    rec = new_brief("A", "How a rainbow works")
    client = MockClient(fixtures)
    try:
        draft(rec, client, source_docs(), CHARACTER_BLOCK, TEST_LOG)
    except Exception:  # noqa: BLE001 - only the request matters here
        pass
    call1 = next(c for c in client.calls if c.spec.prompt_id == "P2" and c.spec.call == "1")
    assert '"length_s":30' in call1.user


@pytest.mark.parametrize("bad", [5, 5000])
def test_the_length_is_bounded(length, bad):
    with pytest.raises(ValueError):
        length(bad)
        get_settings()
