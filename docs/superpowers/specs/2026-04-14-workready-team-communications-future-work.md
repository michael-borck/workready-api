# WorkReady Team Communications — Future Work

Companion to `2026-04-14-workready-team-communications-design.md`.

This document captures non-trivial tangents raised during the brainstorming
session that were deliberately deferred out of scope. Each item is either
too big for the current project or depends on work that hasn't landed yet.
Pick any of these up as its own project when ready.

---

## G2 — Full guardrail escalation ladder

**Context:** the Team Communications project ships with **G1** guardrails —
a single-pass classifier on every outgoing student message that scores
recipient/tone/channel appropriateness and substitutes a gentle in-character
bounce-back on any flag. Infraction counts are tracked in the DB but there
are no escalating consequences beyond the bounce-back itself.

**What G2 would add:**

1. **Four-level escalation ladder**, tracked per application:
   - Level 1 — gentle in-character nudge (what G1 already does)
   - Level 2 — formal mentor notice ("I noticed your tone in the email
     to Karen — let's have a quick chat about that")
   - Level 3 — HR warning letter (written, serious register, in personal
     inbox)
   - Level 4 — termination. Application flips to `status='terminated'`,
     placement ends, student kicked back to re-application (respecting
     `MAX_CYCLES`)
2. **Warning-count DB schema** — either a new `student_warnings` table
   or columns on `applications`. Tracks infractions, levels reached, what
   triggered each.
3. **Escalation state machine** — rules for when a nudge escalates, how
   long between infractions still counts as "repeat offence", whether
   certain infractions skip straight to level 3 (e.g. overt harassment).
4. **Termination flow** — genuinely fires the student mid-placement.
   Journey report must show the termination event and reason. Lecturer
   can see the escalation history. Edge cases: terminated mid-task,
   terminated mid-lunchroom chat, terminated while an exit interview
   is already scheduled.
5. **Student-facing warning history UI** — a small "my conduct" view
   in the portal so students can see what's been flagged and why.
6. **Lecturer visibility** — journey report section surfacing the
   escalation timeline for grading/discussion purposes.

**Why deferred:**

- The pedagogical value of guardrails lives in the **bounce-back itself**,
  not the ladder. A student who sends an inappropriate email and gets a
  polite redirect learns the lesson in one hit. Ladders teach a different
  lesson (consequences accumulate over weeks) that needs repeated
  exposure — a single 20-minute placement doesn't give you that runway.
- G2 multiplies failure modes. A student gets terminated for tone
  problems the LLM classifier wasn't trained well enough to catch, and
  the system has killed their placement irrecoverably. Better to
  battle-test the classifier on G1's soft consequences first.
- G1 is forward-compatible with G2 — the classifier code doesn't change,
  G2 just adds consequence logic on top.

**Pre-requisites before starting G2:**
- G1 running live against a pilot cohort for long enough to evaluate
  classifier accuracy (false-positive rate specifically)
- Lecturer consent on the termination path — some will want a
  lecturer-in-the-loop approval before termination fires

---

## Introduce a proper pytest test framework

**Context:** WorkReady currently has no formal test suite. Every shipped
stage (5b/5c/6, journey report, lifecycle, perf review, Team
Communications) is verified via one-off Python smoke scripts that run
against `/tmp/x.db` with `LLM_PROVIDER=stub`, plus `node --check` for
portal JS and manual browser walks. The convention works at the current
scale but has real costs:

- **No regression safety net.** Shipping a new stage runs the smoke
  scripts for that stage, but no automated check confirms the previous
  stages still work. Regressions get caught by the next smoke run or
  by the operator hitting them in dev, not by an automated guard.
- **No coverage measurement.** We can't tell what code paths are
  exercised by the existing smokes and what's silently untested.
- **Smokes are throwaway.** Each one is written inline, run once, and
  never re-run. They can't be wired into CI or pre-commit.
- **Hard to onboard new contributors.** A proper test command (`uv run
  pytest`) is a standard developer experience; ad-hoc smoke scripts
  are not.

**What a pytest setup would add:**

1. **`tests/` directory** with per-stage subdirectories (`test_stage_2_resume.py`,
   `test_stage_3_interview.py`, etc.) and shared fixtures for seeding
   students, applications, tasks, lunchroom sessions.
2. **Convert existing smoke scripts to pytest tests** — each smoke
   becomes one or more `def test_*(...)` functions that use the shared
   fixtures. No loss of coverage, much easier to run.
3. **`conftest.py` with a fresh-DB fixture** that initialises a
   `/tmp/wr-test.db` per test, seeds via the existing helpers, and
   tears down on exit.
4. **A stub-LLM fixture** that monkeypatches `chat_completion` to a
   deterministic canned-response pattern per test (not just a global
   `LLM_PROVIDER=stub`). Lets individual tests control exactly what
   the LLM returns for their assertion.
5. **GitHub Actions workflow** that runs `uv run pytest` on every PR
   against workready-api. Breaks builds on regression.
6. **`pytest-cov` integration** so coverage reports land in CI output.
   Lecturer / operator can see at a glance whether a new feature has
   test coverage.

**Why deferred:** introducing a test framework mid-project is
disruptive — it forces decisions about test organisation, naming,
fixture scope, and conventions that aren't obvious on day one, and the
decisions are hard to reverse once the suite exists. Better to ship
the Team Communications project using the existing smoke pattern, then
do the pytest introduction as its own focused project where the single
deliverable is "convert existing smokes to pytest and wire up CI".
Doing it that way means the testing convention is chosen deliberately
rather than emerging accidentally from whichever stage happens to ship
first under pytest.

**Pre-requisites before starting:**
- Team Communications project shipped (so the smoke pattern is fully
  documented across every stage)
- Decision on CI provider (probably GitHub Actions since every repo
  is already on GHCR)
- Decision on stub-LLM fixture strategy (pure Python mock vs a
  recorded-response replay via VCR.py or similar)

---

## Calendar realism — student timezones, mentor-presence gating

**Context:** the Team Communications project models public holidays via
a central region-keyed `public_holidays.yaml` and per-company
`holidays_region` references (in scope). Two related realism concerns
remain out of scope:

1. **Student timezone vs. company timezone.** All times are treated as
   company-local. A student working from overseas sees "Karen replied
   at 10am" which is 10am Perth, not 10am wherever they are. Fine for
   a Curtin cohort based in Perth; less fine for remote/international
   students. Would need student-level timezone config and a display
   layer that converts.
2. **Mentor-presence gating for task feedback.** Today, task reviewer
   feedback emails fire on submission regardless of the mentor's
   availability. If a student submits on Monday and the mentor is on
   leave until Friday, they still receive feedback on Monday because
   the illusion "mentor wrote feedback while away" holds. Could be
   tightened to delay task feedback until the mentor's return, but
   that risks frustrating students with unexplained silent gaps.
   Optional future refinement.
3. **Multi-year public holiday configuration.** The holidays file is
   a flat list that needs updating annually. A future refinement could
   auto-generate from an RRULE-style description, or pull from a public
   holiday API, so the operator doesn't have to maintain it by hand.

**Why deferred:** all three are realism polish, not blockers. Student
timezones need a real decision about whether the simulation is intended
for international students. Mentor-presence gating needs a decision
about which matters more — realism or feedback latency — and that
should be informed by watching students use v1. The multi-year holiday
config is pure operator ergonomics and only matters after the simulation
has been running for more than one academic year.

---

## Dynamic team adjustment (lecturer-driven learning outcomes)

**Context:** the Team Communications project defines a student's team
statically via a `team:` field on each job listing in `brief.yaml`. Every
student hired into the same role sees the same team. This is the right
default for a first cut, but it misses an opportunity: lecturers can't
shape the placement experience to a specific learning outcome without
editing the job listing and re-running ensayo.

**What dynamic adjustment would enable:**

- **Teach escalation** — mark the mentor `on_leave` for the first three
  days so the student has to figure out who else to ask
- **Teach working with difficult colleagues** — inject a passive-aggressive
  character ("Marcus, gives minimal feedback, takes credit") and force
  the student to collaborate with them on a task
- **Teach cross-functional communication** — add a Finance character to
  a Marketing team so the student has to translate between domains
- **Teach inclusion / accommodation** — inject characters with distinct
  working styles (very quiet, extremely verbose, email-only, chat-only)
- **Difficulty tiering** — same role, friendlier team for struggling
  students, harder team for advanced ones
- **Cohort-specific scenarios** — "this semester's IronVale students all
  have a tight deadline because the mentor just returned from leave"

**What it would require:**

1. **A team resolver function with a precedence chain** (this project
   will already build the resolver with this hook in mind):
   - Check for an application-level override first
   - Else check for a cohort-level override
   - Else fall back to static `team:` from `jobs.json`
   - Else fall back to the whole `employees[]` default
2. **One additive DB column**: `applications.team_override_json TEXT NULL`.
   Stores a JSON list of character slugs (or a richer object with
   availability overrides, persona overlays, etc.).
3. **Cohort-level override mechanism**: either a new `cohort_overrides`
   table (keyed on some cohort identifier — could be a URL param on the
   student's sign-in link) or a new config file read at session start.
4. **Admin UI**: a new section of the admin page where the lecturer can
   view, edit, and preview team overrides per student or per cohort.
   Needs a character picker, a preview of what the student will see,
   and a "revert to default" button.
5. **Persona overlays for injected characters**: when a lecturer injects
   a character that isn't in the company's employees roster (e.g. a
   generic "difficult colleague" template), that character needs a
   persona prompt. Either (a) a library of generic injectable templates
   the lecturer picks from, or (b) the lecturer writes the persona
   inline at injection time.

**Why deferred:** this is genuine product work. The schema is
forward-compatible (see below) so deferring it costs nothing today, and
doing it later means you've seen how the static team model actually
behaves with real students before deciding how dynamic it needs to be.

**Schema compatibility:** the Team Communications project does NOT need
any schema changes to support dynamic adjustment later. The team resolver
is built as a single function with a clean contract; the override
precedence chain slots in as a one-column addition and a function update
when the future project ships. No refactoring of callers required.

---

## Unify the two character-content stores

**Context:** WorkReady has two separate stores of character content per company:

1. `<company>/brief.yaml` — `employees[].customisation.background` — short
   prose summary. Consumed by `setup-chatbots.py` to build the hiring desk
   AnythingLLM workspace (system prompt + RAG document).
2. `<company>/content/employees/<slug>-prompt.txt` — longer structured
   persona prompt. Consumed by the custom in-sim machinery (lunchroom chat,
   exit interview, performance review, mail.py character replies, hiring
   interview via `manager_persona`).

These were authored separately and can drift. Today the Team Communications
project manages drift by convention — persona updates touch both stores —
but the risk grows with each new cohort and each new persona.

**What a unification pass would do:**

- Single canonical store per character, probably at the `content/employees/`
  location because it's more structured
- Autogenerate the short prose summary that `brief.yaml` needs, or have
  the brief reference the prompt file directly
- Update `setup-chatbots.py` to read from the canonical store
- Update `lunchroom_chat._load_persona` and every other consumer to read
  from the canonical store
- Migrate existing content, verify no drift

**Why deferred:** the drift risk is real but not urgent, and unification is
a content-plumbing refactor with zero user-visible value. It makes sense as
cleanup once the Team Communications project has shipped and we've seen
how often personas actually get updated in practice.

---

## Split Z — Teams-style work interface rewrite

**Context:** the Team Communications project builds all functional pieces
(live chat per character, task-aware context stuffing, business hours
gating, presence states, team directory sidebar, colour-coded inboxes,
classifier bounce-backs) **inside the existing portal UI paradigm** — a
sidebar + main-content area with stacked message lists. The work inbox
and the personal inbox share the same visual style, differing only in
colour coding.

**What Split Z would do:**

Rewrite the work-side of the portal as a Microsoft Teams-style interface:

1. **Left column** — channel list with team members, group chats,
   unread badges, presence indicators
2. **Centre column** — active conversation (chat or email thread)
   rendered in a Teams/Slack-style layout with message bubbles,
   reply threading, reactions maybe
3. **Right column** — contextual panel showing the student's active
   task brief, relevant recent feedback, or the other participant's
   profile card
4. **Distinct visual language** from the personal inbox, which stays
   email-like
5. **Mobile considerations** — Teams-style UIs are notoriously hard to
   responsive-ify; need a narrow-viewport collapse strategy

**Why deferred:**

- The functional code is UI-independent. `mail.py`, `chat_completion()`,
  task-aware context stuffing, business hours gating, presence states,
  classifier — none of these change when the skin on top changes. The
  functional work ships first and is the real unlock.
- We can't usefully design the Teams shell until we've seen the
  functional pieces in place. The left-nav grouping, the channel shape,
  the context-pane content — all depend on what the live chat,
  task-awareness, and presence states actually feel like in daily use.
- Design-heavy work (lots of mockups, iteration, responsive tuning)
  that would dominate a brainstorming session if bundled with the
  functional work.

**Pre-requisites before starting Split Z:**
- Team Communications project shipped and used by a pilot cohort
- Clear picture of which UI affordances students actually use vs
  ignore (informs what the left-nav and context pane should surface)
- Decision on whether Electron/PWA wrapping is coming — the Teams
  paradigm assumes desktop-class real estate

---
