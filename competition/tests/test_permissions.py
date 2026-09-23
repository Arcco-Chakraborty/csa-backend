"""Ownership, field-level edit restrictions and staff CRUD.

The field-level tests are the point of the whole permission layer: a participant who
may legitimately send `status` must not be able to raise their own mission's points in
the same request.
"""

from django.core.cache import cache
from django.urls import reverse
from rest_framework import status
from rest_framework.authtoken.models import Token

from competition.constants import Difficulty, Status
from competition.models import Mission
from competition.permissions import NOT_THE_CLAIMANT, STATUS_ONLY
from competition.serializers import MissionSerializer
from competition.views import HANDLE_ONLY

from .factories import future, make_cracked, make_mission, make_participant, past
from .test_leaderboard import AuthenticatedAPITestCase


class ClaimantStatusChangeTests(AuthenticatedAPITestCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.mission = make_mission(
            points=150, status=Status.IN_PROGRESS, claimed_by=self.alice, hostel=self.hostel_a
        )

    def status_url(self, mission=None):
        return reverse("mission-status", args=[(mission or self.mission).id])

    def detail_url(self, mission=None):
        return reverse("mission-detail", args=[(mission or self.mission).id])

    def test_claimant_cracks_their_mission(self):
        self.auth(self.alice_token)
        response = self.client.patch(
            self.status_url(), {"status": Status.CRACKED}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()["status"], Status.CRACKED)
        self.mission.refresh_from_db()
        self.assertEqual(self.mission.status, Status.CRACKED)

    def test_cracking_scores_for_the_claimants_hostel(self):
        self.auth(self.alice_token)
        self.client.patch(self.status_url(), {"status": Status.CRACKED}, format="json")

        board = {row["id"]: row for row in self.client.get(reverse("leaderboard")).json()}
        self.assertEqual(board[self.hostel_a.id]["score"], 150)
        self.assertEqual(board[self.hostel_a.id]["cracked_count"], 1)

    def test_setting_in_progress_again_is_an_idempotent_no_op(self):
        self.auth(self.alice_token)
        response = self.client.patch(
            self.status_url(), {"status": Status.IN_PROGRESS}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()["status"], Status.IN_PROGRESS)

    def test_cracked_missions_are_final(self):
        self.mission.status = Status.CRACKED
        self.mission.save()
        self.auth(self.alice_token)

        response = self.client.patch(
            self.status_url(), {"status": Status.IN_PROGRESS}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.json()["detail"], "Cracked missions are final.")

    def test_participants_cannot_set_unclaimed_or_expired(self):
        self.auth(self.alice_token)
        for target in (Status.UNCLAIMED, Status.EXPIRED):
            with self.subTest(target=target):
                response = self.client.patch(
                    self.status_url(), {"status": target}, format="json"
                )
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertIn("status", response.json())
        self.mission.refresh_from_db()
        self.assertEqual(self.mission.status, Status.IN_PROGRESS)

    def test_non_claimant_participant_cannot_change_status(self):
        self.auth(self.bob_token)
        response = self.client.patch(
            self.status_url(), {"status": Status.CRACKED}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(response.json()["detail"], NOT_THE_CLAIMANT)
        self.mission.refresh_from_db()
        self.assertEqual(self.mission.status, Status.IN_PROGRESS)

    def test_non_claimant_cannot_change_status_via_the_generic_patch_either(self):
        self.auth(self.bob_token)
        response = self.client.patch(
            self.detail_url(), {"status": Status.CRACKED}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(response.json()["detail"], NOT_THE_CLAIMANT)

    def test_generic_patch_with_status_behaves_like_the_status_route(self):
        self.auth(self.alice_token)
        response = self.client.patch(
            self.detail_url(), {"status": Status.CRACKED}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()["status"], Status.CRACKED)

    def test_anonymous_status_change_is_unauthorized(self):
        self.logout()
        response = self.client.patch(
            self.status_url(), {"status": Status.CRACKED}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_staff_without_a_participant_profile_cannot_use_the_status_route(self):
        self.auth(self.staff_token)
        response = self.client.patch(
            self.status_url(), {"status": Status.CRACKED}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class ParticipantFieldRestrictionTests(AuthenticatedAPITestCase):
    """A claimant may send `status` and nothing else -- through either route."""

    def setUp(self):
        super().setUp()
        self.mission = make_mission(
            codename="ORIGINAL_NAME",
            brief="original brief",
            points=150,
            difficulty=Difficulty.EASY,
            status=Status.IN_PROGRESS,
            claimed_by=self.alice,
            hostel=self.hostel_a,
        )
        self.original = Mission.objects.get(pk=self.mission.pk)

    def forbidden_payloads(self):
        return {
            "points": {"points": 9999},
            "codename": {"codename": "HIJACKED"},
            "brief": {"brief": "rewritten"},
            "difficulty": {"difficulty": Difficulty.BOSS_LEVEL},
            "deadline": {"deadline": future(days=365).isoformat()},
            "hostel": {"hostel": self.hostel_b.id},
            "claimed_by": {"claimed_by": self.bob.id},
        }

    def assert_unchanged(self):
        fresh = Mission.objects.get(pk=self.mission.pk)
        self.assertEqual(fresh.codename, self.original.codename)
        self.assertEqual(fresh.brief, self.original.brief)
        self.assertEqual(fresh.points, self.original.points)
        self.assertEqual(fresh.difficulty, self.original.difficulty)
        self.assertEqual(fresh.deadline, self.original.deadline)
        self.assertEqual(fresh.hostel_id, self.original.hostel_id)
        self.assertEqual(fresh.claimed_by_id, self.original.claimed_by_id)

    def test_claimant_cannot_patch_any_other_field(self):
        self.auth(self.alice_token)
        url = reverse("mission-detail", args=[self.mission.id])

        for field, payload in self.forbidden_payloads().items():
            with self.subTest(field=field):
                response = self.client.patch(url, payload, format="json")
                self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
                self.assertEqual(response.json()["detail"], STATUS_ONLY)
        self.assert_unchanged()

    def test_claimant_cannot_smuggle_a_field_alongside_status(self):
        self.auth(self.alice_token)
        url = reverse("mission-detail", args=[self.mission.id])

        for field, payload in self.forbidden_payloads().items():
            with self.subTest(field=field):
                response = self.client.patch(
                    url, {"status": Status.CRACKED, **payload}, format="json"
                )
                self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
                self.assertEqual(response.json()["detail"], STATUS_ONLY)

        self.assert_unchanged()
        self.assertEqual(
            Mission.objects.get(pk=self.mission.pk).status, Status.IN_PROGRESS
        )

    def test_the_status_route_also_rejects_extra_fields(self):
        self.auth(self.alice_token)
        response = self.client.patch(
            reverse("mission-status", args=[self.mission.id]),
            {"status": Status.CRACKED, "points": 9999},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(response.json()["detail"], STATUS_ONLY)
        self.assert_unchanged()

    def test_put_by_the_claimant_is_refused_too(self):
        """PUT sends every field, so it can never be a status-only payload."""
        self.auth(self.alice_token)
        response = self.client.put(
            reverse("mission-detail", args=[self.mission.id]),
            {
                "codename": "HIJACKED",
                "brief": "b",
                "points": 9999,
                "difficulty": Difficulty.EASY,
                "status": Status.CRACKED,
                "deadline": future().isoformat(),
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assert_unchanged()


class MissionCrudPermissionTests(AuthenticatedAPITestCase):
    def payload(self, **overrides):
        data = {
            "codename": "NEW_MISSION",
            "brief": "A freshly minted mission.",
            "points": 200,
            "difficulty": Difficulty.HARD,
            "deadline": future(days=10).isoformat(),
        }
        data.update(overrides)
        return data

    def test_staff_creates_a_mission(self):
        self.auth(self.staff_token)
        response = self.client.post(reverse("mission-list"), self.payload(), format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        body = response.json()
        self.assertEqual(body["status"], Status.UNCLAIMED)
        self.assertIsNone(body["claimed_by"])
        self.assertIsNone(body["hostel"])

    def test_staff_may_create_a_mission_with_a_past_deadline(self):
        """Needed to demo expired cases; it simply reads as expired straight away."""
        self.auth(self.staff_token)
        response = self.client.post(
            reverse("mission-list"), self.payload(deadline=past().isoformat()), format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.json()["status"], Status.EXPIRED)

    def test_staff_cannot_create_an_in_progress_or_cracked_mission_with_no_claimant(self):
        self.auth(self.staff_token)
        for target in (Status.IN_PROGRESS, Status.CRACKED):
            with self.subTest(status=target):
                response = self.client.post(
                    reverse("mission-list"),
                    self.payload(codename=f"X_{target}", status=target),
                    format="json",
                )
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertIn("status", response.json())

    def test_staff_cannot_set_claimed_by_or_hostel_directly(self):
        """Read-only serializer fields: ignored, never applied."""
        self.auth(self.staff_token)
        response = self.client.post(
            reverse("mission-list"),
            self.payload(claimed_by=self.alice.id, hostel=self.hostel_a.id),
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        created = Mission.objects.get(pk=response.json()["id"])
        self.assertIsNone(created.claimed_by_id)
        self.assertIsNone(created.hostel_id)

    def test_staff_edits_points(self):
        mission = make_mission(points=100)
        self.auth(self.staff_token)

        response = self.client.patch(
            reverse("mission-detail", args=[mission.id]), {"points": 250}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        mission.refresh_from_db()
        self.assertEqual(mission.points, 250)

    def test_staff_resetting_to_unclaimed_releases_the_mission_and_the_score(self):
        mission = make_cracked(self.alice, points=150)
        self.auth(self.staff_token)

        response = self.client.patch(
            reverse("mission-detail", args=[mission.id]),
            {"status": Status.UNCLAIMED},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        mission.refresh_from_db()
        self.assertIsNone(mission.claimed_by_id)
        self.assertIsNone(mission.hostel_id)

        board = {row["id"]: row for row in self.client.get(reverse("leaderboard")).json()}
        self.assertEqual(board[self.hostel_a.id]["score"], 0)

    def test_staff_deletes_a_mission(self):
        mission = make_mission()
        self.auth(self.staff_token)
        response = self.client.delete(reverse("mission-detail", args=[mission.id]))

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Mission.objects.filter(pk=mission.pk).exists())

    def test_participant_cannot_create_a_mission(self):
        self.auth(self.alice_token)
        response = self.client.post(reverse("mission-list"), self.payload(), format="json")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(Mission.objects.filter(codename="NEW_MISSION").exists())

    def test_participant_cannot_delete_a_mission(self):
        mission = make_mission()
        self.auth(self.alice_token)
        response = self.client.delete(reverse("mission-detail", args=[mission.id]))

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertTrue(Mission.objects.filter(pk=mission.pk).exists())

    def test_participant_cannot_delete_even_their_own_claimed_mission(self):
        mission = make_mission(
            status=Status.IN_PROGRESS, claimed_by=self.alice, hostel=self.hostel_a
        )
        self.auth(self.alice_token)
        self.assertEqual(
            self.client.delete(reverse("mission-detail", args=[mission.id])).status_code,
            status.HTTP_403_FORBIDDEN,
        )

    def test_anonymous_create_is_unauthorized(self):
        self.logout()
        response = self.client.post(reverse("mission-list"), self.payload(), format="json")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_authenticated_user_without_a_participant_profile_cannot_patch(self):
        """A plain, non-staff account with no profile: 403, not 500."""
        stranger = make_participant("stranger", hostel=self.hostel_c)
        stranger.delete()  # leaves the User with no participant row

        token = Token.objects.create(user=stranger.user).key
        mission = make_mission()
        self.auth(token)

        response = self.client.patch(
            reverse("mission-detail", args=[mission.id]),
            {"status": Status.CRACKED},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class StaffStatusRestrictionTests(AuthenticatedAPITestCase):
    """Task §4: only the claimant may move a mission into in_progress or cracked.

    Staff administer missions -- they edit the core fields, release a mission
    (``unclaimed``) or void it (``expired``) -- but they cannot award a crack, or undo
    one back into ``in_progress``, on a claimant's behalf.
    """

    def detail_url(self, mission):
        return reverse("mission-detail", args=[mission.id])

    def score(self, hostel):
        board = {row["id"]: row for row in self.client.get(reverse("leaderboard")).json()}
        return board[hostel.id]["score"]

    def test_staff_cannot_crack_a_claimed_mission(self):
        self.auth(self.staff_token)
        for label, deadline in (("before deadline", future()), ("after deadline", past())):
            with self.subTest(label):
                mission = make_mission(
                    points=220,
                    status=Status.IN_PROGRESS,
                    claimed_by=self.bob,
                    deadline=deadline,
                )
                response = self.client.patch(
                    self.detail_url(mission), {"status": Status.CRACKED}, format="json"
                )

                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertIn("status", response.json())
                mission.refresh_from_db()
                self.assertEqual(mission.status, Status.IN_PROGRESS)
        self.assertEqual(self.score(self.hostel_b), 0)

    def test_staff_cannot_revert_a_cracked_mission_to_in_progress(self):
        mission = make_cracked(self.alice, points=150)
        self.auth(self.staff_token)

        response = self.client.patch(
            self.detail_url(mission), {"status": Status.IN_PROGRESS}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("status", response.json())
        mission.refresh_from_db()
        self.assertEqual(mission.status, Status.CRACKED)
        self.assertEqual(self.score(self.hostel_a), 150)

    def test_staff_cannot_move_an_unclaimed_mission_into_in_progress_or_cracked(self):
        self.auth(self.staff_token)
        for target in (Status.IN_PROGRESS, Status.CRACKED):
            with self.subTest(status=target):
                mission = make_mission()
                response = self.client.patch(
                    self.detail_url(mission), {"status": target}, format="json"
                )

                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertIn("status", response.json())
                mission.refresh_from_db()
                self.assertEqual(mission.status, Status.UNCLAIMED)

    def test_staff_put_that_leaves_status_unchanged_still_succeeds(self):
        """A GET-then-PUT round trip echoes the current status back. That is not a
        status change, so it must not be refused."""
        self.auth(self.staff_token)
        for stored in (Status.IN_PROGRESS, Status.CRACKED):
            with self.subTest(status=stored):
                mission = make_mission(points=100, status=stored, claimed_by=self.alice)
                body = self.client.get(self.detail_url(mission)).json()
                body["points"] = 175

                response = self.client.put(self.detail_url(mission), body, format="json")

                self.assertEqual(response.status_code, status.HTTP_200_OK)
                mission.refresh_from_db()
                self.assertEqual(mission.points, 175)
                self.assertEqual(mission.status, stored)
                self.assertEqual(mission.claimed_by_id, self.alice.id)

    def test_a_round_trip_on_an_overdue_mission_does_not_store_expired(self):
        """GET reports an overdue in-progress mission as ``expired``. Echoing that back in
        a PUT that extends the deadline must not persist ``expired`` -- expiry is
        computed, never stored -- so the claimant can crack it once it is live again."""
        mission = make_mission(
            status=Status.IN_PROGRESS, claimed_by=self.alice, deadline=past()
        )
        self.auth(self.staff_token)
        body = self.client.get(self.detail_url(mission)).json()
        self.assertEqual(body["status"], Status.EXPIRED)
        body["deadline"] = future().isoformat()

        response = self.client.put(self.detail_url(mission), body, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        mission.refresh_from_db()
        self.assertEqual(mission.status, Status.IN_PROGRESS)

        self.auth(self.alice_token)
        crack = self.client.patch(
            reverse("mission-status", args=[mission.id]),
            {"status": Status.CRACKED},
            format="json",
        )
        self.assertEqual(crack.status_code, status.HTTP_200_OK)

    def test_a_staff_member_who_claimed_a_mission_is_a_claimant_like_any_other(self):
        """Staff who also compete crack their own missions on the claimant route, which
        enforces the deadline -- and no route lets them crack once it has passed."""
        competitor = make_participant("staff_competitor", hostel=self.hostel_c)
        competitor.user.is_staff = True
        competitor.user.save()
        live = make_mission(status=Status.IN_PROGRESS, claimed_by=competitor)
        overdue = make_mission(
            status=Status.IN_PROGRESS, claimed_by=competitor, deadline=past()
        )
        self.auth(Token.objects.create(user=competitor.user).key)

        response = self.client.patch(
            reverse("mission-status", args=[live.id]),
            {"status": Status.CRACKED},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        for url in (reverse("mission-status", args=[overdue.id]), self.detail_url(overdue)):
            with self.subTest(url=url):
                response = self.client.patch(url, {"status": Status.CRACKED}, format="json")
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        overdue.refresh_from_db()
        self.assertEqual(overdue.status, Status.IN_PROGRESS)

    def test_staff_may_void_a_claimed_mission_by_marking_it_expired(self):
        mission = make_mission(status=Status.IN_PROGRESS, claimed_by=self.bob)
        self.auth(self.staff_token)

        response = self.client.patch(
            self.detail_url(mission), {"status": Status.EXPIRED}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()["status"], Status.EXPIRED)
        mission.refresh_from_db()
        self.assertEqual(mission.status, Status.EXPIRED)


class StaffEditRaceTests(AuthenticatedAPITestCase):
    """A staff edit writes only the fields it sent.

    Saving every column of the instance loaded when the request began would silently
    write a claim or crack that landed mid-request back over. These hand the serializer
    a stale instance -- the interleaving a real race produces, deterministically.
    """

    def staff_edit(self, stale, data):
        serializer = MissionSerializer(stale, data=data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()

    def test_a_staff_edit_does_not_undo_a_crack_that_landed_mid_request(self):
        mission = make_mission(points=100, status=Status.IN_PROGRESS, claimed_by=self.alice)
        stale = Mission.objects.get(pk=mission.pk)
        Mission.objects.filter(pk=mission.pk).update(status=Status.CRACKED)

        self.staff_edit(stale, {"points": 275})

        mission.refresh_from_db()
        self.assertEqual(mission.points, 275)
        self.assertEqual(mission.status, Status.CRACKED)

    def test_a_staff_edit_does_not_undo_a_claim_that_landed_mid_request(self):
        mission = make_mission(points=100)
        stale = Mission.objects.get(pk=mission.pk)
        Mission.objects.filter(pk=mission.pk).update(
            status=Status.IN_PROGRESS, claimed_by=self.alice, hostel=self.hostel_a
        )

        self.staff_edit(stale, {"points": 275})

        mission.refresh_from_db()
        self.assertEqual(mission.points, 275)
        self.assertEqual(mission.status, Status.IN_PROGRESS)
        self.assertEqual(mission.claimed_by_id, self.alice.id)


class ParticipantProfilePermissionTests(AuthenticatedAPITestCase):
    def test_owner_changes_their_own_handle(self):
        self.auth(self.alice_token)
        response = self.client.patch(
            reverse("participant-detail", args=[self.alice.id]),
            {"handle": "alice_prime"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.alice.refresh_from_db()
        self.assertEqual(self.alice.handle, "alice_prime")

    def test_owner_cannot_change_another_participants_handle(self):
        self.auth(self.alice_token)
        response = self.client.patch(
            reverse("participant-detail", args=[self.bob.id]),
            {"handle": "hijacked"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.bob.refresh_from_db()
        self.assertEqual(self.bob.handle, "bob")

    def test_participant_cannot_change_their_own_hostel(self):
        """Decision: refuse outright rather than silently drop the field."""
        self.auth(self.alice_token)
        response = self.client.patch(
            reverse("participant-detail", args=[self.alice.id]),
            {"hostel_id": self.hostel_b.id},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(response.json()["detail"], HANDLE_ONLY)
        self.alice.refresh_from_db()
        self.assertEqual(self.alice.hostel_id, self.hostel_a.id)

    def test_staff_may_move_a_participant_between_hostels(self):
        self.auth(self.staff_token)
        response = self.client.patch(
            reverse("participant-detail", args=[self.alice.id]),
            {"hostel_id": self.hostel_b.id},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.alice.refresh_from_db()
        self.assertEqual(self.alice.hostel_id, self.hostel_b.id)

    def test_duplicate_handle_is_rejected(self):
        self.auth(self.alice_token)
        response = self.client.patch(
            reverse("participant-detail", args=[self.alice.id]),
            {"handle": "bob"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_only_staff_may_delete_a_participant(self):
        self.auth(self.alice_token)
        self.assertEqual(
            self.client.delete(
                reverse("participant-detail", args=[self.bob.id])
            ).status_code,
            status.HTTP_403_FORBIDDEN,
        )

        self.auth(self.staff_token)
        self.assertEqual(
            self.client.delete(
                reverse("participant-detail", args=[self.bob.id])
            ).status_code,
            status.HTTP_204_NO_CONTENT,
        )
