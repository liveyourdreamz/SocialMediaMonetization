"""Shared fixtures. The fake episode and the mock responses live in cap.demo so the
`cap demo` command and the tests exercise the same path."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cap.config import get_settings
from cap.demo import CHARACTER_BLOCK, SOURCES, TEST_LOG, happy_fixtures, ok, source_docs
from cap.models import MockClient
from cap.pipeline import PipelineDeps
from cap.schema.post_record import Brief, Channel, Lane, PostRecord

FIXTURES = Path(__file__).parent / "fixtures"

__all__ = ["SOURCES", "TEST_LOG", "ok", "source_docs"]


@pytest.fixture(autouse=True)
def _local_settings(monkeypatch):
    monkeypatch.setenv("CAP_ENV", "test")
    monkeypatch.setenv("CAP_DEV_REVIEWER_HEADER", "true")
    monkeypatch.setenv("CAP_ARABIC_REVIEWERS", '["layla"]')
    monkeypatch.setenv("CAP_RENDERER", "text")
    monkeypatch.setenv("CAP_VOICE", "silent")
    monkeypatch.delenv("K_SERVICE", raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def fixtures() -> dict:
    return happy_fixtures()


@pytest.fixture
def client(fixtures) -> MockClient:
    return MockClient(fixtures)


@pytest.fixture
def record() -> PostRecord:
    return PostRecord(
        channel_id=Channel.A,
        brief=Brief(
            topic="Notely 7-day test",
            format_id="tested_7_days",
            anchor_elements=["test log"],
            length_s=540,
            lane=Lane.LOW,
        ),
    )


def make_deps(client: MockClient, record: PostRecord, tmp_path: Path) -> PipelineDeps:
    return PipelineDeps(
        client=client,
        anchor_elements={"A": ["test log", "day 3"]},
        character_blocks={"A": CHARACTER_BLOCK},
        test_logs={record.post_id: TEST_LOG},
        render_dir=tmp_path,
    )


@pytest.fixture
def testsets() -> dict:
    return json.loads((FIXTURES / "testsets" / "testsets-v1.0.json").read_text())
