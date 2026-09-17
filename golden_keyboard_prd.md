# PRD: Operation Golden Keyboard - Competition Backend (Seniors Task 1)

Build target: a Django + Django REST Framework API that enforces the rules of a campus-wide, multi-hostel coding competition. The backend is the single source of truth. Nothing about claims, ownership, deadlines or scores may depend on the frontend behaving.

This document is written to be handed to Claude Code. Section 13 contains a kickoff prompt. Sections 2 to 11 are the spec it must satisfy. Section 12 lists the decisions made where the original problem statement is ambiguous - read those before building, and change them if your evaluator expects otherwise.

---

## 1. Goals and non-goals

Goals

- Correct relational model: Hostel, Participant (linked to Django User), Mission.
- Leaderboard computed live with ORM aggregation. No stored score anywhere.
- Custom DRF permission classes and validation that enforce ownership, claim availability, field-level edit restrictions, and deadlines.
- Race-safe claiming: two concurrent claims on one mission cannot both succeed.
- Filtering, search, settings-driven pagination on the missions list.
- Scoped throttle on the claim operation only (5/min per user), returning 429.
- Auto-generated docs at /api/docs/.
- Postman collection + environment (.json) demonstrating success and failure cases.
- Automated tests for every rule below.

Non-goals

- No frontend.
- No production deployment, Docker, Postgres, Celery or background jobs (SQLite is fine).
- No features the problem statement does not ask for (no unclaim, no teams, no notifications, no participant claim limits).

---

## 2. Tech stack

- Python 3.11+, Django 5.x, djangorestframework 3.15+
- django-filter (filtering)
- drf-spectacular (OpenAPI schema + Swagger UI)
- rest_framework.authtoken (token auth, easy to use in Postman)
- SQLite
- Django's test runner with APITestCase (no pytest needed)
- requirements.txt pinned

Project layout

```
golden_keyboard/
  manage.py
  requirements.txt
  README.md
  config/
    settings.py
    urls.py
  competition/
    models.py
    managers.py          (Hostel queryset with_scores, Mission queryset helpers)
    constants.py         (HOSTELS list, DIFFICULTY and STATUS choices)
    serializers.py
    permissions.py
    throttles.py
    filters.py
    views.py
    urls.py
    admin.py
    migrations/
      0001_initial.py
      0002_seed_hostels.py   (data migration)
    management/commands/
      seed_demo.py
    tests/
      test_models.py
      test_leaderboard.py
      test_claims.py
      test_permissions.py
      test_deadlines.py
      test_filters.py
      test_throttling.py
      test_docs.py
  postman/
    golden_keyboard.postman_collection.json
    golden_keyboard.postman_environment.json
```

---

## 3. Data model

### 3.1 Constants

STATUS choices: unclaimed, in_progress, cracked, expired

DIFFICULTY choices: carried over from the previous (juniors) task. The spec only confirms boss_level exists. Default assumption: easy, medium, hard, boss_level. Replace with the exact values from the previous task if they differ.

HOSTELS: the 17 BITS Pilani hostels from the previous task. Put them in one list in constants.py and seed them via a data migration. Copy the exact names from the previous task's sheet; do not invent names. If the list is not available, Claude Code must stop and ask for it.

### 3.2 Hostel

| Field | Type | Notes |
|---|---|---|
| id | auto PK | |
| name | CharField(max_length=64, unique=True) | seeded, 17 rows |

No score field. Score is always derived (see 3.5).

### 3.3 Participant

| Field | Type | Notes |
|---|---|---|
| id | auto PK | |
| user | OneToOneField(settings.AUTH_USER_MODEL, on_delete=CASCADE, related_name="participant") | Django built-in User |
| handle | CharField(max_length=32, unique=True) | unique handle |
| hostel | ForeignKey(Hostel, on_delete=PROTECT, related_name="participants") | |

### 3.4 Mission

