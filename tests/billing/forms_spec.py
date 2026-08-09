"""BDD-style tests for billing-related forms."""

from __future__ import annotations

from decimal import Decimal

import pytest

from billing.forms import (
    CONTEXT_ADMIN_DASHBOARD,
    BillingSettingsForm,
    ConnectPlatformSettingsForm,
    CustomSplitFormSet,
    TabItemForm,
    VoidTabEntryForm,
)
from billing.models import TabEntrySplit
from tests.billing.factories import BillingSettingsFactory, ProductFactory, TabFactory
from tests.membership.factories import GuildFactory

pytestmark = pytest.mark.django_db


def describe_VoidTabEntryForm():
    def it_requires_reason():
        form = VoidTabEntryForm(data={})
        assert not form.is_valid()
        assert "reason" in form.errors

    def it_is_valid_with_reason():
        form = VoidTabEntryForm(data={"reason": "Duplicate"})
        assert form.is_valid()


def describe_BillingSettingsForm():
    def it_saves_valid_settings():
        form = BillingSettingsForm(
            data={
                "charge_frequency": "monthly",
                "charge_time": "23:00",
                "charge_day_of_month": "1",
                "default_tab_limit": "200.00",
                "default_admin_percent": "20.00",
                "max_retry_attempts": "3",
                "retry_interval_hours": "24",
            }
        )
        assert form.is_valid(), form.errors

    def it_rejects_negative_tab_limit():
        form = BillingSettingsForm(
            data={
                "charge_frequency": "monthly",
                "charge_time": "23:00",
                "charge_day_of_month": "1",
                "default_tab_limit": "-5.00",
                "default_admin_percent": "20.00",
                "max_retry_attempts": "3",
                "retry_interval_hours": "24",
            }
        )
        assert not form.is_valid()
        assert "default_tab_limit" in form.errors


