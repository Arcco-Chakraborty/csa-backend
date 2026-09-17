"""Claiming.

The rule that matters most here is the last one: two claims on one mission cannot both
succeed, and the loser gets a clean 409 rather than a corrupted row.
"""

from django.core.cache import cache
from django.urls import reverse
from rest_framework import status

from competition.constants import Status
from competition.models import Mission
from competition.views import MissionUnavailable, claim_mission

from .factories import future, make_cracked, make_mission, make_participant, past
from .test_leaderboard import AuthenticatedAPITestCase


class ClaimTests(AuthenticatedAPITestCase):
    def setUp(self):
        super().setUp()
        # The claim route is throttled at 5/min; a shared cache would leak counts
        # between tests and make failures depend on test order.
        cache.clear()

    def claim_url(self, mission):
        return reverse("mission-claim", args=[mission.id])

    # -- The happy path ---------------------------------------------------------

    def test_participant_claims_an_available_mission(self):
        mission = make_mission(points=150)
        self.auth(self.alice_token)

        response = self.client.post(self.claim_url(mission))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        body = response.json()
        self.assertEqual(body["status"], Status.IN_PROGRESS)
        self.assertEqual(body["claimed_by"]["id"], self.alice.id)
        self.assertEqual(body["hostel"]["id"], self.hostel_a.id)

        mission.refresh_from_db()
        self.assertEqual(mission.status, Status.IN_PROGRESS)
        self.assertEqual(mission.claimed_by_id, self.alice.id)
        self.assertEqual(mission.hostel_id, self.alice.hostel_id)

    def test_claim_copies_the_claimants_hostel_not_the_bodys(self):
        """A client cannot score for a hostel it does not belong to."""
        mission = make_mission()
        self.auth(self.alice_token)

        response = self.client.post(
            self.claim_url(mission),
            {"hostel": self.hostel_b.id, "claimed_by": self.bob.id, "points": 9999},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        mission.refresh_from_db()
        self.assertEqual(mission.hostel_id, self.hostel_a.id)
        self.assertEqual(mission.claimed_by_id, self.alice.id)
        self.assertNotEqual(mission.points, 9999)

    # -- Availability -----------------------------------------------------------

    def test_claiming_an_in_progress_mission_conflicts(self):
        mission = make_mission(
            status=Status.IN_PROGRESS, claimed_by=self.bob, hostel=self.hostel_b
        )
        self.auth(self.alice_token)

        response = self.client.post(self.claim_url(mission))

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.json()["code"], "mission_unavailable")
        mission.refresh_from_db()
        self.assertEqual(mission.claimed_by_id, self.bob.id)

    def test_claiming_a_cracked_mission_conflicts(self):
        mission = make_cracked(self.bob, points=150)
        self.auth(self.alice_token)

        response = self.client.post(self.claim_url(mission))

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.json()["code"], "mission_unavailable")

    def test_claiming_a_stored_expired_mission_is_rejected(self):
        mission = make_mission(status=Status.EXPIRED, deadline=past())
        self.auth(self.alice_token)

        response = self.client.post(self.claim_url(mission))

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.json()["code"], "mission_expired")

    def test_claiming_an_unclaimed_mission_past_its_deadline_is_rejected(self):
        """The stored status says `unclaimed`; the deadline says otherwise."""
        mission = make_mission(status=Status.UNCLAIMED, deadline=past())
        self.auth(self.alice_token)

        response = self.client.post(self.claim_url(mission))

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.json()["detail"], "Mission has expired.")
        mission.refresh_from_db()
        self.assertIsNone(mission.claimed_by_id)

    def test_claiming_a_missing_mission_is_404(self):
        self.auth(self.alice_token)
        self.assertEqual(
            self.client.post(reverse("mission-claim", args=[999999])).status_code,
            status.HTTP_404_NOT_FOUND,
        )

    # -- Who may claim ----------------------------------------------------------

    def test_staff_without_a_participant_profile_cannot_claim(self):
        mission = make_mission()
        self.auth(self.staff_token)

        response = self.client.post(self.claim_url(mission))

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        mission.refresh_from_db()
        self.assertIsNone(mission.claimed_by_id)

    def test_anonymous_claim_is_unauthorized(self):
        mission = make_mission()
        self.logout()

        response = self.client.post(self.claim_url(mission))

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_a_participant_may_hold_several_missions(self):
        """No claim limit is specified, so none is imposed."""
        self.auth(self.alice_token)
        for _ in range(3):
            mission = make_mission()
            self.assertEqual(
                self.client.post(self.claim_url(mission)).status_code,
                status.HTTP_200_OK,
            )
        self.assertEqual(self.alice.claimed_missions.count(), 3)

    # -- Races ------------------------------------------------------------------

    def test_two_simultaneous_claims_leave_exactly_one_winner(self):
        """Both callers read an unclaimed mission, then both try to write it.

        Calling the service twice against the same stale instance is exactly the
        interleaving a real race produces: the conditional UPDATE is what decides it,
        and the loser matches zero rows.
        """
        mission = make_mission()
        stale_for_alice = Mission.objects.get(pk=mission.pk)
        stale_for_bob = Mission.objects.get(pk=mission.pk)

        claim_mission(stale_for_alice, self.alice)

        with self.assertRaises(MissionUnavailable):
            claim_mission(stale_for_bob, self.bob)

        mission.refresh_from_db()
        self.assertEqual(mission.claimed_by_id, self.alice.id)
        self.assertEqual(mission.hostel_id, self.alice.hostel_id)
        self.assertEqual(mission.status, Status.IN_PROGRESS)

    def test_losing_a_race_over_the_api_returns_409(self):
        mission = make_mission()
        self.auth(self.alice_token)
        self.assertEqual(
            self.client.post(self.claim_url(mission)).status_code, status.HTTP_200_OK
        )

        cache.clear()  # isolate bob from alice's throttle bucket
        self.auth(self.bob_token)
        response = self.client.post(self.claim_url(mission))

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)

    def test_a_claim_that_expires_mid_flight_is_rejected_not_written(self):
        """The service is handed a stale, still-live-looking instance; the row is not.

        This is the deadline boundary case: the conditional UPDATE's
        ``deadline__gt=now`` is the only thing standing between the caller and a claim
        on an expired mission.
        """
        mission = make_mission(deadline=future(days=1))
        stale = Mission.objects.get(pk=mission.pk)

        Mission.objects.filter(pk=mission.pk).update(deadline=past())

        with self.assertRaises(Exception) as ctx:
            claim_mission(stale, self.alice)
        self.assertEqual(ctx.exception.status_code, status.HTTP_400_BAD_REQUEST)

        mission.refresh_from_db()
        self.assertIsNone(mission.claimed_by_id)
        self.assertEqual(mission.status, Status.UNCLAIMED)

    # -- Scoring ----------------------------------------------------------------

    def test_claiming_does_not_score(self):
        mission = make_mission(points=150)
        self.auth(self.alice_token)
        self.client.post(self.claim_url(mission))

        board = {row["id"]: row for row in self.client.get(reverse("leaderboard")).json()}
        self.assertEqual(board[self.hostel_a.id]["score"], 0)
