"""Filtering, search, ordering and pagination.

The status filter is the one with teeth: filtering on the stored column would let a
past-deadline mission show up under ``?status=unclaimed``, contradicting what the same
mission's detail endpoint prints.
"""

from pathlib import Path
from unittest import mock

from django.conf import settings as django_settings
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.pagination import PageNumberPagination
from rest_framework.settings import api_settings

from competition.constants import Difficulty, Status

from .factories import future, make_cracked, make_mission, past
from .test_leaderboard import AuthenticatedAPITestCase


class MissionFilterTests(AuthenticatedAPITestCase):
    url = reverse("mission-list")

    def setUp(self):
        super().setUp()
        self.live_unclaimed = make_mission(
            codename="LIVE_UNCLAIMED", difficulty=Difficulty.EASY
        )
        self.stale_unclaimed = make_mission(
            codename="STALE_UNCLAIMED", deadline=past(), difficulty=Difficulty.MEDIUM
        )
        self.live_in_progress = make_mission(
            codename="LIVE_IN_PROGRESS",
            status=Status.IN_PROGRESS,
            claimed_by=self.alice,
            hostel=self.hostel_a,
            difficulty=Difficulty.HARD,
        )
        self.stale_in_progress = make_mission(
            codename="STALE_IN_PROGRESS",
            status=Status.IN_PROGRESS,
            claimed_by=self.bob,
            hostel=self.hostel_b,
            deadline=past(),
        )
        self.cracked = make_cracked(
            self.alice, points=150, codename="CRACKED_ONE", difficulty=Difficulty.BOSS_LEVEL
        )
        self.cracked_stale = make_cracked(
            self.bob, points=90, codename="CRACKED_STALE", deadline=past()
        )
        self.stored_expired = make_mission(
            codename="STORED_EXPIRED", status=Status.EXPIRED, deadline=past()
        )
        self.auth(self.alice_token)

    def ids_for(self, **params):
        response = self.client.get(self.url, params)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return {m["id"] for m in response.json()["results"]}

    # -- Effective status -------------------------------------------------------

    def test_unclaimed_excludes_past_deadline_missions(self):
        self.assertEqual(self.ids_for(status=Status.UNCLAIMED), {self.live_unclaimed.id})

    def test_in_progress_excludes_past_deadline_missions(self):
        self.assertEqual(
            self.ids_for(status=Status.IN_PROGRESS), {self.live_in_progress.id}
        )

    def test_cracked_includes_past_deadline_missions(self):
        self.assertEqual(
            self.ids_for(status=Status.CRACKED),
            {self.cracked.id, self.cracked_stale.id},
        )

    def test_expired_includes_stored_expired_and_timed_out_missions(self):
        self.assertEqual(
            self.ids_for(status=Status.EXPIRED),
            {self.stored_expired.id, self.stale_unclaimed.id, self.stale_in_progress.id},
        )

    def test_the_status_filter_agrees_with_what_the_detail_endpoint_prints(self):
        """No mission may be filed under a status its own payload contradicts."""
        for value in Status.values:
            with self.subTest(status=value):
                for mission_id in self.ids_for(status=value):
                    body = self.client.get(
                        reverse("mission-detail", args=[mission_id])
                    ).json()
                    self.assertEqual(body["status"], value)

    def test_every_mission_falls_under_exactly_one_effective_status(self):
        seen = []
        for value in Status.values:
            seen.extend(self.ids_for(status=value))
        self.assertEqual(len(seen), len(set(seen)))
        self.assertEqual(len(seen), 7)

    # -- Other filters ----------------------------------------------------------

    def test_filter_by_difficulty(self):
        self.assertEqual(
            self.ids_for(difficulty=Difficulty.BOSS_LEVEL), {self.cracked.id}
        )
        self.assertEqual(
            self.ids_for(difficulty=Difficulty.HARD), {self.live_in_progress.id}
        )

    def test_invalid_difficulty_is_rejected(self):
        response = self.client.get(self.url, {"difficulty": "impossible"})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_filter_by_hostel(self):
        self.assertEqual(
            self.ids_for(hostel=self.hostel_a.id),
            {self.live_in_progress.id, self.cracked.id},
        )
        self.assertEqual(
            self.ids_for(hostel=self.hostel_b.id),
            {self.stale_in_progress.id, self.cracked_stale.id},
        )

    def test_hostel_filter_excludes_unclaimed_missions(self):
        """An unclaimed mission has no hostel yet, so it belongs to none of them."""
        self.assertNotIn(self.live_unclaimed.id, self.ids_for(hostel=self.hostel_a.id))

    def test_filters_combine(self):
        self.assertEqual(
            self.ids_for(hostel=self.hostel_a.id, status=Status.CRACKED),
            {self.cracked.id},
        )

    # -- Search -----------------------------------------------------------------

    def test_search_matches_codename(self):
        target = make_mission(codename="BINARY_BLITZ", brief="unrelated text")
        self.assertIn(target.id, self.ids_for(search="binary"))

    def test_search_matches_brief(self):
        target = make_mission(
            codename="UNRELATED_NAME", brief="Implement binary search on a rotated array"
        )
        self.assertIn(target.id, self.ids_for(search="binary"))

    def test_search_is_case_insensitive(self):
        target = make_mission(codename="BINARY_BLITZ", brief="x")
        self.assertIn(target.id, self.ids_for(search="BiNaRy"))

    def test_search_excludes_non_matches(self):
        make_mission(codename="BINARY_BLITZ", brief="x")
        self.assertNotIn(self.cracked.id, self.ids_for(search="binary"))

    def test_search_combines_with_a_filter(self):
        make_mission(codename="BINARY_EASY", difficulty=Difficulty.EASY)
        boss = make_mission(codename="BINARY_BOSS", difficulty=Difficulty.BOSS_LEVEL)
        self.assertEqual(
            self.ids_for(search="binary", difficulty=Difficulty.BOSS_LEVEL), {boss.id}
        )

    # -- Ordering ---------------------------------------------------------------

    def test_ordering_by_points(self):
        response = self.client.get(self.url, {"ordering": "-points"})
        points = [m["points"] for m in response.json()["results"]]
        self.assertEqual(points, sorted(points, reverse=True))

    def test_ordering_by_deadline(self):
        response = self.client.get(self.url, {"ordering": "deadline"})
        deadlines = [m["deadline"] for m in response.json()["results"]]
        self.assertEqual(deadlines, sorted(deadlines))


