"""Runtime settings. Everything secret arrives through the environment (locally a
.env file that is git-ignored; in production Secret Manager mounts the values).
Nothing in this module reads a key from disk or asks for one."""

from __future__ import annotations

import os
from enum import StrEnum
from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LOCAL_DEFAULT_KEY = "local-dev-only-not-a-secret"


class ModelBackend(StrEnum):
    MOCK = "mock"  # fixture-driven, no network; the default for tests and the dry pipeline
    ANTHROPIC = "anthropic"  # production: API key from Secret Manager (D31)


# The app writes .env as UTF-8 (cap.setup). Said explicitly: a pydantic-settings that
# falls back to the locale reads it as cp1252 on Windows, and an Arabic reviewer name or
# a smart quote then breaks start-up. (cap.setup also rewrites a cp1252 .env as UTF-8.)
ENV_FILE_ENCODING = "utf-8"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CAP_",
        env_file=".env",
        env_file_encoding=ENV_FILE_ENCODING,
        extra="ignore",
    )

    env: str = "local"
    # The laptop sign-in (a per-person secret typed on /login) when Google sign-in is
    # not set up. Off unless switched on (`cap serve` does); refused outside local.
    dev_reviewer_header: bool = False
    # D5: who may approve. The token issuer and the publisher both check this list.
    approvers: list[str] = Field(default_factory=lambda: ["hemant"])
    arabic_reviewers: list[str] = Field(default_factory=list)  # D19 stage one, channel C
    # D13 (decided 19 Oct 2026): europe-west4. Overridable per environment; a deploy
    # with an empty region fails in the validator below.
    gcp_region: str = "europe-west4"
    gcp_project: str = ""

    # Postgres everywhere, never SQLite (D45, ADR-006). Empty: the laptop's own Postgres,
    # which the app starts from the portable binaries install.bat put in place
    # (cap.localpg). A URL (Cloud SQL later, or CI's server) is used as given.
    database_url: str = ""
    pg_bin: str = ""  # where initdb and pg_ctl live; empty: found as cap.localpg describes
    pg_port: int = Field(default=54329, ge=1024, le=65535)
    # ADR-011: how long audit entries are kept (CAP_AUDIT_RETENTION_DAYS); 90 until
    # counsel confirms the period. Older entries are removed at start-up.
    audit_retention_days: int = Field(default=90, ge=1, le=3650)

    # Google sign-in (OIDC). When both are set, /login offers "Sign in with Google" and
    # the e-mail must map to a reviewer below. Values come from .env locally and Secret
    # Manager in production; never from code or chat.
    google_client_id: str = ""
    google_client_secret: SecretStr | None = None
    reviewer_emails: dict[str, str] = Field(default_factory=dict)  # e-mail -> reviewer name

    # Where the laptop build keeps its records (posts, verdicts, published files).
    data_dir: str = "cap-data"

    # The media renderer: "video" (voice + typographic frames + ffmpeg) or "text" (the
    # Foundation stand-in, used by the tests). The voice: "edge" (Microsoft neural
    # voices, free) or "silent" (offline, for tests and dry runs).
    renderer: Literal["video", "text"] = "video"
    # D41 (21 Oct 2026): the drafter writes from the topic and its own knowledge; the
    # research step then looks for a source for each claim. "flag": a claim no source
    # confirms is shown on the card as unconfirmed and Hemant decides. "block": any
    # unconfirmed claim rejects the post (the PRD's original agent-10 guardrail).
    fact_mode: Literal["flag", "block"] = "flag"
    # 22 Oct 2026: the most factual claims one post may make (CAP_MAX_CLAIMS in .env).
    # The drafter is told the limit when it writes and when it lists claims; a draft
    # over it is rewritten once to fit, then parked with the count if still over.
    max_claims: int = Field(default=10, ge=1, le=50)
    # 22 Oct 2026: the target length of every new post's video, in seconds
    # (CAP_VIDEO_LENGTH_S in .env). 30 while the whole loop is being proven end to end;
    # the drafter writes to it at the character's speaking rate and the critic checks it.
    video_length_s: int = Field(default=30, ge=15, le=1800)
    voice: Literal["edge", "silent"] = "edge"

    # Where approved posts go: "local" (a folder under data_dir) or "youtube" (a real
    # upload through the Google OAuth client; `cap youtube auth` first).
    publisher: Literal["local", "youtube"] = "local"
    youtube_privacy: Literal["unlisted", "private", "public"] = "unlisted"

    model_backend: ModelBackend = ModelBackend.MOCK
    anthropic_api_key: SecretStr | None = None
    # What one post's model calls should cost at most (D44): the PRD's $500 a month over
    # about 40 posts. A post over it is flagged on the card, never stopped; the hard cap
    # is the spend limit on the API key itself.
    post_budget_usd: float = Field(default=12.5, gt=0)
    log_thinking: bool = False  # test runs: thinking.display summarized, logged by code

    # Approval token signing key (HMAC). Local default is for tests only; the validator
    # below refuses it outside local.
    approval_signing_key: SecretStr = SecretStr(LOCAL_DEFAULT_KEY)
    # The laptop sign-in: person -> their own secret, generated into .env on first run
    # (cap.setup). The secret alone identifies the person; no name is typed.
    signin_secrets: dict[str, SecretStr] = Field(default_factory=dict)
    # Test suite only: honour the X-Reviewer header and the TestClient host names.
    # Has no effect unless CAP_ENV=test, so `cap serve` (local) never honours it.
    test_client: bool = False

    @property
    def test_client_on(self) -> bool:
        return self.test_client and self.env == "test"

    # The address the platforms send OAuth callbacks to; set when the app is reached
    # through a proxy or a public host. Empty: built from the request.
    public_base_url: str = ""

    # D17: generation pauses when the queue has been idle this long.
    queue_idle_pause_hours: int = 72

    # D15 pilot cadence.
    longform_per_channel_per_month: int = 8
    shorts_cap_per_channel_per_month: int = 4

    # Gate 7 thresholds live in the rulebook (R-13 params), one source of truth.

    # D33/D34 reviewer settings.
    blind_first_enabled: bool = True
    blind_review_point_cards: int = 200
    reject_other_share_flag: float = 0.20

    # Canary schedule (P14): (one in N, for the first M cards); the last entry runs
    # until a miss resets the schedule. Front loaded: 1 in 3 for 15 cards, 1 in 5 for
    # the next 85, then 1 in 10.
    canary_schedule: list[tuple[int, int]] = Field(
        default_factory=lambda: [(3, 15), (5, 100), (10, 0)]
    )

    channels: list[str] = Field(default_factory=lambda: ["A", "B", "C"])
    channel_characters: dict[str, str] = Field(
        default_factory=lambda: {"A": "Patch", "B": "Falak", "C": "Falak"}
    )
    channel_languages: dict[str, str] = Field(
        default_factory=lambda: {"A": "en", "B": "en", "C": "ar"}
    )
    # The day channels A and B went live; the kill-gate reviews (PRD 8) count from it.
    pilot_start: str = ""  # ISO date, empty until go-live

    @model_validator(mode="after")
    def _production_guards(self) -> Settings:
        """A deploy outside local fails here, not in a log line nobody reads. A managed
        runtime (Cloud Run sets K_SERVICE, App Engine sets GAE_ENV) is production
        whatever CAP_ENV says, so a forgotten variable cannot fall open."""
        if os.environ.get("K_SERVICE") or os.environ.get("GAE_ENV"):
            if self.env in ("local", "test"):
                self.env = "prod"
        if self.env in ("local", "test"):
            return self
        key = self.approval_signing_key.get_secret_value()
        if key == LOCAL_DEFAULT_KEY or len(key) < 32:
            raise ValueError("CAP_APPROVAL_SIGNING_KEY must be set (32+ chars) outside local")
        if not self.gcp_region:
            raise ValueError("CAP_GCP_REGION must be set before deploying (D13)")
        if self.model_backend == ModelBackend.ANTHROPIC and self.anthropic_api_key is None:
            raise ValueError("CAP_ANTHROPIC_API_KEY is required with the anthropic backend")
        if self.dev_reviewer_header:
            raise ValueError("CAP_DEV_REVIEWER_HEADER is for local runs only")
        if not self.google_client_id or self.google_client_secret is None:
            raise ValueError(
                "Google sign-in (CAP_GOOGLE_CLIENT_ID/SECRET) is required outside local"
            )
        return self

    @property
    def google_enabled(self) -> bool:
        return bool(self.google_client_id and self.google_client_secret)


@lru_cache
def get_settings() -> Settings:
    return Settings()
