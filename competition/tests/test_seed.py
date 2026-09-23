"""The demo seed.

The Postman collection runs against ``seed_demo`` and finds every id by querying for a
state -- "a mission bob holds", "alice's overdue mission" -- so these tests pin the
states it depends on, plus the promise that the command is safe to re-run.
"""

from io import StringIO

from django.conf import settings
from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase

from competition.constants import Status
from competition.models import Mission, Participant

from .factories import make_mission


class SeedDemoTests(TestCase):
    def seed(self, *args):
        call_command("seed_demo", *args, stdout=StringIO())

    def effective(self, queryset, effective_status):
        return [m for m in queryset if m.effective_status == effective_status]

    def test_alice_has_an_in_progress_mission_past_its_deadline(self):
        """Powers the Postman case "Alice cracks her overdue mission (400)"."""
        self.seed()

        overdue = self.effective(
            Mission.objects.filter(claimed_by__handle="alice", status=Status.IN_PROGRESS),
            Status.EXPIRED,
        )

        self.assertEqual(len(overdue), 1)

    def test_bob_holds_a_live_in_progress_mission(self):
        """The target of "Bob claims same mission" (409), "Alice changes status of
        bob's mission" (403), "Staff cracks bob's mission" (400) and the claim spam."""
        self.seed()

        live = self.effective(
            Mission.objects.filter(claimed_by__handle="bob"), Status.IN_PROGRESS
        )

        self.assertEqual(len(live), 1)

    def test_an_unclaimed_row_is_already_past_its_deadline(self):
        """Required case 6: the stored status says unclaimed, the deadline disagrees."""
        self.seed()

        stale = self.effective(
            Mission.objects.filter(status=Status.UNCLAIMED, claimed_by=None),
            Status.EXPIRED,
        )

        self.assertEqual(len(stale), 1)

    def test_there_are_claimable_missions_and_more_than_one_page(self):
        """"Find a claimable mission" and "?page=2" both depend on this."""
        self.seed()

        self.assertTrue(self.effective(Mission.objects.all(), Status.UNCLAIMED))
        self.assertGreater(
            Mission.objects.count(), settings.REST_FRAMEWORK["PAGE_SIZE"]
        )

    def test_alice_and_bob_score_for_different_hostels(self):
        """Keeps the leaderboard non-trivial and gives Postman a hostel that is not
        alice's to try writing onto her mission."""
        self.seed()
        alice = Participant.objects.get(handle="alice")
        bob = Participant.objects.get(handle="bob")

        self.assertNotEqual(alice.hostel_id, bob.hostel_id)
        for participant in (alice, bob):
            with self.subTest(handle=participant.handle):
                self.assertTrue(
                    Mission.objects.filter(
                        status=Status.CRACKED, hostel=participant.hostel
                    ).exists()
                )

    def test_running_twice_creates_no_duplicates(self):
        self.seed()
        before = (Mission.objects.count(), Participant.objects.count(), User.objects.count())

        self.seed()

        self.assertEqual(
            (Mission.objects.count(), Participant.objects.count(), User.objects.count()),
            before,
        )

    def test_reset_clears_missions_the_seed_did_not_create(self):
        self.seed()
        make_mission(codename="LEFTOVER")

        self.seed("--reset")

        self.assertFalse(Mission.objects.filter(codename="LEFTOVER").exists())
