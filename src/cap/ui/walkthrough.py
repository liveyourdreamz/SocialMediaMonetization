"""The walkthrough on the sign-in screen: the platform's main screens, one step at a
time, in plain words, before signing in (Hemant, 25 Oct: "walk me through the main
functionality").

It reads nothing from the store and changes nothing: every picture is a small drawing of
the real screen with example content, marked as an example, so it is safe to show to
anyone who can reach the sign-in page. The hands-on tour, which runs a worked example
through the real pipeline, stays behind sign-in (cap.ui.tour).

The wording of buttons, tabs and chips here is the screens' own; tests/test_walkthrough.py
fails if a screen renames one and this does not follow.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Step:
    key: str  # also picks the drawing in walkthrough.html
    title: str
    where: str  # where it is in the app
    what: str  # what the screen is for, one or two sentences
    do: tuple[str, ...]  # what you do there, in order
    tip: str = ""


STEPS: list[Step] = [
    Step(
        "idea",
        "What the platform does",
        "The whole platform, in one line",
        "It turns topics into short explainer videos presented by Patch or Falak, checks "
        "them, and puts each one in front of you. Nothing is published unless you approve "
        "it, and you can always see why a post is where it is.",
        (
            "Ideas arrive each Monday, or you file a topic yourself.",
            "The agents write, check and make the video.",
            "You read it and decide.",
            "You choose where it goes, and publish.",
        ),  # fmt: skip
        "You only ever do two things: pick topics, and decide on posts. Everything in "
        "between is the platform's job.",
    ),
    Step(
        "portfolio",
        "Home: how things stand",
        "The Portfolio tab, the first screen after you sign in",
        "One look tells you whether anything needs you. The top strip shows how many posts "
        "are waiting and roughly how long they will take to review; below it, money in and "
        "out per post, your review time and each channel's progress.",
        (
            "If the strip says posts are waiting, tap it to go to the queue.",
            "Otherwise there is nothing you need to do today.",
        ),  # fmt: skip
        "Figures marked as examples are illustrations until the channels have real data.",
    ),
    Step(
        "digest",
        "This week's ideas",
        "The Digest tab, refreshed every Monday at 08:00",
        "Fifteen ideas per channel, each with why people are curious about it this week "
        "and the good-news angle behind it. Only ideas you pick become posts.",
        (
            "Read the idea; edit the title if you want a different angle.",
            "Tap Pick to turn it into a post, or Not this one to drop it.",
            "Ideas you leave alone disappear next Monday.",
        ),  # fmt: skip
        "Picking about four a week per channel keeps the pilot cadence (eight a month).",
    ),
    Step(
        "operator",
        "Your own topic",
        "The Operator tab, at the top",
        "When you already know what you want, file it yourself: choose the channel (or let "
        "the platform choose Patch or Falak) and type the topic. The post appears under "
        "Your posts with a progress bar while the agents work.",
        (
            "Choose the channel and type a topic.",
            "Tap Create the post.",
            "Come back in a few minutes; the post moves to your queue by itself.",
        ),  # fmt: skip
        "Operator is also where settings, parked posts and alerts live. You rarely need "
        "the rest of it.",
    ),
    Step(
        "queue",
        "The queue",
        "The Queue tab; the number on it is how many are waiting",
        "Every post that is ready for you, oldest first, with how long it takes to read.",
        ("Tap the oldest post to open it.",),
        "While a post has waited more than a day, no new drafts start, so nothing piles "
        "up while you are away.",
    ),
    Step(
        "card",
        "Reading a post",
        "The card, after you tap a post in the queue",
        "The video, the script and every claim with the quote from its source. You decide "
        "first, before seeing what the automatic checks thought, so they cannot lead you.",
        (
            "Watch the video or read the script.",
            "Check the claims against their quotes.",
            "Ask yourself: would I defend this on camera?",
        ),  # fmt: skip
        "Read properly: the card notices a verdict given faster than the post can be read.",
    ),
    Step(
        "verdict",
        "Your verdict",
        "The two buttons at the bottom of the card",
        "Rejecting is one tap and a reason. Approving takes two small steps on purpose: "
        "you write the opening line the character says, and you confirm one claim against "
        "its source.",
        (
            "To reject: tap Reject, pick the reason, tap Reject with this reason.",
            "To approve: tap Approve…, write the opening line, answer the one claim, tap Approve.",
            "Not sure? Escalate, with a reason, sets it aside under Parked posts on Operator.",
        ),  # fmt: skip
        "Rejecting is normal and costs you nothing; the platform learns from your reasons.",
    ),
    Step(
        "reveal",
        "After your verdict",
        "The same card, once you have decided",
        "Now the automatic checks are shown: originality, ethics, disclosure and facts. "
        "You may change your mind once. Some posts are deliberate test posts with one "
        "planted mistake; the card tells you after your verdict whether you caught it.",
        (
            "Read what the checks found.",
            "Keep your verdict, or change it once.",
            "If you missed a test post, the queue pauses until you have read what you missed.",
        ),  # fmt: skip
        "Test posts are never published, and they look like any other post on purpose: "
        "they keep everyone's attention honest.",
    ),
    Step(
        "outbox",
        "Publishing",
        "The Outbox, on the Operator tab, once an approved post's final video is ready",
        "The final video with your opening line, and the places it can go. YouTube and the "
        "others are offered only once you have connected them. A copy is always kept on "
        "this laptop.",
        (
            "Open the post in the Outbox.",
            "Tick where it should go.",
            "Tap Publish now.",
        ),  # fmt: skip
        "Only exactly what you approved can be published; any change after your approval "
        "is refused.",
    ),
    Step(
        "attention",
        "When something needs you",
        "Bands at the top of every screen, and Alerts on the Operator tab",
        "The platform never stops silently. A band at the top says when the queue is "
        "paused or new drafts are waiting for you, and why. Alerts list anything that "
        "failed after its retries.",
        (
            "Read the band and follow its link.",
            "On Operator, Alerts and Parked posts say what happened and what to do.",
            "A post that stopped on a failure, such as a dropped connection, has a Try "
            "again button: it redoes only the step it stopped on.",
        ),  # fmt: skip
        "After signing in, the hands-on tour on the Portfolio walks one example post "
        "through every step for real, publishing to a folder on this laptop only.",
    ),
]


def step(n: int) -> Step:
    """Step n, counting from 1; IndexError outside 1..len(STEPS)."""
    if not 1 <= n <= len(STEPS):
        raise IndexError(n)
    return STEPS[n - 1]
