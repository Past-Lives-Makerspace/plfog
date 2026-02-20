from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import factory
from django.utils import timezone

from membership.models import Lease, Member, MembershipPlan, Space


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


class LeaseFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Lease

    member = factory.SubFactory(MemberFactory)
    space = factory.SubFactory(SpaceFactory)
    lease_type = Lease.LeaseType.MONTH_TO_MONTH
    base_price = Decimal("200.00")
    monthly_rent = Decimal("200.00")
    start_date = factory.LazyFunction(lambda: timezone.now().date() - timedelta(days=30))
