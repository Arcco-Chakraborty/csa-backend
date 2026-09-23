# HANDOFF — Operation Golden Keyboard (Seniors Task 1 backend)

The evaluator's source of truth is the task sheet, `Backend Task 1 Sen.docx` at repo root
(**untracked on purpose**, and excluded via `.git/info/exclude`). The build spec is
`golden_keyboard_prd.md`, which was re-checked line by line against the sheet on
2026-09-23; its section 14 logs what changed. Read the PRD before changing anything.
User-facing docs are in `README.md`.

## Status snapshot

**Everything is done and every gate is green.** The 2026-09-23 spec-compliance pass is
complete, independently reviewed, and committed to `main` and pushed to `origin`. Run
`git log -1` for the commit. Plan for that pass:
`C:\Users\arcco\.claude\plans\okay-i-need-u-floating-tarjan.md`.

Fresh results on the final tree (2026-09-23):

| Gate | Result |
|---|---|
| `python manage.py test` | **187 passed**, ~4.3s |
| `python manage.py check` | 0 issues |
| `python manage.py spectacular --validate --fail-on-warn` | 0 warnings |
| Postman JSON | valid; regenerating from `generate_collection.py` is byte-identical |
| newman, fresh `seed_demo --reset` + fresh server | **50 requests, 91 assertions, 0 failures** |
| Live task-sheet check (38 checks over §1–§10, ids discovered via the API) | **38/38 pass** |
| Definition-of-done greps (no stored score, page size only in settings, no literal ids/hosts in the collection) | clean |

## In progress

Nothing. The tree is clean apart from the untracked (and excluded) task sheet.

## Spec review 2026-09-23 (PRD vs `Backend Task 1 Sen.docx`)

The PRD covered every task requirement, all 15 deliverables and all 7 minimum Postman
cases. These gaps were found and fixed, in code as well as in the PRD:

1. **Staff could set `in_progress`/`cracked`.** Task §4: "Only the participant who
   claimed a mission should be able to change its status to in_progress or cracked."
   Fixed in `MissionSerializer.validate` (decision 10).
2. **A stored `expired` with a future deadline returned 409 on claim** (and
   `invalid_transition` on status change). Both are now 400 `mission_expired`
   (decision 11).
3. **The README deliverables checklist was not the task's 15.** It now copies them word
   for word (decision 13).
4. **Postman did not show "status change after the deadline → 400"** (task §5). Seed
   `OVERTIME` plus folder-6 requests fix that (decision 12).
5. PRD text: the §6.2 check order, unlogged decisions, and tests missing from §11.

The independent code review then found, and this pass fixed:
- a staff GET-then-PUT that stored a derived `expired`;
- a staff full-row save that could undo a concurrent claim or crack (decision 16);
- a mid-flight release being reported as "expired" (decision 17);
- `/status/` docs missing `mission_expired`;
- doc contradictions, and the generator's hardcoded `D:\` output path.

## Next steps (ordered)

1. Confirm the 17 hostel names against the juniors task sheet; correct
   `competition/constants.py` if they differ (one-file edit, instructions in README).
2. Confirm nothing else carries over from the juniors task. For example, if that sheet
   required a future deadline on create, PRD decision 7 (staff may create past-deadline
   missions) would need revisiting.
3. Optional: if the evaluator wants a single status code for both already-claimed and
   expired, PRD decision 12.5 allows collapsing 409 into 400. The `code` field already
   distinguishes them, so this is a two-line change in `views.py`
   (`MissionUnavailable.status_code`) plus the matching test expectations.
4. Optional, documented rather than coded: a staff account that is also a claimant
   gets the admin edit on the generic PATCH, so it changes status via `/status/`. If
   that ever matters, route a claimant's status-only write to `_change_status`
   regardless of `is_staff` in `MissionViewSet.update`.

## Decisions (append-only)

1. **2026-09-17 — Repo layout.** The PRD's layout diagram roots everything under
   `golden_keyboard/`. Our repo root *is* that root (`D:\Projects\CSA\Backend`), so
   `manage.py`, `config/`, `competition/`, `postman/` live directly at repo root. A
   nested `golden_keyboard/` dir would push the PRD outside the project tree.
2. **2026-09-17 — Python 3.12, not 3.14.** Both installed; 3.12 is the safe target for
   Django 5.2 LTS. Venv at `.venv`, built with `py -3.12`.
3. **2026-09-17 — DIFFICULTY = easy / medium / hard / boss_level.** User confirmed the
   PRD's default assumption (PRD section 3.1).
