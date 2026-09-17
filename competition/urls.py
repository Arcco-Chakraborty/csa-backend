"""Competition routes, all mounted under ``/api/``."""

from django.urls import include, path
from rest_framework.authtoken.views import obtain_auth_token
from rest_framework.routers import DefaultRouter

from .views import (
    HostelViewSet,
    LeaderboardView,
    MissionViewSet,
    ParticipantViewSet,
    RegisterView,
)

router = DefaultRouter()
router.register("hostels", HostelViewSet, basename="hostel")
router.register("participants", ParticipantViewSet, basename="participant")
router.register("missions", MissionViewSet, basename="mission")

urlpatterns = [
    path("auth/register/", RegisterView.as_view(), name="register"),
    path("auth/token/", obtain_auth_token, name="obtain-token"),
    path("leaderboard/", LeaderboardView.as_view(), name="leaderboard"),
    path("", include(router.urls)),
]
