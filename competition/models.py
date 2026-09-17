"""Relational model for the competition.

Two things are deliberately *absent* here:

* ``Hostel`` has no ``score`` column -- see :mod:`competition.managers`.
* Nothing persists the ``expired`` status on a schedule -- see
  :attr:`Mission.effective_status`.
"""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone

from .constants import PERISHABLE_STATUSES, Difficulty, Status
from .managers import HostelQuerySet, MissionQuerySet


class Hostel(models.Model):
    """One of the 17 hostels. Seeded by a data migration; read-only over the API."""

    name = models.CharField(max_length=64, unique=True)

    objects = HostelQuerySet.as_manager()

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Participant(models.Model):
    """A competitor: a Django user, a unique handle, and the hostel they score for."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="participant",
    )
    handle = models.CharField(max_length=32, unique=True)
    hostel = models.ForeignKey(
        Hostel,
        on_delete=models.PROTECT,
        related_name="participants",
    )

    class Meta:
        ordering = ["handle"]

    def __str__(self):
        return self.handle


class Mission(models.Model):
    """A task worth points to whichever hostel cracks it."""

    codename = models.CharField(max_length=100, unique=True)
    brief = models.TextField()
    points = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    difficulty = models.CharField(max_length=16, choices=Difficulty.choices)
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.UNCLAIMED,
    )
    deadline = models.DateTimeField()
    claimed_by = models.ForeignKey(
        Participant,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="claimed_missions",
    )
    # Written only by the claim action, copied from claimed_by.hostel. No client
    # payload can set this -- it is read-only on every serializer.
    hostel = models.ForeignKey(
        Hostel,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="missions",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = MissionQuerySet.as_manager()

    class Meta:
        ordering = ["deadline"]

    def __str__(self):
        return self.codename

    # -- Deadline handling ------------------------------------------------------

    def is_past_deadline(self):
        return timezone.now() >= self.deadline

    @property
    def effective_status(self):
        """The status the API reports, with the deadline folded in.

        A cracked mission stays cracked forever and keeps scoring. An unclaimed or
        in-progress mission past its deadline reads as expired everywhere, even though
        the database still says otherwise.
        """
        if self.status in PERISHABLE_STATUSES and self.is_past_deadline():
            return Status.EXPIRED
        return self.status

    @property
    def is_expired(self):
        return self.effective_status == Status.EXPIRED

    # -- Invariants -------------------------------------------------------------

    def clean(self):
        """Model-level invariants, mirrored by the serializers.

        These guard the admin and any direct ``full_clean()`` caller; the API enforces
        the same rules in ``MissionSerializer.validate`` so clients get field-keyed
        errors instead of a 500.
        """
        errors = {}

        if self.status in (Status.IN_PROGRESS, Status.CRACKED):
            if self.claimed_by_id is None:
                errors["claimed_by"] = (
                    f"A mission with status '{self.status}' must have a claimant."
                )
            if self.hostel_id is None:
                errors["hostel"] = (
                    f"A mission with status '{self.status}' must have a hostel."
                )
        elif self.status == Status.UNCLAIMED:
            if self.claimed_by_id is not None:
                errors["claimed_by"] = "An unclaimed mission cannot have a claimant."
            if self.hostel_id is not None:
                errors["hostel"] = "An unclaimed mission cannot have a hostel."

        if (
            self.hostel_id is not None
            and self.claimed_by_id is not None
            and self.hostel_id != self.claimed_by.hostel_id
        ):
            errors["hostel"] = "Mission hostel must match the claimant's hostel."

        if errors:
            raise ValidationError(errors)
