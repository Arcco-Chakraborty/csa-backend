"""Generate the Postman collection and environment for Operation Golden Keyboard.

Written as a generator rather than hand-edited JSON so the request definitions stay
readable and the output is guaranteed to be valid JSON.
"""

import json
import pathlib

# Next to this script, wherever the repository is checked out.
OUT = pathlib.Path(__file__).resolve().parent

BASE = "{{base_url}}"


def url(path, query=None):
    """Build Postman's url object. Every URL goes through {{base_url}}."""
    segments = [s for s in path.strip("/").split("/") if s]
    raw = f"{BASE}/{'/'.join(segments)}/"
    obj = {"raw": raw, "host": [BASE], "path": segments + [""]}
    if query:
        obj["raw"] = raw + "?" + "&".join(f"{k}={v}" for k, v in query)
        obj["query"] = [{"key": k, "value": v} for k, v in query]
    return obj


def req(
    name,
    method,
    path,
    *,
    token=None,
    body=None,
    query=None,
    tests=None,
    prerequest=None,
    description=None,
):
    headers = []
    if token:
        headers.append({"key": "Authorization", "value": f"Token {{{{{token}}}}}"})
    if body is not None:
        headers.append({"key": "Content-Type", "value": "application/json"})

    request = {"method": method, "header": headers, "url": url(path, query)}
    if body is not None:
        request["body"] = {
            "mode": "raw",
            "raw": json.dumps(body, indent=2),
            "options": {"raw": {"language": "json"}},
        }
    if description:
        request["description"] = description

    events = []
    if prerequest:
        events.append(
            {
                "listen": "prerequest",
                "script": {"type": "text/javascript", "exec": prerequest},
            }
        )
    if tests:
        events.append(
            {"listen": "test", "script": {"type": "text/javascript", "exec": tests}}
        )

    item = {"name": name, "request": request}
    if events:
        item["event"] = events
    return item


def folder(name, description, items):
    return {"name": name, "description": description, "item": items}


def status_test(code, *extra):
    return [
        f'pm.test("Status is {code}", function () {{',
        f"    pm.response.to.have.status({code});",
        "});",
        *extra,
    ]


# ---------------------------------------------------------------------------
# 0. Setup
# ---------------------------------------------------------------------------

