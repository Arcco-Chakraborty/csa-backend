"""Querysets that derive competition state instead of storing it.

The leaderboard is the reason this module exists: a hostel's score is *never* a column.
It is aggregated from cracked missions on every read, so a staff edit that un-cracks a
mission drops the score in the same instant, with no bookkeeping anywhere.
"""

from django.db import models
from django.db.models import Count, Q, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone

from .constants import PERISHABLE_STATUSES, Status


class HostelQuerySet(models.QuerySet):
    def with_scores(self):
        """Annotate ``score`` and ``cracked_count`` from cracked missions only.

        ``Coalesce(..., 0)`` keeps hostels with nothing cracked at ``0`` rather than
        ``None``, which is what makes all 17 hostels appear on the leaderboard.
        """
        cracked = Q(missions__status=Status.CRACKED)
        return self.annotate(
            score=Coalesce(Sum("missions__points", filter=cracked), 0),
            cracked_count=Count("missions", filter=cracked),
        )


class MissionQuerySet(models.QuerySet):
    def with_related(self):
        """Avoid N+1 queries when serializing nested claimant/hostel data."""
        return self.select_related("claimed_by__user", "claimed_by__hostel", "hostel")

    # -- Effective-status helpers -------------------------------------------------
    # "Effective" status folds the deadline in. The stored status may say `unclaimed`
    # while the mission is really expired; these helpers are the single definition of
    # that rule, shared by the filters and the views.

    def live(self, now=None):
        """Missions whose deadline has not passed yet."""
        return self.filter(deadline__gt=now or timezone.now())

    def past_deadline(self, now=None):
        return self.filter(deadline__lte=now or timezone.now())

    def effective_unclaimed(self, now=None):
        return self.filter(status=Status.UNCLAIMED).live(now)

    def effective_in_progress(self, now=None):
        return self.filter(status=Status.IN_PROGRESS).live(now)

    def effective_cracked(self):
        """Cracked is terminal: it survives the deadline and keeps its points."""
        return self.filter(status=Status.CRACKED)

    def effective_expired(self, now=None):
        """Stored-expired, plus anything perishable that ran out of time."""
        now = now or timezone.now()
        return self.filter(
            Q(status=Status.EXPIRED)
            | Q(status__in=PERISHABLE_STATUSES, deadline__lte=now)
        )

    def claimable(self, now=None):
        """The exact condition the race-safe claim UPDATE filters on."""
        return self.filter(
            status=Status.UNCLAIMED,
            claimed_by__isnull=True,
            deadline__gt=now or timezone.now(),
        )
