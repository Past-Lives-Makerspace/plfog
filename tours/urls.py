"""Facility tour URLs on the members site, all under ``/facility-tours/``.

Not ``/tours/``: hub's guided tours already answer ``/tours/<key>/state/`` (``hub_tour_state``).
"""

from __future__ import annotations

from django.urls import path

from tours import views

app_name = "tours"

urlpatterns = [
    path("", views.tour_upcoming, name="upcoming"),
    path("past/", views.tour_past, name="past"),
    path("bookings/<int:pk>/reschedule/", views.tour_booking_reschedule, name="booking_reschedule"),
    path("bookings/<int:pk>/cancel/", views.tour_booking_cancel, name="booking_cancel"),
    path("bookings/<int:pk>/mark/", views.tour_booking_mark, name="booking_mark"),
    path("hours/", views.tour_hours, name="hours"),
    path("hours/save/", views.tour_hours_save, name="hours_save"),
    path("days-off/save/", views.tour_day_off_save, name="day_off_save"),
    path("days-off/<int:pk>/delete/", views.tour_day_off_delete, name="day_off_delete"),
    path("google/connect/", views.tour_google_connect, name="google_connect"),
    path("google/callback/", views.tour_google_callback, name="google_callback"),
    path("google/calendar/", views.tour_google_calendar, name="google_calendar"),
    path("google/disconnect/", views.tour_google_disconnect, name="google_disconnect"),
    path("settings/", views.tour_settings, name="settings"),
    path("settings/kiosk-key/", views.tour_kiosk_key, name="kiosk_key"),
]
