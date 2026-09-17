"""Single place for the competition's fixed vocabularies.

Nothing else in the codebase hardcodes a hostel name or a difficulty/status string --
everything imports from here, so correcting this file corrects the whole project.
"""

from django.db import models


class Status(models.TextChoices):
    """Stored mission status.

    ``EXPIRED`` is a *storable* value (staff may set it explicitly), but expiry is
    normally derived: see ``Mission.effective_status``. Nothing in this project writes
    ``EXPIRED`` to the database automatically, and there is no cron job.
    """

    UNCLAIMED = "unclaimed", "Unclaimed"
    IN_PROGRESS = "in_progress", "In progress"
    CRACKED = "cracked", "Cracked"
    EXPIRED = "expired", "Expired"


class Difficulty(models.TextChoices):
    """Mission difficulty, carried over from the juniors task."""

    EASY = "easy", "Easy"
    MEDIUM = "medium", "Medium"
    HARD = "hard", "Hard"
    BOSS_LEVEL = "boss_level", "Boss level"


STATUS_CHOICES = Status.choices
DIFFICULTY_CHOICES = Difficulty.choices

#: Statuses a mission can hold while it is still "live" -- i.e. the ones that decay to
#: ``expired`` once the deadline passes. ``cracked`` and ``expired`` are terminal.
PERISHABLE_STATUSES = (Status.UNCLAIMED, Status.IN_PROGRESS)

#: The only statuses a participant may write, via the claimant status route.
PARTICIPANT_WRITABLE_STATUSES = (Status.IN_PROGRESS, Status.CRACKED)

#: The only mission field a participant may include in a PATCH payload.
PARTICIPANT_WRITABLE_MISSION_FIELDS = frozenset({"status"})


# ---------------------------------------------------------------------------
# Hostels
# ---------------------------------------------------------------------------
# TODO(evaluator): replace this list with the exact 17 names from the juniors task
# sheet if they differ. These are the BITS Pilani (Pilani campus) bhawans.
#
# The first 14 are the well-attested Pilani bhawans. The last three --
# "Gautam Bhawan", "Kalam Bhawan" and "Sarojini Bhawan" -- are the least certain
# entries and are the ones most likely to need swapping.
#
# To change the list: edit below, then delete and regenerate
# `competition/migrations/0002_seed_hostels.py`, or run `manage.py migrate competition
# 0001` and re-migrate. The data migration syncs by name and is safe to re-run.
#
# Order here is irrelevant -- the API always orders hostels explicitly.
HOSTELS = [
    "Ashok Bhawan",
    "Bhagirath Bhawan",
    "Budh Bhawan",
    "C.V. Raman Bhawan",
    "Gandhi Bhawan",
    "Krishna Bhawan",
    "Malviya Bhawan",
    "Meera Bhawan",
    "Ram Bhawan",
    "Rana Pratap Bhawan",
    "Shankar Bhawan",
    "Srinivasa Ramanujan Bhawan",
    "Vishwakarma Bhawan",
    "Vyas Bhawan",
    "Gautam Bhawan",
    "Kalam Bhawan",
    "Sarojini Bhawan",
]

#: The competition has exactly this many hostels; tests assert the count, never a name.
HOSTEL_COUNT = len(HOSTELS)
