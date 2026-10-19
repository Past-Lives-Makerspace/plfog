"""HQ URLs, all under ``/hq/`` (the guest carve-out in the lockout middleware admits this prefix).

``new/`` sits before ``<slug>/`` so it is never read as a hub; ``hq.forms.RESERVED_SLUGS`` keeps
a hub from taking that address. The volunteer pages live under ``<slug>/volunteers/`` and the
wine wall under ``<slug>/wine/``.
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
    path("<slug:slug>/volunteers/", views.volunteers, name="volunteers"),
    path("<slug:slug>/volunteers/info/", views.volunteer_info, name="volunteer_info"),
    path("<slug:slug>/volunteers/<int:shift_pk>/signup/", views.shift_signup, name="shift_signup"),
    path("<slug:slug>/volunteers/<int:shift_pk>/remove/", views.shift_remove, name="shift_remove"),
    path("<slug:slug>/volunteers/roster/", views.volunteer_roster, name="volunteer_roster"),
    path("<slug:slug>/volunteers/roster.csv", views.volunteer_roster_csv, name="volunteer_roster_csv"),
    path("<slug:slug>/wine/", views.wine, name="wine"),
    path("<slug:slug>/wine/<int:pk>/delete/", views.bottle_delete, name="bottle_delete"),
    path("<slug:slug>/wine/wineries/logo/", views.winery_logo, name="winery_logo"),
    path("<slug:slug>/wine/donors/", views.wine_donors, name="wine_donors"),
    path("<slug:slug>/wine/donors.csv", views.wine_donors_csv, name="wine_donors_csv"),
]
