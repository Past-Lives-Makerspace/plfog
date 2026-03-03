from django.urls import path

from . import views

urlpatterns = [
    path("api/favorites/toggle/", views.favorites_toggle, name="favorites_toggle"),
    path("api/calendar-events/", views.calendar_events, name="calendar_events"),
    path("health/", views.health_check, name="health_check"),
    path("dashboard/", views.dashboard, name="dashboard"),
    path("", views.home, name="home"),
]
