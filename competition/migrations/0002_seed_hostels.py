"""Seed the 17 competition hostels.

Idempotent and name-synced: re-running it is a no-op, and editing
``competition.constants.HOSTELS`` then re-applying this migration adds the new names.
The reverse deletes only the seeded names, and only when no mission or participant
still points at them (the FKs are PROTECT), so a reverse on a populated database fails
loudly rather than silently orphaning data.
"""

from django.db import migrations

from competition.constants import HOSTELS


def seed_hostels(apps, schema_editor):
    Hostel = apps.get_model("competition", "Hostel")
    existing = set(Hostel.objects.values_list("name", flat=True))
    Hostel.objects.bulk_create(
        [Hostel(name=name) for name in HOSTELS if name not in existing]
    )


def unseed_hostels(apps, schema_editor):
    Hostel = apps.get_model("competition", "Hostel")
    Hostel.objects.filter(name__in=HOSTELS).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("competition", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed_hostels, unseed_hostels),
    ]
