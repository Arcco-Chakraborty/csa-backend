"""Serializers.

Two rules run through all of them:

* ``status`` is read as ``effective_status``, never as the raw column.
* ``hostel`` and ``claimed_by`` on a mission are read-only everywhere. They are
  written by exactly one code path -- the claim action -- and no client payload can
  touch them.
"""

from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.db import transaction
from rest_framework import serializers

from .constants import (
    PARTICIPANT_WRITABLE_STATUSES,
    Status,
)
from .models import Hostel, Mission, Participant


class HostelMiniSerializer(serializers.ModelSerializer):
    class Meta:
        model = Hostel
        fields = ["id", "name"]


class ParticipantMiniSerializer(serializers.ModelSerializer):
    hostel = HostelMiniSerializer(read_only=True)

    class Meta:
        model = Participant
        fields = ["id", "handle", "hostel"]


class ClaimantMiniSerializer(serializers.ModelSerializer):
    """The claimant as it appears inside a hostel's cracked-mission list."""

    class Meta:
        model = Participant
        fields = ["id", "handle"]


class ParticipantSerializer(serializers.ModelSerializer):
    """Participant profile.

    ``hostel`` reads as a nested object and is written by id through ``hostel_id``.
    Whether ``hostel_id`` is honoured at all depends on the caller: the view drops it
    for non-staff, so a participant can change their handle but never defect to
    another hostel.
    """

    username = serializers.CharField(source="user.username", read_only=True)
    hostel = HostelMiniSerializer(read_only=True)
    hostel_id = serializers.PrimaryKeyRelatedField(
        queryset=Hostel.objects.all(),
        source="hostel",
        write_only=True,
        required=False,
    )

    class Meta:
        model = Participant
        fields = ["id", "handle", "username", "hostel", "hostel_id"]


class RegisterSerializer(serializers.Serializer):
    """Creates a ``User`` and its ``Participant`` in one transaction."""

    username = serializers.CharField(max_length=150)
    password = serializers.CharField(write_only=True, style={"input_type": "password"})
    handle = serializers.CharField(max_length=32)
    hostel = serializers.PrimaryKeyRelatedField(queryset=Hostel.objects.all())

    def validate_username(self, value):
        if User.objects.filter(username__iexact=value).exists():
            raise serializers.ValidationError("A user with that username already exists.")
        return value

    def validate_handle(self, value):
        if Participant.objects.filter(handle__iexact=value).exists():
            raise serializers.ValidationError("A participant with that handle already exists.")
        return value

    def validate_password(self, value):
        validate_password(value)
        return value

    @transaction.atomic
    def create(self, validated_data):
        user = User.objects.create_user(
            username=validated_data["username"],
            password=validated_data["password"],
        )
        return Participant.objects.create(
            user=user,
            handle=validated_data["handle"],
            hostel=validated_data["hostel"],
        )


class RegisterResponseSerializer(serializers.Serializer):
    """Documents the 201 body: the new participant plus its auth token."""

    participant = ParticipantSerializer(read_only=True)
    token = serializers.CharField(read_only=True)


class MissionSerializer(serializers.ModelSerializer):
    """Mission read/write.

    ``status`` is writable (staff use it) but always *reads back* as
    ``effective_status``, so the API never reports a stale ``unclaimed`` for a mission
    whose deadline has passed.
    """

    status = serializers.ChoiceField(choices=Status.choices, required=False)
    claimed_by = ParticipantMiniSerializer(read_only=True)
    hostel = HostelMiniSerializer(read_only=True)
    is_expired = serializers.BooleanField(read_only=True)

    class Meta:
        model = Mission
        fields = [
            "id",
            "codename",
            "brief",
            "points",
            "difficulty",
            "status",
            "deadline",
            "is_expired",
            "claimed_by",
            "hostel",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "claimed_by", "hostel", "created_at", "updated_at"]

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data["status"] = instance.effective_status
        return data

    def validate(self, attrs):
        """Staff-side invariants from PRD 3.4.

        A staff user cannot conjure an ``in_progress``/``cracked`` mission that nobody
        claimed, because they cannot set ``claimed_by`` at all.
        """
        instance = self.instance
        new_status = attrs.get(
            "status", instance.status if instance else Status.UNCLAIMED
        )
        claimant_id = instance.claimed_by_id if instance else None

        if new_status in (Status.IN_PROGRESS, Status.CRACKED) and claimant_id is None:
            raise serializers.ValidationError(
                {
                    "status": (
                        f"Cannot set status to '{new_status}' on a mission with no "
                        "claimant. A mission becomes claimed only through "
                        "POST /api/missions/{id}/claim/."
                    )
                }
            )
        return attrs

    def update(self, instance, validated_data):
        """Resetting a mission to ``unclaimed`` releases it in the same save."""
        if validated_data.get("status") == Status.UNCLAIMED:
            instance.claimed_by = None
            instance.hostel = None
        return super().update(instance, validated_data)


class MissionStatusSerializer(serializers.Serializer):
    """Body of the claimant-only status route: ``{"status": "cracked"}``."""

    status = serializers.ChoiceField(choices=[(s.value, s.label) for s in PARTICIPANT_WRITABLE_STATUSES])


class CrackedMissionSerializer(serializers.ModelSerializer):
    claimed_by = ClaimantMiniSerializer(read_only=True)

    class Meta:
        model = Mission
        fields = ["id", "codename", "points", "difficulty", "claimed_by"]


class HostelListSerializer(serializers.ModelSerializer):
    """List rows stay light: no nested missions."""

    score = serializers.IntegerField(read_only=True)
    cracked_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Hostel
        fields = ["id", "name", "score", "cracked_count"]


class HostelDetailSerializer(HostelListSerializer):
    cracked_missions = CrackedMissionSerializer(many=True, read_only=True)

    class Meta(HostelListSerializer.Meta):
        fields = HostelListSerializer.Meta.fields + ["cracked_missions"]


class LeaderboardSerializer(serializers.ModelSerializer):
    rank = serializers.IntegerField(read_only=True)
    score = serializers.IntegerField(read_only=True)
    cracked_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Hostel
        fields = ["rank", "id", "name", "score", "cracked_count"]