| Field | Type | Notes |
|---|---|---|
| id | auto PK | |
| codename | CharField(max_length=100, unique=True) | |
| brief | TextField | |
| points | PositiveIntegerField, validators MinValueValidator(1) | |
| difficulty | CharField(choices=DIFFICULTY) | |
| status | CharField(choices=STATUS, default="unclaimed") | stored status |
| deadline | DateTimeField | timezone-aware, USE_TZ = True |
| claimed_by | ForeignKey(Participant, null=True, blank=True, on_delete=SET_NULL, related_name="claimed_missions") | |
| hostel | ForeignKey(Hostel, null=True, blank=True, on_delete=PROTECT, related_name="missions") | set only by server, copied from claimed_by.hostel at claim time |
| created_at / updated_at | auto timestamps | |

Model-level invariants (enforce in Mission.clean() and in serializers):

- status in (in_progress, cracked) requires claimed_by and hostel to be non-null.
- status == unclaimed requires claimed_by and hostel to be null.
- hostel, when set, must equal claimed_by.hostel at the time of claim.

Why store hostel on Mission at all: the spec says missions must "keep track of which hostel that mission belongs to", and filtering by ?hostel=<id> is required. It is written only by the claim action, never by any client payload.

### 3.5 Effective status (deadline handling)

Add to Mission:

```
def is_past_deadline(self):
    return timezone.now() >= self.deadline

@property
def effective_status(self):
    if self.status in ("unclaimed", "in_progress") and self.is_past_deadline():
        return "expired"
    return self.status
```

Rules:

- A cracked mission stays cracked after its deadline and still scores.
- An unclaimed or in_progress mission whose deadline has passed is treated as expired everywhere: API output, filtering, claiming, status changes.
- The API never trusts the stored status alone. Every write path re-checks the deadline against timezone.now().
- Persisting "expired" to the DB is not required. Do not add a cron job.

### 3.6 Scores (derived only)

In managers.py, a HostelQuerySet method:

```
def with_scores(self):
    cracked = Q(missions__status="cracked")
    return self.annotate(
        score=Coalesce(Sum("missions__points", filter=cracked), 0),
        cracked_count=Count("missions", filter=cracked),
    )
```

Hostel.objects = HostelQuerySet.as_manager(). No code anywhere increments or decrements a score.

---

## 4. Authentication and roles

- Auth classes: TokenAuthentication and SessionAuthentication.
- Default permission: IsAuthenticated.
- Roles:
  - Staff: user.is_staff == True. Full mission CRUD. Does not need a Participant profile.
  - Participant: authenticated user with a related Participant row.
  - An authenticated non-staff user without a Participant profile gets 403 on any participant action.

Auth endpoints:

| Method | Path | Access | Behaviour |
|---|---|---|---|
| POST | /api/auth/register/ | AllowAny | body: username, password, handle, hostel (id). Creates User + Participant atomically. Returns 201 with participant data and token. |
| POST | /api/auth/token/ | AllowAny | DRF obtain_auth_token. body: username, password. Returns token. |

---

## 5. API surface

All under /api/. Error bodies use DRF's default {"detail": "..."} for permission/throttle errors and field-keyed dicts for validation errors.

### 5.1 Endpoint table

| Method | Path | Who | Notes |
|---|---|---|---|
| GET | /api/hostels/ | authenticated | list with score, paginated |
| GET | /api/hostels/{id}/ | authenticated | detail: info, score, cracked missions |
| GET | /api/participants/ | authenticated | list, paginated |
| GET | /api/participants/{id}/ | authenticated | detail |
| GET | /api/participants/me/ | participant | own profile |
| PATCH | /api/participants/{id}/ | owner or staff | owner may change handle only; hostel read-only for non-staff |
| DELETE | /api/participants/{id}/ | staff | |
| GET | /api/missions/ | authenticated | filter, search, paginate |
| POST | /api/missions/ | staff | create |
| GET | /api/missions/{id}/ | authenticated | |
| PUT/PATCH | /api/missions/{id}/ | staff: core fields; claimant: status only | see 6.3 |
| DELETE | /api/missions/{id}/ | staff | |
| POST | /api/missions/{id}/claim/ | participant | throttled 5/min per user |
| PATCH | /api/missions/{id}/status/ | claimant | body: {"status": "cracked"} |
| GET | /api/leaderboard/ | AllowAny | not paginated |
| GET | /api/schema/ | AllowAny | OpenAPI YAML/JSON |
| GET | /api/docs/ | AllowAny | Swagger UI |

