"""Leaderboard, read endpoints, response shapes and query efficiency."""

from django.contrib.auth.models import User
from django.urls import reverse
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.test import APITestCase

from competition.constants import HOSTEL_COUNT, Difficulty, Status

from .factories import future, hostels, make_cracked, make_mission, make_participant, past


class AuthenticatedAPITestCase(APITestCase):
    """Base class giving every test a staff user, two participants and their tokens."""

    def setUp(self):
        self.hostel_a, self.hostel_b, self.hostel_c = hostels(3)
        self.alice = make_participant("alice", hostel=self.hostel_a)
        self.bob = make_participant("bob", hostel=self.hostel_b)
        self.staff = User.objects.create_user(
            "staff", password="staffpass123", is_staff=True
        )
        self.alice_token = Token.objects.create(user=self.alice.user).key
        self.bob_token = Token.objects.create(user=self.bob.user).key
        self.staff_token = Token.objects.create(user=self.staff).key

    def auth(self, token):
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {token}")

    def logout(self):
        self.client.credentials()


class LeaderboardTests(AuthenticatedAPITestCase):
    url = reverse("leaderboard")

    def test_anonymous_access_allowed(self):
        self.logout()
        self.assertEqual(self.client.get(self.url).status_code, status.HTTP_200_OK)

    def test_lists_every_hostel_including_zero_scores(self):
        make_cracked(self.alice, points=150)
        body = self.client.get(self.url).json()
        self.assertEqual(len(body), HOSTEL_COUNT)
        self.assertEqual(sum(1 for row in body if row["score"] == 0), HOSTEL_COUNT - 1)

    def test_not_paginated(self):
        """A bare list, not a {count, next, previous, results} envelope."""
        body = self.client.get(self.url).json()
        self.assertIsInstance(body, list)

    def test_ordered_by_score_descending(self):
        make_cracked(self.alice, points=450)
        make_cracked(self.bob, points=300)
        body = self.client.get(self.url).json()
        scores = [row["score"] for row in body]
        self.assertEqual(scores, sorted(scores, reverse=True))
        self.assertEqual(body[0]["id"], self.hostel_a.id)
        self.assertEqual(body[1]["id"], self.hostel_b.id)

    def test_ties_share_a_rank_and_the_next_rank_skips(self):
        carol = make_participant("carol", hostel=self.hostel_c)
        make_cracked(self.alice, points=300)
        make_cracked(self.bob, points=300)
        make_cracked(carol, points=100)

        by_id = {row["id"]: row for row in self.client.get(self.url).json()}
        self.assertEqual(by_id[self.hostel_a.id]["rank"], 1)
        self.assertEqual(by_id[self.hostel_b.id]["rank"], 1)
        self.assertEqual(by_id[self.hostel_c.id]["rank"], 3)

    def test_ties_break_by_name_ascending(self):
        make_cracked(self.alice, points=300)
        make_cracked(self.bob, points=300)
        body = self.client.get(self.url).json()
        tied = [row["name"] for row in body if row["score"] == 300]
        self.assertEqual(tied, sorted(tied))

    def test_only_cracked_missions_count(self):
        make_mission(
            points=999,
            status=Status.IN_PROGRESS,
            claimed_by=self.alice,
            hostel=self.hostel_a,
        )
        body = {row["id"]: row for row in self.client.get(self.url).json()}
        self.assertEqual(body[self.hostel_a.id]["score"], 0)

    def test_row_shape(self):
        body = self.client.get(self.url).json()
        self.assertEqual(
            set(body[0].keys()), {"rank", "id", "name", "score", "cracked_count"}
        )


