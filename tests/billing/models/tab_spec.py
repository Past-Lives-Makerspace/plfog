"""BDD-style tests for Tab model."""

from decimal import Decimal

import pytest
from django.db.models import ProtectedError

from tests.billing.factories import BillingSettingsFactory, TabEntryFactory, TabFactory, UserFactory

pytestmark = pytest.mark.django_db


def describe_Tab():
    def it_has_str_representation():
        tab = TabFactory()
        assert str(tab) == f"Tab for {tab.member}"

    def it_protects_member_on_delete():
        tab = TabFactory()
        with pytest.raises(ProtectedError):
            tab.member.delete()

    def describe_effective_tab_limit():
        def it_returns_per_member_override_when_set():
            tab = TabFactory(tab_limit=Decimal("500.00"))
            assert tab.effective_tab_limit == Decimal("500.00")

        def it_returns_global_default_when_no_override():
            BillingSettingsFactory(default_tab_limit=Decimal("300.00"))
            tab = TabFactory(tab_limit=None)
            assert tab.effective_tab_limit == Decimal("300.00")

    def describe_current_balance():
        def it_returns_zero_with_no_entries():
            tab = TabFactory()
            assert tab.current_balance == Decimal("0.00")

        def it_sums_pending_entries():
            tab = TabFactory()
            TabEntryFactory(tab=tab, amount=Decimal("10.00"))
            TabEntryFactory(tab=tab, amount=Decimal("15.50"))
            assert tab.current_balance == Decimal("25.50")

        def it_excludes_voided_entries():
            tab = TabFactory()
            TabEntryFactory(tab=tab, amount=Decimal("10.00"))
            voided = TabEntryFactory(tab=tab, amount=Decimal("20.00"))
            user = UserFactory()
            voided.void(user=user, reason="test")
            assert tab.current_balance == Decimal("10.00")

        def it_excludes_charged_entries():
            from tests.billing.factories import TabChargeFactory

            tab = TabFactory()
            charge = TabChargeFactory(tab=tab)
            TabEntryFactory(tab=tab, amount=Decimal("10.00"), tab_charge=charge)
            TabEntryFactory(tab=tab, amount=Decimal("5.00"))
            assert tab.current_balance == Decimal("5.00")

    def describe_has_payment_method():
        def it_returns_true_when_set():
            tab = TabFactory(stripe_payment_method_id="pm_test_123")
            assert tab.has_payment_method is True

        def it_returns_false_when_blank():
            tab = TabFactory(stripe_payment_method_id="")
            assert tab.has_payment_method is False

    def describe_can_add_entry():
        def it_returns_true_when_unlocked():
            tab = TabFactory(is_locked=False)
            assert tab.can_add_entry is True

        def it_returns_false_when_locked():
            tab = TabFactory(is_locked=True)
            assert tab.can_add_entry is False

        def it_returns_false_without_a_saved_payment_method():
            # v1.5.0: restored the has_payment_method gate — charges go through the
            # platform account off-session, so a saved card is required up front.
            tab = TabFactory(is_locked=False, stripe_payment_method_id="")
            assert tab.can_add_entry is False

    def describe_remaining_limit():
        def it_returns_full_limit_with_no_entries():
            BillingSettingsFactory(default_tab_limit=Decimal("200.00"))
            tab = TabFactory(tab_limit=None)
            assert tab.remaining_limit == Decimal("200.00")

        def it_subtracts_pending_balance():
            BillingSettingsFactory(default_tab_limit=Decimal("200.00"))
            tab = TabFactory(tab_limit=None)
            TabEntryFactory(tab=tab, amount=Decimal("75.00"))
            assert tab.remaining_limit == Decimal("125.00")

    def describe_lock():
        def it_locks_the_tab():
            tab = TabFactory(is_locked=False)
            tab.lock("Payment failed")
            tab.refresh_from_db()
            assert tab.is_locked is True
            assert tab.locked_reason == "Payment failed"

    def describe_unlock():
        def it_unlocks_the_tab():
            tab = TabFactory(is_locked=True, locked_reason="Payment failed")
            tab.unlock()
            tab.refresh_from_db()
            assert tab.is_locked is False
            assert tab.locked_reason == ""

    def describe_get_or_create_stripe_customer():
        def it_returns_existing_customer_id_without_calling_stripe():
            from unittest.mock import patch

            tab = TabFactory(stripe_customer_id="cus_existing")
            with patch("billing.stripe_utils.create_customer") as mock_create:
                result = tab.get_or_create_stripe_customer()
            assert result == "cus_existing"
            mock_create.assert_not_called()

        def it_creates_customer_when_none_exists():
            from unittest.mock import patch

            tab = TabFactory(stripe_customer_id="")
            with patch("billing.stripe_utils.create_customer", return_value="cus_new") as mock_create:
                result = tab.get_or_create_stripe_customer()
            assert result == "cus_new"
            mock_create.assert_called_once()
            tab.refresh_from_db()
            assert tab.stripe_customer_id == "cus_new"

    def describe_set_payment_method():
        def it_attaches_and_persists_payment_method_when_customer_exists():
            from unittest.mock import patch

            tab = TabFactory(stripe_customer_id="cus_123", stripe_payment_method_id="")
            pm_details = {"id": "pm_new", "last4": "4242", "brand": "visa"}
            with (
                patch("billing.stripe_utils.attach_payment_method") as mock_attach,
                patch("billing.stripe_utils.retrieve_payment_method", return_value=pm_details),
            ):
                tab.set_payment_method("pm_new")
            mock_attach.assert_called_once_with(customer_id="cus_123", payment_method_id="pm_new")
            tab.refresh_from_db()
            assert tab.stripe_payment_method_id == "pm_new"
            assert tab.payment_method_last4 == "4242"
            assert tab.payment_method_brand == "visa"

        def it_skips_attach_when_no_stripe_customer():
            from unittest.mock import patch

            tab = TabFactory(stripe_customer_id="", stripe_payment_method_id="")
            pm_details = {"id": "pm_noattach", "last4": "9999", "brand": "mastercard"}
            with (
                patch("billing.stripe_utils.attach_payment_method") as mock_attach,
                patch("billing.stripe_utils.retrieve_payment_method", return_value=pm_details),
            ):
                tab.set_payment_method("pm_noattach")
            mock_attach.assert_not_called()
            tab.refresh_from_db()
            assert tab.stripe_payment_method_id == "pm_noattach"

    def describe_clear_payment_method():
        def it_detaches_and_clears_payment_fields():
            from unittest.mock import patch

            tab = TabFactory(
                stripe_payment_method_id="pm_old",
                payment_method_last4="1234",
                payment_method_brand="visa",
            )
            with patch("billing.stripe_utils.detach_payment_method") as mock_detach:
                tab.clear_payment_method()
            mock_detach.assert_called_once_with(payment_method_id="pm_old")
            tab.refresh_from_db()
            assert tab.stripe_payment_method_id == ""
            assert tab.payment_method_last4 == ""
            assert tab.payment_method_brand == ""

        def it_does_nothing_when_no_payment_method():
            from unittest.mock import patch

            tab = TabFactory(stripe_payment_method_id="")
            with patch("billing.stripe_utils.detach_payment_method") as mock_detach:
                tab.clear_payment_method()
            mock_detach.assert_not_called()


