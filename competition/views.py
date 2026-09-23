"""API views.

The two service functions at the top -- :func:`claim_mission` and
:func:`apply_status_change` -- hold every rule about claiming and status transitions.
For a claimant, both routes that can change a mission's status call the same one, so
``PATCH /api/missions/{id}/`` and ``PATCH /api/missions/{id}/status/`` cannot drift
apart. Staff edits on the generic route go through ``MissionSerializer`` instead, which
may only release or void a mission -- never claim or crack one.

Both end in a *conditional* ``UPDATE``: the row is only written if the database still
agrees it is claimable / still in progress and still inside its deadline. That single
statement is what makes concurrent claims safe without row locks, on SQLite included.
"""

from django.db import transaction
from django.db.models import F, Prefetch, Window
from django.db.models.functions import Rank
from django.utils import timezone
from drf_spectacular.utils import (
    OpenApiExample,
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
    extend_schema_view,
)
from rest_framework import mixins, status as http_status, viewsets
from rest_framework.authtoken.models import Token
from rest_framework.decorators import action
from rest_framework.exceptions import APIException, PermissionDenied
from rest_framework.generics import GenericAPIView
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from .constants import (
    DIFFICULTY_CHOICES,
    PARTICIPANT_WRITABLE_MISSION_FIELDS,
    STATUS_CHOICES,
    Status,
)
from .filters import MissionFilter
from .models import Hostel, Mission, Participant
from .permissions import (
    NOT_A_PARTICIPANT,
    NOT_THE_CLAIMANT,
    IsMissionClaimant,
    IsOwnerOrStaff,
    IsParticipant,
    IsStaff,
    MissionAccessPolicy,
    STATUS_ONLY,
    get_participant,
)
from .serializers import (
    CodedErrorSerializer,
    ErrorSerializer,
    HostelDetailSerializer,
    HostelListSerializer,
    LeaderboardSerializer,
    MissionSerializer,
    MissionStatusSerializer,
    ParticipantMiniSerializer,
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
# Documented error responses
# ---------------------------------------------------------------------------
# Defined once and shared, so the schema and the code cannot disagree about which
# status codes an operation can return.


def _coded(description, detail, code):
    return OpenApiResponse(
        response=CodedErrorSerializer,
        description=description,
        examples=[
            OpenApiExample(code, value={"detail": detail, "code": code}, response_only=True)
        ],
    )


def _plain(description, detail):
    return OpenApiResponse(
        response=ErrorSerializer,
        description=description,
        examples=[
            OpenApiExample("detail", value={"detail": detail}, response_only=True)
        ],
    )


RESPONSE_EXPIRED = _coded(
    "The mission has expired: its deadline has passed, or staff marked it expired. "
    "Expiry is computed from the deadline, so this happens even when the stored "
    "status still reads 'unclaimed'.",
    "Mission has expired.",
    "mission_expired",
)
RESPONSE_UNAVAILABLE = _coded(
    "The mission is already in progress or cracked, so it cannot be claimed.",
    "Mission is not available to claim.",
    "mission_unavailable",
)
RESPONSE_STATUS_REFUSED = OpenApiResponse(
    response=CodedErrorSerializer,
    description=(
        "The change is refused. `mission_expired`: the deadline has passed, or staff "
        "marked the mission expired -- checked against the real deadline, not the "
        "stored status. `cracked_is_final`: the mission is already cracked. A `status` "
        "other than `in_progress` or `cracked` is a field-keyed validation error."
    ),
    examples=[
        OpenApiExample(
            error.code_value,
            value={"detail": error.default_detail, "code": error.code_value},
            response_only=True,
        )
        for error in (MissionExpired, CrackedIsFinal)
    ],
)
RESPONSE_NOT_CLAIMANT = _plain(
    "You are not this mission's claimant, or you sent a field other than 'status'.",
    NOT_THE_CLAIMANT,
)
RESPONSE_NOT_PARTICIPANT = _plain(
    "You are authenticated but have no participant profile.",
    NOT_A_PARTICIPANT,
)
RESPONSE_NOT_FOUND = _plain("No mission with that id.", "Not found.")
RESPONSE_THROTTLED = _plain(
    "Claim rate limit exceeded: 5 claim attempts per minute per user, successful or "
    "not. The response carries a Retry-After header.",
    "Request was throttled. Expected available in 42 seconds.",
)


# ---------------------------------------------------------------------------
# Services
# ---------------------------------------------------------------------------


def _reject_unclaimable(mission):
    """Turn a mission that failed the claimable test into the right error.

    Called both up-front and again after a losing conditional update, so a caller that
    lost a race gets the same answer it would have got had it arrived a moment later.

    Expired means *effectively* expired: past the deadline, or stored as ``expired`` by
    staff with the deadline untouched. Both are 400, never 409.
    """
    if mission.effective_status == Status.EXPIRED:
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

    # Everything below needs a live, in-progress mission. Expired covers both a passed
    # deadline and a mission staff stored as expired.
    if effective == Status.EXPIRED:
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
        if fresh.claimed_by_id != participant.id:
            # Released or reassigned mid-flight. A moment later the permission layer
            # would refuse this caller outright, so refuse the same way.
            raise PermissionDenied(NOT_THE_CLAIMANT)
        if fresh.status == Status.CRACKED:
            raise CrackedIsFinal()
        raise MissionExpired()

    return Mission.objects.with_related().get(pk=mission.pk)


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


@extend_schema(
    tags=["auth"],
    summary="Register a participant",
    description=(
        "Creates the Django user and the participant profile in one transaction and "
        "returns an auth token ready to use as `Authorization: Token <key>`. The "
        "password is run through Django's configured password validators."
    ),
    auth=[],
    request=RegisterSerializer,
    responses={
        201: OpenApiResponse(
            response=RegisterResponseSerializer,
            description="Participant created. The token is usable immediately.",
        ),
        400: OpenApiResponse(
            response=RegisterSerializer,
            description=(
                "Field-keyed validation errors: duplicate username or handle, a "
                "password rejected by Django's validators, or an unknown hostel id."
            ),
            examples=[
                OpenApiExample(
                    "duplicate handle",
                    value={"handle": ["A participant with that handle already exists."]},
                    response_only=True,
                )
            ],
        ),
    },
)
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


@extend_schema_view(
    list=extend_schema(
        summary="List hostels with live scores",
        description=(
            "Paginated. `score` and `cracked_count` are aggregated from cracked "
            "missions on every request -- no score is stored anywhere."
        ),
    ),
    retrieve=extend_schema(
        summary="Hostel detail with its cracked missions",
        responses={
            200: HostelDetailSerializer,
            404: RESPONSE_NOT_FOUND,
        },
    ),
)
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


@extend_schema(
    tags=["leaderboard"],
    summary="Live standings for every hostel",
    description=(
        "Public, and deliberately not paginated: all 17 hostels are returned, "
        "including those on zero. Ordered by score descending then name ascending. "
        "`rank` is computed with a SQL window function, so tied hostels share a rank "
        "and the next rank skips (1, 2, 2, 4)."
    ),
    auth=[],
    responses={200: LeaderboardSerializer(many=True)},
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


@extend_schema_view(
    list=extend_schema(summary="List participants"),
    retrieve=extend_schema(
        summary="Participant detail", responses={200: ParticipantSerializer, 404: RESPONSE_NOT_FOUND}
    ),
    update=extend_schema(
        summary="Replace a participant profile",
        description=(
            "Owner or staff. A non-staff caller may change `handle` only -- sending "
            "`hostel_id` is refused with 403 rather than silently ignored."
        ),
        responses={
            200: ParticipantSerializer,
            400: OpenApiResponse(
                response=ParticipantSerializer, description="Validation error, e.g. a duplicate handle."
            ),
            403: _plain("Not your profile, or you tried to change your hostel.", HANDLE_ONLY),
            404: RESPONSE_NOT_FOUND,
        },
    ),
    partial_update=extend_schema(
        summary="Update a participant profile",
        description=(
            "Owner or staff. A non-staff caller may change `handle` only -- sending "
            "`hostel_id` is refused with 403 rather than silently ignored."
        ),
        responses={
            200: ParticipantSerializer,
            400: OpenApiResponse(
                response=ParticipantSerializer, description="Validation error, e.g. a duplicate handle."
            ),
            403: _plain("Not your profile, or you tried to change your hostel.", HANDLE_ONLY),
            404: RESPONSE_NOT_FOUND,
        },
    ),
    destroy=extend_schema(
        summary="Delete a participant (staff only)",
        responses={
            204: OpenApiResponse(description="Deleted."),
            403: _plain("Staff access required.", "Staff access required."),
            404: RESPONSE_NOT_FOUND,
        },
    ),
)
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

    @extend_schema(
        summary="Your own participant profile",
        description=(
            "Requires a participant profile. An authenticated user without one -- a "
            "bare staff account, for instance -- gets 403, not 404."
        ),
        responses={
            200: ParticipantSerializer,
            403: RESPONSE_NOT_PARTICIPANT,
        },
    )
    @action(detail=False, methods=["get"])
    def me(self, request):
        return Response(self.get_serializer(get_participant(request.user)).data)


# ---------------------------------------------------------------------------
# Missions
# ---------------------------------------------------------------------------


MISSION_QUERY_PARAMETERS = [
    OpenApiParameter(
        name="status",
        description=(
            "Filter by **effective** status, not the stored column. `unclaimed` and "
            "`in_progress` exclude anything past its deadline; `expired` includes "
            "them alongside missions stored as expired; `cracked` is terminal and "
            "survives its deadline."
        ),
        enum=[value for value, _ in STATUS_CHOICES],
    ),
    OpenApiParameter(
        name="difficulty",
        description="Exact difficulty match, e.g. `?difficulty=boss_level`.",
        enum=[value for value, _ in DIFFICULTY_CHOICES],
    ),
    OpenApiParameter(
        name="hostel",
        type=int,
        description=(
            "Hostel id. A mission only gets a hostel when it is claimed, so unclaimed "
            "missions match no hostel."
        ),
    ),
    OpenApiParameter(
        name="search",
        description="Case-insensitive substring search over `codename` and `brief`.",
    ),
    OpenApiParameter(
        name="ordering",
        description="Sort field: `deadline`, `points` or `created_at`; prefix with `-` to reverse. Defaults to `deadline`.",
    ),
]


@extend_schema_view(
    list=extend_schema(
        summary="List missions",
        description=(
            "Paginated, filterable and searchable. `status` in every response is the "
            "effective status, with the deadline folded in."
        ),
        parameters=MISSION_QUERY_PARAMETERS,
    ),
    retrieve=extend_schema(
        summary="Mission detail",
        responses={200: MissionSerializer, 404: RESPONSE_NOT_FOUND},
    ),
    create=extend_schema(
        summary="Create a mission (staff only)",
        description=(
            "`claimed_by` and `hostel` are read-only and ignored if sent -- a mission "
            "acquires them only by being claimed. Only a claimant can put a mission in "
            "`in_progress` or `cracked`, so a new mission starts `unclaimed` (the "
            "default) or `expired`. Past deadlines are allowed; such a mission simply "
            "reads as expired."
        ),
        responses={
            201: MissionSerializer,
            400: OpenApiResponse(
                response=MissionSerializer,
                description="Validation error, e.g. status in_progress or cracked (only a claimant can set those).",
            ),
            403: _plain("Staff access required.", "Staff access required."),
        },
    ),
    update=extend_schema(
        summary="Replace a mission",
        description=(
            "Staff may edit codename, brief, points, difficulty and deadline. Staff may "
            "set `status` only to `unclaimed` (releases the claim) or `expired`; only "
            "the claimant can move a mission into `in_progress` or `cracked`, so staff "
            "get 400 for those. Re-sending the current status is not a change.\n\n"
            "A claimant may send `status` and nothing else; any other key returns 403."
        ),
        responses={
            200: MissionSerializer,
            400: OpenApiResponse(response=MissionSerializer, description="Validation error, e.g. staff setting status to in_progress or cracked, or an invalid transition."),
            403: RESPONSE_NOT_CLAIMANT,
            404: RESPONSE_NOT_FOUND,
        },
    ),
    partial_update=extend_schema(
        summary="Update a mission",
        description=(
            "Staff may edit codename, brief, points, difficulty and deadline. Staff may "
            "set `status` only to `unclaimed` (releases the claim) or `expired`; only "
            "the claimant can move a mission into `in_progress` or `cracked`, so staff "
            "get 400 for those. Re-sending the current status is not a change.\n\n"
            "A claimant may send `status` and nothing else; any other key returns 403."
        ),
        responses={
            200: MissionSerializer,
            400: OpenApiResponse(response=MissionSerializer, description="Validation error, e.g. staff setting status to in_progress or cracked, or an invalid transition."),
            403: RESPONSE_NOT_CLAIMANT,
            404: RESPONSE_NOT_FOUND,
        },
    ),
    destroy=extend_schema(
        summary="Delete a mission (staff only)",
        responses={
            204: OpenApiResponse(description="Deleted."),
            403: _plain("Staff access required.", "Staff access required."),
            404: RESPONSE_NOT_FOUND,
        },
    ),
)
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
        """Staff edit missions here. Anyone else can only be a claimant changing status,
        which is routed to the same service the dedicated status route uses, so the two
        agree by construction. A staff account always gets the admin edit on this
        route, so a staff member who also competes changes status through /status/.
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

    @extend_schema(
        summary="Claim a mission",
        description=(
            "Claims the mission for the calling participant: sets it to "
            "`in_progress`, records the claimant, and copies **the claimant's** hostel "
            "onto the mission.\n\n"
            "**The request body is ignored.** Any `hostel` or `claimed_by` a client "
            "sends has no effect.\n\n"
            "Throttled to 5 attempts per minute per user. Every attempt counts, "
            "including ones that go on to fail.\n\n"
            "The write is a single conditional UPDATE, so two simultaneous claims "
            "cannot both succeed -- the loser gets 409."
        ),
        request=None,
        responses={
            200: OpenApiResponse(response=MissionSerializer, description="Mission claimed."),
            400: RESPONSE_EXPIRED,
            403: RESPONSE_NOT_PARTICIPANT,
            404: RESPONSE_NOT_FOUND,
            409: RESPONSE_UNAVAILABLE,
            429: RESPONSE_THROTTLED,
        },
    )
    @action(detail=True, methods=["post"], throttle_classes=[ClaimRateThrottle])
    def claim(self, request, pk=None):
        mission = self.get_object()
        claimed = claim_mission(mission, get_participant(request.user))
        return Response(MissionSerializer(claimed).data)

    @extend_schema(
        summary="Update the status of a mission you claimed",
        description=(
            "Claimant only. Allowed transitions:\n\n"
            "| From (effective) | To | Result |\n"
            "|---|---|---|\n"
            "| `in_progress` | `in_progress` | 200, no-op |\n"
            "| `in_progress` | `cracked` | 200 |\n"
            "| `cracked` | `in_progress` or `cracked` | 400 `cracked_is_final` |\n"
            "| expired (deadline passed, or marked by staff) | `in_progress` or "
            "`cracked` | 400 `mission_expired` |\n\n"
            "Any other `status` value is a 400 validation error on the field, and any "
            "key other than `status` returns 403. For a claimant without staff rights, "
            "`PATCH /api/missions/{id}/` with a status-only body does exactly the same "
            "thing -- both routes call one service function. (A staff account's generic "
            "PATCH is an admin edit, so a staff member who also competes uses this "
            "route.)"
        ),
        request=MissionStatusSerializer,
        responses={
            200: OpenApiResponse(response=MissionSerializer, description="Status updated."),
            400: RESPONSE_STATUS_REFUSED,
            403: RESPONSE_NOT_CLAIMANT,
            404: RESPONSE_NOT_FOUND,
        },
    )
    @action(detail=True, methods=["patch"], url_path="status", url_name="status")
    def set_status(self, request, pk=None):
        return self._change_status(request, self.get_object())
