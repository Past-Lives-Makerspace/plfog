from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import factory
from django.contrib.contenttypes.models import ContentType
from django.utils import timezone

from membership.models import (
    Buyable,
    Guild,
    GuildVote,
    GuildWishlistItem,
    Lease,
    Member,
    MembershipPlan,
    Order,
    Space,
    VotingSession,
)


class MembershipPlanFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = MembershipPlan

    name = factory.Sequence(lambda n: f"Plan {n}")
    monthly_price = Decimal("150.00")


class MemberFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Member

    membership_plan = factory.SubFactory(MembershipPlanFactory)
    full_legal_name = factory.Faker("name")
    email = factory.Sequence(lambda n: f"member{n}@example.com")
    status = Member.Status.ACTIVE
    join_date = date(2024, 1, 1)


class SpaceFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Space

    space_id = factory.Sequence(lambda n: f"S-{n:03d}")
    space_type = Space.SpaceType.STUDIO
    status = Space.Status.AVAILABLE
    sublet_guild = None


class GuildFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Guild

    name = factory.Sequence(lambda n: f"Guild {n}")


class VotingSessionFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = VotingSession

    name = factory.Sequence(lambda n: f"Session {n}")
    open_date = factory.LazyFunction(lambda: timezone.now().date())
    close_date = factory.LazyFunction(lambda: timezone.now().date() + timedelta(days=7))
    status = VotingSession.Status.DRAFT


class GuildVoteFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = GuildVote

    session = factory.SubFactory(VotingSessionFactory)
    member = factory.SubFactory(MemberFactory)
    guild = factory.SubFactory(GuildFactory)
    member_airtable_id = factory.Sequence(lambda n: f"rec{n:015d}")
    member_name = factory.LazyAttribute(lambda o: o.member.full_legal_name if o.member else "Test")
    priority = 1


class LeaseFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Lease
        exclude = ["tenant_obj"]

    tenant_obj = factory.SubFactory(MemberFactory)
    content_type = factory.LazyAttribute(lambda o: ContentType.objects.get_for_model(o.tenant_obj))
    object_id = factory.LazyAttribute(lambda o: o.tenant_obj.pk)
    space = factory.SubFactory(SpaceFactory)
    lease_type = Lease.LeaseType.MONTH_TO_MONTH
    base_price = Decimal("200.00")
    monthly_rent = Decimal("200.00")
    start_date = factory.LazyFunction(lambda: timezone.now().date() - timedelta(days=30))


class GuildWishlistItemFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = GuildWishlistItem

    guild = factory.SubFactory(GuildFactory)
    name = factory.Sequence(lambda n: f"Wishlist Item {n}")


class BuyableFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Buyable

    guild = factory.SubFactory(GuildFactory)
    name = factory.Sequence(lambda n: f"Buyable {n}")
    unit_price = Decimal("25.00")


class OrderFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Order

    buyable = factory.SubFactory(BuyableFactory)
    amount = 2500
    status = Order.Status.PENDING