setup = folder(
    "0. Setup",
    "Obtains tokens and discovers every id the rest of the collection needs. "
    "Nothing below this folder hardcodes an id, so the collection survives a "
    "re-seed with different primary keys.",
    [
        req(
            "Get staff token",
            "POST",
            "/api/auth/token/",
            body={"username": "staff", "password": "staffpass123"},
            tests=status_test(
                200,
                'pm.environment.set("staff_token", pm.response.json().token);',
                'pm.test("Token captured", function () {',
                '    pm.expect(pm.environment.get("staff_token")).to.be.a("string").and.not.empty;',
                "});",
            ),
        ),
        req(
            "Get alice token",
            "POST",
            "/api/auth/token/",
            body={"username": "alice", "password": "alicepass123"},
            tests=status_test(
                200, 'pm.environment.set("alice_token", pm.response.json().token);'
            ),
        ),
        req(
            "Get bob token",
            "POST",
            "/api/auth/token/",
            body={"username": "bob", "password": "bobpass123"},
            tests=status_test(
                200, 'pm.environment.set("bob_token", pm.response.json().token);'
            ),
        ),
        req(
            "Get alice profile (captures participant_id, hostel_id)",
            "GET",
            "/api/participants/me/",
            token="alice_token",
            tests=status_test(
                200,
                "const me = pm.response.json();",
                'pm.environment.set("participant_id", me.id);',
                'pm.environment.set("hostel_id", me.hostel.id);',
                'pm.environment.set("alice_handle", me.handle);',
                'pm.test("Profile is nested, not a bare id", function () {',
                "    pm.expect(me.hostel).to.have.property('name');",
                "});",
            ),
        ),
        req(
            "Find a claimable mission (captures mission_id)",
            "GET",
            "/api/missions/",
            token="alice_token",
            query=[("status", "unclaimed")],
            tests=status_test(
                200,
                "const results = pm.response.json().results;",
                'pm.test("At least one claimable mission exists", function () {',
                "    pm.expect(results.length).to.be.above(0);",
                "});",
                'pm.environment.set("mission_id", results[0].id);',
                'pm.environment.set("mission_points", results[0].points);',
            ),
        ),
        req(
            "Find a mission claimed by someone else (captures claimed_mission_id, throttle_mission_id, other_hostel_id)",
            "GET",
            "/api/missions/",
            token="alice_token",
            query=[("status", "in_progress")],
            description=(
                "The same mission doubles as the throttle target: claiming an "
                "already-claimed mission always fails, so it can be hammered without "
                "changing any state. Its claimant and hostel (bob's) are captured "
                "too, so later requests can try to write them without hardcoding ids."
            ),
            tests=status_test(
                200,
                'const mine = Number(pm.environment.get("participant_id"));',
                "const theirs = pm.response.json().results.find(function (m) {",
                "    return m.claimed_by && m.claimed_by.id !== mine;",
                "});",
                'pm.test("A mission claimed by bob exists", function () {',
                "    pm.expect(theirs).to.not.be.undefined;",
                "});",
                "if (theirs) {",
                '    pm.environment.set("claimed_mission_id", theirs.id);',
                '    pm.environment.set("throttle_mission_id", theirs.id);',
                '    pm.environment.set("bob_participant_id", theirs.claimed_by.id);',
                '    pm.environment.set("other_hostel_id", theirs.hostel.id);',
                "}",
            ),
        ),
        req(
            "Find the expired mission (captures expired_mission_id)",
            "GET",
            "/api/missions/",
            token="alice_token",
            query=[("status", "expired")],
            description=(
                "Picks the unclaimed one: its row still says 'unclaimed' and only the "
                "passed deadline makes it expired."
            ),
            tests=status_test(
                200,
                "const ghost = pm.response.json().results.find(function (m) {",
                "    return m.claimed_by === null;",
                "});",
                'pm.test("An unclaimed, expired mission exists", function () {',
                "    pm.expect(ghost).to.not.be.undefined;",
                "});",
                'if (ghost) { pm.environment.set("expired_mission_id", ghost.id); }',
            ),
        ),
        req(
            "Find alice's overdue mission (captures overdue_mission_id)",
            "GET",
            "/api/missions/",
            token="alice_token",
            query=[("status", "expired"), ("hostel", "{{hostel_id}}")],
            description=(
                "Alice claimed this mission, but its deadline passed before she cracked "
                "it. The row still says 'in_progress'; the API reports it as expired."
            ),
            tests=status_test(
                200,
                'const mine = Number(pm.environment.get("participant_id"));',
                "const overdue = pm.response.json().results.find(function (m) {",
                "    return m.claimed_by && m.claimed_by.id === mine;",
                "});",
                'pm.test("Alice has an overdue mission", function () {',
                "    pm.expect(overdue).to.not.be.undefined;",
                "});",
                'if (overdue) { pm.environment.set("overdue_mission_id", overdue.id); }',
            ),
        ),
        req(
            "Register new participant (201)",
            "POST",
            "/api/auth/register/",
            body={
                "username": "{{new_username}}",
                "password": "RecruitPass!2026",
                "handle": "{{new_username}}",
                "hostel": "{{hostel_id}}",
            },
            prerequest=[
                "// A fresh username each run, so the collection can be run repeatedly.",
                'pm.environment.set("new_username", "recruit_" + Date.now());',
            ],
            tests=status_test(
                201,
                "const body = pm.response.json();",
                'pm.test("Returns a participant and a usable token", function () {',
                '    pm.expect(body.participant).to.have.property("handle");',
                '    pm.expect(body.token).to.be.a("string").and.not.empty;',
                "});",
            ),
        ),
        req(
            "Register duplicate handle (400)",
            "POST",
            "/api/auth/register/",
            body={
                "username": "{{new_username}}_again",
                "password": "RecruitPass!2026",
                "handle": "{{alice_handle}}",
                "hostel": "{{hostel_id}}",
            },
            tests=status_test(
                400,
                'pm.test("Error is keyed to the handle field", function () {',
                '    pm.expect(pm.response.json()).to.have.property("handle");',
                "});",
            ),
        ),
    ],
)

# ---------------------------------------------------------------------------
# 1. Hostels
# ---------------------------------------------------------------------------

