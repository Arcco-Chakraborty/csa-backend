"""Populate the database with a demo competition.

Idempotent: re-running it updates the existing rows rather than duplicating them, so
it is safe to run against a database you have already been poking at. ``--reset``
wipes the competition data first (hostels are left alone -- they are migration-seeded).

The mission mix is chosen to make every Postman case reachable in one run:
a claimable mission, one already taken by someone else, cracked missions in two
different hostels, one whose row says ``unclaimed`` while its deadline has passed, and
one alice claimed but did not crack before its deadline.
"""

from datetime import timedelta

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from rest_framework.authtoken.models import Token

from competition.constants import Difficulty, Status
from competition.models import Hostel, Mission, Participant

STAFF = {"username": "staff", "password": "staffpass123"}
PARTICIPANTS = [
    {"username": "alice", "password": "alicepass123", "handle": "alice"},
    {"username": "bob", "password": "bobpass123", "handle": "bob"},
]

#: 15 unclaimed missions with future deadlines. Mixed difficulties, at least one
#: boss_level, and "binary" appears in both a codename and a brief so ?search=binary
#: demonstrates that it matches either field.
UNCLAIMED_MISSIONS = [
    ("BINARY_BLITZ", "Implement binary search on a rotated array.", 150, Difficulty.HARD),
    ("PALINDROME_PATROL", "Detect palindromic substrings in linear time.", 80, Difficulty.EASY),
    ("GRAPH_GAMBIT", "Shortest path through a weighted campus map.", 200, Difficulty.HARD),
    ("SORT_SORTIE", "Stable sort with a custom comparator.", 60, Difficulty.EASY),
    ("HEAP_HEIST", "Build a d-ary heap and prove the bound.", 120, Difficulty.MEDIUM),
    ("REGEX_RAID", "Parse a log format without a regex library.", 110, Difficulty.MEDIUM),
    ("CACHE_CAPER", "LRU cache with O(1) get and put.", 130, Difficulty.MEDIUM),
    ("TRIE_TRAP", "Autocomplete over a million-word dictionary.", 170, Difficulty.HARD),
    ("MATRIX_MARCH", "Spiral traversal with no extra allocation.", 70, Difficulty.EASY),
    ("BITMASK_BRAWL", "Subset-sum using binary representations.", 140, Difficulty.MEDIUM),
    ("THREAD_THICKET", "Fix a deadlock in a producer-consumer queue.", 190, Difficulty.HARD),
    ("QUEUE_QUEST", "Implement a queue from two stacks.", 50, Difficulty.EASY),
    ("HASH_HAVOC", "Open addressing with tombstone deletion.", 160, Difficulty.HARD),
    ("STREAM_SIEGE", "Running median over an unbounded stream.", 180, Difficulty.HARD),
    ("THE_FINAL_FIREWALL", "Break the golden keyboard's last defence.", 500, Difficulty.BOSS_LEVEL),
]


