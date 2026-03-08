"""URL routes for guild voting."""

from django.urls import path

from . import vote_views

urlpatterns = [
    # Member-facing
    path("", vote_views.vote, name="vote"),
    path("results/<int:session_id>/", vote_views.voting_results, name="voting_results"),
    # Admin
    path("manage/", vote_views.voting_dashboard, name="voting_dashboard"),
    path("manage/create-session/", vote_views.voting_create_session, name="voting_create_session"),
    path("manage/calculate/<int:session_id>/", vote_views.voting_calculate, name="voting_calculate"),
    path("manage/email-results/<int:session_id>/", vote_views.voting_email_results, name="voting_email_results"),
    path("manage/set-status/<int:session_id>/", vote_views.voting_set_status, name="voting_set_status"),
]