class HostelEndpointTests(AuthenticatedAPITestCase):
    def test_list_requires_authentication(self):
        self.logout()
        response = self.client.get(reverse("hostel-list"))
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_list_is_paginated_and_light(self):
        self.auth(self.alice_token)
        body = self.client.get(reverse("hostel-list")).json()
        self.assertEqual(body["count"], HOSTEL_COUNT)
        self.assertEqual(
            set(body["results"][0].keys()), {"id", "name", "score", "cracked_count"}
        )

    def test_detail_includes_score_and_cracked_missions(self):
        mission = make_cracked(self.alice, points=150, difficulty=Difficulty.HARD)
        self.auth(self.alice_token)
        body = self.client.get(
            reverse("hostel-detail", args=[self.hostel_a.id])
        ).json()

        self.assertEqual(body["score"], 150)
        self.assertEqual(body["cracked_count"], 1)
        self.assertEqual(len(body["cracked_missions"]), 1)
        self.assertEqual(
            body["cracked_missions"][0],
            {
                "id": mission.id,
                "codename": mission.codename,
                "points": 150,
                "difficulty": Difficulty.HARD,
                "claimed_by": {"id": self.alice.id, "handle": "alice"},
            },
        )

    def test_detail_excludes_non_cracked_missions(self):
        make_mission(
            status=Status.IN_PROGRESS, claimed_by=self.alice, hostel=self.hostel_a
        )
        self.auth(self.alice_token)
        body = self.client.get(reverse("hostel-detail", args=[self.hostel_a.id])).json()
        self.assertEqual(body["cracked_missions"], [])

    def test_hostels_are_read_only(self):
        self.auth(self.staff_token)
        response = self.client.post(reverse("hostel-list"), {"name": "New Bhawan"})
        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)


class ParticipantEndpointTests(AuthenticatedAPITestCase):
    def test_list_requires_authentication(self):
        self.logout()
        self.assertEqual(
            self.client.get(reverse("participant-list")).status_code,
            status.HTTP_401_UNAUTHORIZED,
        )

    def test_detail_shape(self):
        self.auth(self.alice_token)
        body = self.client.get(reverse("participant-detail", args=[self.alice.id])).json()
        self.assertEqual(
            body,
            {
                "id": self.alice.id,
                "handle": "alice",
                "username": "alice",
                "hostel": {"id": self.hostel_a.id, "name": self.hostel_a.name},
            },
        )

    def test_me_returns_own_profile(self):
        self.auth(self.bob_token)
        body = self.client.get(reverse("participant-me")).json()
        self.assertEqual(body["id"], self.bob.id)
        self.assertEqual(body["handle"], "bob")

    def test_me_rejects_a_user_without_a_participant_profile(self):
        self.auth(self.staff_token)
        response = self.client.get(reverse("participant-me"))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class MissionReadTests(AuthenticatedAPITestCase):
    def test_list_requires_authentication(self):
        self.logout()
        self.assertEqual(
            self.client.get(reverse("mission-list")).status_code,
            status.HTTP_401_UNAUTHORIZED,
        )

    def test_unclaimed_mission_shape(self):
        mission = make_mission(points=150, difficulty=Difficulty.HARD)
        self.auth(self.alice_token)
        body = self.client.get(reverse("mission-detail", args=[mission.id])).json()

        self.assertEqual(
            set(body.keys()),
            {
                "id", "codename", "brief", "points", "difficulty", "status",
                "deadline", "is_expired", "claimed_by", "hostel",
                "created_at", "updated_at",
            },
        )
        self.assertIsNone(body["claimed_by"])
        self.assertIsNone(body["hostel"])
        self.assertEqual(body["status"], Status.UNCLAIMED)
        self.assertFalse(body["is_expired"])

    def test_claimed_mission_nests_claimant_and_hostel(self):
        mission = make_mission(
            status=Status.IN_PROGRESS, claimed_by=self.alice, hostel=self.hostel_a
        )
        self.auth(self.bob_token)
        body = self.client.get(reverse("mission-detail", args=[mission.id])).json()

        self.assertEqual(
            body["claimed_by"],
            {
                "id": self.alice.id,
                "handle": "alice",
                "hostel": {"id": self.hostel_a.id, "name": self.hostel_a.name},
            },
        )
        self.assertEqual(body["hostel"], {"id": self.hostel_a.id, "name": self.hostel_a.name})

    def test_status_is_reported_as_effective_status(self):
        """The database says unclaimed; the API must say expired."""
        mission = make_mission(status=Status.UNCLAIMED, deadline=past())
        self.auth(self.alice_token)
        body = self.client.get(reverse("mission-detail", args=[mission.id])).json()

        self.assertEqual(body["status"], Status.EXPIRED)
        self.assertTrue(body["is_expired"])

    def test_cracked_mission_reads_cracked_past_its_deadline(self):
        mission = make_cracked(self.alice, points=150, deadline=past())
        self.auth(self.alice_token)
        body = self.client.get(reverse("mission-detail", args=[mission.id])).json()
        self.assertEqual(body["status"], Status.CRACKED)
        self.assertFalse(body["is_expired"])

    def test_default_ordering_is_by_deadline(self):
        late = make_mission(deadline=future(days=30))
        early = make_mission(deadline=future(days=1))
        self.auth(self.alice_token)
        ids = [m["id"] for m in self.client.get(reverse("mission-list")).json()["results"]]
        self.assertEqual(ids, [early.id, late.id])

    def test_mission_list_does_not_scale_queries_with_row_count(self):
        """Guards the select_related: 10 claimed missions must cost the same as 2."""
        for _ in range(10):
            make_mission(
                status=Status.IN_PROGRESS, claimed_by=self.alice, hostel=self.hostel_a
            )
        self.auth(self.alice_token)
        url = reverse("mission-list")

        # 1 token+user lookup, 1 pagination COUNT, 1 page fetch. The nested claimant
        # and hostel come along in that third query's JOINs, not per row.
        with self.assertNumQueries(3):
            response = self.client.get(url)
        self.assertEqual(len(response.json()["results"]), 10)


