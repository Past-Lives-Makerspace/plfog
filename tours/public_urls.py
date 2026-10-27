"""The visitor's tour pages, served only on tours.pastlives.space (#948), under ``/tour/``.

The host's root is rewritten to :func:`tours.public_views.book` by ``SurfaceMiddleware``, so a
visitor never sees ``/tour/`` itself. Every view 404s on any other host.
"""

from __future__ import annotations

from django.urls import path

from tours import public_views

app_name = "tours_public"

urlpatterns = [
    path("", public_views.book, name="book"),
    path("b/<str:token>/", public_views.manage, name="manage"),
    path("b/<str:token>/reschedule/", public_views.reschedule, name="reschedule"),
    path("b/<str:token>/cancel/", public_views.cancel, name="cancel"),
]
