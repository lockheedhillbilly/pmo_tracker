# Project Context (current engagement)

This is the current engagement's actual team, workstreams, methodology,
and conventions — filled in per `SETUP_GUIDE.md`'s six questions. Two
things read it:

1. **You** (or a future Claude Code session) — read this first to get
   oriented, instead of re-deriving it from scratch.
2. **The parser** (`nlu.py`) — this exact content is included in the
   prompt used to turn a pasted note/email into structured task changes,
   so it can infer a sensible default owner when one isn't stated.

Edit it from the dashboard's "Project" tab (or this file directly) any
time the team or structure changes — the dashboard's saved copy, once
edited there, takes precedence over this file. See `SETUP_GUIDE.md` for
the generic, reusable guidance this was filled in from.

## Team

| Person | Role | Typically owns |
|---|---|---|
| Akshit | Project Leader | Reviews progress, sets up critical meetings, sole reviewer in the review workflow. Occasionally takes hands-on work directly (e.g. UI readiness sign-off) — but is not a *default* owner for a category of work; assign to Akshit only when a note explicitly does. |
| Sparsh | Tech | Data pipelines, scoring logic, offering mapping (technical side), webhook/infra setup. |
| Abhishek | Tech | Same workstream as Sparsh — the two often pair on the same task (see "Two-owner notes" below). |
| Aayushi | Business / Testing / UI | The most cross-functional contributor: business codification, account summaries, test case validation, offering mapping (business side) — *and* Overall UI work (HTML updates, deck updates). Don't assume she's testing-only. |
| Hriday | Business / Testing | Test result review, signal validation. |
| Harsh | UI | External/occasional — UI-specific discussions, not a regular task owner. |

**Default ownership when a note doesn't name anyone:**
- Business analysis or testing work -> Aayushi and/or Hriday
- Tech/engineering work -> Sparsh and/or Abhishek
- Cross-cutting review, scheduling, or "set up a meeting" items -> Akshit

## Tracks (top-level)

`Discovery`, `Tech`, `Data`, `Milestone`

## Use cases / sub-workstreams (the `module` field)

- Account Prioritization
- Account Intelligence
- Overall UI
- Seller Copilot

(New ones can appear — the parser infers a sensible new use-case name if a
note clearly doesn't fit these.)

### Sub-module granularity

Within Account Prioritization specifically, work is further organized by
**offering / sub-offering** — e.g. "finalize offering:sub-offering mapping"
is its own line of work, distinct from the top-level module. This level
isn't a separate structured field today (it lives in the task text) — see
`SETUP_GUIDE.md`'s "Known gaps."

## Methodology

This engagement is a sales-intelligence / account-prioritization PoC:

- **Signals** feed an account-scoring engine, split into two tiers:
  - **Anchor signals** — strong, primary indicators (e.g. an active RFP).
  - **Corroborator signals** — supporting, secondary indicators (e.g. a
    firmographic match). Signals sometimes get *reclassified* between the
    two tiers as testing reveals their real predictive strength.
- **Third-party intent-data vendors** (Bombora, HG) are the external data
  sources feeding those signals.
- **Pilot accounts**: a small number of real accounts are used to validate
  that signals fire correctly and that offering mapping produces sensible
  output, before wider rollout.
- **Offering mapping**: translating a scored account into which
  product/service "offerings" to lead with — this is where sub-offering
  granularity (above) shows up.

## Timeline

A 7-week PoC. Week 1 started **Monday, 13 July 2026** (this is the
`WEEK1_MONDAY` constant in `static/dashboard.js` and `send_digest.py` —
update both if this changes), so Week 7 ends around **31 August 2026**.
No further phase-level breakdown (e.g. "Discovery = weeks 1-2") is
recorded yet.

## Conventions

- **Owner vs. collaborators**: one accountable `owner` per task, plus an
  optional comma-separated `collaborators` string for others helping —
  never combine two names into one owner string like `"A & B"` (breaks
  per-person filtering — a filter for either person should surface it).
- **Priority**: `High` / `Normal`.
- **Status**: `Open` / `Done`.
- **Execution state**: `Not started` / `In progress` / `Blocked`.
- **Review workflow**: Akshit is the sole reviewer.
- **Two-owner notes**: two people mentioned together are usually two
  separate tasks/owners, unless the note clearly describes one joint
  effort — in that case, one task with the first-named person as owner and
  the rest as collaborators.
