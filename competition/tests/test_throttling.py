"""The scoped claim throttle.

The cache is cleared in ``setUp`` because the throttle counters live in LocMemCache,
which is process-wide: without it, one test's claims would eat another's quota and
failures would depend on test ordering.
"""

from unittest import mock

from django.conf import settings as django_settings
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.settings import api_settings

from competition.constants import Status
from competition.throttles import ClaimRateThrottle
from competition.views import MissionViewSet

from .factories import make_mission
from .test_leaderboard import AuthenticatedAPITestCase


class ClaimThrottleTests(AuthenticatedAPITestCase):
    def setUp(self):
        super().setUp()
        cache.clear()

    def claim(self, mission, token):
        self.auth(token)
        return self.client.post(reverse("mission-claim", args=[mission.id]))

    # -- Configuration ----------------------------------------------------------

    def test_rate_comes_from_settings(self):
        self.assertEqual(ClaimRateThrottle.scope, "claim")
        self.assertEqual(api_settings.DEFAULT_THROTTLE_RATES["claim"], "5/min")

    def test_throttle_is_attached_only_to_the_claim_action(self):
        self.assertEqual(
            MissionViewSet.claim.kwargs["throttle_classes"], [ClaimRateThrottle]
        )
        for name in ("set_status", "list", "retrieve", "create"):
            handler = getattr(MissionViewSet, name, None)
            with self.subTest(action=name):
                self.assertNotIn("throttle_classes", getattr(handler, "kwargs", {}))

    def test_no_global_throttle_is_configured(self):
        self.assertEqual(api_settings.DEFAULT_THROTTLE_CLASSES, [])

    # -- Behaviour --------------------------------------------------------------

    def test_the_sixth_claim_in_a_minute_is_throttled(self):
        missions = [make_mission() for _ in range(6)]

        for index, mission in enumerate(missions[:5], start=1):
            with self.subTest(attempt=index):
                self.assertEqual(
                    self.claim(mission, self.alice_token).status_code,
                    status.HTTP_200_OK,
                )

        response = self.claim(missions[5], self.alice_token)
        self.assertEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)

    def test_a_throttled_claim_carries_a_retry_after_header(self):
        for _ in range(5):
            self.claim(make_mission(), self.alice_token)
        response = self.claim(make_mission(), self.alice_token)

        self.assertEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)
        self.assertIn("Retry-After", response.headers)
        self.assertTrue(int(response.headers["Retry-After"]) > 0)

    def test_failed_attempts_count_toward_the_limit(self):
        """Five rejected claims still exhaust the quota; the sixth is throttled."""
        taken = make_mission(
            status=Status.IN_PROGRESS, claimed_by=self.bob, hostel=self.hostel_b
        )
        for _ in range(5):
            self.assertEqual(
                self.claim(taken, self.alice_token).status_code,
                status.HTTP_409_CONFLICT,
            )

        response = self.claim(make_mission(), self.alice_token)
        self.assertEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)

    def test_a_throttled_claim_does_not_touch_the_mission(self):
        for _ in range(5):
            self.claim(make_mission(), self.alice_token)

        mission = make_mission()
        self.claim(mission, self.alice_token)

        mission.refresh_from_db()
        self.assertEqual(mission.status, Status.UNCLAIMED)
        self.assertIsNone(mission.claimed_by_id)

    def test_the_quota_is_per_user(self):
        """Alice burning her quota must not block Bob."""
        for _ in range(6):
            self.claim(make_mission(), self.alice_token)

        response = self.claim(make_mission(), self.bob_token)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    # -- Scope ------------------------------------------------------------------

    def test_reads_are_never_throttled(self):
        for _ in range(6):
            self.claim(make_mission(), self.alice_token)

        self.auth(self.alice_token)
        for _ in range(10):
            self.assertEqual(
                self.client.get(reverse("mission-list")).status_code,
                status.HTTP_200_OK,
            )

    def test_status_changes_are_never_throttled(self):
        missions = [
            make_mission(
                status=Status.IN_PROGRESS, claimed_by=self.alice, hostel=self.hostel_a
            )
            for _ in range(8)
        ]
        for _ in range(6):
            self.claim(make_mission(), self.alice_token)

        self.auth(self.alice_token)
        for mission in missions:
            response = self.client.patch(
                reverse("mission-status", args=[mission.id]),
                {"status": Status.CRACKED},
                format="json",
            )
            self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_the_leaderboard_is_never_throttled(self):
        for _ in range(6):
            self.claim(make_mission(), self.alice_token)

        self.logout()
        for _ in range(10):
            self.assertEqual(
                self.client.get(reverse("leaderboard")).status_code, status.HTTP_200_OK
            )

    # -- Rate is configuration, not code ---------------------------------------

    def test_changing_the_configured_rate_changes_the_limit(self):
        """Proves the rate is configuration, not a number baked into the throttle.

        DRF binds ``SimpleRateThrottle.THROTTLE_RATES = api_settings
        .DEFAULT_THROTTLE_RATES`` once, at import time, so ``override_settings`` alone
        cannot move it. Rebinding it *from* ``api_settings`` inside the override is
        what exercises the real chain: Django setting -> DRF api_settings -> throttle.
        """
        with override_settings(
            REST_FRAMEWORK={
                **django_settings.REST_FRAMEWORK,
                "DEFAULT_THROTTLE_RATES": {"claim": "2/min"},
            }
        ):
            self.assertEqual(api_settings.DEFAULT_THROTTLE_RATES["claim"], "2/min")

            with mock.patch.object(
                ClaimRateThrottle,
                "THROTTLE_RATES",
                api_settings.DEFAULT_THROTTLE_RATES,
            ):
                codes = [
                    self.claim(make_mission(), self.alice_token).status_code
                    for _ in range(3)
                ]

        self.assertEqual(
            codes,
            [
                status.HTTP_200_OK,
                status.HTTP_200_OK,
                status.HTTP_429_TOO_MANY_REQUESTS,
            ],
        )
