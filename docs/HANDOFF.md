# HANDOFF — Operation Golden Keyboard (Seniors Task 1 backend)

Spec: `golden_keyboard_prd.md` at repo root. **It is authoritative.** Read it before changing anything.

## Status snapshot

- Repo root: `D:\Projects\CSA\Backend` (git initialised, no remote).
- Phases 1-3 **done and committed**. 111 tests green, `manage.py check` clean.
- Phase: **4 — filtering, pagination, throttling** (next).

## In progress

Nothing half-finished. Next concrete action: write `competition/tests/test_filters.py`
and `competition/tests/test_throttling.py`. `competition/filters.py` and
`competition/throttles.py` already exist and are wired into `MissionViewSet`; Phase 4
is about proving them.

## Next steps (ordered)

Follows PRD section 13 phase plan. Each phase ends with `python manage.py test` green,
`python manage.py check` clean, then a commit.

1. ~~**Phase 1** — models, derived scores, derived expiry, seeded hostels.~~ DONE (23 tests).
2. ~~**Phase 2** — auth, serializers, read APIs, leaderboard.~~ DONE (+32 tests).
3. ~~**Phase 3** — permissions, claim, status transitions, staff CRUD.~~ DONE (+56 tests).
4. **Phase 4** — `filters.py` (effective-status filter), search, settings-driven
   pagination, `ClaimRateThrottle`. Tests: `test_filters.py`, `test_throttling.py`.
5. **Phase 5** — drf-spectacular URLs + `@extend_schema`;
   `manage.py spectacular --validate --fail-on-warn` must be warning-free.
6. **Phase 6** — `seed_demo` command, Postman collection + environment, curl-verify the
   7 required cases.
7. **Phase 7** — README with the 15-deliverable checklist.

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
   names** — tests must count 17, never assert a specific name.
5. **2026-09-17 — Git.** User asked for `git init` + one commit per PRD phase. No remote,
   no pushing. No Claude attribution in commits (global rule).

## Open questions / blockers

- Hostel names are a best-effort placeholder set (see decision 4). If the evaluator's
  sheet differs, edit `competition/constants.py` and regenerate migration `0002`.

## Key facts & pointers

- Score is **never stored**. `Hostel.objects.with_scores()` annotates it. PRD's
  definition-of-done requires that `grep` finds no code writing a score field.
- Expiry is **computed, never persisted** (PRD 3.5 / 12.3). Every write path re-checks
  `deadline` against `timezone.now()`.
- `Mission.hostel` is written **only** by the claim action, copied from
  `claimed_by.hostel`. No client payload may set it.
- HTTP codes (PRD 6.4): 401 anon · 403 role/forbidden-field · 404 missing ·
  400 expired/invalid-transition/validation · 409 already claimed · 429 throttled.
- Throttle is scoped `claim` at `5/min`, attached **only** to the claim action.
  Tests must `cache.clear()` in `setUp`.

## How to resume from cold

```
cd D:\Projects\CSA\Backend
.venv\Scripts\activate
python manage.py test
python manage.py check
```

Then read "In progress" above and continue from the named file/function.


## Phase 1-3 notes (written while working)

- `manage.py test` runs in ~1.2s because `config/settings.py` swaps in the MD5 password
  hasher when `"test" in sys.argv`. Without it the suite took 28s.
- `@action(url_path="status")` alone gives the reverse name `mission-set-status`;
  `url_name="status"` was added so `reverse("mission-status")` works.
- The mission list costs exactly **3** queries regardless of row count
  (token+user, pagination COUNT, page). `test_leaderboard.py` pins this — if you add a
  serializer field that touches a new relation, that test will catch it.
- `Window(Rank())` composes fine with the `Sum`/`Count` annotations on SQLite. Verified.
- Race tests do not use threads. They hand the service a deliberately **stale** model
  instance while the row underneath has already moved on — the same interleaving a real
  race produces, and deterministic. Used in `test_claims.py` and `test_deadlines.py`.
- DRF checks permissions *before* throttles, so an anonymous claim returns 401 (as the
  PRD's test table requires) rather than being throttled by IP. Authenticated failed
  claims still consume throttle quota, which is what PRD 6.2 actually asks for.
- Domain errors subclass `_CodedError` in `views.py`; putting a dict in `APIException`
  `detail` is what produces the `{"detail": ..., "code": ...}` body.
- Decision taken on the PRD's open choice (section 11, "pick one, test it"): a
  participant PATCHing their own `hostel_id` gets **403**, not a silent field drop.
  Message: `views.HANDLE_ONLY`. Test:
  `test_permissions.py::test_participant_cannot_change_their_own_hostel`.
