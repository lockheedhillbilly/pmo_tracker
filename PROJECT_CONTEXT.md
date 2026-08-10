# Project Context

This is the living reference for how this tracker's project is organized —
team, workstreams, and conventions. Two things read it:

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
| Akshit | Project Leader | Reviews progress, sets up critical meetings, sole reviewer in the review workflow. Not a default owner for a category of work — assign to Akshit only when a note explicitly does. |
| Sparsh | Tech | Data pipelines, scoring logic, offering mapping (technical side), webhook/infra setup. |
| Abhishek | Tech | Same workstream as Sparsh — the two often pair (see "Two-owner notes" below). |
| Aayushi | Business / Testing | Business codification, account summaries, test case validation, offering mapping (business side). |
| Hriday | Business / Testing | Same workstream as Aayushi — test result review, signal validation. |
| Harsh | UI | External/occasional — UI-specific discussions. |

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

## Timeline

A 7-week PoC, tracked by sprint/week (e.g. "Week 4").

## Conventions

- **Owner vs. collaborators**: one accountable `owner` per task, plus an
  optional comma-separated `collaborators` string for others helping —
  never combine two names into one owner string (breaks per-person
  filtering).
- **Priority**: `High` / `Normal`.
- **Status**: `Open` / `Done`.
- **Execution state**: `Not started` / `In progress` / `Blocked`.
- **Review workflow**: Akshit is the sole reviewer.
- **Two-owner notes**: two people mentioned together are usually two
  separate tasks/owners, unless the note clearly describes one joint
  effort — in that case, one task with the first-named person as owner and
  the rest as collaborators.

## When this changes

If the project's team, workstreams, or timeline change, update this
document (or ask Claude to update it) with the new specifics — who's on
the team now, what the workstreams are, any sub-workstreams, and the
broad timeline. Everything downstream (the parser's default-ownership
behavior, this reference doc) follows from what's written here.
