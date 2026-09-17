# Operation Golden Keyboard — Competition Backend

A Django + DRF API that enforces the rules of a campus-wide, multi-hostel coding
competition. **The backend is the single source of truth.** Nothing about claims,
ownership, deadlines or scores depends on a client behaving well.

Built to the spec in [`golden_keyboard_prd.md`](golden_keyboard_prd.md).

---

## Quick start

```bash
# 1. Virtual environment (Python 3.11+; 3.12 recommended)
py -3.12 -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS / Linux

# 2. Dependencies
pip install -r requirements.txt

# 3. Database — migration 0002 seeds the 17 hostels for you
python manage.py migrate

# 4. Demo data (idempotent; add --reset to start clean)
python manage.py seed_demo

# 5. Run
python manage.py runserver
```

Then open **<http://127.0.0.1:8000/api/docs/>**.

`seed_demo` prints every token and mission id you need — copy them into Postman, or
just let the collection discover them itself (it does).

### Verify the build

```bash
python manage.py test                                  # 167 tests
python manage.py check                                 # 0 issues
python manage.py spectacular --validate --fail-on-warn # 0 warnings
```

---

## Demo accounts

| Who | Username | Password | Role |
|---|---|---|---|
| Staff | `staff` | `staffpass123` | `is_staff`, full mission CRUD, no participant profile |
| Alice | `alice` | `alicepass123` | Participant, hostel A |
| Bob | `bob` | `bobpass123` | Participant, hostel B |

Authenticate with `Authorization: Token <key>`. Get a token from
`POST /api/auth/token/`, or use the one `seed_demo` printed.

---

## Endpoints

All under `/api/`.

| Method | Path | Who | Notes |
|---|---|---|---|
| POST | `/auth/register/` | anyone | Creates `User` + `Participant` atomically, returns a token |
| POST | `/auth/token/` | anyone | Username + password → token |
| GET | `/hostels/` | authenticated | Paginated, with live scores |
| GET | `/hostels/{id}/` | authenticated | Score, cracked count, cracked missions |
| GET | `/participants/` | authenticated | Paginated |
| GET | `/participants/{id}/` | authenticated | |
| GET | `/participants/me/` | participant | Own profile |
| PATCH | `/participants/{id}/` | owner or staff | Owner may change `handle` only |
| DELETE | `/participants/{id}/` | staff | |
| GET | `/missions/` | authenticated | Filter, search, order, paginate |
| POST | `/missions/` | staff | |
| GET | `/missions/{id}/` | authenticated | |
| PUT/PATCH | `/missions/{id}/` | staff, or claimant for `status` only | |
| DELETE | `/missions/{id}/` | staff | |
| POST | `/missions/{id}/claim/` | participant | **Throttled 5/min per user** |
| PATCH | `/missions/{id}/status/` | claimant | `{"status": "cracked"}` |
| GET | `/leaderboard/` | **anyone** | Not paginated, all 17 hostels |
| GET | `/schema/` | anyone | OpenAPI |
| GET | `/docs/` | anyone | Swagger UI |

Hostels are read-only over the API — they are seeded data, managed in the Django admin.

### Query parameters on `/api/missions/`

| Parameter | Example | Behaviour |
|---|---|---|
| `status` | `?status=expired` | Filters on **effective** status (see below) |
| `difficulty` | `?difficulty=boss_level` | Exact match |
| `hostel` | `?hostel=5` | Missions belonging to that hostel |
| `search` | `?search=binary` | Case-insensitive, over `codename` and `brief` |
| `ordering` | `?ordering=-points` | `deadline`, `points`, `created_at`; default `deadline` |
| `page` | `?page=2` | Page size is 10, from settings only |

---

## The rules

### Scores are never stored

There is no `score` column anywhere. `Hostel.objects.with_scores()` aggregates points
from cracked missions on every read:

```python
cracked = Q(missions__status="cracked")
return self.annotate(
    score=Coalesce(Sum("missions__points", filter=cracked), 0),
    cracked_count=Count("missions", filter=cracked),
)
```

The consequence you can observe: if staff patch a cracked mission back to `unclaimed`,
the hostel's score drops on the very next leaderboard read, with no bookkeeping code
involved.

Check it yourself — both of these come back empty:

```bash
grep -rn "score" competition/migrations/      # no score column exists
grep -rn "score +=\|score -=\|score=" competition/ --include=*.py \
  | grep -v "with_scores\|Coalesce"           # nothing ever assigns one
```

Leaderboard ranks come from a SQL window function
(`Window(expression=Rank(), order_by=F("score").desc())`), so tied hostels genuinely
share a rank: `1, 2, 2, 4`.

### Expiry is computed, never persisted

A mission that is `unclaimed` or `in_progress` past its deadline reads as `expired`
everywhere — in API output, in filtering, and on every write path — while its database
row still says otherwise. There is no cron job and nothing to fall behind.