def describe_Tab_add_entry_with_splits():
    def it_pulls_splits_from_the_product_when_no_splits_kwarg(db):
        from billing.models import TabEntrySplit
        from tests.billing.factories import ProductFactory

        product = ProductFactory()  # default 20% admin / 80% owning guild
        tab = TabFactory()
        entry = tab.add_entry(description="bag of clay", amount=Decimal("10.00"), product=product)
        assert entry.splits.count() == 2
        admin = entry.splits.get(recipient_type=TabEntrySplit.RecipientType.ADMIN)
        guild_split = entry.splits.get(recipient_type=TabEntrySplit.RecipientType.GUILD)
        assert admin.amount == Decimal("2.00")
        assert guild_split.amount == Decimal("8.00")
        assert guild_split.guild_id == product.guild_id

    def it_uses_explicit_splits_kwarg_when_supplied(db):
        from billing.models import TabEntrySplit

        tab = TabFactory()
        entry = tab.add_entry(
            description="custom",
            amount=Decimal("10.00"),
            splits=[
                {"recipient_type": "admin", "guild": None, "percent": Decimal("100")},
            ],
        )
        assert entry.splits.count() == 1
        only = entry.splits.first()
        assert only.recipient_type == TabEntrySplit.RecipientType.ADMIN
        assert only.amount == Decimal("10.00")

    def it_raises_when_neither_product_nor_splits_supplied(db):
        tab = TabFactory()
        with pytest.raises(ValueError):
            tab.add_entry(description="x", amount=Decimal("5.00"))

    def it_snapshots_split_state_at_creation_time(db):
        # If the product's splits are edited later, the entry's snapshot should not change.
        from billing.models import ProductRevenueSplit
        from tests.billing.factories import ProductFactory

        product = ProductFactory()
        tab = TabFactory()
        entry = tab.add_entry(description="x", amount=Decimal("10.00"), product=product)

        product.splits.all().delete()
        ProductRevenueSplit.objects.create(
            product=product,
            recipient_type=ProductRevenueSplit.RecipientType.ADMIN,
            guild=None,
            percent=Decimal("100"),
        )

        entry.refresh_from_db()
        assert entry.splits.count() == 2  # unchanged

    def it_raises_TabLockedError_when_tab_is_locked(db):
        from billing.exceptions import TabLockedError
        from tests.billing.factories import ProductFactory

        product = ProductFactory()
        tab = TabFactory(is_locked=True, locked_reason="Payment failed")
        with pytest.raises(TabLockedError, match="Payment failed"):
            tab.add_entry(description="x", amount=Decimal("5.00"), product=product)

    def it_raises_TabLimitExceededError_when_entry_would_exceed_limit(db):
        from billing.exceptions import TabLimitExceededError
        from tests.billing.factories import ProductFactory

        product = ProductFactory(price=Decimal("10.00"))
        tab = TabFactory(tab_limit=Decimal("5.00"))
        with pytest.raises(TabLimitExceededError, match="exceed tab limit"):
            tab.add_entry(description="x", amount=Decimal("10.00"), product=product)

    def it_logs_tab_entry_added_activity(db):
        from core.models import SiteActivity
        from tests.billing.factories import ProductFactory, UserFactory

        product = ProductFactory()
        tab = TabFactory()
        actor = UserFactory()
        entry = tab.add_entry(description="studio time", amount=Decimal("15.00"), product=product, added_by=actor)

        assert SiteActivity.objects.filter(kind=SiteActivity.Kind.TAB_ENTRY_ADDED).exists()
        activity = SiteActivity.objects.get(kind=SiteActivity.Kind.TAB_ENTRY_ADDED)
        assert activity.target_id == entry.pk
        assert activity.actor == actor

    def it_logs_tab_entry_added_without_actor(db):
        from core.models import SiteActivity
        from tests.billing.factories import ProductFactory

        product = ProductFactory()
        tab = TabFactory()
        tab.add_entry(description="self-service", amount=Decimal("5.00"), product=product)

        assert SiteActivity.objects.filter(kind=SiteActivity.Kind.TAB_ENTRY_ADDED).exists()
        activity = SiteActivity.objects.get(kind=SiteActivity.Kind.TAB_ENTRY_ADDED)
        assert activity.actor is None
