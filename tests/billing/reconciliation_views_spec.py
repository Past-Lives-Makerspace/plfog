"""BDD specs for the reconciliation views, forms, snapshot, and month-end command."""

from __future__ import annotations

from decimal import Decimal
from io import StringIO
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError
from django.test import Client

from billing.forms import ReconciliationSettingsForm, TransactionAdjustmentForm
from billing.models import BillingSettings, ReconciliationSnapshot, TransactionAdjustment
from core.models import SiteActivity, TransactionalEmailLog
from membership.models import AdminCapability

pytestmark = pytest.mark.django_db

_D = Decimal


def _login_member(client: Client, username: str) -> User:
    user = User.objects.create_user(username=username, password="pass", email=f"{username}@example.com")
    client.login(username=username, password="pass")
    return user


def _login_admin(client: Client, username: str = "recadmin") -> User:
    User.objects.create_superuser(username=username, password="pass", email=f"{username}@example.com")
    client.login(username=username, password="pass")
    return User.objects.get(username=username)


def _login_approver(client: Client, username: str = "recappr") -> None:
    user = _login_member(client, username)
    user.member.admin_capabilities.create(capability=AdminCapability.Capability.BILLING_APPROVER)


_ADMIN_URLS = [
    "/billing/admin/dashboard/?tab=reconciliation",
    "/billing/admin/reconciliation/table/",
    "/billing/admin/reconciliation/export/csv/",
    "/billing/admin/reconciliation/print/",
]

# The money mutating endpoints. fog_admin_required is the OUTER decorator, so a GET on
# these POST only views returns 403 for a non admin BEFORE require_POST answers 405 — which
# lets one sweep pin the gate on every one. A future edit dropping @fog_admin_required from
# any of these would flip a 403 to a 405 and this test would catch it.
_MUTATING_URLS = [
    "/billing/admin/reconciliation/settings/save/",
    "/billing/admin/reconciliation/adjust/class/1/form/",
    "/billing/admin/reconciliation/adjust/class/1/",
    "/billing/admin/reconciliation/clear/class/1/",
    "/billing/admin/reconciliation/snapshots/take/",
    "/billing/admin/reconciliation/snapshots/1/delete/",
]


def describe_reconciliation_gating():
    def it_serves_the_tab_and_endpoints_to_a_fog_admin(client: Client):
        _login_admin(client)
        for url in _ADMIN_URLS:
            assert client.get(url).status_code == 200, url

    def it_403s_every_endpoint_for_a_billing_approver(client: Client):
        _login_approver(client)
        for url in _ADMIN_URLS:
            assert client.get(url).status_code == 403, url

    def it_403s_the_mutating_endpoints_for_a_billing_approver(client: Client):
        _login_approver(client)
        for url in _MUTATING_URLS:
            assert client.get(url).status_code == 403, url

    def it_403s_the_mutating_endpoints_for_a_plain_member(client: Client):
        _login_member(client, "recplain")
        for url in _MUTATING_URLS:
            assert client.get(url).status_code == 403, url

    def it_403s_the_snapshot_detail_reader_for_a_non_admin(client: Client):
        from datetime import date

        snapshot = ReconciliationSnapshot.take(period_start=date(2026, 7, 1), period_end=date(2026, 7, 31))
        url = f"/billing/admin/reconciliation/snapshots/{snapshot.pk}/"
        _login_approver(client)
        assert client.get(url).status_code == 403
        _login_member(client, "snapplain")
        assert client.get(url).status_code == 403

    def it_403s_every_endpoint_for_a_plain_member(client: Client):
        _login_member(client, "plain")
        for url in _ADMIN_URLS:
            assert client.get(url).status_code == 403, url

    def it_shows_the_nav_link_only_to_admins(client: Client):
        _login_admin(client, "navadmin")
        assert "?tab=reconciliation" in client.get("/billing/admin/dashboard/").content.decode()

    def it_hides_the_nav_link_from_an_approver(client: Client):
        _login_approver(client, "navappr")
        assert "?tab=reconciliation" not in client.get("/billing/admin/dashboard/").content.decode()

    def it_403s_the_reconciliation_tab_probe_for_an_approver(client: Client):
        _login_approver(client, "probeappr")
        assert client.get("/billing/admin/dashboard/?tab=reconciliation").status_code == 403