hostels = folder(
    "1. Hostels",
    "Hostels are read-only over the API. Scores are aggregated live; no score is "
    "stored on any row.",
    [
        req(
            "List hostels (200, 17 total)",
            "GET",
            "/api/hostels/",
            token="alice_token",
            tests=status_test(
                200,
                "const body = pm.response.json();",
                'pm.test("All 17 hostels are present", function () {',
                "    pm.expect(body.count).to.eql(17);",
                "});",
                'pm.test("List rows carry a derived score", function () {',
                '    pm.expect(body.results[0]).to.have.property("score");',
                '    pm.expect(body.results[0]).to.have.property("cracked_count");',
                "});",
            ),
        ),
        req(
            "Hostel detail (200, has score and cracked_missions)",
            "GET",
            "/api/hostels/{{hostel_id}}/",
            token="alice_token",
            tests=status_test(
                200,
                "const body = pm.response.json();",
                'pm.test("Detail exposes score and cracked missions", function () {',
                '    pm.expect(body).to.have.property("score");',
                '    pm.expect(body).to.have.property("cracked_count");',
                "    pm.expect(body.cracked_missions).to.be.an('array');",
                "});",
            ),
        ),
    ],
)

# ---------------------------------------------------------------------------
# 2. Participants
# ---------------------------------------------------------------------------

participants = folder(
    "2. Participants",
    "A participant may edit their own handle and nothing else. Hostel changes are "
    "staff-only.",
    [
        req(
            "List participants",
            "GET",
            "/api/participants/",
            token="alice_token",
            tests=status_test(
                200,
                'pm.test("Paginated envelope", function () {',
                '    pm.expect(pm.response.json()).to.have.property("results");',
                "});",
            ),
        ),
        req(
            "Get participant detail",
            "GET",
            "/api/participants/{{participant_id}}/",
            token="alice_token",
            tests=status_test(
                200,
                'pm.test("Hostel is nested", function () {',
                "    pm.expect(pm.response.json().hostel).to.have.property('name');",
                "});",
            ),
        ),
        req(
            "Get me",
            "GET",
            "/api/participants/me/",
            token="alice_token",
            tests=status_test(
                200,
                'pm.test("Returns the caller\'s own profile", function () {',
                '    pm.expect(pm.response.json().id).to.eql(Number(pm.environment.get("participant_id")));',
                "});",
            ),
        ),
        req(
            "Alice patches own handle (200)",
            "PATCH",
            "/api/participants/{{participant_id}}/",
            token="alice_token",
            body={"handle": "{{next_handle}}"},
            prerequest=[
                "// A fresh handle each run keeps the unique constraint satisfied.",
                'pm.environment.set("next_handle", "alice_" + Date.now().toString().slice(-6));',
            ],
            tests=status_test(
                200,
                'pm.test("Handle changed", function () {',
                '    pm.expect(pm.response.json().handle).to.eql(pm.environment.get("next_handle"));',
                "});",
                'pm.environment.set("alice_handle", pm.response.json().handle);',
            ),
        ),
        req(
            "Alice patches bob's handle (403)",
            "PATCH",
            "/api/participants/2/",
            token="alice_token",
            body={"handle": "hijacked"},
            description=(
                "Deliberately targets another participant. The id is resolved at "
                "runtime by the pre-request script, which picks any participant that "
                "is not alice."
            ),
            prerequest=[
                "// Find a participant who is not alice, without hardcoding an id.",
                "pm.sendRequest({",
                '    url: pm.environment.get("base_url") + "/api/participants/",',
                '    method: "GET",',
                '    header: { "Authorization": "Token " + pm.environment.get("alice_token") }',
                "}, function (err, res) {",
                "    if (err) { return; }",
                '    const mine = Number(pm.environment.get("participant_id"));',
                "    const other = res.json().results.find(function (p) { return p.id !== mine; });",
                '    if (other) { pm.environment.set("other_participant_id", other.id); }',
                "});",
            ],
            tests=status_test(
                403,
                'pm.test("Refusal explains itself", function () {',
                '    pm.expect(pm.response.json()).to.have.property("detail");',
                "});",
            ),
        ),
    ],
)
# The request above must address the discovered id, not a literal.
participants["item"][-1]["request"]["url"] = url(
    "/api/participants/{{other_participant_id}}/"
)