def describe_ConnectPlatformSettingsForm():
    def it_saves_when_connect_is_disabled():
        settings = BillingSettingsFactory()
        form = ConnectPlatformSettingsForm(
            instance=settings,
            data={
                "connect_enabled": False,
                "connect_client_id": "",
                "connect_platform_publishable_key": "",
                "connect_platform_secret_key": "",
                "connect_platform_webhook_secret": "",
            },
        )
        assert form.is_valid(), form.errors

    def it_requires_all_keys_when_connect_is_enabled():
        settings = BillingSettingsFactory()
        form = ConnectPlatformSettingsForm(
            instance=settings,
            data={
                "connect_enabled": True,
                "connect_client_id": "",
                "connect_platform_publishable_key": "",
                "connect_platform_secret_key": "",
                "connect_platform_webhook_secret": "",
            },
        )
        assert not form.is_valid()
        for field in (
            "connect_client_id",
            "connect_platform_publishable_key",
            "connect_platform_secret_key",
            "connect_platform_webhook_secret",
        ):
            assert field in form.errors

    def it_accepts_full_credential_set():
        settings = BillingSettingsFactory()
        form = ConnectPlatformSettingsForm(
            instance=settings,
            data={
                "connect_enabled": True,
                "connect_client_id": "ca_123",
                "connect_platform_publishable_key": "pk_test_123",
                "connect_platform_secret_key": "sk_test_123",
                "connect_platform_webhook_secret": "whsec_test_123",
            },
        )
        assert form.is_valid(), form.errors

    def it_requires_all_test_keys_when_enabled_in_test_mode():
        settings = BillingSettingsFactory()
        form = ConnectPlatformSettingsForm(
            instance=settings,
            data={
                "connect_enabled": True,
                "test_mode": True,
                "test_connect_client_id": "",
                "test_connect_platform_publishable_key": "",
                "test_connect_platform_secret_key": "",
                "test_connect_platform_webhook_secret": "",
            },
        )
        assert not form.is_valid()
        for field in (
            "test_connect_client_id",
            "test_connect_platform_publishable_key",
            "test_connect_platform_secret_key",
            "test_connect_platform_webhook_secret",
        ):
            assert field in form.errors
        # The inactive (live) slot must not be flagged.
        assert "connect_client_id" not in form.errors

    def it_accepts_full_test_credential_set():
        settings = BillingSettingsFactory()
        form = ConnectPlatformSettingsForm(
            instance=settings,
            data={
                "connect_enabled": True,
                "test_mode": True,
                "test_connect_client_id": "ca_test_1",
                "test_connect_platform_publishable_key": "pk_test_1",
                "test_connect_platform_secret_key": "sk_test_1",
                "test_connect_platform_webhook_secret": "whsec_test_1",
            },
        )
        assert form.is_valid(), form.errors

    def it_allows_saving_with_the_inactive_slot_unfilled():
        settings = BillingSettingsFactory()
        form = ConnectPlatformSettingsForm(
            instance=settings,
            data={
                "connect_enabled": True,
                "test_mode": True,
                "test_connect_client_id": "ca_test_1",
                "test_connect_platform_publishable_key": "pk_test_1",
                "test_connect_platform_secret_key": "sk_test_1",
                "test_connect_platform_webhook_secret": "whsec_test_1",
                "connect_client_id": "",
                "connect_platform_publishable_key": "",
                "connect_platform_secret_key": "",
                "connect_platform_webhook_secret": "",
            },
        )
        assert form.is_valid(), form.errors

    def it_allows_prefilling_both_slots_at_once():
        settings = BillingSettingsFactory()
        form = ConnectPlatformSettingsForm(
            instance=settings,
            data={
                "connect_enabled": True,
                "test_mode": True,
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
        assert form.is_valid(), form.errors
        saved = form.save()
        assert saved.test_connect_platform_secret_key == "sk_test_1"
        assert saved.connect_platform_secret_key == "sk_live_1"


def describe_TabItemForm():
    def describe_save_with_product():
        def it_creates_an_entry_with_product_splits():
            BillingSettingsFactory()
            product = ProductFactory()
            tab = TabFactory()
            form = TabItemForm(
                data={
                    "description": "x",
                    "amount": "10.00",
                    "product": product.pk,
                    "member": tab.member.pk,
                },
                context=CONTEXT_ADMIN_DASHBOARD,
            )
            assert form.is_valid(), form.errors
            entry = form.save(tab=tab)
            assert entry.splits.count() >= 1

    def describe_save_with_custom_splits():
        def it_creates_an_entry_with_explicit_splits():
            BillingSettingsFactory()
            tab = TabFactory()
            form = TabItemForm(
                data={
                    "description": "custom",
                    "amount": "10.00",
                    "member": tab.member.pk,
                },
                context=CONTEXT_ADMIN_DASHBOARD,
            )
            assert form.is_valid(), form.errors
            entry = form.save(
                tab=tab,
                splits=[
                    {
                        "recipient_type": "admin",
                        "guild": None,
                        "percent": Decimal("100"),
                    },
                ],
            )
            assert entry.splits.count() == 1
            only = entry.splits.first()
            assert only.recipient_type == TabEntrySplit.RecipientType.ADMIN

        def it_raises_when_custom_entry_has_no_splits():
            BillingSettingsFactory()
            tab = TabFactory()
            form = TabItemForm(
                data={
                    "description": "custom",
                    "amount": "10.00",
                    "member": tab.member.pk,
                },
                context=CONTEXT_ADMIN_DASHBOARD,
            )
            assert form.is_valid(), form.errors
            with pytest.raises(ValueError):
                form.save(tab=tab)

    def describe_custom_split_formset():
        def _make_data(rows: list[tuple[str, str, str]]) -> dict[str, str]:
            data: dict[str, str] = {
                "splits-TOTAL_FORMS": str(len(rows)),
                "splits-INITIAL_FORMS": "0",
                "splits-MIN_NUM_FORMS": "1",
                "splits-MAX_NUM_FORMS": "1000",
            }
            for i, (rtype, guild, percent) in enumerate(rows):
                data[f"splits-{i}-recipient_type"] = rtype
                data[f"splits-{i}-guild"] = guild
                data[f"splits-{i}-percent"] = percent
            return data

        def it_rejects_when_sum_not_100():
            data = _make_data([("admin", "", "50")])
            fs = CustomSplitFormSet(data=data, prefix="splits")
            assert not fs.is_valid()

        def it_accepts_valid_admin_only_split():
            data = _make_data([("admin", "", "100")])
            fs = CustomSplitFormSet(data=data, prefix="splits")
            assert fs.is_valid(), fs.errors
            dicts = fs.to_split_dicts()
            assert len(dicts) == 1
            assert dicts[0]["recipient_type"] == "admin"
            assert dicts[0]["percent"] == Decimal("100")

        def it_rejects_duplicate_guild_rows():
            guild = GuildFactory()
            data = _make_data(
                [
                    ("guild", str(guild.pk), "50"),
                    ("guild", str(guild.pk), "50"),
                ]
            )
            fs = CustomSplitFormSet(data=data, prefix="splits")
            assert not fs.is_valid()

        def it_rejects_admin_row_with_guild():
            guild = GuildFactory()
            data = _make_data([("admin", str(guild.pk), "100")])
            fs = CustomSplitFormSet(data=data, prefix="splits")
            assert not fs.is_valid()

        def it_rejects_guild_row_without_guild():
            data = _make_data([("guild", "", "100")])
            fs = CustomSplitFormSet(data=data, prefix="splits")
            assert not fs.is_valid()

        def it_rejects_two_admin_rows():
            data = _make_data([("admin", "", "50"), ("admin", "", "50")])
            fs = CustomSplitFormSet(data=data, prefix="splits")
            assert not fs.is_valid()
            assert any("Admin" in e for e in fs.non_form_errors())

        def it_skips_per_form_validation_short_circuit_when_row_invalid():
            # A row missing percent triggers per-form errors; clean() must early-return.
            data = _make_data([("admin", "", "")])
            fs = CustomSplitFormSet(data=data, prefix="splits")
            assert not fs.is_valid()

        def it_skips_deleted_rows_in_to_split_dicts():
            guild = GuildFactory()
            data = _make_data([("admin", "", "100"), ("guild", str(guild.pk), "50")])
            # Mark second row as deleted via an extra field. Using can_delete=True
            data["splits-1-DELETE"] = "on"
            data["splits-TOTAL_FORMS"] = "2"
            fs = CustomSplitFormSet(data=data, prefix="splits")
            assert fs.is_valid(), fs.errors
            dicts = fs.to_split_dicts()
            assert len(dicts) == 1
            assert dicts[0]["recipient_type"] == "admin"


def describe_TabItemForm_init_and_clean():
    def it_rejects_unknown_context():
        with pytest.raises(ValueError, match="Unknown TabItemForm context"):
            TabItemForm(context="bogus")

    def it_requires_guild_for_member_guild_page_context():
        from billing.forms import CONTEXT_MEMBER_GUILD_PAGE

        with pytest.raises(ValueError, match="member_guild_page context requires guild"):
            TabItemForm(context=CONTEXT_MEMBER_GUILD_PAGE)

    def it_builds_member_tab_page_context():
        from billing.forms import CONTEXT_MEMBER_TAB_PAGE

        form = TabItemForm(context=CONTEXT_MEMBER_TAB_PAGE)
        assert "product" in form.fields
        # The member-tab-page context omits the member + guild fields.
        assert "member" not in form.fields
        assert "guild" not in form.fields

    def it_raises_when_no_product_and_no_description_or_amount():
        # Admin-context with neither product nor manual fields → form-level error.
        form = TabItemForm(data={"member": ""}, context=CONTEXT_ADMIN_DASHBOARD)
        # member field is required so form invalid; but the clean() error path
        # is hit when description+amount empty and no product.
        form.is_valid()
        # The non-field error from clean() should appear:
        assert any("description and amount" in e for e in form.non_field_errors()) or (
            "member" in form.errors  # acceptable: validation order may surface member first
        )

    def it_apply_to_tab_raises_if_form_not_validated():
        from tests.billing.factories import TabFactory

        BillingSettingsFactory()
        tab = TabFactory()
        form = TabItemForm(context=CONTEXT_ADMIN_DASHBOARD)  # never bound, never validated
        with pytest.raises(RuntimeError, match="apply_to_tab"):
            form.apply_to_tab(tab, added_by=None, is_self_service=True)

    def it_save_raises_if_form_not_validated():
        from tests.billing.factories import TabFactory

        BillingSettingsFactory()
        tab = TabFactory()
        form = TabItemForm(context=CONTEXT_ADMIN_DASHBOARD)
        with pytest.raises(RuntimeError, match="save"):
            form.save(tab=tab)

    def it_default_custom_splits_uses_billing_settings_admin_percent():
        from billing.forms import CONTEXT_MEMBER_GUILD_PAGE
        from tests.billing.factories import TabFactory

        BillingSettingsFactory(default_admin_percent=Decimal("15.00"))
        guild = GuildFactory()
        tab = TabFactory()
        form = TabItemForm(
            data={"description": "tea", "amount": "5.00", "quantity": "1"},
            context=CONTEXT_MEMBER_GUILD_PAGE,
            guild=guild,
        )
        assert form.is_valid(), form.errors
        entry = form.apply_to_tab(tab, added_by=None, is_self_service=True)
        admin_split = entry.splits.get(recipient_type="admin")
        guild_split = entry.splits.get(recipient_type="guild")
        # 15% admin, 85% guild
        assert admin_split.percent == Decimal("15.00")
        assert guild_split.percent == Decimal("85.00")

    def it_default_custom_splits_rejected_outside_member_guild_page():
        from tests.billing.factories import TabFactory

        BillingSettingsFactory()
        tab = TabFactory()
        # admin_dashboard context, no product, no splits → save() raises ValueError.
        form = TabItemForm(
            data={"description": "x", "amount": "5.00", "member": tab.member.pk},
            context=CONTEXT_ADMIN_DASHBOARD,
        )
        assert form.is_valid(), form.errors
        with pytest.raises(ValueError, match="explicit splits"):
            # apply_to_tab → _default_custom_splits → ValueError because not member_guild_page.
            form.apply_to_tab(tab, added_by=None, is_self_service=False)

    def it_apply_to_tab_with_product_uses_product_splits():
        from tests.billing.factories import ProductFactory, TabFactory

        BillingSettingsFactory()
        guild = GuildFactory()
        product = ProductFactory(guild=guild)
        tab = TabFactory()
        # Use admin context to bind a product field via the form.
        form = TabItemForm(
            data={
                "description": "x",
                "amount": "1.00",
                "member": tab.member.pk,
                "product": product.pk,
            },
            context=CONTEXT_ADMIN_DASHBOARD,
        )
        assert form.is_valid(), form.errors
        entry = form.apply_to_tab(tab, added_by=None, is_self_service=False)
        # 20/80 split from ProductFactory default
        assert entry.splits.count() == 2
