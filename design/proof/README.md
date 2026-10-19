Proof is the design system for the Content Agent Portfolio: the approval queue Hemant reviews on his phone, the stage-one card the Arabic reviewer uses for channel C, and the weekly digest. It is built for one job: a person reading machine-written content carefully, several times a week, without being steered by the machines that checked it first.

The name is the brief. A proof is what a printer sends before the run; it is also what a claim needs before it ships. Every screen in this system is one or the other.

## Principles

1. **The reviewer reads first.** The content under review is set in a reading face (`title`, `script`, `quote`); the interface around it is set in the interface face (`ui`, `label`, `button`). A reviewer should be able to tell at a glance what is the post and what is the tool.
2. **Blind by default.** Gate outcomes, near-miss scores and critic notes are not greyed out or collapsed before the verdict; they are not on the screen. Never design a "peek". The reveal screen is the only place they appear, and it asks whether they changed the reviewer's mind.
3. **Reject is cheap, approve is dear.** Reject is a `secondary` button that opens a chip sheet; one tap finishes it. Approve is a `primary` button that opens a two-step screen (an opening line, a spot check). Never equalise them: the asymmetry is the point (D34).
4. **Evidence sits beside the claim.** A claim is never shown without its source quote, source name and date. The `quote` style with its `quote-bar` is reserved for verbatim text from a source; never use it for the system's own words.
5. **State is a word and a mark, never a colour alone.** `pass` always has the check; `signal` always has the `!` or a sentence. Green and amber are close in lightness, so colour cannot be the only cue.
6. **Borders, not shadows.** Every object is lifted by `hairline` of `line` on `surface` over `ground`. There are no shadows in the system.
7. **English and Arabic are peers.** Arabic content uses the `Arabic` type group with `dir="rtl"` on the content block. The interface around it stays in the reviewer's language and direction. Mirror the `quote-bar` and the back arrow; never mirror numerals, marks or the check.
8. **One accent, used sparingly.** `accent` is for the link, the channel tag and the back arrow. Actions are `ink`. If a screen has more than three `accent` elements, something has been promoted that should not have been.

## Screens

One web app, phone-first, behind Google sign-in. Hemant has four tabs in a fixed order on the TabBar; the Arabic reviewer has one screen and no tab bar.

| Screen | Who | What it is for |
| --- | --- | --- |
| Portfolio (landing) | Hemant | The queue strip first, then StatTiles: revenue and cost per approved post, review time, canary record; a channel card each for A, B and C with cadence, average percent viewed and progress to the platform threshold; the next kill-gate review with its date and what must be true; a sources line with the pull time. Weekly figures. |
| Queue → Card | Hemant; the Arabic reviewer for channel C | The work: QueueItems oldest first; the blind card; the Reject sheet of ReasonChips; the Approve steps; the Reveal with GateResults and one revision; the paused VerdictBanner after a missed canary. |
| Digest | Hemant | The weekly note, also pushed on Sunday: approvals and rejections, reject rate beside the critic's, "other" share, pre- and post-reveal agreement, canary outcomes, the learning loop's proposals to accept or decline. |
| Operator | Hemant | Channels and cadence, allow-lists, canary schedule, the paused state, parked posts with a decide control, the runbook, the audit log. Secrets are never shown or entered. |

Not on any screen: daily views, subscriber deltas, trending topics, a publish button, gate scores before a verdict, near-miss scores anywhere but the reveal, keys or tokens.

## Voice

- Second person, plain, short. "Two things before it goes out." "Nothing has published."
- The machine reports in the passive; the person acts in the active. "Your verdict, recorded." "Keep approve." The system never says "we" about itself.
- No exclamation marks, no emoji, no praise. A caught canary is a count, not a celebration.
- Reason chips are things the reviewer saw, in their words: "Title takes a side", "A number with no source". Rule ids (R-09) and fault types (`out_group_title`) appear only in the `id` style, after the words, if at all.
- Timings are honest and visible: "4 min 10 s on the card · expected about 6 min". Never hide the clock.
- Numbers use a thin middle dot as the separator in meta lines: `5 claims · 3 clips · no sponsor`.

## Colour

