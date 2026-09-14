# Auth Migration Plan — Email Identity → Contractor Codes

> Historical design plan. The current implementation uses opaque expiring
> sessions stored as hashes, rather than JWTs. See `workready_api/auth.py`,
> the API README and `workready-deploy/PRIVACY-RELEASE.md`. Do not treat older
> privacy guarantees or endpoint examples below as the current contract.

> Goal: remove all real-world PII from the system. Students authenticate with an
> issued access code ("contractor code"); the code→student mapping lives only in
> the lecturer's own records (LMS/spreadsheet), never on this server.
> Pattern proven in cloudcore/sim-booking-api (`badges` + fail-closed redemption).
>
> **No data migration is required** — there are no users. We reset the schema
> rather than migrate it. Any existing dev/prod DB is disposable.

## Current state (one paragraph)

Identity = bare email typed into the portal, passed as a path/form param on every
call with no auth. First call materialises a `students` row keyed on email;
name is derived from the email local part (`_name_from_email`, `app.py:629`).
Legacy denormalised `student_email` columns exist on `applications` and
`messages`. Admin is a shared static bearer token. No SMTP exists; all "email"
is in-app fiction.

## Target model

| Concern | Today | After |
|---|---|---|
| Credential | email string (guessable) | issued code `WR-XXXX-XXXX` (CSPRNG, ~10¹² keyspace) |
| Stored identity | email + derived name | random code + generated fictional handle; optional self-declared display name |
| Session | none | signed token, `Authorization: Bearer`, configurable TTL |
| Code→human mapping | n/a | lecturer's offline CSV/LMS only |
| Signup gate | none (any string accepted) | possession of an active issued code (fail-closed; unknown codes never auto-create) |
| SMTP needed | no | still no |

Code format mirrors cloudcore: prefix + 2×4 chars from unambiguous alphabet
`ABCDEFGHJKMNPQRSTUVWXYZ23456789` (no 0/O, 1/I/L). Deliberately **not** a hash
of anything — hashes of emails are trivially enumerable against a known cohort.

---

## Phase 1 — API core: schema, auth, session

### 1a. Schema change (clean break)

In `workready_api/db.py` schema block (~lines 27–34):

```sql
CREATE TABLE IF NOT EXISTS codes (
    code        TEXT PRIMARY KEY,            -- 'WR-XXXX-XXXX'
    cohort      TEXT NOT NULL DEFAULT 'default',
    active      INTEGER NOT NULL DEFAULT 1,
    issued_at   TEXT NOT NULL,
    redeemed_at TEXT,                        -- first successful login
    note        TEXT                         -- lecturer memo; never identity
);

CREATE TABLE IF NOT EXISTS students (
    id            TEXT PRIMARY KEY,          -- uuid4 (already uuid-style today)
    code          TEXT UNIQUE NOT NULL REFERENCES codes(code),
    handle        TEXT UNIQUE NOT NULL,      -- fictional in-sim mailbox, e.g. 'wr4xkq9m2t@student.campus.workready.au'
    display_name  TEXT,                      -- OPTIONAL self-declared; NULL until student sets it
    created_at    TEXT NOT NULL,
    last_login_at TEXT
);
```

Remove from other tables (fresh schema, not migrations):
- `applications.student_email` (db.py:52) and its write sites (db.py:609–611, 1071–1073)
- `messages.student_email` legacy column intent — keep `sender_email` /
  `recipient_email` but they now hold **fictional** addresses only
  (`noreply@workready.eduserver.au` defaults already fictional, db.py:420–428)

Because there are no users: delete any `workready.db*` files, let `init_db()`
create the new schema. Do **not** extend `_migrate()` for this — document in
SYSTEM.md that release N+1 requires a fresh volume (deploy note in Phase 6).

### 1b. New module `workready_api/auth.py`

- `_normalize_code(raw)` — strip, upper, validate `^WR-[A-Z]{4}-[A-Z]{4}$`-ish pattern
- `_make_handle(code)` — deterministic fictional mailbox from code chars
- `login(code)` flow:
  1. normalize; lookup `codes`; **fail closed** — unknown code ⇒ generic 401
     (same message for unknown/inactive: anti-enumeration, cf. cloudcore
     `routes/session.py:118`)
  2. inactive ⇒ same 401
  3. `get_or_create_student(code)` (rekeyed, below) + `mark_student_login`
     stamps `codes.redeemed_at` on first use
  4. issue token, return `{token, student:{id, handle, display_name}}`