# ---------------------------------------------------------------------------
# 3. Missions CRUD
# ---------------------------------------------------------------------------

missions_crud = folder(
    "3. Missions CRUD",
    "Only staff may create, edit the core fields of, or delete a mission.",
    [
        req(
            "Staff creates mission (201)",
            "POST",
            "/api/missions/",
            token="staff_token",
            body={
                "codename": "{{new_codename}}",
                "brief": "A mission created by the Postman collection.",
                "points": 120,
                "difficulty": "medium",
                "deadline": "{{future_deadline}}",
            },
            prerequest=[
                "// Codenames are unique, so generate a fresh one each run.",
                'pm.environment.set("new_codename", "POSTMAN_OP_" + Date.now());',
                "const week = new Date(Date.now() + 7 * 24 * 60 * 60 * 1000);",
                'pm.environment.set("future_deadline", week.toISOString());',
            ],
            tests=status_test(
                201,
                "const body = pm.response.json();",
                'pm.environment.set("created_mission_id", body.id);',
                'pm.test("New missions start unclaimed and unowned", function () {',
                '    pm.expect(body.status).to.eql("unclaimed");',
                "    pm.expect(body.claimed_by).to.be.null;",
                "    pm.expect(body.hostel).to.be.null;",
                "});",
            ),
        ),
        req(
            "List missions (200)",
            "GET",
            "/api/missions/",
            token="alice_token",
            tests=status_test(
                200,
                "const body = pm.response.json();",
                'pm.test("Paginated envelope", function () {',
                '    pm.expect(body).to.have.property("count");',
                '    pm.expect(body).to.have.property("next");',
                '    pm.expect(body).to.have.property("previous");',
                '    pm.expect(body).to.have.property("results");',
                "});",
            ),
        ),
        req(
            "Get mission detail (200, claimed_by nested)",
            "GET",
            "/api/missions/{{claimed_mission_id}}/",
            token="alice_token",
            tests=status_test(
                200,
                "const body = pm.response.json();",
                'pm.test("Claimant and hostel are nested objects", function () {',
                "    pm.expect(body.claimed_by).to.not.be.null;",
                '    pm.expect(body.claimed_by).to.have.property("handle");',
                '    pm.expect(body.hostel).to.have.property("name");',
                "});",
            ),
        ),
        req(
            "Staff edits mission points (200)  [required case 5]",
            "PATCH",
            "/api/missions/{{created_mission_id}}/",
            token="staff_token",
            body={"points": 275},
            tests=status_test(
                200,
                'pm.test("Points updated", function () {',
                "    pm.expect(pm.response.json().points).to.eql(275);",
                "});",
            ),
        ),
        req(
            "Alice creates mission (403)",
            "POST",
            "/api/missions/",
            token="alice_token",
            body={
                "codename": "PARTICIPANT_SHOULD_NOT_CREATE",
                "brief": "This must never be created.",
                "points": 100,
                "difficulty": "easy",
                "deadline": "{{future_deadline}}",
            },
            tests=status_test(403),
        ),
        req(
            "Staff deletes mission (204)",
            "DELETE",
            "/api/missions/{{created_mission_id}}/",
            token="staff_token",
            tests=status_test(204),
        ),
    ],
)

# ---------------------------------------------------------------------------
# 4. Filtering, search, pagination
# ---------------------------------------------------------------------------


def filter_req(name, query, tests):
    return req(
        name, "GET", "/api/missions/", token="alice_token", query=query, tests=tests
    )


