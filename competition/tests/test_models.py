"""Model invariants, derived scores and derived expiry.

The two properties under test here are the ones the whole design rests on: a hostel
score is never stored, and a mission's expiry is never stored.
"""

from unittest import mock

from django.core.exceptions import ValidationError
from django.test import TestCase

from competition.constants import HOSTEL_COUNT, Difficulty, Status
from competition.models import Hostel, Mission

from .factories import future, hostels, make_cracked, make_mission, make_participant, past


class HostelSeedTests(TestCase):
    def test_data_migration_seeded_all_hostels(self):
        self.assertEqual(Hostel.objects.count(), HOSTEL_COUNT)

    def test_hostel_names_are_unique(self):
        names = list(Hostel.objects.values_list("name", flat=True))
        self.assertEqual(len(names), len(set(names)))


class ScoreAnnotationTests(TestCase):
    def setUp(self):
        self.hostel_a, self.hostel_b = hostels(2)
        self.alice = make_participant("alice", hostel=self.hostel_a)
        self.bob = make_participant("bob", hostel=self.hostel_b)

    def score_of(self, hostel):
        return Hostel.objects.with_scores().get(pk=hostel.pk)

    def test_hostel_with_no_cracked_missions_scores_zero(self):
        row = self.score_of(self.hostel_a)
        self.assertEqual(row.score, 0)
        self.assertEqual(row.cracked_count, 0)

    def test_every_hostel_is_annotated_even_with_no_missions(self):
        rows = Hostel.objects.with_scores()
        self.assertEqual(rows.count(), HOSTEL_COUNT)
        self.assertTrue(all(row.score == 0 for row in rows))

    def test_cracked_missions_sum_into_the_score(self):
        make_cracked(self.alice, points=150)
        make_cracked(self.alice, points=300)
        row = self.score_of(self.hostel_a)
        self.assertEqual(row.score, 450)
        self.assertEqual(row.cracked_count, 2)

    def test_in_progress_missions_do_not_contribute(self):
        make_mission(
            points=500,
            status=Status.IN_PROGRESS,
            claimed_by=self.alice,
            hostel=self.hostel_a,
        )
        row = self.score_of(self.hostel_a)
        self.assertEqual(row.score, 0)
        self.assertEqual(row.cracked_count, 0)

    def test_expired_missions_do_not_contribute(self):
        make_mission(points=500, status=Status.EXPIRED, deadline=past())
        make_mission(points=500, status=Status.UNCLAIMED, deadline=past())
        self.assertEqual(self.score_of(self.hostel_a).score, 0)

    def test_scores_are_isolated_per_hostel(self):
        make_cracked(self.alice, points=150)
        make_cracked(self.bob, points=70)
        self.assertEqual(self.score_of(self.hostel_a).score, 150)
        self.assertEqual(self.score_of(self.hostel_b).score, 70)

    def test_uncracking_a_mission_drops_the_score_immediately(self):
        """Proves the score is derived: no bookkeeping runs, yet the total changes."""
        mission = make_cracked(self.alice, points=150)
        self.assertEqual(self.score_of(self.hostel_a).score, 150)

        # Exactly what a staff PATCH back to `unclaimed` does.
        mission.status = Status.UNCLAIMED
        mission.claimed_by = None
        mission.hostel = None
        mission.save()

        self.assertEqual(self.score_of(self.hostel_a).score, 0)
        self.assertEqual(self.score_of(self.hostel_a).cracked_count, 0)

    def test_no_model_field_stores_a_score(self):
        field_names = {f.name for f in Hostel._meta.get_fields()}
        self.assertNotIn("score", field_names)
        self.assertNotIn("cracked_count", field_names)
        self.assertNotIn("points", field_names)


