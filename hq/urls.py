"""HQ URLs, all under ``/hq/``, every one behind ``hq.access.hq_gate``.

``new/`` (Load Lee's HQ) sits before ``<slug>/`` so it is never read as a hub; ``hq.forms.RESERVED_SLUGS`` keeps
a hub from taking that address. The volunteer pages live under ``<slug>/volunteers/``, Vendor Tables under
``<slug>/vendors/`` and the PL Ledger under ``<slug>/ledger/``.
"""

from __future__ import annotations

from django.urls import path

from hq import views

app_name = "hq"

urlpatterns = [
    path("", views.index, name="index"),
    path("new/", views.setup, name="setup"),
    path("<slug:slug>/", views.hub_page, name="page"),
    path("<slug:slug>/edit/", views.edit, name="edit"),
    path("<slug:slug>/hero/delete/", views.hero_delete, name="hero_delete"),
    path("<slug:slug>/doors/update/", views.doors_update, name="doors_update"),
    path("<slug:slug>/cards/<int:pk>/edit/", views.card_edit, name="card_edit"),
    path("<slug:slug>/cards/<int:pk>/move/", views.card_move, name="card_move"),
    path("<slug:slug>/volunteers/", views.volunteers, name="volunteers"),
    path("<slug:slug>/volunteers/info/", views.volunteer_info, name="volunteer_info"),
    path("<slug:slug>/volunteers/<int:shift_pk>/signup/", views.shift_signup, name="shift_signup"),
    path("<slug:slug>/volunteers/<int:shift_pk>/remove/", views.shift_remove, name="shift_remove"),
    path("<slug:slug>/volunteers/roster/", views.volunteer_roster, name="volunteer_roster"),
    path("<slug:slug>/volunteers/roster.csv", views.volunteer_roster_csv, name="volunteer_roster_csv"),
    path("<slug:slug>/vendors/", views.vendors, name="vendors"),
    path("<slug:slug>/vendors.csv", views.vendors_csv, name="vendors_csv"),
    path("<slug:slug>/vendors/add/", views.vendor_add, name="vendor_add"),
    path("<slug:slug>/vendors/import/", views.vendor_import, name="vendor_import"),
    path("<slug:slug>/vendors/floor/", views.vendor_floor, name="vendor_floor"),
    path("<slug:slug>/vendors/discord/", views.vendor_discord_check, name="vendor_discord_check"),
    path("<slug:slug>/vendors/discord/add/", views.vendor_discord_add, name="vendor_discord_add"),
    path("<slug:slug>/vendors/discord/skip/", views.vendor_discord_skip, name="vendor_discord_skip"),
    path("<slug:slug>/vendors/discord/channel/", views.vendor_discord_channel, name="vendor_discord_channel"),
    path("<slug:slug>/vendors/<int:pk>/edit/", views.vendor_edit, name="vendor_edit"),
    path("<slug:slug>/vendors/<int:pk>/delete/", views.vendor_delete, name="vendor_delete"),
    path("<slug:slug>/ledger/", views.ledger, name="ledger"),
    path("<slug:slug>/ledger.csv", views.ledger_csv, name="ledger_csv"),
    path("<slug:slug>/ledger/receipts/", views.ledger_receipts, name="ledger_receipts"),
    path("<slug:slug>/ledger/approval/", views.ledger_approval, name="ledger_approval"),
    path("<slug:slug>/ledger/lines/add/", views.ledger_line_add, name="ledger_line_add"),
    path("<slug:slug>/ledger/lines/<int:pk>/edit/", views.ledger_line_edit, name="ledger_line_edit"),
    path("<slug:slug>/ledger/lines/<int:pk>/delete/", views.ledger_line_delete, name="ledger_line_delete"),
    path("<slug:slug>/ledger/entries/add/", views.ledger_entry_add, name="ledger_entry_add"),
    path("<slug:slug>/ledger/entries/<int:pk>/receipt/", views.ledger_receipt, name="ledger_receipt"),
    path("<slug:slug>/ledger/entries/<int:pk>/delete/", views.ledger_entry_delete, name="ledger_entry_delete"),
    path("<slug:slug>/ledger/bar.csv", views.ledger_bar_csv, name="ledger_bar_csv"),
    path("<slug:slug>/ledger/bar/quick/", views.ledger_bar_quick, name="ledger_bar_quick"),
    path("<slug:slug>/ledger/bar/undo/", views.ledger_bar_undo, name="ledger_bar_undo"),
    path("<slug:slug>/ledger/bar/batch/", views.ledger_bar_batch, name="ledger_bar_batch"),
    path("<slug:slug>/ledger/bar/prices/", views.ledger_bar_prices, name="ledger_bar_prices"),
    path("<slug:slug>/ledger/bar/<int:pk>/delete/", views.ledger_bar_delete, name="ledger_bar_delete"),
    path("<slug:slug>/ledger/door/", views.ledger_door, name="ledger_door"),
    path("<slug:slug>/ledger/people.csv", views.ledger_people_csv, name="ledger_people_csv"),
    path("<slug:slug>/ledger/launch/add/", views.ledger_launch_add, name="ledger_launch_add"),
    path("<slug:slug>/ledger/launch/<int:pk>/edit/", views.ledger_launch_edit, name="ledger_launch_edit"),
    path("<slug:slug>/ledger/launch/<int:pk>/delete/", views.ledger_launch_delete, name="ledger_launch_delete"),
    path("<slug:slug>/ledger/staff/add/", views.ledger_staff_add, name="ledger_staff_add"),
    path("<slug:slug>/ledger/staff/<int:pk>/delete/", views.ledger_staff_delete, name="ledger_staff_delete"),
    path("<slug:slug>/ledger/staff.csv", views.ledger_staff_csv, name="ledger_staff_csv"),
    path("<slug:slug>/ledger/guilds/add/", views.ledger_guild_add, name="ledger_guild_add"),
    path("<slug:slug>/ledger/guilds/add-all/", views.ledger_guilds_add_all, name="ledger_guilds_add_all"),
    path("<slug:slug>/ledger/guilds/<int:pk>/edit/", views.ledger_guild_edit, name="ledger_guild_edit"),
    path("<slug:slug>/ledger/guilds/<int:pk>/delete/", views.ledger_guild_delete, name="ledger_guild_delete"),
]
