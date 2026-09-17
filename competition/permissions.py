"""Permission classes.

Every rule the competition depends on is enforced here or in the views -- never by
assuming a client sends a well-formed payload. The mission policy in particular
inspects the *payload keys*, because a participant who may edit `status` must not be
able to smuggle `points` into the same PATCH.
"""

from rest_framework import permissions
from rest_framework.exceptions import PermissionDenied

from .constants import PARTICIPANT_WRITABLE_MISSION_FIELDS

#: Reused so the wording is identical wherever a rule is enforced.
NOT_A_PARTICIPANT = "You must have a participant profile to do this."
NOT_THE_CLAIMANT = "You can only update missions you claimed."
STATUS_ONLY = "Participants may only update mission status."


def get_participant(user):
    """The caller's participant profile, or ``None``.

    Staff do not need one -- they administer the competition rather than compete.
    """
    if not user or not user.is_authenticated:
        return None
    return getattr(user, "participant", None)


class IsStaff(permissions.BasePermission):
    message = "Staff access required."

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.is_staff)


class IsParticipant(permissions.BasePermission):
    """Authenticated *and* holding a participant profile.

    An authenticated user without one (a bare staff account, say) gets 403, not 401.
    """

    message = NOT_A_PARTICIPANT

    def has_permission(self, request, view):
        return get_participant(request.user) is not None


class IsMissionClaimant(permissions.BasePermission):
    """Object-level: the caller claimed this mission."""

    message = NOT_THE_CLAIMANT

    def has_permission(self, request, view):
        return get_participant(request.user) is not None

    def has_object_permission(self, request, view, obj):
        participant = get_participant(request.user)
        return participant is not None and obj.claimed_by_id == participant.id


class IsOwnerOrStaff(permissions.BasePermission):
    """Object-level: the caller is this participant, or is staff."""

    message = "You can only modify your own profile."

    def has_object_permission(self, request, view, obj):
        if request.user.is_staff:
            return True
        participant = get_participant(request.user)
        return participant is not None and participant.id == obj.id


class MissionAccessPolicy(permissions.BasePermission):
    """The whole of mission CRUD access, in one place.

    =================  ==========================================================
    Reads              any authenticated user
    Create / delete    staff only
    Update             staff freely; the claimant may send ``status`` and nothing
                       else; anyone else is refused
    =================  ==========================================================

    Refusals raise :class:`PermissionDenied` with a specific ``detail`` rather than
    returning ``False``, so a client can tell "not yours" apart from "not that field".
    """

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated):
            return False

        if request.method in permissions.SAFE_METHODS:
            return True

        if user.is_staff:
            return True

        # Non-staff: creating and deleting missions is never allowed.
        if request.method in ("POST", "DELETE"):
            raise PermissionDenied(IsStaff.message)

        # PUT/PATCH: needs a participant profile; ownership is checked per-object.
        if get_participant(user) is None:
            raise PermissionDenied(NOT_A_PARTICIPANT)
        return True

    def has_object_permission(self, request, view, obj):
        user = request.user

        if request.method in permissions.SAFE_METHODS or user.is_staff:
            return True

        if request.method == "DELETE":
            raise PermissionDenied(IsStaff.message)

        # Ownership first, then the field whitelist -- PRD 6.3 checks in this order,
        # so a non-claimant learns they are not the claimant regardless of payload.
        participant = get_participant(user)
        if participant is None or obj.claimed_by_id != participant.id:
            raise PermissionDenied(NOT_THE_CLAIMANT)

        submitted = set(request.data.keys())
        if not submitted.issubset(PARTICIPANT_WRITABLE_MISSION_FIELDS):
            raise PermissionDenied(STATUS_ONLY)

        return True