4. **2026-09-17 — Hostel names.** User chose the "standard BITS Pilani list" option
   over pasting the previous task's sheet, accepting the stated risk. `constants.py`
   carries a prominent TODO naming exactly which entries are least certain, so swapping
   the real sheet in is a one-file edit. **Nothing else in the codebase hardcodes hostel
   names** — tests count 17 and never assert a specific name.
5. **2026-09-17 — Git.** User asked for `git init` + one commit per PRD phase. No remote,
   no pushing. No Claude attribution in commits (global rule).
6. **2026-09-17 — Participant editing own hostel returns 403**, not a silently ignored
   field. PRD section 11 explicitly left this open ("pick one, test it"). Chose 403 for
   consistency with how forbidden *mission* fields are handled, and because a silent
   drop lets a client believe a hostel switch worked. Constant: `views.HANDLE_ONLY`.
7. **2026-09-17 — Postman JSON is generated, not hand-written.**
   `postman/generate_collection.py` is the source; it guarantees valid JSON and keeps
   the request definitions readable. **Edit the generator and re-run it**, never the
   JSON directly.
8. **2026-09-17 — Folder 8 keeps bob's token**, exactly as PRD section 10.3 specifies,
   even though it makes back-to-back collection runs collide with the throttle. The PRD
   anticipated this and asked for the workaround to be documented rather than designed
   away. Documented in the README and in folder 8's own description: restart the server
   or wait 60s before re-running. Verified: a fresh server + fresh seed passes 85/85;
   an immediate re-run fails exactly 2 assertions, both in "Bob claims same mission".
9. **2026-09-23 — PRD re-checked against the task doc; user chose "fix everything".**
   That covers PRD, code, tests, seed, Postman and README. The task doc is authoritative
   over the PRD where they conflict. Its text is in `Backend Task 1 Sen.docx` at repo
   root (see "Key facts" for how to read it).
10. **2026-09-23 — Only the claimant moves a mission into `in_progress`/`cracked`.**
    The user chose this over an admin override (task §4 is explicit). Staff status
    writes over the API are limited to `unclaimed` (release) and `expired` (void). An
    *unchanged* status in a PUT is accepted, so GET-then-PUT round trips still work.
    Violations return 400 with a field error on `status`, the same shape as the
    existing create rule. The rule is **API-only**: Django admin stays the trusted repair
    surface its docstring describes. A staff account that is also the claimant changes
    status through `/status/`.
11. **2026-09-23 — An expired mission always gives 400 `mission_expired`.** That covers
    claim and status change, and includes a *stored* `expired` with a future deadline
    (reachable because staff may set `expired`). The check is
    `effective_status == expired`, not `is_past_deadline()`.
12. **2026-09-23 — Seed and Postman now show "status change after the deadline".**
    Seed mission `OVERTIME` is alice's, in_progress, deadline now − 2h. Env var
    `overdue_mission_id`; folder 6 request "Alice cracks her overdue mission (400)".
13. **2026-09-23 — The README deliverables checklist is the task's own 15, word for
    word.** The old list was a paraphrase of PRD goals.
14. **2026-09-23 — Documented, not coded:** `mission.hostel` is a claim-time snapshot, so
    staff moving a participant doesn't move earlier points. `claimed_by` SET_NULL on
    participant deletion leaves missions with no claimant: cracked points stay with the
    hostel, and in-progress missions wait for a staff reset or their deadline.

15. **2026-09-23 — User authorized commit + push to `origin` (GitHub
    `Arcco-Chakraborty/csa-backend`, branch `main`).** This supersedes decision 5's
    "no remote, no pushing": a remote now exists and `main` tracks `origin/main`.
    Still no Claude attribution. `Backend Task 1 Sen.docx` stays **untracked**: it is
    the seniors' task sheet, not ours to publish.

## Open questions / blockers

- **Hostel names are a best-effort placeholder set** (decision 4). The last three
  (`Gautam Bhawan`, `Kalam Bhawan`, `Sarojini Bhawan`) are the least certain. If the
  evaluator's sheet differs, edit `competition/constants.py` and re-run
  `manage.py migrate competition 0001 && manage.py migrate`.

- **The juniors task's other Mission rules are unknown.** The PRD carries over only the
  difficulty values and hostel names. If that sheet had extra validation (e.g. "deadline
  must be in the future on create"), it would clash with PRD decision 7 (staff may create
  past-deadline missions).

