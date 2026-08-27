"""BDD-style tests for billing admin dashboard and add-entry views."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.test import Client

from billing.models import BillingSettings, TabCharge
from core.models import SiteConfiguration
from membership.models import AdminCapability
from tests.billing.factories import BillingSettingsFactory, TabChargeFactory, TabEntryFactory, TabFactory
from tests.membership.factories import MemberFactory

pytestmark = pytest.mark.django_db


def _create_superuser(client: Client) -> User:
    user = User.objects.create_superuser(username="admin", password="pass", email="admin@example.com")
    client.login(username="admin", password="pass")
    return user


def _login_billing_approver(client: Client, username: str = "biller") -> User:
    """A non-admin member holding only the BILLING_APPROVER capability."""
    user = User.objects.create_user(username=username, password="pass", email=f"{username}@example.com")
    client.login(username=username, password="pass")
    user.member.admin_capabilities.create(capability=AdminCapability.Capability.BILLING_APPROVER)  # type: ignore[attr-defined]
    return user


def _disable_my_tab() -> None:
    config = SiteConfiguration.load()
    config.my_tab_enabled = False
    config.save()


def describe_admin_tab_dashboard():
    def it_requires_staff(client: Client):
        response = client.get("/billing/admin/dashboard/")
        assert response.status_code == 302

    def it_renders_for_superuser(client: Client):
        _create_superuser(client)
        response = client.get("/billing/admin/dashboard/")

        assert response.status_code == 200
        assert "total_outstanding" in response.context

    def it_shows_aggregate_stats(client: Client):
        _create_superuser(client)
        member = MemberFactory(status="active")
        tab = TabFactory(member=member)
        TabEntryFactory(tab=tab, amount=Decimal("30.00"))
        TabEntryFactory(tab=tab, amount=Decimal("20.00"))
        TabChargeFactory(tab=tab, status=TabCharge.Status.FAILED, amount=Decimal("10.00"))

        response = client.get("/billing/admin/dashboard/")

        assert response.context["total_outstanding"] == Decimal("50.00")
        assert response.context["failed_count"] == 1

    def it_shows_outstanding_tabs(client: Client):
        _create_superuser(client)
        member = MemberFactory(status="active")
        tab = TabFactory(member=member)
        TabEntryFactory(tab=tab, amount=Decimal("30.00"))

        response = client.get("/billing/admin/dashboard/")

        # At least our tab with entries shows up
        assert tab in response.context["outstanding_tabs"]

    def it_shows_locked_count(client: Client):
        _create_superuser(client)
        TabFactory(is_locked=True)

        response = client.get("/billing/admin/dashboard/")

        assert response.context["locked_count"] == 1


def describe_admin_tab_dashboard_extended():
    def it_defaults_to_overview_tab(client: Client):
        _create_superuser(client)
        response = client.get("/billing/admin/dashboard/")
        assert response.context["active_tab"] == "overview"

    def it_sets_active_tab_from_query_param(client: Client):
        _create_superuser(client)
        for tab_name in ["overview", "open-tabs", "settings", "stripe"]:
            response = client.get(f"/billing/admin/dashboard/?tab={tab_name}")
            assert response.context["active_tab"] == tab_name

    def it_unknown_tab_defaults_to_overview(client: Client):
        _create_superuser(client)
        response = client.get("/billing/admin/dashboard/?tab=bogus")
        assert response.context["active_tab"] == "overview"

    def it_provides_open_tabs_filter_outstanding(client: Client):
        _create_superuser(client)
        member = MemberFactory(status="active")
        tab = TabFactory(member=member)
        TabEntryFactory(tab=tab, amount=Decimal("10.00"))

        response = client.get("/billing/admin/dashboard/?tab=open-tabs&filter=outstanding")
        assert tab in response.context["open_tabs"]

    def it_provides_open_tabs_filter_all(client: Client):
        _create_superuser(client)
        member = MemberFactory(status="active")
        tab = TabFactory(member=member)
        TabEntryFactory(tab=tab, amount=Decimal("5.00"))

        response = client.get("/billing/admin/dashboard/?tab=open-tabs&filter=all")
        assert tab in response.context["open_tabs"]

    def it_excludes_zero_balance_tabs(client: Client):
        _create_superuser(client)
        member = MemberFactory(status="active")
        tab = TabFactory(member=member)

        response = client.get("/billing/admin/dashboard/?tab=open-tabs&filter=all")
        assert tab not in response.context["open_tabs"]

    def it_provides_open_tabs_filter_failed(client: Client):
        _create_superuser(client)
        member = MemberFactory(status="active")
        tab = TabFactory(member=member)
        TabEntryFactory(tab=tab, amount=Decimal("5.00"))
        TabChargeFactory(tab=tab, status=TabCharge.Status.FAILED)

        response = client.get("/billing/admin/dashboard/?tab=open-tabs&filter=failed")
        assert tab in response.context["open_tabs"]

    def it_provides_settings_form(client: Client):
        _create_superuser(client)
        BillingSettingsFactory()

        response = client.get("/billing/admin/dashboard/?tab=settings")
        assert "settings_form" in response.context
        from billing.forms import BillingSettingsForm

        assert isinstance(response.context["settings_form"], BillingSettingsForm)

    def it_provides_stripe_context(client: Client):
        _create_superuser(client)
        from tests.billing.factories import ProductFactory

        ProductFactory()

        response = client.get("/billing/admin/dashboard/?tab=stripe")
        assert "products" in response.context
        assert "guilds" in response.context

    def it_shows_test_mode_badge_when_test_mode_on(client: Client):
        _create_superuser(client)
        bs = BillingSettings.load()
        bs.test_mode = True
        bs.save()

        response = client.get("/billing/admin/dashboard/?tab=stripe")
        content = response.content.decode()
        assert "TEST MODE" in content
        assert "LIVE MODE" not in content

    def it_shows_live_mode_badge_when_test_mode_off(client: Client):
        _create_superuser(client)
        bs = BillingSettings.load()
        bs.test_mode = False
        bs.save()

        response = client.get("/billing/admin/dashboard/?tab=stripe")
        content = response.content.decode()
        assert "LIVE MODE" in content
        assert "TEST MODE" not in content


def describe_when_my_tab_disabled():
    def it_defaults_to_payments_tab(client: Client):
        _disable_my_tab()
        _create_superuser(client)
        response = client.get("/billing/admin/dashboard/")
        assert response.status_code == 200
        assert response.context["active_tab"] == "payments"

    def it_falls_back_to_payments_for_overview(client: Client):
        _disable_my_tab()
        _create_superuser(client)
        response = client.get("/billing/admin/dashboard/?tab=overview")
        assert response.status_code == 200
        assert response.context["active_tab"] == "payments"

    def it_falls_back_to_payments_for_open_tabs(client: Client):
        _disable_my_tab()
        _create_superuser(client)
        response = client.get("/billing/admin/dashboard/?tab=open-tabs")
        assert response.status_code == 200
        assert response.context["active_tab"] == "payments"

    def it_falls_back_cleanly_for_open_tabs_with_filter(client: Client):
        _disable_my_tab()
        _create_superuser(client)
        response = client.get("/billing/admin/dashboard/?tab=open-tabs&filter=failed")
        assert response.status_code == 200
        assert response.context["active_tab"] == "payments"

    def it_falls_back_to_payments_for_unknown_tab(client: Client):
        _disable_my_tab()
        _create_superuser(client)
        response = client.get("/billing/admin/dashboard/?tab=nonsense")
        assert response.status_code == 200
        assert response.context["active_tab"] == "payments"

    def it_hides_overview_and_open_tabs_nav_but_keeps_the_rest_for_admin(client: Client):
        _disable_my_tab()
        _create_superuser(client)
        content = client.get("/billing/admin/dashboard/").content.decode()
        assert "?tab=overview" not in content
        assert "?tab=open-tabs" not in content
        assert "?tab=payments" in content
        assert "?tab=settings" in content
        assert "?tab=stripe" in content

    def it_shows_the_admin_flag_off_subtitle(client: Client):
        _disable_my_tab()
        _create_superuser(client)
        content = client.get("/billing/admin/dashboard/").content.decode()
        assert "Payment history and Stripe configuration." in content

    def it_shows_the_approver_flag_off_subtitle_without_stripe(client: Client):
        _disable_my_tab()
        _login_billing_approver(client, "subapprover")
        content = client.get("/billing/admin/dashboard/").content.decode()
        assert "Payment history across the makerspace." in content
        # The approver's subtitle omits Stripe; the admin-flavored sentence must not appear.
        # (A bare "Stripe configuration" check collides with the CHANGELOG rendered into context.)
        assert "Payment history and Stripe configuration." not in content

    def it_omits_the_add_charge_modal(client: Client):
        _disable_my_tab()
        _create_superuser(client)
        content = client.get("/billing/admin/dashboard/").content.decode()
        assert 'id="add-charge-modal"' not in content
        assert "Add Charge to Tab" not in content

    def it_keeps_the_tab_detail_modal(client: Client):
        _disable_my_tab()
        _create_superuser(client)
        content = client.get("/billing/admin/dashboard/").content.decode()
        assert 'id="tab-detail-modal"' in content

    def it_pins_the_tabs_off_js_flag(client: Client):
        _disable_my_tab()
        _create_superuser(client)
        content = client.get("/billing/admin/dashboard/").content.decode()
        assert "const TABS_ON = false" in content

    def it_pins_the_tabs_on_js_flag_when_enabled(client: Client):
        _create_superuser(client)  # flag defaults to True
        content = client.get("/billing/admin/dashboard/").content.decode()
        assert "const TABS_ON = true" in content

    def it_suppresses_the_single_tab_nav_for_an_approver(client: Client):
        _disable_my_tab()
        _login_billing_approver(client, "navapprover")
        content = client.get("/billing/admin/dashboard/").content.decode()
        assert 'class="pl-tab-nav"' not in content

    def it_keeps_the_tab_nav_for_an_admin_with_flag_off(client: Client):
        _disable_my_tab()
        _create_superuser(client)
        content = client.get("/billing/admin/dashboard/").content.decode()
        assert 'class="pl-tab-nav"' in content

    def it_keeps_the_tab_nav_for_an_approver_with_flag_on(client: Client):
        _login_billing_approver(client, "navapproveron")  # flag defaults True → 3 tabs
        content = client.get("/billing/admin/dashboard/").content.decode()
        assert 'class="pl-tab-nav"' in content

    def it_shows_view_in_admin_for_a_fog_admin(client: Client):
        _disable_my_tab()
        _create_superuser(client)
        content = client.get("/billing/admin/dashboard/").content.decode()
        assert 'id="tdm-admin-link"' in content

    def it_hides_view_in_admin_for_an_approver(client: Client):
        _disable_my_tab()
        _login_billing_approver(client, "vialink")
        content = client.get("/billing/admin/dashboard/").content.decode()
        assert 'id="tdm-admin-link"' not in content

    def it_hides_view_in_admin_for_an_approver_with_flag_on(client: Client):
        _login_billing_approver(client, "vialinkon")  # flag defaults True
        content = client.get("/billing/admin/dashboard/").content.decode()
        assert 'id="tdm-admin-link"' not in content

    def it_shows_view_in_admin_for_a_fog_admin_with_flag_on(client: Client):
        _create_superuser(client)  # flag defaults True
        content = client.get("/billing/admin/dashboard/").content.decode()
        assert 'id="tdm-admin-link"' in content

    def it_omits_overview_context_keys(client: Client):
        _disable_my_tab()
        _create_superuser(client)
        response = client.get("/billing/admin/dashboard/")
        assert "outstanding_tabs" not in response.context
        assert "total_outstanding" not in response.context
        assert "open_tabs" not in response.context
        assert "add_charge_form" not in response.context

    def it_still_403s_the_settings_tab_for_a_non_admin_approver(client: Client):
        _disable_my_tab()
        _login_billing_approver(client, "settings403")
        response = client.get("/billing/admin/dashboard/?tab=settings")
        assert response.status_code == 403


def describe_billing_admin_tab_detail_api():
    def it_requires_staff(client: Client):
        response = client.get("/billing/admin/tab/999/detail/")
        assert response.status_code == 302

    def it_returns_404_for_missing_tab(client: Client):
        _create_superuser(client)
        response = client.get("/billing/admin/tab/999999/detail/")
        assert response.status_code == 404

    def it_returns_tab_data_as_json(client: Client):
        _create_superuser(client)
        member = MemberFactory(full_legal_name="Jane Doe")
        tab = TabFactory(
            member=member,
            stripe_payment_method_id="pm_test",
            payment_method_brand="visa",
            payment_method_last4="4242",
            tab_limit=Decimal("150.00"),
        )
        TabEntryFactory(tab=tab, description="Laser time", amount=Decimal("20.00"))

        response = client.get(f"/billing/admin/tab/{tab.pk}/detail/")

        assert response.status_code == 200
        data = response.json()
        assert data["member_name"] == "Jane Doe"
        assert data["balance"] == "20.00"
        assert data["limit"] == "150.00"
        assert data["payment_method"] == "visa 4242"
        assert data["is_locked"] is False
        assert len(data["pending_entries"]) == 1
        assert data["pending_entries"][0]["description"] == "Laser time"
        assert data["pending_entries"][0]["amount"] == "20.00"

    def it_returns_charge_history(client: Client):
        _create_superuser(client)
        member = MemberFactory()
        tab = TabFactory(member=member)
        TabChargeFactory(
            tab=tab,
            status=TabCharge.Status.SUCCEEDED,
            amount=Decimal("50.00"),
            stripe_receipt_url="https://receipt.test",
        )

        response = client.get(f"/billing/admin/tab/{tab.pk}/detail/")
        data = response.json()

        assert len(data["charge_history"]) == 1
        assert data["charge_history"][0]["amount"] == "50.00"
        assert data["charge_history"][0]["status"] == "succeeded"
        assert data["charge_history"][0]["receipt_url"] == "https://receipt.test"

    def it_shows_no_payment_method_when_absent(client: Client):
        _create_superuser(client)
        member = MemberFactory()
        tab = TabFactory(member=member, stripe_payment_method_id="", payment_method_brand="", payment_method_last4="")

        response = client.get(f"/billing/admin/tab/{tab.pk}/detail/")
        data = response.json()

        assert data["payment_method"] == ""


def describe_billing_admin_save_settings():
    def it_requires_staff(client: Client):
        response = client.post("/billing/admin/save-settings/", {})
        assert response.status_code == 302
        assert "/accounts/login/" in response.url or "/admin/login/" in response.url

    def it_saves_valid_settings_and_redirects(client: Client):
        _create_superuser(client)
        BillingSettingsFactory()

        response = client.post(
            "/billing/admin/save-settings/",
            {
                "charge_frequency": "weekly",
                "charge_time": "22:00",
                "charge_day_of_week": "1",
                "charge_day_of_month": "",
                "default_tab_limit": "150.00",
                "default_admin_percent": "20.00",
                "max_retry_attempts": "5",
                "retry_interval_hours": "12",
            },
        )

        assert response.status_code == 302
        assert response.url == "/billing/admin/dashboard/?tab=settings"
        settings = BillingSettings.load()
        assert settings.charge_frequency == "weekly"
        assert settings.max_retry_attempts == 5

    def it_redirects_with_error_on_invalid_data(client: Client):
        _create_superuser(client)
        BillingSettingsFactory()

        response = client.post(
            "/billing/admin/save-settings/",
            {
                "charge_frequency": "daily",
                "charge_time": "23:00",
                "charge_day_of_week": "",
                "charge_day_of_month": "",
                "default_tab_limit": "-50.00",
                "max_retry_attempts": "3",
                "retry_interval_hours": "24",
            },
        )

        assert response.status_code == 302
        assert "tab=settings" in response.url
        settings = BillingSettings.load()
        assert settings.default_tab_limit != Decimal("-50.00")

    def it_only_accepts_post(client: Client):
        _create_superuser(client)
        response = client.get("/billing/admin/save-settings/")
        assert response.status_code == 405


def describe_billing_admin_retry_charge():
    def it_requires_staff(client: Client):
        response = client.post("/billing/admin/retry-charge/999/")
        assert response.status_code == 302

    def it_returns_404_for_missing_charge(client: Client):
        _create_superuser(client)
        response = client.post("/billing/admin/retry-charge/999999/")
        assert response.status_code == 404

    def it_succeeds_when_stripe_succeeds(client: Client):
        _create_superuser(client)
        tab = TabFactory(stripe_customer_id="cus_test", stripe_payment_method_id="pm_test")
        charge = TabChargeFactory(
            tab=tab,
            status=TabCharge.Status.FAILED,
            amount=Decimal("25.00"),
        )

        mock_result = {"id": "pi_test123", "charge_id": "ch_test123", "receipt_url": "https://receipt.test"}
        with patch("billing.views.stripe_utils.create_payment_intent", return_value=mock_result):
            response = client.post(f"/billing/admin/retry-charge/{charge.pk}/")

        assert response.status_code == 200
        assert response.json()["status"] == "succeeded"
        charge.refresh_from_db()
        assert charge.status == TabCharge.Status.SUCCEEDED
        assert charge.stripe_payment_intent_id == "pi_test123"

    def it_returns_failed_json_when_stripe_raises(client: Client):
        _create_superuser(client)
        tab = TabFactory(stripe_customer_id="cus_test", stripe_payment_method_id="pm_test")
        charge = TabChargeFactory(
            tab=tab,
            status=TabCharge.Status.FAILED,
            amount=Decimal("25.00"),
        )

        with patch("billing.views.stripe_utils.create_payment_intent", side_effect=Exception("Card declined")):
            response = client.post(f"/billing/admin/retry-charge/{charge.pk}/")

        assert response.status_code == 200
        assert response.json()["status"] == "failed"
        charge.refresh_from_db()
        assert charge.status == TabCharge.Status.FAILED

    def it_still_retries_with_my_tab_disabled(client: Client):
        # Collecting existing debt is legitimate with the feature off — retry is NOT gated.
        _create_superuser(client)
        _disable_my_tab()
        tab = TabFactory(stripe_customer_id="cus_test", stripe_payment_method_id="pm_test")
        charge = TabChargeFactory(tab=tab, status=TabCharge.Status.FAILED, amount=Decimal("25.00"))

        mock_result = {"id": "pi_retry_off", "charge_id": "ch_off", "receipt_url": "https://receipt.test"}
        with patch("billing.views.stripe_utils.create_payment_intent", return_value=mock_result):
            response = client.post(f"/billing/admin/retry-charge/{charge.pk}/")

        assert response.status_code == 200
        assert response.json()["status"] == "succeeded"
        charge.refresh_from_db()
        assert charge.status == TabCharge.Status.SUCCEEDED


def describe_admin_add_tab_entry():
    def it_requires_staff(client: Client):
        response = client.get("/billing/admin/add-entry/")
        assert response.status_code == 302

    def it_renders_form(client: Client):
        _create_superuser(client)
        response = client.get("/billing/admin/add-entry/")

        assert response.status_code == 200
        assert "form" in response.context

    def it_creates_entry_when_product_selected(client: Client):
        from tests.billing.factories import ProductFactory

        BillingSettingsFactory()
        _create_superuser(client)
        member = MemberFactory(status="active")
        product = ProductFactory()  # default 20/80 split
        response = client.post(
            "/billing/admin/add-entry/",
            data={
                "member": str(member.pk),
                "product": str(product.pk),
                "description": "ignored",
                "amount": "1.00",
                # Empty splits formset still required by form parsing
                "splits-TOTAL_FORMS": "0",
                "splits-INITIAL_FORMS": "0",
                "splits-MIN_NUM_FORMS": "1",
                "splits-MAX_NUM_FORMS": "1000",
            },
        )
        assert response.status_code == 302
        from billing.models import Tab

        tab = Tab.objects.get(member=member)
        assert tab.entries.count() == 1

    def it_creates_entry_when_custom_splits_supplied(client: Client):
        BillingSettingsFactory()
        _create_superuser(client)
        member = MemberFactory(status="active")
        response = client.post(
            "/billing/admin/add-entry/",
            data={
                "member": str(member.pk),
                "description": "manual entry",
                "amount": "5.00",
                "splits-TOTAL_FORMS": "1",
                "splits-INITIAL_FORMS": "0",
                "splits-MIN_NUM_FORMS": "1",
                "splits-MAX_NUM_FORMS": "1000",
                "splits-0-recipient_type": "admin",
                "splits-0-guild": "",
                "splits-0-percent": "100",
            },
        )
        assert response.status_code == 302
        from billing.models import Tab

        tab = Tab.objects.get(member=member)
        assert tab.entries.count() == 1

    def it_re_renders_when_custom_splits_invalid(client: Client):
        BillingSettingsFactory()
        _create_superuser(client)
        member = MemberFactory(status="active")
        response = client.post(
            "/billing/admin/add-entry/",
            data={
                "member": str(member.pk),
                "description": "manual entry",
                "amount": "5.00",
                # Splits don't sum to 100 → formset invalid
                "splits-TOTAL_FORMS": "1",
                "splits-INITIAL_FORMS": "0",
                "splits-MIN_NUM_FORMS": "1",
                "splits-MAX_NUM_FORMS": "1000",
                "splits-0-recipient_type": "admin",
                "splits-0-guild": "",
                "splits-0-percent": "50",
            },
        )
        assert response.status_code == 200
        from billing.models import TabEntry

        # No tab/entry created
        assert TabEntry.objects.filter(tab__member=member).count() == 0

    def it_re_renders_when_tab_locked(client: Client):
        from tests.billing.factories import ProductFactory, TabFactory

        BillingSettingsFactory()
        _create_superuser(client)
        member = MemberFactory(status="active")
        TabFactory(member=member, is_locked=True, locked_reason="test")
        product = ProductFactory()
        response = client.post(
            "/billing/admin/add-entry/",
            data={
                "member": str(member.pk),
                "product": str(product.pk),
                "description": "x",
                "amount": "1.00",
                "splits-TOTAL_FORMS": "0",
                "splits-INITIAL_FORMS": "0",
                "splits-MIN_NUM_FORMS": "1",
                "splits-MAX_NUM_FORMS": "1000",
            },
        )
        assert response.status_code == 200  # re-renders the form

    def it_re_renders_when_form_invalid(client: Client):
        # POST with no member field at all → form-level invalid → re-renders
        BillingSettingsFactory()
        _create_superuser(client)
        response = client.post(
            "/billing/admin/add-entry/",
            data={
                "description": "x",
                "amount": "1.00",
                "splits-TOTAL_FORMS": "0",
                "splits-INITIAL_FORMS": "0",
                "splits-MIN_NUM_FORMS": "1",
                "splits-MAX_NUM_FORMS": "1000",
            },
        )
        assert response.status_code == 200

    def describe_when_my_tab_disabled():
        def it_redirects_a_valid_post_without_creating_an_entry(client: Client):
            from tests.billing.factories import ProductFactory

            BillingSettingsFactory()
            _create_superuser(client)
            _disable_my_tab()
            member = MemberFactory(status="active")
            product = ProductFactory()
            response = client.post(
                "/billing/admin/add-entry/",
                data={
                    "member": str(member.pk),
                    "product": str(product.pk),
                    "description": "would be valid",
                    "amount": "1.00",
                    "splits-TOTAL_FORMS": "0",
                    "splits-INITIAL_FORMS": "0",
                    "splits-MIN_NUM_FORMS": "1",
                    "splits-MAX_NUM_FORMS": "1000",
                },
            )
            assert response.status_code == 302
            assert response.url == "/billing/admin/dashboard/"
            from billing.models import TabEntry

            assert TabEntry.objects.filter(tab__member=member).count() == 0

        def it_redirects_the_standalone_get_page(client: Client):
            _create_superuser(client)
            _disable_my_tab()
            response = client.get("/billing/admin/add-entry/")
            assert response.status_code == 302
            assert response.url == "/billing/admin/dashboard/"

        def it_sets_an_info_message(client: Client):
            _create_superuser(client)
            _disable_my_tab()
            response = client.get("/billing/admin/add-entry/", follow=True)
            messages = [str(m) for m in response.context["messages"]]
            assert any("My Tab is off" in m for m in messages)


def describe_admin_reports():
    def it_requires_staff(client: Client):
        response = client.get("/billing/admin/reports/")
        assert response.status_code == 302

    def it_renders_with_default_current_month_when_no_filters(client: Client):
        _create_superuser(client)
        response = client.get("/billing/admin/reports/")
        assert response.status_code == 200
        assert "rows" in response.context
        assert "payout_summary" in response.context
        assert "admin_total" in response.context

    def it_renders_with_explicit_filters(client: Client):
        from tests.billing.factories import ProductFactory

        BillingSettingsFactory()
        _create_superuser(client)
        product = ProductFactory()
        tab = TabFactory()
        tab.add_entry(description="x", amount=Decimal("10.00"), product=product)
        response = client.get(
            "/billing/admin/reports/?start_date=2020-01-01&end_date=2099-12-31&guilds=99&charge_type=product&status=pending"
        )
        assert response.status_code == 200
        assert response.context["selected_charge_types"] == ["product"]
        assert response.context["selected_statuses"] == ["pending"]
        assert response.context["selected_guilds"] == ["99"]


def describe_admin_reports_csv():
    def it_requires_staff(client: Client):
        response = client.get("/billing/admin/reports/export/csv/")
        assert response.status_code == 302

    def it_streams_csv(client: Client):
        from tests.billing.factories import ProductFactory

        BillingSettingsFactory()
        _create_superuser(client)
        product = ProductFactory()
        tab = TabFactory()
        tab.add_entry(description="bag", amount=Decimal("10.00"), product=product)
        response = client.get("/billing/admin/reports/export/csv/")
        assert response.status_code == 200
        body = b"".join(response.streaming_content).decode()
        assert "date,member,description" in body
        assert "bag" in body


def describe_billing_test_platform_connection():
    def it_requires_staff(client: Client):
        response = client.post("/billing/admin/connect-platform/test/", {"secret_key": "sk_test_abc"})
        assert response.status_code == 302

    def it_only_accepts_post(client: Client):
        _create_superuser(client)
        response = client.get("/billing/admin/connect-platform/test/")
        assert response.status_code == 405

    def it_returns_error_when_secret_key_is_missing(client: Client):
        _create_superuser(client)
        response = client.post("/billing/admin/connect-platform/test/", {"secret_key": ""})
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is False
        assert "required" in data["error"].lower()

    def it_returns_error_when_key_has_invalid_prefix(client: Client):
        _create_superuser(client)
        response = client.post("/billing/admin/connect-platform/test/", {"secret_key": "invalid_key_abc"})
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is False
        assert "sk_test_" in data["error"]

    def it_returns_error_when_stripe_raises(client: Client):
        _create_superuser(client)
        with patch("billing.views.stripe_utils.verify_platform_credentials", side_effect=Exception("Invalid API Key")):
            response = client.post("/billing/admin/connect-platform/test/", {"secret_key": "sk_test_validformat"})
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is False
        assert "Invalid API Key" in data["error"]

    def it_returns_ok_true_with_result_on_success(client: Client):
        _create_superuser(client)
        mock_result = {"account_id": "acct_test123", "display_name": "Test Account"}
        with patch("billing.views.stripe_utils.verify_platform_credentials", return_value=mock_result):
            response = client.post("/billing/admin/connect-platform/test/", {"secret_key": "sk_live_validformat"})
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert data["account_id"] == "acct_test123"

    def it_accepts_rk_prefixed_keys(client: Client):
        _create_superuser(client)
        mock_result = {"account_id": "acct_rk123"}
        with patch("billing.views.stripe_utils.verify_platform_credentials", return_value=mock_result):
            response = client.post("/billing/admin/connect-platform/test/", {"secret_key": "rk_live_validformat"})
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True


def describe_billing_save_connect_platform():
    def it_requires_staff(client: Client):
        response = client.post("/billing/admin/connect-platform/save/", {})
        assert response.status_code == 302
        assert "/accounts/login/" in response.url or "/admin/login/" in response.url

    def it_only_accepts_post(client: Client):
        _create_superuser(client)
        response = client.get("/billing/admin/connect-platform/save/")
        assert response.status_code == 405

    def it_saves_valid_settings_and_redirects(client: Client):
        from tests.billing.factories import BillingSettingsFactory

        _create_superuser(client)
        BillingSettingsFactory()

        response = client.post(
            "/billing/admin/connect-platform/save/",
            {
                "connect_enabled": "on",
                "connect_client_id": "ca_test_123",
                "connect_platform_publishable_key": "pk_test_123",
                "connect_platform_secret_key": "sk_test_123",
                "connect_platform_webhook_secret": "whsec_123",
            },
        )

        assert response.status_code == 302
        assert response.url == "/billing/admin/dashboard/?tab=stripe"
        from billing.models import BillingSettings

        settings = BillingSettings.load()
        assert settings.connect_enabled is True
        assert settings.connect_client_id == "ca_test_123"

    def it_shows_error_messages_on_invalid_form_and_redirects(client: Client):
        from tests.billing.factories import BillingSettingsFactory

        _create_superuser(client)
        BillingSettingsFactory()

        # connect_enabled=on but missing all required fields → form invalid
        response = client.post(
            "/billing/admin/connect-platform/save/",
            {
                "connect_enabled": "on",
                "connect_client_id": "",
                "connect_platform_publishable_key": "",
                "connect_platform_secret_key": "",
                "connect_platform_webhook_secret": "",
            },
        )

        assert response.status_code == 302
        assert response.url == "/billing/admin/dashboard/?tab=stripe"

    def it_saves_test_mode_and_test_keys(client: Client):
        from tests.billing.factories import BillingSettingsFactory

        _create_superuser(client)
        BillingSettingsFactory()

        response = client.post(
            "/billing/admin/connect-platform/save/",
            {
                "connect_enabled": "on",
                "test_mode": "on",
                "test_connect_client_id": "ca_test_1",
                "test_connect_platform_publishable_key": "pk_test_1",
                "test_connect_platform_secret_key": "sk_test_1",
                "test_connect_platform_webhook_secret": "whsec_test_1",
            },
        )

        assert response.status_code == 302
        settings = BillingSettings.load()
        assert settings.test_mode is True
        assert settings.test_connect_platform_secret_key == "sk_test_1"

    def it_saves_both_key_slots_at_once(client: Client):
        from tests.billing.factories import BillingSettingsFactory

        _create_superuser(client)
        BillingSettingsFactory()

        response = client.post(
            "/billing/admin/connect-platform/save/",
            {
                "connect_enabled": "on",
                "test_mode": "on",
                "test_connect_client_id": "ca_test_1",
                "test_connect_platform_publishable_key": "pk_test_1",
                "test_connect_platform_secret_key": "sk_test_1",
                "test_connect_platform_webhook_secret": "whsec_test_1",
                "connect_client_id": "ca_live_1",
                "connect_platform_publishable_key": "pk_live_1",
                "connect_platform_secret_key": "sk_live_1",
                "connect_platform_webhook_secret": "whsec_live_1",
            },
        )

        assert response.status_code == 302
        settings = BillingSettings.load()
        assert settings.test_mode is True
        assert settings.test_connect_platform_secret_key == "sk_test_1"
        assert settings.connect_platform_secret_key == "sk_live_1"