Hostels are read-only through the API (seeded data). Staff manage them in Django admin if needed.

### 5.2 Response shapes

Mission (read):

```
{
  "id": 12,
  "codename": "BINARY_BLITZ",
  "brief": "Implement binary search on a rotated array",
  "points": 150,
  "difficulty": "hard",
  "status": "in_progress",          <- effective_status, not raw DB value
  "deadline": "2026-09-20T18:00:00+05:30",
  "is_expired": false,
  "claimed_by": {"id": 3, "handle": "arcco", "hostel": {"id": 5, "name": "<hostel name>"}},
  "hostel": {"id": 5, "name": "<hostel name>"},
  "created_at": "...",
  "updated_at": "..."
}
```

Unclaimed missions return "claimed_by": null, "hostel": null.

Hostel detail:

```
{
  "id": 5,
  "name": "<hostel name>",
  "score": 450,
  "cracked_count": 3,
  "cracked_missions": [
    {"id": 12, "codename": "BINARY_BLITZ", "points": 150, "difficulty": "hard",
     "claimed_by": {"id": 3, "handle": "arcco"}}
  ]
}
```

Hostel list items: id, name, score, cracked_count (no nested missions, to keep the list light).

Participant:

```
{"id": 3, "handle": "arcco", "username": "arcco_user", "hostel": {"id": 5, "name": "<hostel name>"}}
```

Leaderboard:

```
[
  {"rank": 1, "id": 5, "name": "<hostel>", "score": 450, "cracked_count": 3},
  {"rank": 2, "id": 9, "name": "<hostel>", "score": 300, "cracked_count": 2},
  {"rank": 2, "id": 1, "name": "<hostel>", "score": 300, "cracked_count": 1},
  {"rank": 4, "id": 2, "name": "<hostel>", "score": 0, "cracked_count": 0}
]
```

All 17 hostels appear, including zero-score ones. Ordering: score desc, then name asc. Rank computed in the ORM with Window(expression=Rank(), order_by=F("score").desc()) so ties share a rank.

### 5.3 Serializers

- HostelMiniSerializer: id, name.
- ParticipantMiniSerializer: id, handle, hostel (HostelMiniSerializer).
- ParticipantSerializer: id, handle, username (source="user.username", read-only), hostel (read as nested mini; write via hostel_id PrimaryKeyRelatedField, staff only).
- RegisterSerializer: username, password (write_only, run Django password validators), handle, hostel (id). create() wraps User + Participant in transaction.atomic().
- MissionSerializer:
  - status: SerializerMethodField-style read of effective_status; writable status handled by a separate write field or in validate() (see 6.3).
  - claimed_by: ParticipantMiniSerializer, read_only.
  - hostel: HostelMiniSerializer, read_only.
  - is_expired: read-only boolean.
  - Staff create/update validation: status on create must be unclaimed or expired (a staff user cannot create an in_progress or cracked mission with no claimant). If staff sets status back to unclaimed on an existing mission, clear claimed_by and hostel in the same save.
- MissionStatusSerializer: single field status, choices limited to in_progress and cracked.
- CrackedMissionSerializer: id, codename, points, difficulty, claimed_by (id, handle).
- HostelDetailSerializer: id, name, score, cracked_count, cracked_missions (CrackedMissionSerializer, many, sourced from a Prefetch of missions filtered to status="cracked").
- LeaderboardSerializer: rank, id, name, score, cracked_count.

Hostel viewset queryset: Hostel.objects.with_scores(), with prefetch on detail. No N+1 queries: mission list uses select_related("claimed_by__hostel", "hostel").

