"""22 Oct: the adapter (P7) parked a post because the script carried
[[HEMANT_OPENING_LINE]] and nothing told it the token is intentional. Every prompt
that is handed script text must be told; and no prompt may describe Patch as the old
tool tester (D42), which made models judge his posts against the wrong brief."""

import pytest

from cap.prompts import SPECS, system_prompt

SLOT = "[[HEMANT_OPENING_LINE]]"
# prompts that receive a script or text cut from one
SEES_SCRIPT = [
    ("P1", "A"),
    ("P1", "B"),
    ("P2", "1"),
    ("P2", "2"),
    ("P3", "default"),
    ("P7", "default"),
    ("P9", "default"),
    ("P14", "default"),
]


def test_the_list_covers_real_specs():
    for key in SEES_SCRIPT:
        assert key in SPECS, key


@pytest.mark.parametrize("key", SEES_SCRIPT)
def test_every_prompt_that_sees_a_script_is_told_the_slot_is_intentional(key):
    text = system_prompt(SPECS[key], "n0nce")
    assert SLOT in text and "intentional" in text, key


@pytest.mark.parametrize("key", SEES_SCRIPT)
def test_a_short_length_is_never_a_reason_to_stop(key):
    assert "never by itself a reason to block" in system_prompt(SPECS[key], "n0nce")


@pytest.mark.parametrize("key", sorted(SPECS))
def test_no_prompt_describes_the_old_patch(key):
    text = system_prompt(SPECS[key], "n0nce")
    for stale in ("AI tools and software tutorials", "Tested for 7 days", "testable over 7 days"):
        assert stale not in text, (key, stale)
