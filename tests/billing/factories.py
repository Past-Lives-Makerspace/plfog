from __future__ import annotations

from decimal import Decimal

import factory
from django.contrib.auth import get_user_model

from billing.models import (
    BillingSettings,
    PaymentRefund,
    Product,
    ProductRevenueSplit,
    Tab,
    TabCharge,
    TabEntry,
    TabEntrySplit,
)
from tests.membership.factories import GuildFactory, MemberFactory

User = get_user_model()


class UserFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = User

    username = factory.Sequence(lambda n: f"user{n}")
    email = factory.LazyAttribute(lambda o: f"{o.username}@example.com")


class BillingSettingsFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = BillingSettings
        django_get_or_create = ("pk",)

    pk = 1
    charge_frequency = BillingSettings.ChargeFrequency.MONTHLY
    default_tab_limit = Decimal("200.00")
    default_admin_percent = Decimal("20.00")


class ProductFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Product
        # ``with_default_splits`` only creates related rows; it never mutates the
        # Product, so factory-boy's extraneous post-hook save is not needed.
        skip_postgeneration_save = True

    name = factory.Sequence(lambda n: f"Product {n}")
    price = Decimal("10.00")
    guild = factory.SubFactory(GuildFactory)

    @factory.post_generation
    def with_default_splits(self, create, extracted, **kwargs):
        """Auto-attach 20% Admin / 80% owning-guild splits unless caller opts out."""
        if not create:
            return
        if extracted is False:
            return  # caller passed `with_default_splits=False`
        if self.splits.exists():
            return  # caller already added splits manually
        ProductRevenueSplit.objects.create(
            product=self,
            recipient_type=ProductRevenueSplit.RecipientType.ADMIN,
            guild=None,
            percent=Decimal("20"),
        )
        ProductRevenueSplit.objects.create(
            product=self,
            recipient_type=ProductRevenueSplit.RecipientType.GUILD,
            guild=self.guild,
            percent=Decimal("80"),
        )


class ProductRevenueSplitFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = ProductRevenueSplit

    product = factory.SubFactory(ProductFactory, with_default_splits=False)
    recipient_type = ProductRevenueSplit.RecipientType.GUILD
    guild = factory.SubFactory(GuildFactory)
    percent = Decimal("100")


class TabFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Tab

    member = factory.SubFactory(MemberFactory)
    stripe_payment_method_id = "pm_test_1234"
    payment_method_last4 = "4242"
    payment_method_brand = "visa"


class TabEntryFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = TabEntry

    tab = factory.SubFactory(TabFactory)
    product = None
    description = factory.Faker("sentence", nb_words=4)
    amount = Decimal("25.00")
    entry_type = TabEntry.EntryType.MANUAL


class TabEntrySplitFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = TabEntrySplit

    entry = factory.SubFactory(TabEntryFactory)
    recipient_type = TabEntrySplit.RecipientType.GUILD
    guild = factory.SubFactory(GuildFactory)
    percent = Decimal("100")
    amount = Decimal("10.00")


class TabChargeFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = TabCharge

    tab = factory.SubFactory(TabFactory)
    amount = Decimal("100.00")
    status = TabCharge.Status.PENDING


class PaymentRefundFactory(factory.django.DjangoModelFactory):
    """A refund ledger row, defaulting to the registration side (per the engine contract)."""

    class Meta:
        model = PaymentRefund

    registration = factory.SubFactory("classes.factories.RegistrationFactory")
    stripe_refund_id = factory.Sequence(lambda n: f"re_test_{n}")
    amount_cents = 2500
    status = PaymentRefund.Status.PENDING
    source = PaymentRefund.Source.IN_APP
