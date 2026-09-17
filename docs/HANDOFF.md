# HANDOFF — Operation Golden Keyboard (Seniors Task 1 backend)

Spec: `golden_keyboard_prd.md` at repo root. **It is authoritative.** Read it before
changing anything. User-facing docs are in `README.md`.

## Status snapshot

**All 7 PRD phases complete and committed.** Nothing is in flight.

| Gate | Result |
|---|---|
| `python manage.py test` | **167 passed**, ~3.9s |
| `python manage.py check` | 0 issues |
| `python manage.py spectacular --validate --fail-on-warn` | 0 warnings |
| Postman via newman, fresh `seed_demo` | **47 requests, 85 assertions, 0 failures** |
| Definition-of-done greps (no stored score, no hardcoded page size) | clean |

All 7 required Postman cases verified twice: once by `curl` against a running server,
once by a full newman run.

## In progress

Nothing. The tree is clean and every gate is green.

## Next steps (ordered)

Only open item is the hostel names — see "Open questions" below. Everything else the
PRD asks for is delivered.

1. Confirm the 17 hostel names against the juniors task sheet; correct
   `competition/constants.py` if they differ (one-file edit, instructions in README).
2. Optional: if the evaluator wants a single status code for both already-claimed and
   expired, PRD decision 12.5 allows collapsing 409 into 400. The `code` field already
   distinguishes them, so this is a two-line change in `views.py`
   (`MissionUnavailable.status_code`) plus the matching test expectations.

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

## Open questions / blockers

- **Hostel names are a best-effort placeholder set** (decision 4). The last three
  (`Gautam Bhawan`, `Kalam Bhawan`, `Sarojini Bhawan`) are the least certain. If the
  evaluator's sheet differs, edit `competition/constants.py` and re-run
  `manage.py migrate competition 0001 && manage.py migrate`.

No blockers.

## Key facts & pointers

### Design invariants — do not break these

- Score is **never stored**. `Hostel.objects.with_scores()` annotates it.
- Expiry is **computed, never persisted** (PRD 3.5 / 12.3). Every write path re-checks
  `deadline` against `timezone.now()`.
- `Mission.hostel` is written **only** by `claim_mission()`, copied from
  `claimed_by.hostel`. It is read-only on every serializer.
- Both mission write routes call **one** service function each
  (`claim_mission` / `apply_status_change`) so they cannot drift apart.
- Both services end in a **conditional UPDATE** that re-asserts status, claimant and
  deadline. That, not a lock, is what makes concurrent claims safe on SQLite.
- HTTP codes (PRD 6.4): 401 anon · 403 role/forbidden-field · 404 missing ·
  400 expired/invalid-transition/validation · 409 already claimed · 429 throttled.

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

python manage.py test                                   # expect 167 OK
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
npm install newman
newman run postman/golden_keyboard.postman_collection.json \
       -e postman/golden_keyboard.postman_environment.json
# expect 47 requests, 85 assertions, 0 failures — restart the server first
```
