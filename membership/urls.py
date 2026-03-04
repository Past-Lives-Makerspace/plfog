from django.urls import path

from . import views

urlpatterns = [
    # Guild pages (public)
    path("", views.guild_list, name="guild_list"),
    path("<slug:slug>/", views.guild_detail, name="guild_detail"),
    # Buyable pages (public)
    path("<slug:slug>/buy/<slug:buyable_slug>/", views.buyable_detail, name="buyable_detail"),
    path("<slug:slug>/buy/<slug:buyable_slug>/checkout/", views.buyable_checkout, name="buyable_checkout"),
    path("<slug:slug>/buy/<slug:buyable_slug>/qr/", views.buyable_qr, name="buyable_qr"),
    # Guild lead management
    path("<slug:slug>/manage/", views.guild_manage, name="guild_manage"),
    path("<slug:slug>/manage/add/", views.buyable_add, name="buyable_add"),
    path("<slug:slug>/manage/<slug:buyable_slug>/edit/", views.buyable_edit, name="buyable_edit"),
    path("<slug:slug>/manage/orders/", views.guild_orders, name="guild_orders"),
    path("<slug:slug>/manage/orders/<int:pk>/", views.order_detail, name="order_detail"),
]
