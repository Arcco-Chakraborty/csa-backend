"""API views.

The two service functions at the top -- :func:`claim_mission` and
:func:`apply_status_change` -- hold every rule about claiming and status transitions.
Both routes that can change a mission's status call the same one, so
``PATCH /api/missions/{id}/`` and ``PATCH /api/missions/{id}/status/`` cannot drift
apart.

Both end in a *conditional* ``UPDATE``: the row is only written if the database still
agrees it is claimable / still in progress and still inside its deadline. That single
statement is what makes concurrent claims safe without row locks, on SQLite included.
"""

from django.db import transaction
from django.db.models import F, Prefetch, Window
from django.db.models.functions import Rank
from django.utils import timezone
from rest_framework import mixins, status as http_status, viewsets
from rest_framework.authtoken.models import Token
from rest_framework.decorators import action
from rest_framework.exceptions import APIException, PermissionDenied
from rest_framework.generics import GenericAPIView
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from .constants import PARTICIPANT_WRITABLE_MISSION_FIELDS, Status
from .filters import MissionFilter
from .models import Hostel, Mission, Participant
from .permissions import (
    IsMissionClaimant,
    IsOwnerOrStaff,
    IsParticipant,
    IsStaff,
    MissionAccessPolicy,
    STATUS_ONLY,
    get_participant,
)
from .serializers import (
    HostelDetailSerializer,
    HostelListSerializer,
    LeaderboardSerializer,
    MissionSerializer,
    MissionStatusSerializer,
    ParticipantSerializer,
    RegisterResponseSerializer,
    RegisterSerializer,
)
from .throttles import ClaimRateThrottle

HANDLE_ONLY = "Participants may only update their handle."


# ---------------------------------------------------------------------------
# Domain errors
# ---------------------------------------------------------------------------
# DRF renders ``exc.detail`` straight into the body, so putting a dict there gives the
# ``{"detail": ..., "code": ...}`` shape the spec asks for, with a machine-readable
# code a client can branch on without string-matching.


class _CodedError(APIException):
    code_value = "error"
    default_detail = "Request could not be completed."

    def __init__(self, detail=None, code=None):
        super().__init__(
            {
                "detail": detail or self.default_detail,
                "code": code or self.code_value,
            }
        )


class MissionExpired(_CodedError):
    status_code = http_status.HTTP_400_BAD_REQUEST
    code_value = "mission_expired"
    default_detail = "Mission has expired."


class MissionUnavailable(_CodedError):
    status_code = http_status.HTTP_409_CONFLICT
    code_value = "mission_unavailable"
    default_detail = "Mission is not available to claim."


class InvalidTransition(_CodedError):
    status_code = http_status.HTTP_400_BAD_REQUEST
    code_value = "invalid_transition"
    default_detail = "Invalid status transition."


class CrackedIsFinal(InvalidTransition):
    code_value = "cracked_is_final"
    default_detail = "Cracked missions are final."


# ---------------------------------------------------------------------------
# Services
# ---------------------------------------------------------------------------


def _reject_unclaimable(mission):
    """Turn a mission that failed the claimable test into the right error.

    Called both up-front and again after a losing conditional update, so a caller that
    lost a race gets the same answer it would have got had it arrived a moment later.
    """
    if mission.is_past_deadline() and mission.status != Status.CRACKED:
        raise MissionExpired()
    raise MissionUnavailable()


def claim_mission(mission, participant):
    """Claim ``mission`` for ``participant``, or raise.

    The request body is irrelevant: the hostel written to the mission is always
    ``participant.hostel``, so a client cannot score for a hostel it does not belong to.
    """
    if mission.effective_status != Status.UNCLAIMED:
        _reject_unclaimable(mission)

    now = timezone.now()
    with transaction.atomic():
        # Race-safe: the WHERE clause re-asserts every precondition, so of two
        # simultaneous claims exactly one can match a row and the other updates zero.
        updated = (
            Mission.objects.filter(pk=mission.pk)
            .claimable(now)
            .update(
                status=Status.IN_PROGRESS,
                claimed_by=participant,
                hostel=participant.hostel,
                updated_at=now,
            )
        )

    if updated == 0:
        _reject_unclaimable(Mission.objects.get(pk=mission.pk))

    return Mission.objects.with_related().get(pk=mission.pk)


def apply_status_change(mission, participant, new_status):
    """Apply a claimant's status change, or raise.

    Ownership and the payload-key whitelist are already enforced by the permission
    layer; what is left is the deadline and the transition table.
    """
    effective = mission.effective_status

    if effective == Status.CRACKED:
        raise CrackedIsFinal()

    # Everything below needs a live, in-progress mission.
    if mission.is_past_deadline():
        raise MissionExpired()

    if effective != Status.IN_PROGRESS:
        raise InvalidTransition(
            f"Cannot change status from '{effective}' to '{new_status}'."
        )

    if new_status == Status.IN_PROGRESS:
        # Idempotent no-op: claiming already set in_progress.
        return Mission.objects.with_related().get(pk=mission.pk)

    now = timezone.now()
    with transaction.atomic():
        # Conditional again, so a crack submitted exactly on the deadline boundary
        # cannot slip past the check above.
        updated = (
            Mission.objects.filter(
                pk=mission.pk,
                status=Status.IN_PROGRESS,
                claimed_by=participant,
                deadline__gt=now,
            ).update(status=Status.CRACKED, updated_at=now)
        )

    if updated == 0:
        fresh = Mission.objects.get(pk=mission.pk)
        if fresh.status == Status.CRACKED:
            raise CrackedIsFinal()
        raise MissionExpired()

    return Mission.objects.with_related().get(pk=mission.pk)


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


