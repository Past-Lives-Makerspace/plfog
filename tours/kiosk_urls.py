"""The front desk kiosk (#950), at ``/kiosk/`` on tours.pastlives.space only.

Every view 404s on any other host and sends a device without the kiosk cookie to the public
booking page (:mod:`tours.kiosk_views`).
"""

from __future__ import annotations

from django.urls import path

from tours import kiosk_views

app_name = "tours_kiosk"

urlpatterns = [
    path("", kiosk_views.home, name="home"),
    path("check-in/", kiosk_views.check_in, name="check_in"),
    path("check-in/done/", kiosk_views.check_in_result, name="check_in_result"),
    path("book/", kiosk_views.book, name="book"),
    path("book/done/", kiosk_views.booked, name="booked"),
]
