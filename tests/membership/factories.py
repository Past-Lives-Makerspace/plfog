from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import factory
from django.contrib.auth.models import User
from django.contrib.contenttypes.models import ContentType
from django.db.models.signals import post_save
from django.utils import timezone
from factory.django import mute_signals

from membership.models import (
    FundingSnapshot,
    Guild,
    GuildAnnouncement,
    GuildFAQItem,
    GuildLink,
    GuildMembership,
    Lease,
    Member,
    MemberEmail,
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
    _pre_signup_email = factory.Sequence(lambda n: f"member{n}@example.com")
    status = Member.Status.ACTIVE
    join_date = date(2024, 1, 1)


class MemberEmailFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = MemberEmail

    member = factory.SubFactory(MemberFactory)
    email = factory.Sequence(lambda n: f"alias{n}@example.com")


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


class GuildFAQItemFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = GuildFAQItem

    guild = factory.SubFactory(GuildFactory)
    question = factory.Sequence(lambda n: f"Question {n}?")
    answer = "An answer."


class GuildLinkFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = GuildLink

    guild = factory.SubFactory(GuildFactory)
    label = factory.Sequence(lambda n: f"Link {n}")
    url = "https://example.com"


class GuildAnnouncementFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = GuildAnnouncement

    guild = factory.SubFactory(GuildFactory)
    title = factory.Sequence(lambda n: f"Announcement {n}")
    body = "Body text."


class GuildMembershipFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = GuildMembership

    guild = factory.SubFactory(GuildFactory)
    member = factory.SubFactory(MemberFactory)


class VotePreferenceFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = VotePreference

    member = factory.SubFactory(MemberFactory)
    guild_1st = factory.SubFactory(GuildFactory)
    guild_2nd = factory.SubFactory(GuildFactory)
    guild_3rd = factory.SubFactory(GuildFactory)

    @factory.post_generation
    def signed_up(obj, create, extracted, **kwargs):  # noqa: N805
        """Ensure the voting member has a linked User by default.

        Votes in production only exist for signed-up members; the live-standings
        and snapshot queries now filter to those. Existing tests didn't create
        Users, so we auto-link one here unless ``signed_up=False`` is passed to
        exercise the exclusion path.
        """
        if not create or extracted is False or obj.member.user_id is not None:
            return
        with mute_signals(post_save):
            user = User.objects.create_user(username=f"voter_{obj.member.pk}")
        obj.member.user = user
        obj.member.save(update_fields=["user"])


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