```python
@property
def effective_status(self):
    if self.status in ("unclaimed", "in_progress") and self.is_past_deadline():
        return "expired"
    return self.status
```

A `cracked` mission stays cracked past its deadline and keeps its points.

### Claiming is race-safe

`POST /api/missions/{id}/claim/` ends in a single conditional `UPDATE` whose `WHERE`
clause re-asserts every precondition:

```python
Mission.objects.filter(pk=pk, status="unclaimed",
                       claimed_by__isnull=True, deadline__gt=now).update(...)
```

Of two simultaneous claims, exactly one can match a row; the other updates zero rows
and receives `409`. This needs no row locks and works on SQLite.

**The request body is ignored.** The hostel written to the mission is always
`participant.hostel`, so a client cannot score for a hostel it does not belong to.
`mission.hostel` is written by this one code path and nothing else.

### Ownership is enforced field by field

Only the claimant may change a mission's status, and only the `status` field. The
permission layer checks ownership *first*, then the payload keys — so a claimant who
sends `{"status": "cracked", "points": 9999}` gets `403` and **nothing is written**,
rather than having the allowed half applied.

`PATCH /api/missions/{id}/` and `PATCH /api/missions/{id}/status/` call the same
service function, so the two routes cannot drift apart.

### Status codes

| Situation | Code |
|---|---|
| Not logged in | `401` |
| Not staff / not participant / not claimant / forbidden field | `403` |
| Mission not found | `404` |
| Expired mission, invalid transition, validation error | `400` |
| Mission already claimed or cracked | `409` |
| Claim rate limit exceeded | `429` (with `Retry-After`) |

Competition errors carry a stable machine-readable code so clients need not
string-match:

```json
{"detail": "Mission has expired.", "code": "mission_expired"}
```

Codes in use: `mission_expired`, `mission_unavailable`, `invalid_transition`,
`cracked_is_final`.

---

## Postman

1. Import **both** files from `postman/`:
   - `golden_keyboard.postman_collection.json`
   - `golden_keyboard.postman_environment.json`
2. Select the **Golden Keyboard - Local** environment (top right).
3. Make sure the server is running and `seed_demo` has been run.
4. Open the Collection Runner and run the whole collection **in folder order**.

Expected result: **47 requests, 85 assertions, 0 failures.**

Folder 0 captures every token and id into the environment, so no request hardcodes a
URL or an id — the collection works unchanged after a re-seed that renumbers the
primary keys.

### The seven required cases

| # | Case | Where | Code |
|---|---|---|---|
| 1 | Participant claims an available mission | 5. Claiming and status | `200` |
| 2 | Another participant claims the same mission | 5. Claiming and status | `409` |
| 3 | Participant changes the status of someone else's mission | 6. Rule violations | `403` |
| 4 | Participant patches `points` on their own mission | 6. Rule violations | `403` |
| 5 | Staff edits a mission's points | 3. Missions CRUD | `200` |
| 6 | Participant claims an expired mission | 6. Rule violations | `400` |
| 7 | Sixth claim within a minute | 8. Rate limiting | `429` |

They are tagged `[required case N]` in the request names.

### Re-running the collection

Folder 8 deliberately exhausts bob's claim quota. The throttle counters live in the
server's local-memory cache, so **re-seeding the database does not clear them**.

Before running the collection a second time, either:

- **restart the server** (`Ctrl+C`, then `python manage.py runserver`), or
- **wait 60 seconds.**

Otherwise folder 5's "Bob claims same mission (409)" returns `429` instead.

The rate limit can also be demonstrated by hand: run folder 8's single request six
times in the Collection Runner and watch the sixth return `429`.

The JSON is generated by `postman/generate_collection.py`; edit that and re-run it
rather than hand-editing the JSON.

---

## Tests

```bash
python manage.py test              # everything
python manage.py test competition.tests.test_claims   # one module
```

167 tests across eight modules:

| Module | Covers |
|---|---|
| `test_models.py` | Derived scores, derived expiry, model invariants, hostel seeding |
| `test_leaderboard.py` | Ranking and ties, response shapes, registration, N+1 query count |
| `test_claims.py` | Claiming, availability, **races**, who may claim |
| `test_permissions.py` | Ownership, field-level restrictions, staff CRUD, profile edits |
| `test_deadlines.py` | Deadline boundaries on every write path, nothing persisted |
| `test_filters.py` | Effective-status filtering, search, ordering, pagination |
| `test_throttling.py` | The 5/min claim limit and everything it must *not* affect |
| `test_docs.py` | Schema completeness, security scheme, documented failure modes |

Notes on how they are written:

- **No sleeps.** Time is controlled with `mock.patch` on `django.utils.timezone.now`,
  or by building missions with past deadlines.
- **No threads in the race tests.** They hand a service a deliberately *stale* model
  instance while the row underneath has already moved on — the same interleaving a real
  race produces, and fully deterministic.