def describe_reconciliation_settings_form():
    def it_rejects_an_orientation_triad_over_100_with_the_exact_message():
        form = ReconciliationSettingsForm(
            data={
                "orientation_orientator_percent": "70",
                "orientation_guild_percent": "15",
                "orientation_pl_percent": "20",
                "class_instructor_percent": "70",
                "class_guild_percent": "10",
                "class_pl_percent": "20",
            }
        )
        assert not form.is_valid()
        assert "Orientation percentages must add up to 100 (currently 105)." in str(form.errors)

    def it_rejects_a_class_triad_under_100():
        form = ReconciliationSettingsForm(
            data={
                "orientation_orientator_percent": "70",
                "orientation_guild_percent": "15",
                "orientation_pl_percent": "15",
                "class_instructor_percent": "70",
                "class_guild_percent": "10",
                "class_pl_percent": "15",
            }
        )
        assert not form.is_valid()
        assert "Class percentages must add up to 100 (currently 95)." in str(form.errors)

    def it_rejects_a_percent_above_100():
        form = ReconciliationSettingsForm(
            data={
                "orientation_orientator_percent": "150",
                "orientation_guild_percent": "0",
                "orientation_pl_percent": "0",
                "class_instructor_percent": "70",
                "class_guild_percent": "10",
                "class_pl_percent": "20",
            }
        )
        assert not form.is_valid()

    def it_returns_early_when_a_percent_is_left_blank():
        form = ReconciliationSettingsForm(
            data={
                "orientation_orientator_percent": "",
                "orientation_guild_percent": "15",
                "orientation_pl_percent": "15",
                "class_instructor_percent": "70",
                "class_guild_percent": "10",
                "class_pl_percent": "20",
            }
        )
        assert not form.is_valid()
        assert "orientation_orientator_percent" in form.errors

    def it_saves_a_valid_set_to_billing_settings(client: Client):
        _login_admin(client)
        response = client.post(
            "/billing/admin/reconciliation/settings/save/",
            data={
                "orientation_orientator_percent": "60",
                "orientation_guild_percent": "20",
                "orientation_pl_percent": "20",
                "class_instructor_percent": "50",
                "class_guild_percent": "30",
                "class_pl_percent": "20",
            },
        )
        assert response.status_code == 302
        settings_obj = BillingSettings.load()
        assert settings_obj.class_instructor_percent == _D("50.00")
        assert settings_obj.orientation_orientator_percent == _D("60.00")


def describe_transaction_adjustment_form():
    def it_lets_an_omitted_class_form_skip_the_percent_triad():
        form = TransactionAdjustmentForm(data={"is_omitted": "on"}, source_kind="class", source_pk=1)
        assert form.is_valid()

    def it_rejects_a_class_override_that_does_not_sum_to_100():
        form = TransactionAdjustmentForm(
            data={"percent_instructor": "60", "percent_guild": "20", "percent_pl": "30"},
            source_kind="class",
            source_pk=1,
        )
        assert not form.is_valid()
        assert "The three percentages must add up to 100." in str(form.errors)

    def it_accepts_a_class_override_summing_to_100():
        form = TransactionAdjustmentForm(
            data={"percent_instructor": "60", "percent_guild": "20", "percent_pl": "20"},
            source_kind="class",
            source_pk=1,
        )
        assert form.is_valid()

    def it_gives_a_tab_form_no_percent_fields():
        form = TransactionAdjustmentForm(data={"is_omitted": "on"}, source_kind="tab", source_pk=1)
        assert form.percent_fields == []
        assert form.is_valid()

    def it_requires_all_three_percentages_when_not_omitted():
        form = TransactionAdjustmentForm(data={"percent_instructor": "70"}, source_kind="class", source_pk=1)
        assert not form.is_valid()
        assert "Enter all three percentages." in str(form.errors)

    def it_rejects_an_unknown_source_kind():
        with pytest.raises(ValueError, match="Unknown source_kind"):
            TransactionAdjustmentForm(source_kind="bogus", source_pk=1)

    def it_saves_an_omitted_class_adjustment_with_no_override():
        form = TransactionAdjustmentForm(data={"is_omitted": "on"}, source_kind="class", source_pk=3)
        assert form.is_valid()
        adjustment = form.save(actor=None)
        assert adjustment.is_omitted is True
        assert adjustment.override_percents is None

    def it_saves_an_override_as_a_transaction_adjustment():
        form = TransactionAdjustmentForm(
            data={"percent_instructor": "60", "percent_guild": "20", "percent_pl": "20", "reason": "instructor deal"},
            source_kind="class",
            source_pk=7,
        )
        assert form.is_valid()
        adjustment = form.save(actor=None)
        assert adjustment.source_pk == 7
        assert adjustment.override_percents == {"instructor": "60", "guild": "20", "pl": "20"}
        assert adjustment.reason == "instructor deal"