class EffectiveStatusTests(TestCase):
    def setUp(self):
        self.hostel_a = hostels(1)[0]
        self.alice = make_participant("alice", hostel=self.hostel_a)

    def test_unclaimed_before_deadline_reads_unclaimed(self):
        mission = make_mission(status=Status.UNCLAIMED, deadline=future())
        self.assertEqual(mission.effective_status, Status.UNCLAIMED)
        self.assertFalse(mission.is_expired)
        self.assertFalse(mission.is_past_deadline())

    def test_unclaimed_past_deadline_reads_expired(self):
        mission = make_mission(status=Status.UNCLAIMED, deadline=past())
        self.assertEqual(mission.effective_status, Status.EXPIRED)
        self.assertTrue(mission.is_expired)
        # The database still disagrees -- expiry is computed, never written.
        self.assertEqual(Mission.objects.get(pk=mission.pk).status, Status.UNCLAIMED)

    def test_in_progress_past_deadline_reads_expired(self):
        mission = make_mission(
            status=Status.IN_PROGRESS,
            deadline=past(),
            claimed_by=self.alice,
            hostel=self.hostel_a,
        )
        self.assertEqual(mission.effective_status, Status.EXPIRED)

    def test_cracked_stays_cracked_past_deadline(self):
        mission = make_cracked(self.alice, points=150, deadline=past())
        self.assertEqual(mission.effective_status, Status.CRACKED)
        self.assertFalse(mission.is_expired)

    def test_cracked_still_scores_past_deadline(self):
        make_cracked(self.alice, points=150, deadline=past())
        self.assertEqual(Hostel.objects.with_scores().get(pk=self.hostel_a.pk).score, 150)

    def test_stored_expired_reads_expired_even_before_its_deadline(self):
        mission = make_mission(status=Status.EXPIRED, deadline=future())
        self.assertEqual(mission.effective_status, Status.EXPIRED)

    def test_deadline_boundary_is_inclusive(self):
        """`now >= deadline`: a mission is expired *at* its deadline, not after."""
        mission = make_mission(status=Status.UNCLAIMED, deadline=future(days=1))
        with mock.patch("django.utils.timezone.now", return_value=mission.deadline):
            self.assertTrue(mission.is_past_deadline())
            self.assertEqual(mission.effective_status, Status.EXPIRED)


class MissionInvariantTests(TestCase):
    def setUp(self):
        self.hostel_a, self.hostel_b = hostels(2)
        self.alice = make_participant("alice", hostel=self.hostel_a)

    def build(self, **kwargs):
        defaults = dict(
            codename="INVARIANT_CHECK",
            brief="b",
            points=10,
            difficulty=Difficulty.EASY,
            deadline=future(),
        )
        defaults.update(kwargs)
        return Mission(**defaults)

    def test_in_progress_requires_a_claimant(self):
        with self.assertRaises(ValidationError) as ctx:
            self.build(status=Status.IN_PROGRESS).full_clean()
        self.assertIn("claimed_by", ctx.exception.message_dict)

    def test_cracked_requires_a_claimant(self):
        with self.assertRaises(ValidationError) as ctx:
            self.build(status=Status.CRACKED).full_clean()
        self.assertIn("claimed_by", ctx.exception.message_dict)

    def test_unclaimed_rejects_a_claimant(self):
        with self.assertRaises(ValidationError) as ctx:
            self.build(
                status=Status.UNCLAIMED, claimed_by=self.alice, hostel=self.hostel_a
            ).full_clean()
        self.assertIn("claimed_by", ctx.exception.message_dict)

    def test_hostel_must_match_the_claimants_hostel(self):
        with self.assertRaises(ValidationError) as ctx:
            self.build(
                status=Status.IN_PROGRESS, claimed_by=self.alice, hostel=self.hostel_b
            ).full_clean()
        self.assertIn("hostel", ctx.exception.message_dict)

    def test_valid_claimed_mission_passes(self):
        self.build(
            status=Status.IN_PROGRESS, claimed_by=self.alice, hostel=self.hostel_a
        ).full_clean()

    def test_points_must_be_at_least_one(self):
        with self.assertRaises(ValidationError) as ctx:
            self.build(points=0).full_clean()
        self.assertIn("points", ctx.exception.message_dict)
