from django.urls import path
from django.views.generic import RedirectView

from . import views

urlpatterns = [
    # Guild pages (public)
    path("", views.guild_list, name="guild_list"),
    path("<slug:slug>/", views.guild_detail, name="guild_detail"),
    # Buyable pages (public)
    path("<slug:slug>/buy/<slug:buyable_slug>/", views.buyable_detail, name="buyable_detail"),
    path("<slug:slug>/buy/<slug:buyable_slug>/checkout/", views.buyable_checkout, name="buyable_checkout"),
    path("<slug:slug>/buy/<slug:buyable_slug>/qr/", views.buyable_qr, name="buyable_qr"),
    # Redirects from old guild manage URLs
    path("<slug:slug>/manage/", RedirectView.as_view(url="/member-hub/manage/%(slug)s/", permanent=True)),
    path("<slug:slug>/manage/add/", RedirectView.as_view(url="/member-hub/manage/%(slug)s/add/", permanent=True)),
    path(
        "<slug:slug>/manage/<slug:buyable_slug>/edit/",
        RedirectView.as_view(url="/member-hub/manage/%(slug)s/%(buyable_slug)s/edit/", permanent=True),
    ),
    path("<slug:slug>/manage/orders/", RedirectView.as_view(url="/member-hub/manage/%(slug)s/orders/", permanent=True)),
    path(
        "<slug:slug>/manage/orders/<int:pk>/",
        RedirectView.as_view(url="/member-hub/manage/%(slug)s/orders/%(pk)s/", permanent=True),
    ),
]
