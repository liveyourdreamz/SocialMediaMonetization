# Content Agent Portfolio

Multi-agent system that researches, drafts, checks and publishes character-led explainer videos, with a human approving every post.

Specifications live in the claude.ai Project "Social Media Monetization" (PRD, Agent Prompt Pack v1.1.2, Regression Test Sets v1.0, Master Context); the Proof design system and the UX canvas are artifacts there, with the tokens and brand book copied under `design/proof/`. This repository holds the code.

No credential is ever committed here; keys live in Secret Manager.

## Run it on your laptop

Mac or Linux:

```bash
git clone https://github.com/liveyourdreamz/SocialMediaMonetization.git
cd SocialMediaMonetization && git checkout bootstrap
./run.sh
```

Windows: download `install.bat` from the repository, double-click it. It installs Git
and `uv` if missing, downloads the code to `%USERPROFILE%\SocialMediaMonetization`,
asks for your Anthropic API key once (typed masked, saved only to the local `.env`,
blank for mock mode), then starts the app. Run it again to update; `set-key.bat`
changes the key. The console says which model backend is in use when it starts.
`install.bat` only fetches the code and hands over to `tools\start.bat`, so an old
downloaded copy still runs the current steps. `run.ps1` does the
same from a clone you already have.

`run.sh` asks for the key the same way on a Mac. The key never goes anywhere but
`.env` and, from there, to Anthropic.

The script installs `uv` if you do not have it, creates `.env` from `.env.example`,
seeds three channels and a canary, starts the app and opens the browser at
<http://127.0.0.1:8000/login>. Sign in as `hemant` (or `layla` for channel C). On your
phone, use your laptop's address on the same Wi-Fi. Records live in `./cap-data` and
survive restarts; `./run.sh --reset` starts clean.

**The loop, end to end.** Operator tab → *File a brief* (topic, source URLs, your
test-log notes) → in the background the sources are fetched, the drafter writes,
the critic reviews, the platform cuts are made, the four gates run, the voice is
synthesised and the video rendered → the card lands in your Queue → you approve
(opening line, spot check) → the post waits in the Outbox with its token → you open
the preview, watch the video, press Publish → the plain-code publisher verifies the
token against the file and sends it: to `cap-data/published/<post id>/` by default,
or to YouTube (unlisted) once `cap youtube auth` has run and `CAP_PUBLISHER=youtube`.

What is scrappy, and what replaces it later (all behind one function each):

| Stage | Now | Later (PRD) |
| --- | --- | --- |
| Sources | URLs you paste; a fetcher pulls the text | Trend-research agent (P4) |
| Voice | Microsoft neural voices via `edge-tts`, free | ElevenLabs private voices (D21) |
| Picture | Scenes: Patch or Falak drawn from the Anchor Registry, animated and speaking in time with the voice, beside a screen panel showing what each sentence describes (the app's sync failing, a stopwatch to the measured figure, the local-then-cloud flow, the verdict stamp, a figure from the logbook) or screenshots uploaded with the brief; reconstructions are labelled; the AI-character label on every frame; a sources card | Richer character animation, credited footage, C2PA signing |
| Signing | none yet | C2PA manifest (D29) |
| Publish | local folder, or YouTube via your OAuth client | YouTube, then Meta, by the scheduler |

With the mock backend the drafter returns the sample episode; set
`CAP_MODEL_BACKEND=anthropic` and the key in `.env` for real drafts.

**Google sign-in.** `.env.example` says how: an OAuth client in your own Google Cloud
console, two values in `.env`, and your e-mail mapped to your reviewer name. The
login page then offers "Sign in with Google"; the local name box stays as a fallback
on the laptop and is refused outside local.

## Guided tour

The Portfolio screen has a **Guided tour**: one worked Patch episode (a seven-day
test of a note app) in thirteen stops, idea to published. The first nine show what
each agent produced (brief, material, character block, script, claims, critic,
platform cuts, the four gates, the rendered video); the last four you do for real
on that post: open the blind card, give your verdict, see the Outbox, publish to
the local folder. It needs no API key; the drafter uses prepared output, the voice
and video are rendered for real.

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