- Token: HS256 JWT via **PyJWT** (only new dep; small, well-audited). Claims:
  `{typ:"student", sid:<students.id>, exp}` — **no code, no PII**, so a leaked
  token deanonymises nothing beyond what `sid` rows already show.
- `get_current_student` FastAPI dependency: parse `Authorization: Bearer`,
  verify signature + `typ=="student"`, load student row, attach to request.
  Missing/expired ⇒ 401.
- In-process login rate limiter (stdlib deque sliding window, per-IP):
  default 5 failures/min ⇒ 429. No new deps, fits the "no workers" philosophy.
- Fail closed when `WORKREADY_SESSION_SECRET` unset: `/auth/login` returns 503,
  mirroring the existing admin-token behaviour (`admin.py:54`).

### 1c. Rekey db helpers

- `get_or_create_student(email, name)` → `get_or_create_student(code)` — creates
  the `students` row joined to the (already-validated) code; drops
  `_name_from_email` (`app.py:629`) entirely.
- `mark_student_login` unchanged, plus set `codes.redeemed_at` if NULL.
- Welcome message dispatch uses the handle.

### Env vars added

```
WORKREADY_SESSION_SECRET   # required for login; unset ⇒ 503
STUDENT_SESSION_DAYS=60    # teaching-period TTL
LOGIN_MAX_FAILURES_PER_MIN=5
```

## Phase 2 — Convert student-facing endpoints (session-derived identity)

Every endpoint stops accepting an email param and resolves identity from the
session dependency instead.

| Route today | Becomes |
|---|---|
| `GET /api/v1/student/{email}` (app.py:608) | admin-only (see Phase 5) |
| `GET /api/v1/student/{email}/state` (app.py:683) | `GET /api/v1/me/state` |
| `GET /api/v1/inbox/{email}` (app.py:745) | `GET /api/v1/inbox` |
| message read/read-all (app.py:765) | same path, ownership via `messages.student_id` |
| `POST /api/v1/resume` (form carries student_email) | drop field; student from session |
| `GET /api/v1/mail/sent/{email}` (mail.py:555) | `GET /api/v1/mail/sent` |
| mail compose/reply `student_email: Form(...)` (mail.py:219, 405) | drop field; sender = handle |
| thread/delete/attachments `student_email` (mail.py:582–610) | ownership via FK join, not supplied email |

### Ownership guard (fixes the enumerable-ID holes)

New helper `assert_application_owner(application_id, student)` — 404 (not 403)
on mismatch. Apply to every ID-scoped route: `/application/{id}`,
`interview/start|message|end`, `exit/*`, `perf-review/*`, `tasks/*/submit`,
`chat/send`, lunchroom activate/post, practice/talk-buddy exports. This closes
the cross-student access found in the audit regardless of auth mechanism.

### Internal threading

Signature-only changes, mechanical:
- `notifications.notify(student_email, …)` → `notify(handle, …)`
  (notifications.py:109, 131, 175)
- `context_builder.student_email` → `handle` (context_builder.py:49)
- `db.py` insert/create helpers carrying `student_email` params
  (db.py:599, 1058, 1150, 1196) → take `student_id`/handle as appropriate
- `email_registry.py` untouched (fictional recipient registry)

## Phase 3 — Code issuance (admin)

Extend `workready_api/admin.py` (existing static-token plane):

```
POST /api/v1/admin/codes/generate   {count, cohort?, note?} → text/csv (codes only)
POST /api/v1/admin/codes/{code}/revoke
GET  /api/v1/admin/codes            ?cohort=&active=
```

- Generation: `secrets.choice` over the unambiguous alphabet; uniqueness-checked
  against the whole table; cap count ≤ 1000/batch (cloudcore parity).
- The CSV contains **codes only** — the lecturer pairs them with students in
  their LMS/spreadsheet. That pairing is the mapping table; it never enters
  this system.
- Revocation flips `active=0` (immediate: next request 401s on... actually
  sessions survive revocation until expiry — acceptable for MVP; optionally
  check `active` inside `get_current_student` since it's one indexed lookup.
  Recommend: yes, check it. Revocation should be immediate.)

## Phase 4 — Portal (`workready-portal`)

Small surface — only 3 localStorage refs (`app.js:114,123,2747`).

