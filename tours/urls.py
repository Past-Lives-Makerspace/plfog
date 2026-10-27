"""Facility tour URLs on the members site, all under ``/facility-tours/``.

Not ``/tours/``: hub's guided tours already answer ``/tours/<key>/state/`` (``hub_tour_state``).
"""

from __future__ import annotations

from django.urls import path

from tours import views

app_name = "tours"

urlpatterns = [
    path("", views.tour_hours, name="hours"),
    path("hours/save/", views.tour_hours_save, name="hours_save"),
    path("days-off/save/", views.tour_day_off_save, name="day_off_save"),
    path("days-off/<int:pk>/delete/", views.tour_day_off_delete, name="day_off_delete"),
    path("settings/", views.tour_settings, name="settings"),
]
