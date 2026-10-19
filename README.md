# Content Agent Portfolio

Multi-agent system that researches, drafts, checks and publishes character-led explainer videos, with a human approving every post.

Specifications live in the claude.ai Project "Social Media Monetization" (PRD, Agent Prompt Pack v1.1.2, Regression Test Sets v1.0, Master Context); the Proof design system and the UX canvas are artifacts there, with the tokens and brand book copied under `design/proof/`. This repository holds the code.

No credential is ever committed here; keys live in Secret Manager.

## Testing the solution

Three layers; each one takes a minute and needs no credentials.

**1. Mechanical** (what CI runs on every commit):

```bash
uv sync
uv run ruff check src tests && uv run mypy src && uv run pytest -q
uv run cap prompts check      # the 17 prompt calls load from the Pack version the code expects
uv run cap schema export      # the post-record JSON schema regenerates without drift
```

**2. One post end to end** (mock models, no network):

```bash
uv run cap demo
```

Runs a Patch episode through draft, critic, adapt, the four gates and the stand-in
renderer; it ends `awaiting_approval` with its history printed.

**3. The app, on your phone** (the part that matters most, D33, D34, D39):

```bash
uv run cap serve --demo
```

Open <http://127.0.0.1:8000/login> and sign in as `hemant` (`layla` for channel C). On
your phone: `uv run cap serve --demo --host 0.0.0.0`, then your laptop's address on the
same Wi-Fi; or use the browser's phone emulation. You land on the Portfolio, built
on the Proof design system (`design/proof/`): the queue strip, the four figures, a
card per channel, the next kill gate, a sources line. The demo marks its figures as
examples; without analytics the screen says "no pull yet" rather than inventing.

Then walk a card the way D33 and D34 say it goes:

| Step | What to expect |
| --- | --- |
| Queue → the oldest post | The blind card: content, claims with quotes, media. No gate, score or critic anywhere on the page (the server never sends them) |
| Approve… | Two steps: your opening line, and one random claim against its quote. The button wakes only when both are done; the server checks again |
| After the verdict | "Now, what the machines thought": the four gates and the critic, revealed; one change allowed |
| Reject | A bottom sheet of nine reason chips; one tap finishes it |
| The canary ("THEIR note apps") | Reject it with the wrong chip and finalise: the queue pauses, every screen carries the band, and only an approver can reopen it from /paused |
| Channel C | Approve is disabled until the Arabic reviewer has recorded stage one. Sign in as `layla`: Arabic chrome, right-to-left, her channel only, no tabs, never a gate |
| Canaries | Scored on the blind verdict only; the planted fault is shown once the record is closed, so the reveal cannot hand you the answer |
| Time on card | Measured by the server from the first open; a 3-second approval is flagged |

The JSON API that the screens use is at `/api` (docs at `/api/docs`); the same rules,
the same store. Walk it with the header `X-Reviewer: hemant` if you prefer curl:

| Step | Call | What to expect |
| --- | --- | --- |
| See the queue | `GET /api/queue` | Two posts; `gates` is `null` on both (blind-first, D33); each shows one spot-check claim and `expected_reading_s` |
| Approve too fast, no opening line | `POST /api/posts/{id}/verdict` `{"decision":"approve","time_on_card_s":5}` | 422: approve needs the opening line |
| Approve properly | same, with `time_on_card_s` 240, an `opening_line` and `spot_check_supported: true` | Card returns with `gates_revealed: true`; the verdict is stored and cannot be changed |
| Finalise | `POST /api/posts/{id}/finalise` `{}` | `status: approved`, `token_issued: true` (the token itself never leaves the server) |
| The canary | reject the *THEIR note apps* post with `reason_code: "language"` then finalise | `canary: missed`, `session_paused: true`; the queue returns 423 until an operator clears it. Reject it with `out_group_title` instead and it is `caught` |

The harness (`uv run cap harness run`) also runs, but with the mock backend every
judge-dependent item reports **BLOCKED** and no percentage is printed. That is the
designed behaviour: a judge-free run must not look like a pass. The Red, Green and
Made-for-Kids numbers only mean something with `CAP_MODEL_BACKEND=anthropic` and an API
key in the environment (Secret Manager in production, D31).