class PaginationTests(AuthenticatedAPITestCase):
    url = reverse("mission-list")

    def setUp(self):
        super().setUp()
        for i in range(25):
            make_mission(codename=f"PAGED_{i:02d}", deadline=future(days=i + 1))
        self.auth(self.alice_token)

    def test_envelope_shape(self):
        body = self.client.get(self.url).json()
        self.assertEqual(set(body.keys()), {"count", "next", "previous", "results"})
        self.assertEqual(body["count"], 25)
        self.assertIsNotNone(body["next"])
        self.assertIsNone(body["previous"])

    def test_page_length_comes_from_settings(self):
        body = self.client.get(self.url).json()
        self.assertEqual(len(body["results"]), django_settings.REST_FRAMEWORK["PAGE_SIZE"])

    def test_second_page_has_a_previous_link(self):
        body = self.client.get(self.url, {"page": 2}).json()
        self.assertIsNotNone(body["previous"])
        self.assertIsNotNone(body["next"])
        self.assertEqual(len(body["results"]), 10)

    def test_pages_do_not_overlap(self):
        first = {m["id"] for m in self.client.get(self.url).json()["results"]}
        second = {m["id"] for m in self.client.get(self.url, {"page": 2}).json()["results"]}
        self.assertEqual(first & second, set())

    def test_last_page_is_short(self):
        body = self.client.get(self.url, {"page": 3}).json()
        self.assertEqual(len(body["results"]), 5)
        self.assertIsNone(body["next"])

    def test_page_out_of_range_is_404(self):
        self.assertEqual(
            self.client.get(self.url, {"page": 99}).status_code,
            status.HTTP_404_NOT_FOUND,
        )

    def test_no_application_module_mentions_a_page_size(self):
        """The PRD's actual requirement: page size lives in settings and nowhere else.

        A source scan rather than an attribute check, because a page size could be
        hidden on a view, a custom paginator or a query-param override.
        """
        app_dir = Path(__file__).resolve().parent.parent
        offenders = []
        for source in app_dir.rglob("*.py"):
            if "tests" in source.parts or "migrations" in source.parts:
                continue
            text = source.read_text(encoding="utf-8")
            if "page_size" in text or "PAGE_SIZE" in text:
                offenders.append(source.name)
        self.assertEqual(offenders, [])

    def test_only_settings_defines_the_page_size(self):
        self.assertEqual(django_settings.REST_FRAMEWORK["PAGE_SIZE"], 10)

    def test_changing_the_setting_changes_the_page_size(self):
        """Proves the page size is settings-driven, not hardcoded in a view.

        DRF binds ``PageNumberPagination.page_size = api_settings.PAGE_SIZE`` once, at
        import time, so ``override_settings`` alone cannot move an already-imported
        class attribute. Rebinding it *from* ``api_settings`` inside the override is
        what exercises the real chain: Django setting -> DRF api_settings -> paginator.
        """
        with override_settings(
            REST_FRAMEWORK={**django_settings.REST_FRAMEWORK, "PAGE_SIZE": 3}
        ):
            # The override really did reach DRF's settings object.
            self.assertEqual(api_settings.PAGE_SIZE, 3)

            with mock.patch.object(
                PageNumberPagination, "page_size", api_settings.PAGE_SIZE
            ):
                body = self.client.get(self.url).json()

        self.assertEqual(len(body["results"]), 3)
        self.assertEqual(body["count"], 25)

    def test_hostels_and_participants_are_paginated_too(self):
        for url in (reverse("hostel-list"), reverse("participant-list")):
            with self.subTest(url=url):
                body = self.client.get(url).json()
                self.assertEqual(
                    set(body.keys()), {"count", "next", "previous", "results"}
                )
