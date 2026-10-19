"""Runtime settings. Everything secret arrives through the environment (locally a
.env file that is git-ignored; in production Secret Manager mounts the values).
Nothing in this module reads a key from disk or asks for one."""

from __future__ import annotations

import os
from enum import StrEnum
from functools import lru_cache

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LOCAL_DEFAULT_KEY = "local-dev-only-not-a-secret"


class ModelBackend(StrEnum):
    MOCK = "mock"  # fixture-driven, no network; the default for tests and the dry pipeline
    ANTHROPIC = "anthropic"  # production: API key from Secret Manager (D31)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CAP_", env_file=".env", extra="ignore")

    env: str = "local"
    # Foundation stand-in for Google sign-in: a reviewer identity from a header. Off
    # unless switched on explicitly, and never with the default signing key outside local.
    dev_reviewer_header: bool = False
    # D5: who may approve. The token issuer and the publisher both check this list.
    approvers: list[str] = Field(default_factory=lambda: ["hemant"])
    arabic_reviewers: list[str] = Field(default_factory=list)  # D19 stage one, channel C
    # D13 (decided 19 Oct 2026): europe-west4. Overridable per environment; a deploy
    # with an empty region fails in the validator below.
    gcp_region: str = "europe-west4"
    gcp_project: str = ""

    database_url: str = "sqlite:///cap-local.db"
    checkpoint_path: str = "cap-checkpoints.sqlite"

    model_backend: ModelBackend = ModelBackend.MOCK
    anthropic_api_key: SecretStr | None = None
    anthropic_max_budget_usd_per_post: float = 5.0  # not yet enforced; kill-gate work
    log_thinking: bool = False  # test runs: thinking.display summarized, logged by code

    # Approval token signing key (HMAC). Local default is for tests only; the validator
    # below refuses it outside local.
    approval_signing_key: SecretStr = SecretStr(LOCAL_DEFAULT_KEY)

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
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
