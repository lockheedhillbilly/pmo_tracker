# Setup Guide — Onboarding a New Engagement

This tracker's behavior (the parser's default-ownership inference, the
dashboard's "Project" tab) is driven by `PROJECT_CONTEXT.md` — a
case-specific document describing the *current* engagement's team,
workstreams, and conventions. This file is the opposite: engagement-
agnostic guidance for filling in (or rewriting) `PROJECT_CONTEXT.md` for
a new case. Nothing in this file should ever need a company or client
name — if it does, it belongs in `PROJECT_CONTEXT.md` instead.

## What PROJECT_CONTEXT.md needs to answer

Ask the person setting this up directly — don't guess. A short intake
conversation covering these six points is enough for a first draft;
refine as the engagement runs.

1. **Team roster** — who's on it, their role/workstream, and (critically)
   what kind of work they should be the *default* owner for when a note
   doesn't name anyone. Ask directly: "if I don't know who did something,
   who's the safe default guess for [X] kind of work?"
2. **Tracks** (top-level categories) — "what are the 3-6 broad buckets
   you'd sort any piece of work into?" (`Discovery/Tech/Data/Milestone`
   is one prior example, not a universal default.)
3. **Modules / use-cases** (the actual workstreams) — "what are the named
   workstreams or deliverables this project is organized around?", and
   whether any of them have sub-workstream granularity worth tracking
   separately (e.g. "offering / sub-offering" in one prior engagement).
4. **Timeline** — start date, planned duration, and phase boundaries if
   any exist (e.g. "weeks 1-2 are discovery, weeks 3-5 are build").
5. **Methodology, if relevant** — does this engagement have its own
   scoring/prioritization/qualification methodology worth the parser
   understanding? Not every engagement will have one — skip if not
   applicable, don't force-fit a prior engagement's methodology.
6. **Conventions** — anything about how tasks should be structured that
   isn't obvious: how joint work gets represented (owner vs.
   collaborators), what the priority/status/execution-state values mean,
   who reviews what.

## Known gaps in this tool

Fix these before reusing the tool for a genuinely different company —
right now they require a code change, not just a new `PROJECT_CONTEXT.md`:

- **Tracks, priorities, and statuses are hardcoded** in `db.py` (Python
  constants + SQL `CHECK` constraints), not configurable data.
- **`WEEK1_MONDAY`** (the timeline anchor) is hardcoded in two separate
  places (`static/dashboard.js` and `send_digest.py`) instead of being
  one piece of config.
- **No structured team roster** — `PROJECT_CONTEXT.md`'s team table is
  prose an LLM interprets fresh each time, not data the code looks up
  directly. Fine at small scale; would degrade with a larger team or a
  much longer doc.
- **No name aliasing** — a nickname or last name creates a second,
  fragmented owner rather than resolving to the same person.