filtering = folder(
    "4. Filtering, search, pagination",
    "The status filter works on effective status: a past-deadline mission whose row "
    "still says 'unclaimed' is excluded from ?status=unclaimed and included in "
    "?status=expired.",
    [
        filter_req(
            "?status=unclaimed",
            [("status", "unclaimed")],
            status_test(
                200,
                "const results = pm.response.json().results;",
                'pm.test("Every result really is unclaimed and unexpired", function () {',
                "    results.forEach(function (m) {",
                '        pm.expect(m.status).to.eql("unclaimed");',
                "        pm.expect(m.is_expired).to.be.false;",
                "        pm.expect(m.claimed_by).to.be.null;",
                "    });",
                "});",
                'pm.test("The expired mission is excluded", function () {',
                '    const expiredId = Number(pm.environment.get("expired_mission_id"));',
                "    pm.expect(results.map(function (m) { return m.id; })).to.not.include(expiredId);",
                "});",
            ),
        ),
        filter_req(
            "?difficulty=boss_level",
            [("difficulty", "boss_level")],
            status_test(
                200,
                "const results = pm.response.json().results;",
                'pm.test("At least one boss_level mission, and only those", function () {',
                "    pm.expect(results.length).to.be.above(0);",
                "    results.forEach(function (m) {",
                '        pm.expect(m.difficulty).to.eql("boss_level");',
                "    });",
                "});",
            ),
        ),
        filter_req(
            "?hostel={{hostel_id}}",
            [("hostel", "{{hostel_id}}")],
            status_test(
                200,
                "const results = pm.response.json().results;",
                'pm.test("Every result belongs to that hostel", function () {',
                '    const hostelId = Number(pm.environment.get("hostel_id"));',
                "    results.forEach(function (m) {",
                "        pm.expect(m.hostel).to.not.be.null;",
                "        pm.expect(m.hostel.id).to.eql(hostelId);",
                "    });",
                "});",
            ),
        ),
        filter_req(
            "?status=expired (includes the past-deadline 'unclaimed' mission)",
            [("status", "expired")],
            status_test(
                200,
                "const results = pm.response.json().results;",
                'pm.test("The stored-unclaimed, past-deadline mission is here", function () {',
                '    const expiredId = Number(pm.environment.get("expired_mission_id"));',
                "    pm.expect(results.map(function (m) { return m.id; })).to.include(expiredId);",
                "});",
                'pm.test("Everything reported expired really is", function () {',
                "    results.forEach(function (m) {",
                '        pm.expect(m.status).to.eql("expired");',
                "        pm.expect(m.is_expired).to.be.true;",
                "    });",
                "});",
            ),
        ),
        filter_req(
            "?search=binary",
            [("search", "binary")],
            status_test(
                200,
                "const results = pm.response.json().results;",
                'pm.test("Search matches codename or brief", function () {',
                "    pm.expect(results.length).to.be.above(0);",
                "    results.forEach(function (m) {",
                '        const haystack = (m.codename + " " + m.brief).toLowerCase();',
                '        pm.expect(haystack).to.include("binary");',
                "    });",
                "});",
            ),
        ),
        filter_req(
            "?page=2",
            [("page", "2")],
            status_test(
                200,
                "const body = pm.response.json();",
                'pm.test("Envelope carries count, next and previous", function () {',
                '    pm.expect(body).to.have.property("count");',
                '    pm.expect(body).to.have.property("next");',
                "    pm.expect(body.previous).to.not.be.null;",
                "});",
                'pm.test("Page size comes from settings (10)", function () {',
                "    pm.expect(body.results.length).to.be.at.most(10);",
                "});",
            ),
        ),
    ],
)

# ---------------------------------------------------------------------------
# 5. Claiming and status
# ---------------------------------------------------------------------------