class RegisterView(GenericAPIView):
    """Create a user and their participant profile, and hand back a token."""

    permission_classes = [AllowAny]
    serializer_class = RegisterSerializer

    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        participant = serializer.save()
        token, _ = Token.objects.get_or_create(user=participant.user)
        return Response(
            {
                "participant": ParticipantSerializer(participant).data,
                "token": token.key,
            },
            status=http_status.HTTP_201_CREATED,
        )


# ---------------------------------------------------------------------------
# Read-only resources
# ---------------------------------------------------------------------------


class HostelViewSet(viewsets.ReadOnlyModelViewSet):
    """Hostels are seeded data: read-only over the API, managed in the admin."""

    permission_classes = [IsAuthenticated]
    filter_backends = []

    def get_queryset(self):
        queryset = Hostel.objects.with_scores()
        if self.action == "retrieve":
            queryset = queryset.prefetch_related(
                Prefetch(
                    "missions",
                    queryset=Mission.objects.filter(
                        status=Status.CRACKED
                    ).select_related("claimed_by"),
                    to_attr="cracked_missions",
                )
            )
        return queryset.order_by("name")

    def get_serializer_class(self):
        return (
            HostelDetailSerializer if self.action == "retrieve" else HostelListSerializer
        )


class LeaderboardView(GenericAPIView):
    """Live standings for all hostels. Public, and never paginated."""

    permission_classes = [AllowAny]
    serializer_class = LeaderboardSerializer
    pagination_class = None
    filter_backends = []

    def get_queryset(self):
        # Rank is computed by the database, so ties genuinely share a rank
        # (1, 2, 2, 4) instead of being numbered off by position.
        return (
            Hostel.objects.with_scores()
            .annotate(
                rank=Window(expression=Rank(), order_by=F("score").desc())
            )
            .order_by("-score", "name")
        )

    def get(self, request):
        serializer = self.get_serializer(self.get_queryset(), many=True)
        return Response(serializer.data)


class ParticipantViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """Participants are created through registration, not here."""

    serializer_class = ParticipantSerializer
    queryset = Participant.objects.select_related("user", "hostel").order_by("handle")
    filter_backends = []

    def get_permissions(self):
        if self.action == "destroy":
            return [IsStaff()]
        if self.action in ("update", "partial_update"):
            return [IsAuthenticated(), IsOwnerOrStaff()]
        if self.action == "me":
            return [IsParticipant()]
        return [IsAuthenticated()]

    def perform_update(self, serializer):
        """Non-staff may change their handle and nothing else.

        Refusing outright (rather than silently dropping the field) means a client
        that tries to switch hostels finds out, instead of believing it worked.
        """
        if not self.request.user.is_staff and "hostel" in serializer.validated_data:
            raise PermissionDenied(HANDLE_ONLY)
        serializer.save()

    @action(detail=False, methods=["get"])
    def me(self, request):
        return Response(self.get_serializer(get_participant(request.user)).data)


# ---------------------------------------------------------------------------
# Missions
# ---------------------------------------------------------------------------


class MissionViewSet(viewsets.ModelViewSet):
    serializer_class = MissionSerializer
    permission_classes = [MissionAccessPolicy]
    queryset = Mission.objects.with_related()
    filterset_class = MissionFilter
    search_fields = ["codename", "brief"]
    ordering_fields = ["deadline", "points", "created_at"]
    ordering = ["deadline"]

    def get_permissions(self):
        if self.action == "claim":
            return [IsParticipant()]
        if self.action == "set_status":
            return [IsParticipant(), IsMissionClaimant()]
        return super().get_permissions()

    def get_serializer_class(self):
        return MissionStatusSerializer if self.action == "set_status" else MissionSerializer

    # -- Writes ----------------------------------------------------------------

    def update(self, request, *args, **kwargs):
        """Staff edit missions here; a claimant's status-only PATCH is routed to the
        same service the dedicated status route uses, so the two agree by construction.
        """
        if not request.user.is_staff:
            mission = self.get_object()  # runs MissionAccessPolicy object checks
            return self._change_status(request, mission)
        return super().update(request, *args, **kwargs)

    def _change_status(self, request, mission):
        # Also enforced by MissionAccessPolicy for the generic PATCH route; repeated
        # here because the dedicated /status/ route does not go through that policy.
        submitted = set(request.data.keys())
        if not submitted.issubset(PARTICIPANT_WRITABLE_MISSION_FIELDS):
            raise PermissionDenied(STATUS_ONLY)

        serializer = MissionStatusSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        updated = apply_status_change(
            mission,
            get_participant(request.user),
            serializer.validated_data["status"],
        )
        return Response(MissionSerializer(updated).data)

    # -- Custom actions --------------------------------------------------------

    @action(detail=True, methods=["post"], throttle_classes=[ClaimRateThrottle])
    def claim(self, request, pk=None):
        mission = self.get_object()
        claimed = claim_mission(mission, get_participant(request.user))
        return Response(MissionSerializer(claimed).data)

    @action(detail=True, methods=["patch"], url_path="status", url_name="status")
    def set_status(self, request, pk=None):
        return self._change_status(request, self.get_object())
