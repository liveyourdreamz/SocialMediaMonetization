"""Runtime settings. Everything secret arrives through the environment (locally a
.env file that is git-ignored; in production Secret Manager mounts the values).
Nothing in this module reads a key from disk or asks for one."""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class ModelBackend(StrEnum):
    MOCK = "mock"  # fixture-driven, no network; the default for tests and the dry pipeline
    ANTHROPIC = "anthropic"  # production: API key from Secret Manager (D31)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CAP_", env_file=".env", extra="ignore")

    env: str = "local"
    # D13: Google Cloud region is still an open decision; the placeholder makes any
    # deploy fail loudly rather than silently pick a default.
    gcp_region: str = "TODO-D13"
    gcp_project: str = ""

    database_url: str = "sqlite:///cap-local.db"
    checkpoint_path: str = "cap-checkpoints.sqlite"

    model_backend: ModelBackend = ModelBackend.MOCK
    anthropic_api_key: SecretStr | None = None
    anthropic_max_budget_usd_per_post: float = 5.0

    # Approval token signing key (HMAC). Local default is for tests only; production
    # must set CAP_APPROVAL_SIGNING_KEY from Secret Manager.
    approval_signing_key: SecretStr = SecretStr("local-dev-only-not-a-secret")

    # D17: generation pauses when the queue has been idle this long.
    queue_idle_pause_hours: int = 72

    # D15 pilot cadence.
    longform_per_channel_per_month: int = 8
    shorts_cap_per_channel_per_month: int = 4

    # Gate 7 thresholds (PRD 4.12). Code constants, changed only with a signed decision.
    originality_similarity_block: float = 0.85
    originality_skeleton_cap: float = 0.30
    originality_history_days: int = 90

    # D33/D34 reviewer settings.
    blind_first_enabled: bool = True
    blind_review_point_cards: int = 200
    reject_other_share_flag: float = 0.20

    # Canary schedule (P14): one in N approved-post slots, front loaded.
    canary_schedule: list[int] = Field(default_factory=lambda: [3, 5, 10])

    channels: list[str] = Field(default_factory=lambda: ["A", "B", "C"])


@lru_cache
def get_settings() -> Settings:
    return Settings()
