"""The strict base every record part and model-output envelope shares."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class Strict(BaseModel):
    """Base with additionalProperties=false, as the structured-output rules require.
    It is shared with the model-output envelopes (cap.agents.outputs), so it stays
    strict: records stored on disk are read through cap.schema.migrate.load_record,
    which tolerates fields an older or newer build wrote."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True, validate_assignment=True)