---

## 6. Competition rules (the core of the task)

### 6.1 Permission classes (competition/permissions.py)

- IsStaff: request.user.is_staff.
- IsParticipant: authenticated and hasattr(user, "participant").
- IsMissionClaimant (object-level): obj.claimed_by_id == request.user.participant.id.
- MissionAccessPolicy (used on MissionViewSet):
  - SAFE_METHODS: authenticated.
  - POST (create), DELETE: staff only.
  - PUT/PATCH on detail: staff passes. Non-staff passes has_permission only if participant, and has_object_permission only if claimant AND the payload keys are a subset of {"status"}. Any other key in the payload (points, codename, brief, difficulty, deadline, hostel, claimed_by) returns 403 with detail "Participants may only update mission status."
- IsOwnerOrStaff for participant PATCH.

Use explicit permission_classes / get_permissions() per action. Do not rely on the frontend.

### 6.2 Claiming - POST /api/missions/{id}/claim/

Order of checks:

1. Throttle (ClaimRateThrottle) - runs before everything; failed attempts count toward the limit.
2. Authenticated + IsParticipant, else 403.
3. Mission exists, else 404.
4. If mission.is_past_deadline() and status is not cracked -> 400 {"detail": "Mission has expired.", "code": "mission_expired"}.
5. If effective status is not unclaimed -> 409 {"detail": "Mission is not available to claim.", "code": "mission_unavailable"}.
6. Atomic conditional update (race-safe, works on SQLite):

```
with transaction.atomic():
    updated = Mission.objects.filter(
        pk=pk,
        status="unclaimed",
        claimed_by__isnull=True,
        deadline__gt=timezone.now(),
    ).update(
        status="in_progress",
        claimed_by=participant,
        hostel=participant.hostel,
        updated_at=timezone.now(),
    )
if updated == 0:
    re-fetch and return 400 mission_expired or 409 mission_unavailable as appropriate
```

7. Return 200 with the full MissionSerializer payload.

The request body is ignored. Any hostel or claimed_by value a client sends has no effect.

### 6.3 Status change

Primary route: PATCH /api/missions/{id}/status/ with {"status": "cracked"}. The generic PATCH /api/missions/{id}/ with only {"status": ...} behaves identically for the claimant (same service function).

Checks, in order:

1. IsParticipant, else 403.
2. IsMissionClaimant, else 403 {"detail": "You can only update missions you claimed."}.
3. Payload keys subset of {"status"}, else 403.
4. Deadline passed (and stored status is not cracked) -> 400 mission_expired.
5. Allowed transitions for participants:

| From (effective) | To | Result |
|---|---|---|
| in_progress | in_progress | 200, no-op |
| in_progress | cracked | 200 |
| cracked | anything | 400 "Cracked missions are final." |
| any | unclaimed or expired | 400 invalid transition |

Use a conditional update for the cracked transition too (filter status="in_progress", deadline__gt=now) so a crack submitted at the deadline boundary cannot slip through.

Staff PUT/PATCH on /api/missions/{id}/ may edit codename, brief, points, difficulty, deadline, and status (subject to the invariants in 3.4). Staff still cannot write hostel or claimed_by directly.

### 6.4 Summary of HTTP codes

| Situation | Code |
|---|---|
| Not logged in | 401 |
| Not staff / not participant / not claimant / forbidden field | 403 |
| Mission not found | 404 |
| Expired mission claim or status change, invalid transition, validation error | 400 |
| Mission already claimed / in progress / cracked | 409 |
| Claim rate limit exceeded | 429 |

---

## 7. Filtering, search, pagination

In competition/filters.py, MissionFilter(django_filters.FilterSet):

- status: ChoiceFilter with a custom method so it filters on effective status:
  - unclaimed -> status="unclaimed" AND deadline > now
  - in_progress -> status="in_progress" AND deadline > now
  - cracked -> status="cracked"
  - expired -> status="expired" OR (status in [unclaimed, in_progress] AND deadline <= now)
