"""Deadlines.

Time is controlled by mocking ``django.utils.timezone.now`` or by building missions
with past deadlines -- never by sleeping.

The point of these tests is that the *stored* status is not trusted anywhere. A
mission whose row says ``in_progress`` is refused once its deadline has passed, on
every write path.
"""

from datetime import timedelta
from unittest import mock

from django.core.cache import cache
from django.urls import reverse
from rest_framework import status

from competition.constants import Status
from competition.models import Mission
from competition.views import apply_status_change

from .factories import future, make_cracked, make_mission, past
from .test_leaderboard import AuthenticatedAPITestCase


class DeadlineEnforcementTests(AuthenticatedAPITestCase):
    def setUp(self):
        super().setUp()
        cache.clear()

    def status_url(self, mission):
        return reverse("mission-status", args=[mission.id])

    def claim_url(self, mission):
        return reverse("mission-claim", args=[mission.id])

    # -- Status changes ---------------------------------------------------------

    def test_status_change_after_the_deadline_is_rejected(self):
        mission = make_mission(
            status=Status.IN_PROGRESS,
            claimed_by=self.alice,
            hostel=self.hostel_a,
            deadline=past(),
        )
        self.auth(self.alice_token)

        response = self.client.patch(
            self.status_url(mission), {"status": Status.CRACKED}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.json()["code"], "mission_expired")
        mission.refresh_from_db()
        self.assertEqual(mission.status, Status.IN_PROGRESS)

    def test_status_change_is_allowed_right_up_to_the_deadline(self):
        mission = make_mission(
            status=Status.IN_PROGRESS,
            claimed_by=self.alice,
            hostel=self.hostel_a,
            deadline=future(days=1),
        )
        self.auth(self.alice_token)

        one_second_early = mission.deadline - timedelta(seconds=1)
        with mock.patch("django.utils.timezone.now", return_value=one_second_early):
            response = self.client.patch(
                self.status_url(mission), {"status": Status.CRACKED}, format="json"
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        mission.refresh_from_db()
        self.assertEqual(mission.status, Status.CRACKED)

    def test_status_change_exactly_at_the_deadline_is_rejected(self):
        """`now >= deadline` means the deadline instant itself is too late."""
        mission = make_mission(
            status=Status.IN_PROGRESS,
            claimed_by=self.alice,
            hostel=self.hostel_a,
            deadline=future(days=1),
        )
        self.auth(self.alice_token)

        with mock.patch("django.utils.timezone.now", return_value=mission.deadline):
            response = self.client.patch(
                self.status_url(mission), {"status": Status.CRACKED}, format="json"
            )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.json()["code"], "mission_expired")

    def test_a_mission_that_expires_between_read_and_write_is_not_cracked(self):
        """The conditional UPDATE is the last line of defence, and it holds.

        The service is handed an instance that still looks live while the row behind it
        has already expired -- exactly the interleaving of a crack submitted on the
        deadline boundary. Only ``deadline__gt=now`` in the UPDATE can catch it.
        """
        mission = make_mission(
            status=Status.IN_PROGRESS,
            claimed_by=self.alice,
            hostel=self.hostel_a,
            deadline=future(days=1),
        )
        stale = Mission.objects.get(pk=mission.pk)
        Mission.objects.filter(pk=mission.pk).update(deadline=past())

        with self.assertRaises(Exception) as ctx:
            apply_status_change(stale, self.alice, Status.CRACKED)

        self.assertEqual(ctx.exception.status_code, status.HTTP_400_BAD_REQUEST)
        mission.refresh_from_db()
        self.assertEqual(mission.status, Status.IN_PROGRESS)

    # -- Claims -----------------------------------------------------------------

    def test_claiming_after_the_deadline_is_rejected(self):
        mission = make_mission(deadline=past())
        self.auth(self.alice_token)

        response = self.client.post(self.claim_url(mission))

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.json()["code"], "mission_expired")

    def test_claiming_at_the_exact_deadline_is_rejected(self):
        mission = make_mission(deadline=future(days=1))
        self.auth(self.alice_token)

        with mock.patch("django.utils.timezone.now", return_value=mission.deadline):
            response = self.client.post(self.claim_url(mission))

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        mission.refresh_from_db()
        self.assertIsNone(mission.claimed_by_id)

    # -- Cracked survives -------------------------------------------------------

    def test_a_cracked_mission_keeps_its_points_after_the_deadline(self):
        make_cracked(self.alice, points=150, deadline=past())
        board = {row["id"]: row for row in self.client.get(reverse("leaderboard")).json()}
        self.assertEqual(board[self.hostel_a.id]["score"], 150)

    def test_a_cracked_mission_past_its_deadline_is_final_not_expired(self):
        mission = make_cracked(self.alice, points=150, deadline=past())
        self.auth(self.alice_token)

        response = self.client.patch(
            self.status_url(mission), {"status": Status.IN_PROGRESS}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.json()["detail"], "Cracked missions are final.")

    # -- Nothing is ever persisted ---------------------------------------------

    def test_reading_an_expired_mission_does_not_write_expired_to_the_database(self):
        mission = make_mission(status=Status.UNCLAIMED, deadline=past())
        self.auth(self.alice_token)

        self.client.get(reverse("mission-detail", args=[mission.id]))
        self.client.get(reverse("mission-list"))
        self.client.get(reverse("mission-list"), {"status": Status.EXPIRED})

        self.assertEqual(
            Mission.objects.get(pk=mission.pk).status, Status.UNCLAIMED
        )

    def test_a_failed_claim_does_not_write_expired_either(self):
        mission = make_mission(status=Status.UNCLAIMED, deadline=past())
        self.auth(self.alice_token)

        self.client.post(self.claim_url(mission))

        fresh = Mission.objects.get(pk=mission.pk)
        self.assertEqual(fresh.status, Status.UNCLAIMED)
        self.assertIsNone(fresh.claimed_by_id)