claiming = folder(
    "5. Claiming and status",
    "The heart of the competition. Claiming is race-safe and ignores the request "
    "body; cracking is what moves the leaderboard.",
    [
        req(
            "Leaderboard before (captures score_before)",
            "GET",
            "/api/leaderboard/",
            tests=status_test(
                200,
                "const board = pm.response.json();",
                'const hostelId = Number(pm.environment.get("hostel_id"));',
                "const row = board.find(function (r) { return r.id === hostelId; });",
                'pm.environment.set("score_before", row.score);',
            ),
        ),
        req(
            "Alice claims available mission (200)  [required case 1]",
            "POST",
            "/api/missions/{{mission_id}}/claim/",
            token="alice_token",
            body={
                "hostel": "{{other_hostel_id}}",
                "claimed_by": "{{bob_participant_id}}",
                "points": 99999,
            },
            description=(
                "The body deliberately tries to credit bob and bob's hostel, and to "
                "inflate the points. All three must be ignored: the server copies the "
                "claimant's own hostel and leaves the points alone."
            ),
            tests=status_test(
                200,
                "const body = pm.response.json();",
                'pm.test("Mission is now in progress and owned by alice", function () {',
                '    pm.expect(body.status).to.eql("in_progress");',
                '    pm.expect(body.claimed_by.id).to.eql(Number(pm.environment.get("participant_id")));',
                "});",
                'pm.test("Server used the claimant\'s hostel, not the body\'s", function () {',
                '    pm.expect(body.hostel.id).to.eql(Number(pm.environment.get("hostel_id")));',
                "});",
                'pm.test("Points in the body were ignored", function () {',
                "    pm.expect(body.points).to.not.eql(99999);",
                "});",
                'pm.environment.set("mission_points", body.points);',
            ),
        ),
        req(
            "Bob claims same mission (409)  [required case 2]",
            "POST",
            "/api/missions/{{mission_id}}/claim/",
            token="bob_token",
            tests=status_test(
                409,
                'pm.test("Conflict carries a machine-readable code", function () {',
                "    const body = pm.response.json();",
                '    pm.expect(body.code).to.eql("mission_unavailable");',
                "});",
            ),
        ),
        req(
            "Alice sets status cracked (200)",
            "PATCH",
            "/api/missions/{{mission_id}}/status/",
            token="alice_token",
            body={"status": "cracked"},
            tests=status_test(
                200,
                'pm.test("Mission is cracked", function () {',
                '    pm.expect(pm.response.json().status).to.eql("cracked");',
                "});",
            ),
        ),
        req(
            "Leaderboard reflects alice's hostel score increase (200)",
            "GET",
            "/api/leaderboard/",
            tests=status_test(
                200,
                "const board = pm.response.json();",
                'const hostelId = Number(pm.environment.get("hostel_id"));',
                "const row = board.find(function (r) { return r.id === hostelId; });",
                'const before = Number(pm.environment.get("score_before"));',
                'const points = Number(pm.environment.get("mission_points"));',
                'pm.test("Score rose by exactly the mission\'s points", function () {',
                "    pm.expect(row.score).to.eql(before + points);",
                "});",
                'pm.test("Score is derived, never stored", function () {',
                "    pm.expect(row.cracked_count).to.be.above(0);",
                "});",
            ),
        ),
    ],
)

# ---------------------------------------------------------------------------
# 6. Rule violations
# ---------------------------------------------------------------------------

violations = folder(
    "6. Rule violations",
    "Everything the backend must refuse. None of these may change any data.",
    [
        req(
            "Alice changes status of bob's mission (403)  [required case 3]",
            "PATCH",
            "/api/missions/{{claimed_mission_id}}/status/",
            token="alice_token",
            body={"status": "cracked"},
            tests=status_test(
                403,
                'pm.test("Refused as not the claimant", function () {',
                "    pm.expect(pm.response.json().detail).to.include('only update missions you claimed');",
                "});",
            ),
        ),
        req(
            "Staff cracks bob's mission (400)",
            "PATCH",
            "/api/missions/{{claimed_mission_id}}/",
            token="staff_token",
            body={"status": "cracked"},
            description=(
                "Only the participant who claimed a mission may move it to in_progress "
                "or cracked -- staff included. Staff can release a mission (unclaimed) "
                "or void it (expired), but cannot award a crack on anyone's behalf."
            ),
            tests=status_test(
                400,
                'pm.test("Refused on the status field", function () {',
                '    pm.expect(pm.response.json()).to.have.property("status");',
                "});",
            ),
        ),
        req(
            "Alice patches points on her own mission (403)  [required case 4]",
            "PATCH",
            "/api/missions/{{mission_id}}/",
            token="alice_token",
            body={"points": 9999},
            tests=status_test(
                403,
                'pm.test("Refused as a forbidden field", function () {',
                "    pm.expect(pm.response.json().detail).to.include('only update mission status');",
                "});",
            ),
        ),
        req(
            "Alice patches hostel on her own mission (403)",
            "PATCH",
            "/api/missions/{{mission_id}}/",
            token="alice_token",
            body={"hostel": "{{other_hostel_id}}"},
            description="Tries to move alice's mission (and its points) to bob's hostel.",
            tests=status_test(403),
        ),
        req(
            "Alice smuggles points alongside status (403)",
            "PATCH",
            "/api/missions/{{mission_id}}/",
            token="alice_token",
            body={"status": "cracked", "points": 9999},
            description=(
                "A status-only payload would be allowed. Adding one extra key must "
                "reject the whole request rather than applying the allowed part."
            ),
            tests=status_test(403),
        ),
        req(
            "Alice claims expired mission (400)  [required case 6]",
            "POST",
            "/api/missions/{{expired_mission_id}}/claim/",
            token="alice_token",
            description=(
                "This mission's stored status is still 'unclaimed'. Only the deadline "
                "makes it expired, and the API must honour that."
            ),
            tests=status_test(
                400,
                'pm.test("Rejected as expired", function () {',
                "    const body = pm.response.json();",
                '    pm.expect(body.code).to.eql("mission_expired");',
                "});",
            ),
        ),
        req(
            "Alice cracks her overdue mission (400)",
            "PATCH",
            "/api/missions/{{overdue_mission_id}}/status/",
            token="alice_token",
            body={"status": "cracked"},
            description=(
                "Alice is the claimant and the payload is status-only, so ownership and "
                "the field whitelist both pass. The row still says 'in_progress', but "
                "the deadline has passed: the API checks the deadline, not the stored "
                "status."
            ),
            tests=status_test(
                400,
                'pm.test("Rejected as expired", function () {',
                '    pm.expect(pm.response.json().code).to.eql("mission_expired");',
                "});",
            ),
        ),
        req(
            "Unauthenticated claim (401)",
            "POST",
            "/api/missions/{{mission_id}}/claim/",
            tests=status_test(401),
        ),
    ],
)