def describe_transaction_adjustment_model():
    def it_maps_adjustments_by_source_key():
        TransactionAdjustment.objects.create(source_kind="class", source_pk=1, is_omitted=True)
        TransactionAdjustment.objects.create(source_kind="tab", source_pk=1, is_omitted=True)
        mapping = TransactionAdjustment.objects.as_map()
        assert set(mapping.keys()) == {("class", 1), ("tab", 1)}

    def it_is_unique_per_source_kind_and_pk():
        TransactionAdjustment.objects.create(source_kind="class", source_pk=1, is_omitted=True)
        with pytest.raises(IntegrityError):
            TransactionAdjustment.objects.create(source_kind="class", source_pk=1, is_omitted=False)


def describe_adjust_endpoints():
    def it_saves_an_adjustment_and_triggers_a_refresh(client: Client):
        _login_admin(client)
        response = client.post(
            "/billing/admin/reconciliation/adjust/class/5/",
            data={"percent_instructor": "60", "percent_guild": "20", "percent_pl": "20"},
        )
        assert response.status_code == 204
        assert response["HX-Trigger"]
        assert TransactionAdjustment.objects.filter(source_kind="class", source_pk=5).exists()

    def it_re_renders_the_form_on_a_bad_override(client: Client):
        _login_admin(client, "adj2")
        response = client.post(
            "/billing/admin/reconciliation/adjust/class/5/",
            data={"percent_instructor": "60", "percent_guild": "20", "percent_pl": "30"},
        )
        assert response.status_code == 200
        assert b"add up to 100" in response.content

    def it_clears_an_adjustment(client: Client):
        _login_admin(client, "adj3")
        TransactionAdjustment.objects.create(source_kind="class", source_pk=9, is_omitted=True)
        response = client.post("/billing/admin/reconciliation/clear/class/9/")
        assert response.status_code == 204
        assert not TransactionAdjustment.objects.filter(source_kind="class", source_pk=9).exists()

    def it_serves_the_adjust_form_for_a_class_transaction(client: Client):
        _login_admin(client, "adjform")
        response = client.get("/billing/admin/reconciliation/adjust/class/5/form/")
        assert response.status_code == 200
        assert b"Save adjustment" in response.content

    def it_prefills_the_adjust_form_from_an_existing_override(client: Client):
        _login_admin(client, "adjform2")
        TransactionAdjustment.objects.create(
            source_kind="class", source_pk=5, override_percents={"instructor": "60", "guild": "20", "pl": "20"}
        )
        response = client.get("/billing/admin/reconciliation/adjust/class/5/form/")
        assert response.status_code == 200
        assert b"60" in response.content

    def it_serves_the_omit_only_adjust_form_for_a_tab_charge(client: Client):
        _login_admin(client, "adjtab")
        response = client.get("/billing/admin/reconciliation/adjust/tab/5/form/")
        assert response.status_code == 200
        assert b"only exclude the whole charge" in response.content

    def it_404s_an_unknown_source_kind_on_the_form(client: Client):
        _login_admin(client, "adj4")
        assert client.get("/billing/admin/reconciliation/adjust/bogus/1/form/").status_code == 404

    def it_404s_an_unknown_source_kind_on_the_post(client: Client):
        _login_admin(client, "adj5")
        assert client.post("/billing/admin/reconciliation/adjust/bogus/1/").status_code == 404

    def it_reports_an_invalid_settings_save(client: Client):
        _login_admin(client, "setbad")
        response = client.post(
            "/billing/admin/reconciliation/settings/save/",
            data={
                "orientation_orientator_percent": "70",
                "orientation_guild_percent": "15",
                "orientation_pl_percent": "20",
                "class_instructor_percent": "70",
                "class_guild_percent": "10",
                "class_pl_percent": "20",
            },
        )
        assert response.status_code == 302  # redirects back with a Django error message