No blockers.

## Key facts & pointers

### The task doc (the evaluator's source of truth)

- `Backend Task 1 Sen.docx` at repo root. Pandoc is **not** installed. Read it with
  Python's `zipfile`: parse `word/document.xml`, join the `w:t` runs per `w:p`. Set
  `PYTHONIOENCODING=utf-8`, because the text contains `→` and the Windows console
  codepage crashes on it.
- Its shape: sections 1–10. §9 lists 7 minimum Postman cases. §10 lists 15 numbered
  deliverables, which the README checklist now copies word for word.

### Design invariants — do not break these

- Score is **never stored**. `Hostel.objects.with_scores()` annotates it.
- Expiry is **computed, never persisted** (PRD 3.5 / 12.3). Every path that can claim or
  crack re-checks `deadline` against `timezone.now()`. "Expired" is always
  `effective_status == expired`, which also covers a *stored* `expired` (decision 11).
- **Only the claimant moves a mission into `in_progress`/`cracked`** (decision 10).
  `MissionSerializer.validate()` refuses staff with 400. The set is
  `constants.PARTICIPANT_WRITABLE_STATUSES`.
- `Mission.hostel` is written **only** by `claim_mission()`, copied from
  `claimed_by.hostel`. It is read-only on every serializer.
- Both mission write routes call **one** service function each
  (`claim_mission` / `apply_status_change`) so they cannot drift apart.
- Both services end in a **conditional UPDATE** that re-asserts status, claimant and
  deadline. That, not a lock, is what makes concurrent claims safe on SQLite.
- HTTP codes (PRD 6.4): 401 anon · 403 role/forbidden-field/not-claimant · 404 missing ·
  400 expired/invalid-transition/validation (incl. staff setting in_progress/cracked)
  · 409 in progress or cracked · 429 throttled.
- Staff edits save with `update_fields`: never reintroduce a full-row save in
  `MissionSerializer.update` (`StaffEditRaceTests` will catch it).

### Gotchas discovered while building

- **DRF binds `PAGE_SIZE` and `THROTTLE_RATES` at import time** as class attributes
  (`PageNumberPagination.page_size`, `SimpleRateThrottle.THROTTLE_RATES`).
  `override_settings` alone therefore cannot move them. The two "settings-driven" tests
  rebind the attribute *from* `api_settings` inside the override, which exercises the
  real chain; both carry a docstring explaining why.
- **`@action(url_path="status")` alone** gives the reverse name `mission-set-status`.
  `url_name="status"` was added so `reverse("mission-status")` works.
- **DRF checks permissions before throttles.** That is what makes an anonymous claim
  return 401 rather than being IP-throttled, matching the PRD's test table.
  Authenticated *failed* claims still consume quota, which is what PRD 6.2 asks for.
- **Domain errors** subclass `_CodedError` in `views.py`. Putting a dict in
  `APIException.detail` is what produces the `{"detail": ..., "code": ...}` body.
- **Throttle counters survive a database re-seed** — they live in LocMemCache in the
  server process. Restart the server to clear them.
- **The mission list costs exactly 3 queries** regardless of row count (token+user,
  pagination COUNT, page). `test_leaderboard.py` pins this; adding a serializer field
  that touches a new relation will fail that test.
- **Race tests use no threads.** They hand a service a deliberately *stale* model
  instance while the row underneath has already moved on — the same interleaving a real
  race produces, and deterministic. See `test_claims.py` and `test_deadlines.py`.
- The suite runs in ~4s because `config/settings.py` swaps in the MD5 password hasher
  when `"test" in sys.argv`. Without it, 28s.
- `Window(Rank())` composes fine with the `Sum`/`Count` annotations on SQLite. Verified.

## How to resume from cold

```bash
cd D:\Projects\CSA\Backend
.venv\Scripts\activate

python manage.py test                                   # expect 187 OK
python manage.py check                                  # expect 0 issues
python manage.py spectacular --validate --fail-on-warn  # expect silence
```

To exercise the API end to end:

```bash
python manage.py seed_demo --reset    # prints all tokens and ids
python manage.py runserver            # then open /api/docs/
```

To re-run the Postman collection (needs node):

```bash
npx --yes newman@6 run postman/golden_keyboard.postman_collection.json \
       -e postman/golden_keyboard.postman_environment.json
# expect 50 requests, 91 assertions, 0 failures — reseed and restart the server first
# (npx keeps newman out of the repo: no node_modules/ or package.json to commit)
```
