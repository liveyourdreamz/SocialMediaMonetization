"""Guarantees the second review's mutation testing found no test held (24 Oct): each test
here fails if the one rule it names is removed or loosened."""

from __future__ import annotations

import pytest

from cap import release
from cap.config import get_settings
from cap.domain import token as tok
from cap.domain.approval import approval_refusal
from cap.domain.canary import PastCanary, next_fault
from cap.domain.verdict import gates_cleared
from cap.policy import AI_ROLE_DISCLOSURE
from cap.publisher import LocalFolderAdapter, PublishRefusedError, publish_to
from cap.schema.enums import FaultType
from cap.schema.post_record import Sponsor
from tests.releases import approved, final_files, sealed


def test_outside_the_laptop_the_default_signing_key_is_refused(monkeypatch):
    """A second line behind the settings' own start-up checks: tokens signed with the
    published default key would be forgeable."""
    from types import SimpleNamespace

    from pydantic import SecretStr

    from cap.config import LOCAL_DEFAULT_KEY

    cloud = SimpleNamespace(env="production", approvers=["hemant"],
                            approval_signing_key=SecretStr(LOCAL_DEFAULT_KEY))  # fmt: skip
    monkeypatch.setattr(tok, "get_settings", lambda: cloud)
    with pytest.raises(RuntimeError, match="CAP_APPROVAL_SIGNING_KEY"):
        tok.issue("p1", "a" * 64, "hemant", "approve", False)


def test_no_opening_line_no_release(tmp_path):
    r = approved(tmp_path)
    r.human_contribution.text = "  "
    assert approval_refusal(r, get_settings()) == "no opening line from the approver"


def test_a_sponsor_without_a_granted_permit_is_refused(tmp_path):
    r = approved(tmp_path)
    r.sponsor = Sponsor(sponsor_id="s1", name="NoteCo", permit_status="applied")
    assert "without the UAE permit" in (approval_refusal(r, get_settings()) or "")


def test_sealing_rechecks_the_approval(tmp_path):
    r = approved(tmp_path)
    r.approval.decided_at = None  # the card was never finalised
    with pytest.raises(PublishRefusedError, match="never finalised"):
        release.seal(r, final_files(tmp_path, r))


def test_a_refusal_anywhere_sends_nothing_anywhere(tmp_path):
    """Every destination is authorised before the first upload: a tour post asked to go
    to a platform must not even make the local copy."""
    r, t, _ = sealed(tmp_path, tour=True)

    class Platform:
        name, leaves_machine = "youtube", True

        def upload(self, manifest):  # type: ignore[no-untyped-def]
            raise AssertionError("never called")

    with pytest.raises(PublishRefusedError):
        publish_to(r, t, [LocalFolderAdapter(tmp_path / "pub"), Platform()])
    assert not (tmp_path / "pub").exists()


def test_reel_platforms_publish_the_shorts_text(tmp_path):
    r, _, _ = sealed(tmp_path)
    m = release.build_manifest(r)
    for platform in ("facebook", "instagram", "tiktok"):
        assert m.text_for(platform).title == "Notely in 60s"


def test_the_published_description_carries_the_ai_disclosure(tmp_path):
    r = approved(tmp_path)
    r.draft.caption = "What a week of notes taught us."
    assert AI_ROLE_DISCLOSURE in release.description_for(r)


def test_an_open_question_on_the_record_blocks_until_acknowledged(tmp_path):
    r = approved(tmp_path)
    r.flags.needs_decision = ["made_for_kids:doubtful"]
    assert not gates_cleared(r)
    r.approval.decisions_acknowledged = ["made_for_kids:doubtful"]
    assert gates_cleared(r)


def test_a_disclosure_state_the_rules_do_not_name_fails_closed(tmp_path):
    r = approved(tmp_path)
    r.gates.disclosure.passed, r.gates.disclosure.mfk_band = False, "clear"
    assert not gates_cleared(r)


def test_a_fault_the_gates_missed_comes_round_sooner():
    """Gate-blind faults count double: here the blind one is not the oldest, yet wins."""
    faults = list(FaultType)
    oldest, blind, rest = faults[0], faults[1], faults[2:]
    past = [PastCanary(oldest, gate_blind=False), PastCanary(blind, gate_blind=True)]
    past += [PastCanary(f, gate_blind=False) for f in rest]
    assert next_fault(past, sponsored=True) == blind  # every type is open to it
