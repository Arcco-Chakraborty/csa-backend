"""Admin registration.

Hostels are read-only over the API, so this is where staff manage them if they ever
need to. ``claimed_by``/``hostel`` stay editable *here* on purpose -- the admin is a
trusted surface, and ``Mission.clean()`` still enforces the invariants on save.
"""

from django.contrib import admin

from .models import Hostel, Mission, Participant


@admin.register(Hostel)
class HostelAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "score", "cracked_count")
    search_fields = ("name",)
    ordering = ("name",)

    def get_queryset(self, request):
        return super().get_queryset(request).with_scores()

    @admin.display(ordering="score", description="Score")
    def score(self, obj):
        return obj.score

    @admin.display(ordering="cracked_count", description="Cracked")
    def cracked_count(self, obj):
        return obj.cracked_count


@admin.register(Participant)
class ParticipantAdmin(admin.ModelAdmin):
    list_display = ("id", "handle", "user", "hostel")
    list_filter = ("hostel",)
    search_fields = ("handle", "user__username")
    autocomplete_fields = ("hostel",)


@admin.register(Mission)
class MissionAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "codename",
        "points",
        "difficulty",
        "status",
        "effective_status",
        "deadline",
        "claimed_by",
        "hostel",
    )
    list_filter = ("status", "difficulty", "hostel")
    search_fields = ("codename", "brief")
    readonly_fields = ("created_at", "updated_at")
    autocomplete_fields = ("claimed_by", "hostel")

    def get_queryset(self, request):
        return super().get_queryset(request).with_related()

    @admin.display(description="Effective status")
    def effective_status(self, obj):
        return obj.effective_status
