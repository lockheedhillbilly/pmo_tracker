# Project Context

This is the living reference for how this tracker's project is organized —
team, workstreams, methodology, and conventions. Client/company names are
deliberately excluded — this file is meant to work for any engagement, not
just the one it was first written for. Two things read it:

1. **You** (or a future Claude Code session) — read this first to get
   oriented, instead of re-deriving it from scratch.
2. **The parser** (`nlu.py`) — this exact content is included in the prompt
   used to turn a pasted note/email into structured task changes, so it can
   infer a sensible default owner when one isn't stated. Edit it from the
   dashboard's "Project" tab (or this file directly) any time the team or
   structure changes; the dashboard's copy (once saved there) takes
   precedence over this file.

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
"Known gaps" below.

## Methodology (this kind of engagement, generalized)

This tracker was built for a sales-intelligence / account-prioritization
PoC. The pattern, if setting this up for a similar engagement:

- **Signals** feed an account-scoring engine, split into two tiers:
  - **Anchor signals** — strong, primary indicators (e.g. an active RFP).
  - **Corroborator signals** — supporting, secondary indicators (e.g. a
    firmographic match). Signals sometimes get *reclassified* between the
    two tiers as testing reveals their real predictive strength.
- **Third-party intent-data vendors** (e.g. Bombora, HG) are typical
  external data sources feeding those signals — expect data-download and
  integration tasks tied to whichever vendors the engagement uses.
- **Pilot accounts**: a small number of real accounts are used to validate
  that signals fire correctly and that offering mapping produces sensible
  output, before wider rollout.
- **Offering mapping**: translating a scored account into which
  product/service "offerings" to lead with — this is where sub-offering
  granularity (above) shows up.

## Timeline

A 7-week PoC. Week 1 started **Monday, 13 July 2026** (this is the
`WEEK1_MONDAY` constant in `static/dashboard.js` and `send_digest.py` —
update both if this changes for a new engagement), so Week 7 ends around
**31 August 2026**. No further phase-level breakdown (e.g. "Discovery =
weeks 1-2") is recorded yet — see "Known gaps."

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

## Known gaps (things to fix before reusing this for a genuinely different company)

- **Tracks, priorities, and statuses are hardcoded** in `db.py` (Python
  constants + SQL `CHECK` constraints), not configurable data. Onboarding
  a new company today means editing code, not filling in a form.
- **`WEEK1_MONDAY` is hardcoded** in two separate JS/Python files (see
  Timeline above) rather than being one piece of project config.
- **No structured team roster** — the team table above is prose an LLM
  interprets fresh each time, not data the code can look up directly. Fine
  at this scale; would get slower/less deterministic with a larger team or
  a longer doc.
- **No name aliasing** — "Sparsh" vs. a nickname or last name would create
  a second, fragmented owner rather than resolving to the same person.

## When this changes

If the project's team, workstreams, or timeline change, update this
document (or ask Claude to update it) with the new specifics — who's on
the team now, what the workstreams are, any sub-workstreams, and the
broad timeline. Everything downstream (the parser's default-ownership
behavior, this reference doc) follows from what's written here.
