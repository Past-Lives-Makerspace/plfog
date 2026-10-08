"""Kiln ticket URLs, all under ``/kiln/`` (the guest gate admits a kiln student to this prefix only)."""

from __future__ import annotations

from django.urls import path

from kiln import views

app_name = "kiln"

urlpatterns = [
    path("", views.my_tickets, name="mine"),
    path("new/", views.ticket_new, name="new"),
    path("<int:pk>/", views.ticket_detail, name="detail"),
    path("<int:pk>/edit/", views.ticket_edit, name="edit"),
    path("lists/", views.lists, name="lists"),
    path("lists/<str:kind>/add/", views.list_add, name="list_add"),
    path("lists/<str:kind>/<int:pk>/rename/", views.list_rename, name="list_rename"),
    path("lists/<str:kind>/<int:pk>/archive/", views.list_archive, name="list_archive"),
    path("lists/<str:kind>/<int:pk>/restore/", views.list_restore, name="list_restore"),
]