def describe_reconciliation_snapshot():
    def it_creates_a_row_and_logs_activity_without_email_or_events():
        from datetime import date

        with patch("core.events.emit.emit") as emit_mock:
            snapshot = ReconciliationSnapshot.take(period_start=date(2026, 7, 1), period_end=date(2026, 7, 31))
        assert ReconciliationSnapshot.objects.count() == 1
        assert snapshot.grand_total_cents == 0
        assert SiteActivity.objects.filter(kind=SiteActivity.Kind.RECONCILIATION_SNAPSHOT_TAKEN).count() == 1
        assert TransactionalEmailLog.objects.count() == 0
        emit_mock.assert_not_called()

    def it_takes_and_deletes_via_the_views(client: Client):
        _login_admin(client)
        take = client.post("/billing/admin/reconciliation/snapshots/take/?start=2026-07-01&end=2026-07-31")
        assert take.status_code == 302
        snapshot = ReconciliationSnapshot.objects.get()
        history = client.get("/billing/admin/dashboard/?tab=reconciliation")
        assert history.status_code == 200
        assert snapshot.delete_url.encode() in history.content
        detail = client.get(f"/billing/admin/reconciliation/snapshots/{snapshot.pk}/")
        assert detail.status_code == 200
        delete = client.post(f"/billing/admin/reconciliation/snapshots/{snapshot.pk}/delete/")
        assert delete.status_code == 302
        assert ReconciliationSnapshot.objects.count() == 0
        assert SiteActivity.objects.filter(kind=SiteActivity.Kind.RECONCILIATION_SNAPSHOT_DELETED).count() == 1


def describe_take_reconciliation_snapshot_command():
    def it_freezes_a_named_month_once():
        call_command("take_reconciliation_snapshot", "--month", "2026-07", stdout=StringIO())
        call_command("take_reconciliation_snapshot", "--month", "2026-07", stdout=StringIO())
        snapshots = ReconciliationSnapshot.objects.filter(period_start__year=2026, period_start__month=7)
        assert snapshots.count() == 1
        assert snapshots.first().is_auto is False

    def it_defaults_to_the_just_ended_month_as_an_auto_snapshot():
        from datetime import timedelta

        from django.utils import timezone

        call_command("take_reconciliation_snapshot", stdout=StringIO())
        prev_month_end = timezone.localdate().replace(day=1) - timedelta(days=1)
        snap = ReconciliationSnapshot.objects.get()
        assert snap.period_end == prev_month_end
        assert snap.is_auto is True

    def it_freezes_december_to_the_last_day_of_the_year():
        from datetime import date

        call_command("take_reconciliation_snapshot", "--month", "2026-12", stdout=StringIO())
        snap = ReconciliationSnapshot.objects.get()
        assert snap.period_start == date(2026, 12, 1)
        assert snap.period_end == date(2026, 12, 31)

    def it_rejects_a_malformed_month():
        with pytest.raises(CommandError):
            call_command("take_reconciliation_snapshot", "--month", "nope", stdout=StringIO())


def describe_model_reprs():
    def it_renders_the_transaction_adjustment_str():
        adjustment = TransactionAdjustment.objects.create(source_kind="class", source_pk=3, is_omitted=True)
        assert str(adjustment) == "Class #3 (omitted)"

    def it_renders_the_snapshot_str_and_delete_url():
        from datetime import date

        snap = ReconciliationSnapshot.objects.create(
            period_start=date(2026, 7, 1), period_end=date(2026, 7, 31), grand_total_cents=12345
        )
        assert "Reconciliation Jul 2026" in str(snap)
        assert str(snap.pk) in snap.delete_url


def describe_reconciliation_templates():
    def it_renders_the_empty_state(client: Client):
        _login_admin(client)
        body = client.get("/billing/admin/dashboard/?tab=reconciliation").content.decode()
        assert "No payments to reconcile in this window" in body

    def it_flags_a_reversed_date_range(client: Client):
        _login_admin(client, "dateadmin")
        body = client.get(
            "/billing/admin/dashboard/?tab=reconciliation&start=2026-08-31&end=2026-08-01"
        ).content.decode()
        assert "End date must be on or after the start date." in body

    def it_renders_the_print_view(client: Client):
        _login_admin(client, "printadmin")
        response = client.get("/billing/admin/reconciliation/print/")
        assert response.status_code == 200
        assert b"Print / Save as PDF" in response.content
