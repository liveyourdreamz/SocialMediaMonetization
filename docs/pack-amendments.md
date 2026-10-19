# Prompt Pack amendments raised by the build

Items the code needed that the Agent Prompt Pack v1.1.1 did not say, or said
differently. **All fourteen were accepted by Hemant on 19 October 2026** and are now
section 19 of the Agent Prompt Pack v1.1.2 (PRD decision D36); the prompt text and
examples in the Pack are corrected where an amendment touched them (A2, A9, A13), and
the extracted prompts in `src/cap/prompts/pack/` come from v1.1.2. This file stays as
the build-side record.

| # | Where | What the code does | Why |
| --- | --- | --- | --- |
| A1 | P10 call C, shared blocks | Call C receives the untrusted-text rule and the fail-closed rule but not the pipeline context block. | The Pack says exceptions are stated in the prompt's own text; call C's text says nothing, and the Pack's intent ("told nothing about the pipeline") still needs the security rules because C reads a fetched page. |
| A2 | P10 calls B and C output | The per-claim status field is `claim_status`, not `status`. | `status` collides with the envelope's `status` (`ok` or `blocked`). The Pack's examples emit `"status":"supported"`, which cannot validate. |
| A3 | Rulebook R-05, R-11 | `judged_by: both`: code runs first, the judge is a second net. | The Pack's P1 section lists R-05 among the hard rules code runs first; R3 gives R-05 and R-11 code detectors. |
| A4 | Rulebook R-06, R-14 | Split into R-06a (code: flag and label) / R-06b (judge: real person, crisis, minor, non-consenting adult) and R-14a (judge: this post's imagery) / R-14b (ops: 30-day drift). | R3's R-06 first clause is an action, not a judgement; the P1 prompt's own example expects the judge to assess R-14 from the manifest. |
| A5 | Rulebook R-10 | The sponsor-verdict rule (no Keep/Recommend/Skip on the sponsor's product) stays in the P1 interpretations and the Sponsor record, not in the rule text. | The rule text is counsel's and Hemant's; the pipeline must not add rules without a decision ID. |
| A6 | Rulebook R-20 | "There is no override." | R3's "two people" cannot exist with a sole approver (D5). |
| A7 | Section 1, max_tokens | High-effort calls start at 32k output tokens and retry once at 4x on a max_tokens stop. | Adaptive thinking counts toward max_tokens; 8k is exhausted before any JSON at effort high. |
| A8 | Section 2, envelope | `injection_suspected`, `explanation` and `blocked_reason` are required keys, not defaulted. | "Code rejects any output missing them" is only true if the schema requires them. |
| A9 | P14 / D34 chips | One `FaultType` enum (seven values) is shared by the canary writer, the planted-fault record and the reviewer's reason chips. | A catch counts only when the chip matches the planted type; different vocabularies would never match. |
| A10 | P1 aggregation | Judge A fail + judge B pass is a fail (the Pack: any fail fails) and is also recorded as a disagreement; the PRD 4.4 wording "disagreement escalates" applies only when neither judge failed. | The stricter reading; nothing fails one judge and still publishes. |
| A11 | PRD 3 "a post that fails is revised once, then discarded" | The gates reject on first failure; the one revision pass is the critic's (PRD agent 5), not a gate re-run. | A gate re-run needs the rejected draft to go back through the Drafter with the gate findings; built with the real renderer in week 2. |
| A12 | P10 call C | Call C runs only on claims call B supported; on the record its status stays `pending` for the others. | C cannot add support, so a C run on a B-unsupported claim changes nothing; halves the verifier cost. |
| A13 | P9 | The factor table with weights travels as a static block; `strength` and `intended_audience_note` are accepted and stored, not scored. | The Pack's P9 examples use factor ids the code does not weight; the block makes the ids the model returns the ids the code scores. |
| A14 | PRD 4.12 originality | Checked on the draft and on every platform cut; the anchor requirement applies to the post, the similarity check to every cut. The lexical stand-in catches verbatim reuse only (S4 paraphrase is a known miss until embeddings). | PRD example: a Short reusing a cold open must fail. |