class RegisterTests(APITestCase):
    def setUp(self):
        self.hostel_a = hostels(1)[0]

    def test_register_creates_user_participant_and_token(self):
        response = self.client.post(
            reverse("register"),
            {
                "username": "newbie",
                "password": "SuperSecret!42",
                "handle": "newbie",
                "hostel": self.hostel_a.id,
            },
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        body = response.json()
        self.assertEqual(body["participant"]["handle"], "newbie")
        self.assertEqual(body["participant"]["hostel"]["id"], self.hostel_a.id)
        self.assertTrue(body["token"])

        user = User.objects.get(username="newbie")
        self.assertTrue(user.check_password("SuperSecret!42"))
        self.assertEqual(Token.objects.get(user=user).key, body["token"])
        self.assertFalse(user.is_staff)

    def test_duplicate_handle_rejected(self):
        make_participant("existing", handle="taken", hostel=self.hostel_a)
        response = self.client.post(
            reverse("register"),
            {
                "username": "other",
                "password": "SuperSecret!42",
                "handle": "taken",
                "hostel": self.hostel_a.id,
            },
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("handle", response.json())

    def test_duplicate_username_rejected(self):
        make_participant("existing", handle="existing", hostel=self.hostel_a)
        response = self.client.post(
            reverse("register"),
            {
                "username": "existing",
                "password": "SuperSecret!42",
                "handle": "fresh",
                "hostel": self.hostel_a.id,
            },
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("username", response.json())

    def test_weak_password_rejected_by_django_validators(self):
        response = self.client.post(
            reverse("register"),
            {
                "username": "weakling",
                "password": "123",
                "handle": "weakling",
                "hostel": self.hostel_a.id,
            },
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("password", response.json())

    def test_failed_registration_creates_nothing(self):
        """The User+Participant create is atomic."""
        before = User.objects.count()
        self.client.post(
            reverse("register"),
            {
                "username": "ghost",
                "password": "123",
                "handle": "ghost",
                "hostel": self.hostel_a.id,
            },
        )
        self.assertEqual(User.objects.count(), before)

    def test_unknown_hostel_rejected(self):
        response = self.client.post(
            reverse("register"),
            {
                "username": "nowhere",
                "password": "SuperSecret!42",
                "handle": "nowhere",
                "hostel": 9999,
            },
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class TokenEndpointTests(AuthenticatedAPITestCase):
    def test_obtain_token_with_valid_credentials(self):
        response = self.client.post(
            reverse("obtain-token"), {"username": "staff", "password": "staffpass123"}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()["token"], self.staff_token)

    def test_obtain_token_with_bad_credentials(self):
        response = self.client.post(
            reverse("obtain-token"), {"username": "staff", "password": "wrong"}
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
