"""HQ URLs, all under ``/hq/`` (the guest carve-out in the lockout middleware admits this prefix).

``new/`` sits before ``<slug>/`` so it is never read as a hub; ``hq.forms.RESERVED_SLUGS`` keeps
a hub from taking that address. C2 adds ``<slug>/volunteers/`` and C3 ``<slug>/wine/``.
"""

from __future__ import annotations

from django.urls import path

from hq import views

app_name = "hq"

urlpatterns = [
    path("", views.index, name="index"),
    path("new/", views.create, name="create"),
    path("<slug:slug>/", views.hub_page, name="page"),
    path("<slug:slug>/edit/", views.edit, name="edit"),
    path("<slug:slug>/hero/delete/", views.hero_delete, name="hero_delete"),
    path("<slug:slug>/cards/<int:pk>/edit/", views.card_edit, name="card_edit"),
    path("<slug:slug>/cards/<int:pk>/move/", views.card_move, name="card_move"),
]
