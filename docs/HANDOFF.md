# HANDOFF — Operation Golden Keyboard (Seniors Task 1 backend)

Spec: `golden_keyboard_prd.md` at repo root. **It is authoritative.** Read it before changing anything.

## Status snapshot

- Repo root: `D:\Projects\CSA\Backend` (git initialised, no remote).
- Phase: **1 — scaffold and models** (in progress).
- Nothing built yet beyond git + this doc.

## In progress

Creating venv + Django scaffold. Next concrete action: create `.venv` on Python 3.12,
install pinned requirements, `django-admin startproject config .`.

## Next steps (ordered)

Follows PRD section 13 phase plan. Each phase ends with `python manage.py test` green,
`python manage.py check` clean, then a commit.

1. **Phase 1** — settings, `constants.py`, models, `HostelQuerySet.with_scores`,
   `Mission.effective_status`, migration 0001 + data migration 0002 (seed 17 hostels),
   admin. Tests: `test_models.py`.
2. **Phase 2** — token auth, register/token endpoints, all serializers, Hostel +
   Participant viewsets, mission read endpoints, leaderboard with `Window(Rank())`.
   Tests: `test_leaderboard.py` + `assertNumQueries` N+1 check.
3. **Phase 3** — `permissions.py`, claim action (conditional update), status action,
   staff CRUD validation. Tests: `test_claims.py`, `test_permissions.py`,
   `test_deadlines.py`.
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