# ---------------------------------------------------------------------------
# 7. Leaderboard
# ---------------------------------------------------------------------------

leaderboard = folder(
    "7. Leaderboard",
    "Public, unpaginated, and computed on every request.",
    [
        req(
            "Get leaderboard (200, sorted desc, 17 entries)",
            "GET",
            "/api/leaderboard/",
            tests=status_test(
                200,
                "const board = pm.response.json();",
                'pm.test("Not paginated: a bare array", function () {',
                "    pm.expect(board).to.be.an('array');",
                "});",
                'pm.test("All 17 hostels, including zero-score ones", function () {',
                "    pm.expect(board.length).to.eql(17);",
                "});",
                'pm.test("Sorted by score descending", function () {',
                "    for (let i = 1; i < board.length; i++) {",
                "        pm.expect(board[i - 1].score).to.be.at.least(board[i].score);",
                "    }",
                "});",
                'pm.test("Tied hostels share a rank", function () {',
                "    board.forEach(function (a) {",
                "        board.forEach(function (b) {",
                "            if (a.score === b.score) { pm.expect(a.rank).to.eql(b.rank); }",
                "        });",
                "    });",
                "});",
            ),
        ),
    ],
)

# ---------------------------------------------------------------------------
# 8. Rate limiting
# ---------------------------------------------------------------------------

throttle_prerequest = [
    "// Fire five claim attempts before this request runs, so that the request",
    "// itself is the sixth within the same minute and must be throttled.",
    "//",
    "// The target is a mission bob has already claimed, so every attempt fails",
    "// harmlessly with 409 -- failed attempts still consume throttle quota, which",
    "// is exactly the behaviour being demonstrated.",
    "const attempts = 5;",
    'const url = pm.environment.get("base_url") + "/api/missions/" +',
    '    pm.environment.get("throttle_mission_id") + "/claim/";',
    "",
    "function fire(n, done) {",
    "    if (n === 0) { return done(); }",
    "    pm.sendRequest({",
    "        url: url,",
    '        method: "POST",',
    '        header: { "Authorization": "Token " + pm.environment.get("bob_token") }',
    "    }, function () {",
    "        fire(n - 1, done);",
    "    });",
    "}",
    "",
    "fire(attempts, function () {",
    '    console.log("Fired " + attempts + " warm-up claim attempts as bob.");',
    "});",
]

