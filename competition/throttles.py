"""Scoped throttling.

Only the claim action is throttled. The rate lives in
``REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]["claim"]`` so it is configuration, not code.
"""

from rest_framework.throttling import UserRateThrottle


class ClaimRateThrottle(UserRateThrottle):
    """5 claim attempts per minute per user.

    Every attempt counts, successful or not -- the throttle runs before the mission is
    even looked up, so hammering claims on an already-taken mission still costs quota.
    """

    scope = "claim"
