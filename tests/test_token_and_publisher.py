from pathlib import Path

import pytest

from cap.approval import token as tok
from cap.publisher import DryRunAdapter, PublishRefusedError, publish
from cap.schema.post_record import Channel, PostRecord, PostStatus, Verdict


def _approved_record(tmp_path: Path) -> tuple[PostRecord, Path]:
    asset = tmp_path / "ep.txt"
    asset.write_text("rendered episode")
    r = PostRecord(channel_id=Channel.A, status=PostStatus.APPROVED)
    r.asset.uri, r.asset.sha256 = str(asset), tok.sha256_file(asset)
    r.gates.originality.passed = True
    r.gates.ethics.verdict = Verdict.PASS
    r.gates.disclosure.passed, r.gates.disclosure.mfk_band = True, "clear"
    r.gates.facts.passed = True
    return r, asset


def test_token_round_trip_and_binding(tmp_path):
    r, asset = _approved_record(tmp_path)
    t = tok.issue(r.post_id, r.asset.sha256, "hemant", "approve", canary=False)
    assert tok.verify(t, r.post_id, r.asset.sha256) == (True, "ok")
    # tampering with any field breaks the signature
    bad = tok.ApprovalToken(**{**t.__dict__, "decision": "approve", "post_id": "other"})
    assert tok.verify(bad, "other", r.asset.sha256) == (False, "bad signature")
    # re-rendered file: hash mismatch
    assert tok.verify(t, r.post_id, "0" * 64)[1].startswith("asset hash")
    # an unauthorised reviewer cannot get a token at all
    with pytest.raises(PermissionError):
        tok.issue(r.post_id, r.asset.sha256, "intruder", "approve", canary=False)
    # an expired token is refused
    old = tok.ApprovalToken(**{**t.__dict__, "issued_at": "2020-01-01T00:00:00+00:00"})
    assert tok.verify(old, r.post_id, r.asset.sha256)[1] == "bad signature"  # re-signed below
    import hashlib
    import hmac

    from cap.config import get_settings

    sig = hmac.new(
        get_settings().approval_signing_key.get_secret_value().encode(),
        old.payload(),
        hashlib.sha256,
    ).hexdigest()
    old = tok.ApprovalToken(**{**old.__dict__, "signature": sig})
    assert tok.verify(old, r.post_id, r.asset.sha256) == (False, "token has expired")
    # encode/decode is lossless
    assert tok.ApprovalToken.decode(t.encode()) == t


def test_publisher_happy_path(tmp_path):
    r, asset = _approved_record(tmp_path)
    t = tok.issue(r.post_id, r.asset.sha256, "hemant", "approve", canary=False)
    adapter = DryRunAdapter()
    out = publish(r, asset, t, adapter)
    assert out.status == PostStatus.PUBLISHED
    assert adapter.uploads and out.publish.platform_post_ids["dry_run"].startswith("dry-")


@pytest.mark.parametrize(
    "break_it",
    [
        "canary",
        "not_approved",
        "gate_failed",
        "file_changed",
        "reject_token",
        "no_permit",
        "no_hash",
    ],
)
def test_publisher_refuses(tmp_path, break_it):
    r, asset = _approved_record(tmp_path)
    t = tok.issue(r.post_id, r.asset.sha256, "hemant", "approve", canary=False)
    if break_it == "canary":
        r.flags.canary = True
    elif break_it == "not_approved":
        r.status = PostStatus.AWAITING_APPROVAL
    elif break_it == "gate_failed":
        r.gates.facts.passed = False
    elif break_it == "file_changed":
        asset.write_text("someone edited the file after approval")
    elif break_it == "reject_token":
        t = tok.issue(r.post_id, r.asset.sha256, "hemant", "reject", canary=False)
    elif break_it == "no_permit":
        from cap.schema.post_record import Sponsor

        r.sponsor = Sponsor(sponsor_id="s", name="NoteCo", permit_status="applied")
    elif break_it == "no_hash":
        r.asset.sha256 = ""
    with pytest.raises(PublishRefusedError):
        publish(r, asset, t, DryRunAdapter())
    assert r.status != PostStatus.PUBLISHED


def test_canary_token_never_verifies(tmp_path):
    r, asset = _approved_record(tmp_path)
    t = tok.issue(r.post_id, r.asset.sha256, "hemant", "approve", canary=True)
    ok, reason = tok.verify(t, r.post_id, r.asset.sha256)
    assert not ok and "canary" in reason