- Page is `ground`; cards, sheets and inputs are `surface` with a `hairline` of `line`. `surface-sunken` only under an open sheet.
- All text is `ink` or `muted`. `muted` is for meta and labels; anything the reviewer must act on or judge is `ink`.
- The primary button and the verdict banner are `ink` filled with `on-ink` text. The thumbnail-text block is the same pair, because it previews the one piece of the post set in capitals.
- `accent` for links and the channel tag. `pass` with its check for gates passed, canaries caught, stage-one approvals. `signal` with its mark for a near miss, the paused band and the missed-canary count.
- There is no danger red. Rejecting a post is a normal outcome, not an error, so the reject button is `secondary`, not red. The only alarming state is the paused queue, and that is `signal`.
- Both themes ship. Night inverts `ground` and `ink` and lightens `accent`, `pass` and `signal` so they stay above 4.5:1 on the dark ground; their `on-` tokens go dark accordingly. Test every pairing in both.

## Typography

- `ui` (IBM Plex Sans) for the interface, `read` (Source Serif 4) for the content under review, `mono` (IBM Plex Mono) for ids and hashes. Arabic: `ui-ar` (IBM Plex Sans Arabic) and `read-ar` (Noto Naskh Arabic). All five are hosted on Google Fonts; load with one `css2` link, `display=swap`, and keep the fallback stacks as written in `tokens.json`.
- The scale is fixed: `label` 11, `meta` 13, `ui` 15, `button` 16, `heading` 22, `title` 26, `display` 28; reading: `script` 17/26, `quote` 16/22. Do not add sizes between them.
- Arabic sets one size up with more leading (`ui-ar` 16/26, `script-ar` 18/32). Do not apply the Latin sizes to Arabic text.
- `label` is uppercase and letter-spaced; nothing else is uppercase, including buttons.
- Headings use `text-wrap: balance`. The script measure is the phone width minus the gutters, which lands at 60 to 70 characters.

## Spacing, radius, borders

- The phone gutter is `space-5` (20px) on every screen. Card padding is `space-4`, sections are `space-6` apart, cards are `space-4` apart, chips are `space-2` apart.
- One radius for objects (`radius-md`, 12px), one for state surfaces (`radius-lg`, 14px), one for chips and marks (`radius-pill`), one for the sheet (`radius-sheet`). Do not introduce a fifth.
- Borders: `hairline` of `line` for structure; `stroke` of `line-strong` for meaning (secondary button, selected chip, focused input). Focus is a 2px solid `focus` ring, offset 2px, on every control.

## Iconography

- Inline stroke SVG only: 24-unit viewBox, `stroke-width` 2.2 (3 inside a 22px disc), round caps and joins, `currentColor`. Sizes 14, 16, 20. No icon font, no emoji.
- The set is small on purpose: check, close, chevron-left, and the `!` mark set as text inside a disc. Add an icon only when a word cannot do the job.
- Icon-only controls carry `aria-label`; decorative icons carry `aria-hidden="true"`.

## Layout and devices

- Design at 390 × 844 first; the queue is used on a phone, often one-handed. Primary actions live in a bottom bar, `space-5` from the edges, above the home indicator.
- A screen may be taller than the phone; it scrolls. The bottom action bar stays fixed. Never clip the script.
- Touch targets are 44px minimum; buttons are 52px; chips are 44px.
- On a wider screen the same layout sits in a 480px column, centred; do not redesign for desktop.

## Motion

- The only animated moment is the reveal: gate rows enter in order over 200ms after the verdict is recorded, so the reviewer sees that they were hidden and are now shown. Everything else cuts. Respect `prefers-reduced-motion`: the reveal then appears at once.

## Right-to-left

- Set `dir="rtl"` on the content block (title, script, caption, claims) for channel C; the chrome keeps the reviewer's direction.
- Mirror: the `quote-bar` (to the right edge), the chevron on the back control, text alignment inside the content block.
- Do not mirror: numerals and timings, the check and `!` marks, the order of the bottom action bar (Reject stays on the leading side of the reviewer's language).
- Stage-one review for channel C shows the Arabic reviewer the same card in Arabic chrome (`ui-ar`), with `dir="rtl"` on the whole screen; she is the primary reader there.

## States

- Buttons: default, pressed (`surface-sunken` fill for secondary; `ink` at 85% for primary via opacity on the fill, text unchanged), disabled (`muted` text on `surface`, `line` border, no fill), focus ring.
- Chips: unselected (`surface`, `line`), selected (`ink` fill, `on-ink` text), focus ring. One chip selected at a time on the reject sheet.
- Inputs: default (`line`), focused (`stroke` of `line-strong` plus the focus ring), filled.
- The paused queue is a full-width `signal` band at the top with `on-signal` text, above everything, until the reviewer reopens the queue. No modal.
