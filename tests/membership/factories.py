from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import factory
from django.contrib.contenttypes.models import ContentType
from django.utils import timezone

from membership.models import (
    FundingSnapshot,
    Guild,
    Lease,
    Member,
    MembershipPlan,
    Space,
    VotePreference,
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
    is_active = True


class VotePreferenceFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = VotePreference

    member = factory.SubFactory(MemberFactory)
    guild_1st = factory.SubFactory(GuildFactory)
    guild_2nd = factory.SubFactory(GuildFactory)
    guild_3rd = factory.SubFactory(GuildFactory)


class FundingSnapshotFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = FundingSnapshot

    cycle_label = factory.Sequence(lambda n: f"Month {n} 2026")
    contributor_count = 10
    funding_pool = Decimal("100.00")
    results = factory.LazyFunction(dict)


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
