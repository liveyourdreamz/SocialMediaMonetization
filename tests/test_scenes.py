"""The picture of an episode: scenes planned from the words of the script."""

from pathlib import Path

from cap.render import scenes


def _plan(sentences, character="Patch", **kw):
    return scenes.plan(
        sentences,
        character=character,
        title=kw.pop("title", "We tested Notely for 7 days"),
        topic=kw.pop("topic", "Notely 7-day test"),
        on_screen=kw.pop("on_screen", []),
        media=kw.pop("media", []),
        **kw,
    )


def test_patch_scenes_follow_the_story_of_the_worked_example():
    plan = _plan(
        [
            "We tested Notely for 7 days",
            "Today we test Notely, a note-taking tool, for 7 days.",
            "On day 3 the sync failed twice in our test log.",
            "Notely stores notes locally first, then syncs; the vendor says sync completes "
            "within 10 seconds on a normal connection.",
            "Our test measured 12 seconds on average.",
            "Verdict from the test log: it works, with one caveat.",
        ]
    )
    kinds = [s.kind for s in plan]
    assert kinds == ["calendar", "calendar", "app_fail", "flow", "timer", "verdict"]
    fail, flow, timer, verdict = plan[2], plan[3], plan[4], plan[5]
    assert fail.day == 3 and fail.times == 2 and fail.tool == "Notely"
    assert flow.label == "claimed: within 10 seconds"
    assert timer.number == 12.0 and timer.claimed == 10.0 and timer.mood == "measure"
    assert verdict.mood == "watch"


def test_tool_name_skips_common_words():
    assert scenes.tool_name("We tested Notely for 7 days", "Notely 7-day test") == "Notely"
    assert scenes.tool_name("Seven days with a tool", "a week") == "the tool"


def test_falak_scenes_open_from_orbit_and_show_figures():
    plan = _plan(
        [
            "Why desalination costs what it costs",
            "From orbit, the plant is a white rectangle on the coast.",
            "It produces 500 million litres a day.",
        ],
        character="Falak",
        title="Why desalination costs what it costs",
        topic="desalination",
    )
    assert [s.kind for s in plan] == ["talk", "orbit", "figure"]
    assert plan[2].number == 500 and plan[2].unit == "million"


def test_uploaded_screenshots_take_the_panel(tmp_path: Path):
    shot = tmp_path / "day3.png"
    from PIL import Image

    Image.new("RGB", (640, 400), (200, 50, 50)).save(shot)
    plan = _plan(["Title", "On day 3 the sync failed twice."], media=[shot])
    assert plan[1].kind == "media" and plan[1].media == shot


def test_every_scene_kind_draws_in_both_layouts(tmp_path: Path):
    shot = tmp_path / "s.png"
    from PIL import Image

    Image.new("RGB", (320, 200)).save(shot)
    sentences = [
        "We tested Notely for 7 days",
        "Today we test Notely for 7 days.",
        "On day 3 the sync failed twice.",
        "Notely stores notes locally first, then syncs within 10 seconds, the vendor says.",
        "Our test measured 12 seconds on average.",
        "Verdict: keep.",
        "A plain sentence with nothing to show.",
    ]
    plan = _plan(sentences, media=[shot]) + [scenes.Scene(kind="sources", text="", sentences=[])]
    for size in ((1920, 1080), (1080, 1920)):
        lay = scenes.layout(size)
        for sc in plan:
            im = scenes.frame(
                sc,
                3,
                10,
                1.5,
                lay,
                character="Patch",
                channel="A",
                level=0.5,
                arabic=False,
                sources=["https://x.example/a"],
            )
            assert im.size == size
    falak = _plan(
        ["t", "From orbit it is a dot.", "It makes 500 litres.", "Steps."],
        character="Falak",
        on_screen=["intake", "membranes", "brine"],
    )
    lay = scenes.layout((1920, 1080))
    for sc in falak:
        assert scenes.frame(
            sc, 0, 10, 0.0, lay, character="Falak", channel="B", level=0.0, arabic=False
        ).size == (1920, 1080)
    thumb = scenes.thumbnail((1280, 720), "7-day test", "Patch", "A", "Notely", False)
    assert thumb.size == (1280, 720)
