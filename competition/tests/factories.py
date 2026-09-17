"""Shared builders for the test suite.

Kept deliberately thin -- these only remove boilerplate. Every test still states the
field that matters to it explicitly, so a test reads as its own specification.
"""

from datetime import timedelta

from django.contrib.auth.models import User
from django.utils import timezone

from competition.constants import Difficulty, Status
from competition.models import Hostel, Mission, Participant

_codename_counter = 0


def future(**kwargs):
    return timezone.now() + timedelta(**(kwargs or {"days": 7}))


def past(**kwargs):
    return timezone.now() - timedelta(**(kwargs or {"days": 1}))


def hostels(n=2):
    """The first ``n`` seeded hostels, by id.

    Tests never name a hostel -- the real 17 come from the juniors task sheet and may
    be corrected at any time.
    """
    return list(Hostel.objects.order_by("id")[:n])


def make_participant(username, handle=None, hostel=None, password="testpass123"):
    user = User.objects.create_user(username=username, password=password)
    return Participant.objects.create(
        user=user,
        handle=handle or username,
        hostel=hostel or hostels(1)[0],
    )


def make_staff(username="staff", password="staffpass123"):
    return User.objects.create_user(username=username, password=password, is_staff=True)


def make_mission(
    codename=None,
    *,
    brief="Do the thing.",
    points=100,
    difficulty=Difficulty.EASY,
    status=Status.UNCLAIMED,
    deadline=None,
    claimed_by=None,
    hostel=None,
):
    """Create a mission directly, bypassing API rules.

    Bypassing is the point: tests need to set up states the API would refuse to
    create, such as a stored-``unclaimed`` mission whose deadline has already passed.
    """
    global _codename_counter
    _codename_counter += 1
    return Mission.objects.create(
        codename=codename or f"MISSION_{_codename_counter:04d}",
        brief=brief,
        points=points,
        difficulty=difficulty,
        status=status,
        deadline=deadline if deadline is not None else future(),
        claimed_by=claimed_by,
        hostel=hostel if hostel is not None else (claimed_by.hostel if claimed_by else None),
    )


def make_cracked(participant, points=100, **kwargs):
    kwargs.setdefault("deadline", future())
    return make_mission(
        points=points,
        status=Status.CRACKED,
        claimed_by=participant,
        hostel=participant.hostel,
        **kwargs,
    )