class Command(BaseCommand):
    help = "Seed a demo competition: staff, two participants, and a spread of missions."

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Delete existing missions, participants and demo users first.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        hostels = list(Hostel.objects.order_by("id"))
        if len(hostels) < 2:
            raise CommandError(
                "Hostels are not seeded. Run `python manage.py migrate` first."
            )

        if options["reset"]:
            self._reset()

        now = timezone.now()
        staff, staff_token = self._make_staff()
        alice, alice_token = self._make_participant(PARTICIPANTS[0], hostels[0])
        bob, bob_token = self._make_participant(PARTICIPANTS[1], hostels[1])

        unclaimed = [
            self._upsert_mission(
                codename=codename,
                brief=brief,
                points=points,
                difficulty=difficulty,
                deadline=now + timedelta(days=7 + index),
                status=Status.UNCLAIMED,
                claimed_by=None,
                hostel=None,
            )
            for index, (codename, brief, points, difficulty) in enumerate(UNCLAIMED_MISSIONS)
        ]

        # Already taken by bob: powers "claim an already-claimed mission" (409) and
        # "change someone else's mission" (403).
        claimed = self._upsert_mission(
            codename="OPERATION_OVERLORD",
            brief="Bob got here first. Binary trees, balanced.",
            points=220,
            difficulty=Difficulty.HARD,
            deadline=now + timedelta(days=5),
            status=Status.IN_PROGRESS,
            claimed_by=bob,
            hostel=bob.hostel,
        )

        # One cracked mission per hostel, so the leaderboard has a real top two.
        self._upsert_mission(
            codename="FIRST_BLOOD",
            brief="Alice cracked this one already.",
            points=300,
            difficulty=Difficulty.MEDIUM,
            deadline=now + timedelta(days=3),
            status=Status.CRACKED,
            claimed_by=alice,
            hostel=alice.hostel,
        )
        self._upsert_mission(
            codename="SECOND_WIND",
            brief="Bob cracked this one already.",
            points=90,
            difficulty=Difficulty.EASY,
            deadline=now + timedelta(days=3),
            status=Status.CRACKED,
            claimed_by=bob,
            hostel=bob.hostel,
        )

        # The interesting one: the row says unclaimed, the deadline says otherwise.
        # The API must report it as expired and refuse to let anyone claim it.
        expired = self._upsert_mission(
            codename="GHOST_PROTOCOL",
            brief="The deadline passed while this sat unclaimed.",
            points=250,
            difficulty=Difficulty.MEDIUM,
            deadline=now - timedelta(days=1),
            status=Status.UNCLAIMED,
            claimed_by=None,
            hostel=None,
        )

        # Alice claimed this one, but the clock ran out before she cracked it. The row
        # says in_progress; the deadline says expired, so cracking it must fail.
        overdue = self._upsert_mission(
            codename="OVERTIME",
            brief="Alice claimed this, but the deadline passed before she cracked it.",
            points=120,
            difficulty=Difficulty.MEDIUM,
            deadline=now - timedelta(hours=2),
            status=Status.IN_PROGRESS,
            claimed_by=alice,
            hostel=alice.hostel,
        )

        self._report(
            staff_token=staff_token,
            alice_token=alice_token,
            bob_token=bob_token,
            alice=alice,
            bob=bob,
            claimable=unclaimed[0],
            throttle_target=claimed,
            claimed=claimed,
            expired=expired,
            overdue=overdue,
        )

    # -- Builders ---------------------------------------------------------------

    def _reset(self):
        Mission.objects.all().delete()
        usernames = [STAFF["username"], *(p["username"] for p in PARTICIPANTS)]
        Participant.objects.filter(user__username__in=usernames).delete()
        User.objects.filter(username__in=usernames).delete()
        self.stdout.write(self.style.WARNING("Reset: missions and demo users deleted."))

    def _make_staff(self):
        user, _ = User.objects.get_or_create(username=STAFF["username"])
        user.is_staff = True
        user.set_password(STAFF["password"])
        user.save()
        token, _ = Token.objects.get_or_create(user=user)
        return user, token.key

    def _make_participant(self, spec, hostel):
        user, _ = User.objects.get_or_create(username=spec["username"])
        user.set_password(spec["password"])
        user.save()
        participant, _ = Participant.objects.update_or_create(
            user=user, defaults={"handle": spec["handle"], "hostel": hostel}
        )
        token, _ = Token.objects.get_or_create(user=user)
        return participant, token.key

    def _upsert_mission(self, codename, **fields):
        mission, _ = Mission.objects.update_or_create(codename=codename, defaults=fields)
        return mission

    # -- Output -----------------------------------------------------------------

    def _report(self, **ctx):
        write = self.stdout.write
        ok = self.style.SUCCESS

        write("")
        write(ok("Demo competition seeded."))
        write("")
        write("Tokens (paste into the Postman environment):")
        write(f"  staff_token   {ctx['staff_token']}   (staff / {STAFF['password']})")
        write(f"  alice_token   {ctx['alice_token']}   (alice / {PARTICIPANTS[0]['password']})")
        write(f"  bob_token     {ctx['bob_token']}   (bob   / {PARTICIPANTS[1]['password']})")
        write("")
        write("Mission ids:")
        write(f"  mission_id            {ctx['claimable'].id}   {ctx['claimable'].codename} (unclaimed, claimable)")
        write(f"  claimed_mission_id    {ctx['claimed'].id}   {ctx['claimed'].codename} (in_progress, bob's)")
        write(f"  expired_mission_id    {ctx['expired'].id}   {ctx['expired'].codename} (stored unclaimed, past deadline)")
        write(f"  overdue_mission_id    {ctx['overdue'].id}   {ctx['overdue'].codename} (alice's, stored in_progress, past deadline)")
        write(f"  throttle_mission_id   {ctx['throttle_target'].id}   {ctx['throttle_target'].codename} (claim it repeatedly to hit 429)")
        write("")
        write("Other ids:")
        write(f"  hostel_id            {ctx['alice'].hostel_id}   {ctx['alice'].hostel.name} (alice's)")
        write(f"  participant_id       {ctx['alice'].id}   alice")
        write(f"  other_hostel_id      {ctx['bob'].hostel_id}   {ctx['bob'].hostel.name} (bob's)")
        write(f"  bob_participant_id   {ctx['bob'].id}   bob")
        write("")
        write(f"Missions in total: {Mission.objects.count()}")
        write("")