- `index.html:24–30`: replace email input with code input
  (`placeholder="WR-XXXX-XXXX"`), help copy: "Enter the access code from your
  unit coordinator." Remove "use any email".
- `app.js`: store token in `localStorage['wr_token']`; `api()` helper
  (app.js:103) gains `Authorization: Bearer` header; sign-in calls
  `POST /auth/login`; auto-resignin (2747) restores session from token and
  falls back to the sign-in screen on 401; logout discards token.
- State poll loop (2752) hits `/me/state`.

## Phase 5 — seek.jobs (`workready-jobs`) ⚠ separate origin

The job board runs on its own virtual host, so it **cannot share the portal's
localStorage/token**. It has its own email sign-in today
(`src/app.js:174–209`, `signin-email` input) and threads email into
`postings?email=` (grey-out logic) and the Quick Apply form.

- Replace its sign-in modal email input with the same code entry; it calls
  `POST /api/v1/auth/login` itself and caches `seekjobs_token` in its own
  localStorage.
- All its API calls gain the bearer header; `postings` blocking info moves to
  `GET /api/v1/me/blocking` (session-derived) or stays in `/me/state`.
- Quick Apply drops the `apply-email`/`student_email` field; identity from
  bearer token.

## Phase 6 — Admin page + deploy + docs

**`admin.html`**: student search/list switches email → code; journey report
unchanged (keyed on application id). Add the three code-management panels
(generate/revoke/list, download CSV). Fix constant-time token compare while
there (`secrets.compare_digest`, mirrors cloudcore `analytics.py:15-24`).

**Deploy (`workready-deploy/`)**:
- `install.sh` / compose: require/auto-generate `WORKREADY_SESSION_SECRET`
  (persist it — regenerating invalidates all sessions).
- Tier 1 demo ergonomics: `DEMO_CODES=N` env causes the API to mint N demo
  codes logged at startup so evaluation stays near-zero-config.
- Breaking-change note: fresh SQLite volume required for this release.

**Docs**: update `SYSTEM.md` — repo inventory line for auth ("no password"
section at 376–380), journey step 1, config knobs, roadmap (Phase 2's
"magic-code auth" item is superseded by this design). Update README quickstart
(`get_or_create_student('test@example.com', …)` example at SYSTEM.md:267).

---

## Implementation order (PR-sized slices)

1. Schema + db rekey + handles (API only, behind nothing — no users yet)
2. `auth.py`: login/token/dependency/rate-limiter + `/auth/*` routes
3. Convert `app.py` student/inbox/resume routes + `me/state`
4. Convert `mail.py` + `notifications.py` + `context_builder.py`
5. Ownership guard across ID-scoped routes
6. Admin code-management endpoints
7. Portal conversion
8. seek.jobs conversion
9. `admin.html` + deploy wiring + docs sweep

Steps 1–6 are independently testable with curl before any frontend work.

## Test checklist (no test suite exists yet — smoke-script these)

With `LLM_PROVIDER=stub`, two codes A and B:

- [ ] unknown code → 401 (same body as wrong/inactive)
- [ ] revoked code → 401 after revocation, even with valid old token
- [ ] login twice with A → same `students.id` (state continuity)
- [ ] `/me/state`, inbox, compose-as-handle, reply, sent box all work on token
- [ ] B cannot read/modify any of A's messages, applications, tasks, threads
      (spot-check each formerly-unguarded route)
- [ ] sequential application IDs unreachable without ownership
- [ ] expired token → 401 → portal shows sign-in again
- [ ] 6th failed login within a minute → 429
- [ ] unset `WORKREADY_SESSION_SECRET` → `/auth/login` 503, students endpoints 401
- [ ] codes CSV contains no emails/names; `strings workready.db | grep -i curtin`
      finds nothing
- [ ] full six-stage journey completes on code A end-to-end

## Explicitly out of scope (follow-ups)

- Transcript/message retention policy (currently indefinite) — separate piece
- Lecturer dashboard/self-service accounts (staff PII would live in a separate
  service à la cloudcore-api; admin-token + CSV is fine until cohorts grow)
- Real outbound email channels (Phase 3 roadmap) — unaffected by this change
- Cookie-based sessions — rejected for now because portal/jobs/API are separate
  virtual hosts (cross-origin cookies need SameSite=None + credentials-aware
  CORS); bearer tokens match the existing admin-page pattern
