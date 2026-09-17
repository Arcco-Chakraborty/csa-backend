"""Mission filtering.

The interesting one is ``status``: filtering on the stored column would leak
past-deadline missions into ``?status=unclaimed``. Each choice therefore maps to a
queryset helper that folds the deadline in, so the filter agrees with what the
serializer prints.
"""

import django_filters
from django.utils import timezone

from .constants import DIFFICULTY_CHOICES, STATUS_CHOICES, Status
from .models import Hostel, Mission


class MissionFilter(django_filters.FilterSet):
    status = django_filters.ChoiceFilter(
        choices=STATUS_CHOICES,
        method="filter_effective_status",
        label="Effective status (deadline-aware)",
        help_text=(
            "Filters on effective status, not the stored column: 'unclaimed' and "
            "'in_progress' exclude anything past its deadline, and 'expired' includes "
            "them."
        ),
    )
    difficulty = django_filters.ChoiceFilter(choices=DIFFICULTY_CHOICES)
    hostel = django_filters.ModelChoiceFilter(
        queryset=Hostel.objects.all(),
        label="Hostel the mission belongs to",
        help_text="Hostel id. A mission gets a hostel when it is claimed.",
    )

    class Meta:
        model = Mission
        fields = ["status", "difficulty", "hostel"]

    def filter_effective_status(self, queryset, name, value):
        now = timezone.now()
        handlers = {
            Status.UNCLAIMED: lambda qs: qs.effective_unclaimed(now),
            Status.IN_PROGRESS: lambda qs: qs.effective_in_progress(now),
            Status.CRACKED: lambda qs: qs.effective_cracked(),
            Status.EXPIRED: lambda qs: qs.effective_expired(now),
        }
        handler = handlers.get(value)
        return handler(queryset) if handler else queryset
