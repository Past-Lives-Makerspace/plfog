from django.urls import path

from . import views

urlpatterns = [
    path("guilds/voting/", views.guild_voting, name="hub_guild_voting"),
    path("guilds/<int:pk>/", views.guild_detail, name="hub_guild_detail"),
    path("settings/profile/", views.profile_settings, name="hub_profile_settings"),
    path("settings/emails/", views.email_preferences, name="hub_email_preferences"),
]