- The suite runs in about 3 seconds because `config/settings.py` swaps in a fast
  password hasher when `"test" in sys.argv`. Without it, it takes 28.

---

## Deliverables checklist

| # | Deliverable | Where |
|---|---|---|
| 1 | Hostel, Participant, Mission models with correct relations | `competition/models.py` |
| 2 | 17 hostels seeded | `competition/constants.py`, migration `0002_seed_hostels` |
| 3 | Leaderboard computed live by ORM aggregation, no stored score | `competition/managers.py`, `LeaderboardView` |
| 4 | Custom DRF permission classes | `competition/permissions.py` |
| 5 | Ownership enforced — only the claimant edits status | `MissionAccessPolicy`, `apply_status_change` |
| 6 | Field-level edit restrictions | `MissionAccessPolicy.has_object_permission` |
| 7 | Claim availability enforced | `claim_mission` in `competition/views.py` |
| 8 | Race-safe claiming | Conditional `UPDATE` in `claim_mission`; `test_claims.py` |
| 9 | Deadline enforcement on every write path | `Mission.effective_status`, `test_deadlines.py` |
| 10 | Filtering by status, difficulty and hostel | `competition/filters.py` |
| 11 | Search over codename and brief | `MissionViewSet.search_fields` |
| 12 | Settings-driven pagination | `REST_FRAMEWORK["PAGE_SIZE"]`; nothing else mentions a page size |
| 13 | Scoped throttle on claim only, returning 429 | `competition/throttles.py`, `test_throttling.py` |
| 14 | Auto-generated docs at `/api/docs/` | `config/urls.py`, `@extend_schema` annotations |
| 15 | Postman collection + environment covering success and failure | `postman/` |

---

## Decisions

Taken from the PRD's section 12, plus the choices it left open. Change them here if
your evaluator expects otherwise.

1. **Claiming sets `in_progress` directly.** A claimed mission must leave `unclaimed`,
   and `in_progress` is listed as "not available to claim". The claimant setting
   `in_progress` again is an idempotent no-op.
2. **Hostel is stored on Mission**, not only read through `claimed_by` — `?hostel=<id>`
   requires it. It is written by the claim action alone.
3. **Expiry is computed, not persisted.** No scheduled job. The stored status may say
   `unclaimed` while the API reports `expired`.
4. **Cracked missions keep their points** after the deadline passes.
5. **`409` for already-claimed, `400` for expired.** Both bodies carry a `code`.
6. **No unclaiming.** Not in the spec.
7. **Staff may create missions with past deadlines** (needed to demo expired cases).
   Staff cannot set `claimed_by` or `hostel` directly — those are read-only fields.
8. **The leaderboard is public.** Everything else requires authentication.
9. **Difficulty values:** `easy`, `medium`, `hard`, `boss_level` — the PRD's stated
   default assumption, confirmed before building.
10. **Participants editing their own hostel get `403`**, not a silently dropped field.
    The PRD left this open ("pick one, test it"); refusing outright is consistent with
    how forbidden mission fields are handled, and it means a client that tries to
    switch hostels finds out instead of believing it worked.
    Test: `test_permissions.py::test_participant_cannot_change_their_own_hostel`.

### ⚠️ Hostel names need confirming

`competition/constants.py` carries the BITS Pilani (Pilani campus) bhawan names. The
first 14 are well attested; the last three (`Gautam Bhawan`, `Kalam Bhawan`,
`Sarojini Bhawan`) are the least certain and are the ones most likely to need
swapping for the exact names on the juniors task sheet.

To correct them: edit the `HOSTELS` list in `competition/constants.py`, then

```bash
python manage.py migrate competition 0001
python manage.py migrate
```

The data migration syncs by name and is safe to re-run. Nothing else in the codebase
hardcodes a hostel name, and no test asserts one — they assert the count is 17.

---

## Project layout

```
manage.py
requirements.txt
config/
  settings.py          REST_FRAMEWORK, CACHES, SPECTACULAR_SETTINGS
  urls.py              /admin/, /api/schema/, /api/docs/, /api/
competition/
  constants.py         HOSTELS, Status, Difficulty  — the only place they are defined
  models.py            Hostel, Participant, Mission (+ effective_status, clean())
  managers.py          with_scores(), effective-status queryset helpers
  serializers.py
  permissions.py       IsStaff, IsParticipant, IsMissionClaimant, MissionAccessPolicy
  throttles.py         ClaimRateThrottle (scope "claim")
  filters.py           Deadline-aware MissionFilter
  views.py             claim_mission(), apply_status_change(), viewsets
  urls.py
  admin.py
  migrations/
    0001_initial.py
    0002_seed_hostels.py
  management/commands/
    seed_demo.py
  tests/               8 modules, 167 tests
postman/
  golden_keyboard.postman_collection.json
  golden_keyboard.postman_environment.json
  generate_collection.py
docs/
  HANDOFF.md           Running state of the work
```
