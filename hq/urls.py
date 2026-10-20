"""HQ URLs, all under ``/hq/`` (the guest carve-out in the lockout middleware admits this prefix).

``new/`` (Load Lee's HQ) sits before ``<slug>/`` so it is never read as a hub; ``hq.forms.RESERVED_SLUGS`` keeps
a hub from taking that address. The volunteer pages live under ``<slug>/volunteers/`` and the
wine wall under ``<slug>/wine/``, the Marketing Tracker under ``<slug>/marketing/``, the Sponsorship Tracker
under ``<slug>/sponsors/``, Vendor Tables under ``<slug>/vendors/``, the Market Check-in under
``<slug>/check-in/``, the event flyer and sharing page under ``<slug>/flyer/`` and the PL Ledger under
``<slug>/ledger/``.
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
    path("<slug:slug>/wine/", views.wine, name="wine"),
    path("<slug:slug>/wine/<int:pk>/delete/", views.bottle_delete, name="bottle_delete"),
    path("<slug:slug>/wine/wineries/logo/", views.winery_logo, name="winery_logo"),
    path("<slug:slug>/wine/donors/", views.wine_donors, name="wine_donors"),
    path("<slug:slug>/wine/donors.csv", views.wine_donors_csv, name="wine_donors_csv"),
    path("<slug:slug>/wine/donors/thanked/", views.wine_donor_thanked, name="wine_donor_thanked"),
    path("<slug:slug>/marketing/", views.marketing, name="marketing"),
    path("<slug:slug>/marketing/add/", views.marketing_add, name="marketing_add"),
    path("<slug:slug>/marketing/<int:pk>/delete/", views.marketing_delete, name="marketing_delete"),
    path("<slug:slug>/sponsors/", views.sponsors, name="sponsors"),
    path("<slug:slug>/sponsors.csv", views.sponsors_csv, name="sponsors_csv"),
    path("<slug:slug>/sponsors/add/", views.sponsor_add, name="sponsor_add"),
    path("<slug:slug>/sponsors/<int:pk>/edit/", views.sponsor_edit, name="sponsor_edit"),
    path("<slug:slug>/sponsors/<int:pk>/log/", views.sponsor_log, name="sponsor_log"),
    path("<slug:slug>/sponsors/<int:pk>/delete/", views.sponsor_delete, name="sponsor_delete"),
    path("<slug:slug>/vendors/", views.vendors, name="vendors"),
    path("<slug:slug>/vendors.csv", views.vendors_csv, name="vendors_csv"),
    path("<slug:slug>/vendors/add/", views.vendor_add, name="vendor_add"),
    path("<slug:slug>/vendors/floor/", views.vendor_floor, name="vendor_floor"),
    path("<slug:slug>/vendors/<int:pk>/edit/", views.vendor_edit, name="vendor_edit"),
    path("<slug:slug>/vendors/<int:pk>/delete/", views.vendor_delete, name="vendor_delete"),
    path("<slug:slug>/check-in/", views.checkin, name="checkin"),
    path("<slug:slug>/check-in/report/", views.checkin_report, name="checkin_report"),
    path("<slug:slug>/check-in/hours/log/", views.checkin_hours_log, name="checkin_hours_log"),
    path("<slug:slug>/check-in/hours/<int:pk>/delete/", views.checkin_hours_delete, name="checkin_hours_delete"),
    path("<slug:slug>/check-in/reports/", views.checkin_reports, name="checkin_reports"),
    path("<slug:slug>/check-in/reports.csv", views.checkin_reports_csv, name="checkin_reports_csv"),
    path("<slug:slug>/check-in/hours/", views.checkin_hours, name="checkin_hours"),
    path("<slug:slug>/check-in/hours.csv", views.checkin_hours_csv, name="checkin_hours_csv"),
    path("<slug:slug>/flyer/", views.flyer, name="flyer"),
    path("<slug:slug>/flyer/links/", views.flyer_links, name="flyer_links"),
    path("<slug:slug>/flyer/<slug:slot>/download/", views.flyer_download, name="flyer_download"),
    path("<slug:slug>/flyer/<slug:slot>/upload/", views.flyer_upload, name="flyer_upload"),
    path("<slug:slug>/flyer/<slug:slot>/remove/", views.flyer_remove, name="flyer_remove"),
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
    path("<slug:slug>/ledger/tickets/", views.ledger_tickets, name="ledger_tickets"),
    path("<slug:slug>/ledger/tickets/prices/", views.ledger_ticket_prices, name="ledger_ticket_prices"),
]