- difficulty: exact ChoiceFilter (e.g. ?difficulty=boss_level)
- hostel: ModelChoiceFilter on mission.hostel (e.g. ?hostel=5)

Search: SearchFilter with search_fields = ["codename", "brief"] (?search=binary).

Ordering (optional, cheap): OrderingFilter on deadline, points, created_at; default ordering by deadline.

Settings:

```
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.TokenAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 10,
    "DEFAULT_FILTER_BACKENDS": [
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
        "rest_framework.filters.OrderingFilter",
    ],
    "DEFAULT_THROTTLE_RATES": {"claim": "5/min"},
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
}
```

Page size lives only in settings. No page_size in any view. Leaderboard sets pagination_class = None.

---

## 8. Rate limiting

competition/throttles.py:

```
class ClaimRateThrottle(UserRateThrottle):
    scope = "claim"
```

Attach only to the claim action: @action(detail=True, methods=["post"], throttle_classes=[ClaimRateThrottle]). No global throttle on other endpoints.

CACHES uses LocMemCache (explicit in settings). Tests call cache.clear() in setUp. The 6th claim attempt within 60 seconds by the same user returns 429 with a Retry-After header, regardless of whether earlier attempts succeeded.

---

## 9. API documentation

- drf-spectacular installed, SPECTACULAR_SETTINGS with TITLE "Operation Golden Keyboard API", VERSION "1.0.0", DESCRIPTION summarising the rules.
- /api/schema/ -> SpectacularAPIView
- /api/docs/ -> SpectacularSwaggerView
- @extend_schema on claim, status, leaderboard, register, and me actions: request body, response serializers, and documented error responses (400, 403, 404, 409, 429) with OpenApiResponse descriptions.
- @extend_schema_view on MissionViewSet documenting the status, difficulty, hostel and search query parameters with OpenApiParameter.
- TokenAuthentication shown as a security scheme so "Authorize" works in Swagger.
- `python manage.py spectacular --validate --fail-on-warn` must pass with zero warnings.

---

## 10. Seed data and Postman

### 10.1 seed_demo management command

`python manage.py seed_demo` (idempotent, safe to rerun, has a --reset flag):

- Staff user: staff / staffpass123 (is_staff=True), with token.
- Participant alice (handle alice, hostel A) and bob (handle bob, hostel B), passwords alicepass123 / bobpass123, with tokens.
- Missions:
  - 15 unclaimed missions with future deadlines, mixed difficulties, at least one boss_level, at least one with "binary" in codename or brief (for search and pagination demos).
  - 1 mission already claimed by bob, in_progress (for "modify another's mission" and "claim already claimed").
  - 2 cracked missions, one per hostel, so the leaderboard is non-trivial.
  - 1 mission with stored status unclaimed but deadline in the past (the "expired but DB says unclaimed" case).
- Prints all tokens and key mission ids at the end.

### 10.2 Postman environment (golden_keyboard.postman_environment.json)

