# Content Agent Portfolio

Multi-agent system that researches, drafts, checks and publishes character-led explainer videos, with a human approving every post.

Specifications live in the claude.ai Project "Social Media Monetization" (PRD, Agent Prompt Pack v1.1.2, Regression Test Sets v1.0, Master Context). This repository holds the code.

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

**3. The approval queue, by hand** (the part that matters most, D33 and D34):

```bash
uv run cap serve --canary
```

Then open <http://127.0.0.1:8000/docs>, send every call with the header
`X-Reviewer: hemant`. Walk the card:

| Step | Call | What to expect |
| --- | --- | --- |
| See the queue | `GET /queue` | Two posts; `gates` is `null` on both (blind-first, D33); each shows one spot-check claim and `expected_reading_s` |
| Approve too fast, no opening line | `POST /posts/{id}/verdict` `{"decision":"approve","time_on_card_s":5}` | 422: approve needs the opening line |
| Approve properly | same, with `time_on_card_s` 240, an `opening_line` and `spot_check_supported: true` | Card returns with `gates_revealed: true`; the verdict is stored and cannot be changed |
| Finalise | `POST /posts/{id}/finalise` `{}` | `status: approved`, `token_issued: true` (the token itself never leaves the server) |
| The canary | reject the *THEIR note apps* post with `reason_code: "language"` then finalise | `canary: missed`, `session_paused: true`; the queue returns 423 until an operator clears it. Reject it with `out_group_title` instead and it is `caught` |

The harness (`uv run cap harness run`) also runs, but with the mock backend every
judge-dependent item reports **BLOCKED** and no percentage is printed. That is the
designed behaviour: a judge-free run must not look like a pass. The Red, Green and
Made-for-Kids numbers only mean something with `CAP_MODEL_BACKEND=anthropic` and an API
key in the environment (Secret Manager in production, D31).
