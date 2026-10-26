"""Shared fixtures. The fake episode and the mock responses live in cap.demo so the
`cap demo` command and the tests exercise the same path."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cap.config import get_settings
from cap.demo import CHARACTER_BLOCK, SOURCES, TEST_LOG, happy_fixtures, ok, source_docs
from cap.gates.history import InMemoryHistory
from cap.models import MockClient
from cap.pipeline import PipelineDeps
from cap.schema.post_record import Brief, Channel, Lane, PostRecord

FIXTURES = Path(__file__).parent / "fixtures"

__all__ = ["SIGNIN", "laptop_wifi", "SOURCES", "TEST_LOG", "ok", "source_docs"]

# each person's laptop sign-in secret in the suite (cap.setup generates real ones)
SIGNIN = {"hemant": "hemant-test-code", "layla": "layla-test-code"}


from cap.worker import drain  # noqa: E402,F401 - re-exported for the tests


@pytest.fixture(autouse=True)
def _stop_workers():
    """Every worker a test's app started stops with the test."""
    yield
    from cap.server import stop_workers

    stop_workers()


@pytest.fixture(scope="session")
def _c2pa_dir(tmp_path_factory):
    return tmp_path_factory.mktemp("c2pa")  # one signing certificate for the whole run


@pytest.fixture(autouse=True)
def _local_settings(monkeypatch, _c2pa_dir):
    monkeypatch.setenv("CAP_C2PA_DIR", str(_c2pa_dir))
    monkeypatch.setenv("CAP_ENV", "test")
    monkeypatch.setenv("CAP_DEV_REVIEWER_HEADER", "true")
    monkeypatch.setenv("CAP_TEST_CLIENT", "true")  # X-Reviewer and the TestClient hosts
    monkeypatch.setenv("CAP_SIGNIN_SECRETS", json.dumps(SIGNIN))
    monkeypatch.setenv("CAP_ARABIC_REVIEWERS", '["layla"]')
    monkeypatch.setenv("CAP_RENDERER", "text")
    monkeypatch.setenv("CAP_VOICE", "silent")
    monkeypatch.setenv(
        "CAP_JOB_RETRIES", "0"
    )  # a failure parks at once (tests/test_worker.py retries)
    monkeypatch.delenv("K_SERVICE", raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def laptop_wifi(monkeypatch, ip: str) -> None:
    """Make `ip` this machine's Wi-Fi address for the Host check (cap.approval.hosts)."""
    try:
        from cap.approval import hosts
    except ImportError:  # the pre-fix code, when a new test is run against it
        return
    monkeypatch.setattr(hosts, "lan_ipv4", lambda: frozenset({ip}))


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
        history=InMemoryHistory(),
        anchor_elements={"A": ["test log", "day 3"]},
        character_blocks={"A": CHARACTER_BLOCK},
        test_logs={record.post_id: TEST_LOG},
        render_dir=tmp_path,
    )


@pytest.fixture
def testsets() -> dict:
    return json.loads((FIXTURES / "testsets" / "testsets-v1.0.json").read_text())


# ---- Postgres (M1, ADR-006) ------------------------------------------------------------
# CAP_TEST_DATABASE_URL names a server the tests may create databases on (CI: a Postgres
# service on Linux, the preinstalled one on Windows). Each test gets its own empty
# database, dropped afterwards. With CAP_REQUIRE_PG=1 (set in CI) a missing server fails
# the run instead of skipping, so CI can never pass without touching Postgres.


@pytest.fixture
def pg_url():
    import os
    import uuid

    import psycopg

    base = os.environ.get("CAP_TEST_DATABASE_URL", "")
    if not base:
        if os.environ.get("CAP_REQUIRE_PG") == "1":
            pytest.fail("CAP_REQUIRE_PG=1 but CAP_TEST_DATABASE_URL is not set")
        pytest.skip("no test Postgres (set CAP_TEST_DATABASE_URL)")
    name = f"cap_test_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(base, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{name}"')
    url = psycopg.conninfo.make_conninfo(base, dbname=name)
    try:
        yield url
    finally:
        with psycopg.connect(base, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


# ---- canaries (P14, review R2) ---------------------------------------------------------
# Any brief filed can become a canary once enough cards are closed, at a random gap. So
# that a test filing briefs is never surprised, canaries are off unless a test asks for
# the `canaries` fixture (tests/test_canary.py does).


@pytest.fixture(autouse=True)
def _no_surprise_canaries(request, monkeypatch):
    if "canaries" not in request.fixturenames:
        monkeypatch.setattr("cap.demo.canary_job.canary_due", lambda store, rng=None: False)


@pytest.fixture
def canaries(monkeypatch):
    """Opt in: briefs filed in this test become canaries when one is due (they are off
    for Hemant by default since 26 Oct: CAP_CANARIES)."""
    from cap.config import get_settings

    monkeypatch.setenv("CAP_CANARIES", "true")
    get_settings.cache_clear()
