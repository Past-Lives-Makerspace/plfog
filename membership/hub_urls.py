from django.urls import path

from . import hub_views

urlpatterns = [
    path("", hub_views.dashboard, name="hub_dashboard"),
    path("profile/", hub_views.profile_edit, name="profile_edit"),
    path("orders/", hub_views.user_orders, name="user_orders"),
    path("guilds/", hub_views.my_guilds, name="hub_guilds"),
    path("spaces/", hub_views.my_spaces, name="hub_spaces"),
    # Guild lead management
    path("manage/<slug:slug>/", hub_views.guild_manage, name="guild_manage"),
    path("manage/<slug:slug>/add/", hub_views.buyable_add, name="buyable_add"),
    path("manage/<slug:slug>/<slug:buyable_slug>/edit/", hub_views.buyable_edit, name="buyable_edit"),
    path("manage/<slug:slug>/orders/", hub_views.guild_orders, name="guild_orders"),
    path("manage/<slug:slug>/orders/<int:pk>/", hub_views.order_detail, name="order_detail"),
]