Variables: base_url (http://127.0.0.1:8000), staff_token, alice_token, bob_token, mission_id, claimed_mission_id, expired_mission_id, throttle_mission_id, hostel_id, participant_id, created_mission_id.

No hardcoded URLs or ids anywhere in the collection. Every request uses {{base_url}} and variables. Auth header: Authorization: Token {{alice_token}} etc.

### 10.3 Postman collection (golden_keyboard.postman_collection.json)

Folders, run in order. Each request has a Tests script asserting the status code, and where relevant pm.environment.set(...) to capture ids and tokens.

0. Setup
   - Get staff token (captures staff_token)
   - Get alice token, Get bob token
   - Register new participant (201)
   - Register duplicate handle (400)
1. Hostels
   - List hostels (200, 17 total)
   - Hostel detail (200, has score and cracked_missions)
2. Participants
   - List participants
   - Get participant detail
   - Get me
   - Alice patches own handle (200)
   - Alice patches bob's handle (403)
3. Missions CRUD
   - Staff creates mission (201, captures created_mission_id)
   - List missions (200)
   - Get mission detail (200, claimed_by nested)
   - Staff edits mission points (200)               <- required case 5
   - Alice creates mission (403)
   - Staff deletes mission (204)
4. Filtering, search, pagination
   - ?status=unclaimed
   - ?difficulty=boss_level
   - ?hostel={{hostel_id}}
   - ?status=expired (includes the past-deadline "unclaimed" mission)
   - ?search=binary
   - ?page=2 (asserts count, next, previous present)
5. Claiming and status
   - Alice claims available mission (200)          <- required case 1
   - Bob claims same mission (409)                  <- required case 2
   - Alice sets status cracked (200)
   - Leaderboard reflects alice's hostel score increase (200)
6. Rule violations
   - Alice changes status of bob's mission (403)    <- required case 3
   - Alice patches points on her own mission (403)  <- required case 4
   - Alice patches hostel on her own mission (403)
   - Alice claims expired mission (400)             <- required case 6
   - Unauthenticated claim (401)
7. Leaderboard
   - Get leaderboard (200, sorted desc, 17 entries)
8. Rate limiting                                     <- required case 7
   - "Claim spam" request with a pre-request script that fires 5 claim attempts via pm.sendRequest against {{throttle_mission_id}} using bob's token, then the request itself (6th attempt) asserts 429.
   - README also explains the alternative: run this single request 6 times in Collection Runner.

Folder 8 should run last and after waiting at least 60 seconds from folder 5/6 if those used bob's token for claims.

---

## 11. Tests (competition/tests/)

Every case below is a separate test method. All must pass with `python manage.py test`.

Models and scores

- Score is 0 for a hostel with no cracked missions.
- Only cracked missions contribute; in_progress and expired points do not.
- Changing a mission from cracked to unclaimed (staff) drops the hostel score immediately (proves no stored score).
- effective_status returns expired for unclaimed and in_progress past deadline; cracked stays cracked past deadline.

Leaderboard

- Returns all 17 hostels, ordered by score desc.
- Ties share the same rank.
- Anonymous access allowed.

Claims

- Participant claims available mission -> 200, status in_progress, claimed_by set, hostel equals participant's hostel.
- Client-supplied hostel in claim body is ignored.
- Claim in_progress mission -> 409.
- Claim cracked mission -> 409.
- Claim stored-expired mission -> 400.
- Claim mission with stored status unclaimed but past deadline -> 400.
- Staff user without participant profile claims -> 403.
- Unauthenticated claim -> 401.
- Race: simulate two claims by calling the claim service twice for the same mission with different participants (second sees updated == 0) -> exactly one succeeds.

Permissions and editing

- Claimant sets in_progress -> cracked -> 200.
- Non-claimant participant changes status -> 403.
- Participant PATCH with points / codename / brief / difficulty / deadline -> 403 (one test per field, or a subTest loop).
- Participant PATCH with hostel -> 403.
- Participant creates mission -> 403; participant deletes mission -> 403.
- Staff edits points -> 200.
- Staff creates mission with status cracked and no claimant -> 400.
- Cracked -> in_progress by claimant -> 400.
- Participant sets status unclaimed or expired -> 400.
- Participant cannot change own hostel via participant PATCH -> 403 or field ignored (pick one, test it).

Deadlines

- Status change on in_progress mission after deadline -> 400.
- Use unittest.mock.patch on django.utils.timezone.now (or create missions with past deadlines) - no sleeps.

Filtering, search, pagination

- ?status=unclaimed excludes past-deadline unclaimed missions.
- ?status=expired includes them.
- ?difficulty=boss_level, ?hostel=<id> return correct subsets.
- ?search=binary matches codename and brief.
- Response contains count, next, previous, results; results length equals settings PAGE_SIZE; override_settings changing PAGE_SIZE changes result length (proves it is settings-driven).

Throttling

- 5 claim attempts succeed or fail normally, the 6th returns 429 (cache cleared in setUp).
- Throttle does not apply to GET /api/missions/.

Docs

- GET /api/docs/ -> 200; GET /api/schema/ -> 200 and contains /api/missions/{id}/claim/.

---

## 12. Ambiguities in the problem statement and decisions taken

Change these if your evaluator or seniors specify otherwise.

1. Claiming sets status to in_progress. The spec says only the claimant can set in_progress or cracked, and also lists in_progress as "not available to claim", so a claimed mission must leave unclaimed. Claim therefore sets in_progress directly; the claimant's in_progress update is an idempotent no-op.
2. Hostel is stored on Mission (not just read through claimed_by) because filtering by ?hostel=<id> and "missions should keep track of which hostel" both require it. It is written only by the claim action.
3. Expiry is computed, not persisted. No scheduled job. The stored status may say unclaimed while the API reports expired.
4. Cracked missions keep their points after the deadline passes.
5. 409 Conflict for already-claimed missions, 400 for expired. If a single code is preferred, use 400 for both and keep the "code" field in the body.
6. Participants cannot unclaim a mission. Not in the spec.
7. Staff may create missions with past deadlines (needed to demo expired cases). Staff cannot set claimed_by or hostel directly.
8. Leaderboard is public (AllowAny); everything else requires authentication.
9. Difficulty values and the 17 hostel names come from the previous task and are not in this document. Fill them in constants.py before building.

---

## 13. Build plan for Claude Code

Build in this order. Each phase ends green (`python manage.py test` passes, `python manage.py check` clean) before the next starts. Commit after each phase.

Phase 1 - Scaffold and models
- Project, app, settings (REST_FRAMEWORK dict, CACHES, USE_TZ, installed apps), requirements.txt.
- constants.py, models, HostelQuerySet.with_scores, Mission.effective_status, data migration seeding hostels, admin registration.
- Tests: models and scores.

Phase 2 - Auth, serializers, read APIs
- Token auth, register, token endpoints.
- All serializers, Hostel and Participant viewsets, Mission read endpoints, leaderboard with Window Rank.
- Tests: leaderboard, nested response shapes, N+1 check with assertNumQueries on mission list.

Phase 3 - Rules
- permissions.py, claim action with conditional update, status action, staff CRUD validation.
- Tests: claims, permissions, deadlines.

Phase 4 - Filtering, pagination, throttling
- filters.py with effective-status filter, SearchFilter, pagination from settings, ClaimRateThrottle.
- Tests: filters, throttling.

Phase 5 - Docs
- drf-spectacular URLs, extend_schema annotations, spectacular --validate --fail-on-warn passes.

Phase 6 - Seed and Postman
- seed_demo command.
- Postman collection + environment JSON exactly per section 10. Validate the JSON loads (python -m json.tool). Run the server with seeded data and manually hit at least the 7 required cases with curl to confirm codes match the collection's assertions.

Phase 7 - README
- Setup: venv, pip install, migrate, seed_demo, runserver.
- How to import the Postman collection and environment, run order, rate-limit demo instructions.
- Endpoint table, rule summary, list of decisions from section 12.

### Definition of done

- All 15 deliverables in the problem statement map to something in this repo (checklist in README).
- `python manage.py test` all green.
- `python manage.py spectacular --validate --fail-on-warn` clean.
- /api/docs/ renders and Authorize with a token works.
- Postman collection runs top to bottom against a fresh `seed_demo` with every assertion passing.
- grep finds no code that writes a score field or increments hostel points.

### Kickoff prompt (paste into Claude Code with this file in the repo root)

```
Read golden_keyboard_prd.md fully before writing any code. Build the project it
describes, following the phase order in section 13. Do not start a phase until the
previous one has passing tests and a commit. Before Phase 1, confirm the 17 hostel
names and the difficulty choices in section 3.1 with me if they are not already in
constants.py - do not invent them. Do not add features that are not in the PRD.
Where the PRD lists a decision in section 12, implement it as written. At the end,
run the full test suite, spectacular validation, and the curl checks for the 7
required Postman cases, and report the results.
```