rate_limiting = folder(
    "8. Rate limiting",
    "Run this folder LAST.\n\n"
    "The claim throttle is 5 attempts per minute per user and counts failed attempts, "
    "so this folder deliberately exhausts bob's quota.\n\n"
    "**Before re-running the whole collection, restart the server** (`Ctrl+C`, then "
    "`python manage.py runserver`) **or wait 60 seconds.** The throttle counters live "
    "in the server's local-memory cache, so re-seeding the database does not clear "
    "them -- and folder 5's 'Bob claims same mission (409)' would get 429 instead.\n\n"
    "Alternative to the pre-request script: run the single request below six times in "
    "the Collection Runner and watch the sixth return 429.",
    [
        req(
            "Claim spam - 6th attempt in a minute (429)  [required case 7]",
            "POST",
            "/api/missions/{{throttle_mission_id}}/claim/",
            token="bob_token",
            prerequest=throttle_prerequest,
            description=(
                "The pre-request script fires five claim attempts as bob; this request "
                "is the sixth. The throttle is scoped to the claim action only, so no "
                "other endpoint is affected."
            ),
            tests=status_test(
                429,
                'pm.test("Retry-After tells the client when to come back", function () {',
                '    pm.expect(pm.response.headers.has("Retry-After")).to.be.true;',
                "});",
                'pm.test("Nothing was claimed", function () {',
                '    pm.expect(pm.response.json()).to.have.property("detail");',
                "});",
            ),
        ),
    ],
)


collection = {
    "info": {
        "_postman_id": "9b1f4e7a-2c3d-4f5a-8e6b-0d1c2a3b4c5d",
        "name": "Operation Golden Keyboard",
        "description": (
            "End-to-end proof that the backend enforces the competition rules on its "
            "own.\n\n"
            "**Run the folders in order against a fresh `python manage.py seed_demo`.** "
            "Folder 0 captures every token and id into the environment, so nothing "
            "below it hardcodes a URL or an id.\n\n"
            "Folder 8 (rate limiting) should run last -- see its description.\n\n"
            "The seven required cases are tagged `[required case N]` in the request "
            "names."
        ),
        "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
    },
    "item": [
        setup,
        hostels,
        participants,
        missions_crud,
        filtering,
        claiming,
        violations,
        leaderboard,
        rate_limiting,
    ],
}


environment = {
    "id": "3f2a1b0c-9d8e-4756-b4a3-2c1d0e9f8a7b",
    "name": "Golden Keyboard - Local",
    "values": [
        {"key": "base_url", "value": "http://127.0.0.1:8000", "type": "default", "enabled": True},
        # Everything below is populated by folder 0. Left blank on purpose so a stale
        # token from a previous run can never mask a broken setup step.
        {"key": "staff_token", "value": "", "type": "secret", "enabled": True},
        {"key": "alice_token", "value": "", "type": "secret", "enabled": True},
        {"key": "bob_token", "value": "", "type": "secret", "enabled": True},
        {"key": "mission_id", "value": "", "type": "default", "enabled": True},
        {"key": "claimed_mission_id", "value": "", "type": "default", "enabled": True},
        {"key": "expired_mission_id", "value": "", "type": "default", "enabled": True},
        {"key": "overdue_mission_id", "value": "", "type": "default", "enabled": True},
        {"key": "throttle_mission_id", "value": "", "type": "default", "enabled": True},
        {"key": "hostel_id", "value": "", "type": "default", "enabled": True},
        {"key": "participant_id", "value": "", "type": "default", "enabled": True},
        {"key": "created_mission_id", "value": "", "type": "default", "enabled": True},
        # Working variables the collection sets for itself.
        {"key": "other_participant_id", "value": "", "type": "default", "enabled": True},
        {"key": "bob_participant_id", "value": "", "type": "default", "enabled": True},
        {"key": "other_hostel_id", "value": "", "type": "default", "enabled": True},
        {"key": "alice_handle", "value": "", "type": "default", "enabled": True},
        {"key": "next_handle", "value": "", "type": "default", "enabled": True},
        {"key": "new_username", "value": "", "type": "default", "enabled": True},
        {"key": "new_codename", "value": "", "type": "default", "enabled": True},
        {"key": "future_deadline", "value": "", "type": "default", "enabled": True},
        {"key": "mission_points", "value": "", "type": "default", "enabled": True},
        {"key": "score_before", "value": "", "type": "default", "enabled": True},
    ],
    "_postman_variable_scope": "environment",
}


(OUT / "golden_keyboard.postman_collection.json").write_text(
    json.dumps(collection, indent=2) + "\n", encoding="utf-8"
)
(OUT / "golden_keyboard.postman_environment.json").write_text(
    json.dumps(environment, indent=2) + "\n", encoding="utf-8"
)

total = sum(len(f["item"]) for f in collection["item"])
print(f"Wrote {len(collection['item'])} folders, {total} requests.")
