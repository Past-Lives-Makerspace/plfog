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
    path("calendar/", views.tour_calendar, name="calendar"),
    path("calendar/events/", views.tour_calendar_events, name="calendar_events"),
    path("bookings/<int:pk>/notes/", views.tour_booking_notes, name="booking_notes"),
    path("bookings/<int:pk>/notes/add/", views.tour_booking_note, name="booking_note"),
    path("guide/", views.tour_guide, name="guide"),
    path("give/", views.tour_give, name="give"),
    path("give/become/", views.tour_become_guide, name="become_guide"),
    path("bookings/<int:pk>/reschedule/", views.tour_booking_reschedule, name="booking_reschedule"),
    path("bookings/<int:pk>/cancel/", views.tour_booking_cancel, name="booking_cancel"),
    path("bookings/<int:pk>/mark/", views.tour_booking_mark, name="booking_mark"),
    path("bookings/<int:pk>/confirm/", views.tour_booking_confirm, name="booking_confirm"),
    path("bookings/<int:pk>/decline/", views.tour_booking_decline, name="booking_decline"),
    # The old My Hours tab and what it posted to: My Hours sets hours since #1300, so each redirects there.
    path("hours/", views.tour_hours, name="hours"),
    path("hours/save/", views.tour_old_hours_editor, name="hours_save"),
    path("hours/dates/save/", views.tour_old_hours_editor, name="dates_save"),
    path("hours/dates/<int:pk>/delete/", views.tour_old_hours_editor, name="dates_delete"),
    path("hours/notice/save/", views.tour_notice_save, name="notice_save"),
    path("days-off/save/", views.tour_old_hours_editor, name="day_off_save"),
    path("days-off/<int:pk>/delete/", views.tour_old_hours_editor, name="day_off_delete"),
    path("google/connect/", views.tour_google_connect, name="google_connect"),
    path("google/callback/", views.tour_google_callback, name="google_callback"),
    path("google/calendar/", views.tour_google_calendar, name="google_calendar"),
    path("google/disconnect/", views.tour_google_disconnect, name="google_disconnect"),
    path("settings/", views.tour_settings, name="settings"),
    path("settings/kiosk-key/", views.tour_kiosk_key, name="kiosk_key"),
]
