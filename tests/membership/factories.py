from __future__ import annotations

from datetime import date, time, timedelta
from decimal import Decimal

import factory
from django.contrib.auth.models import User
from django.contrib.contenttypes.models import ContentType
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db.models.signals import post_save
from django.utils import timezone
from factory.django import mute_signals

from membership.models import (
    CommunityEvent,
    FundingSnapshot,
    Guild,
    GuildAnnouncement,
    GuildFAQItem,
    GuildLink,
    GuildMeetingNote,
    GuildMeetingNoteAttachment,
    GuildMembership,
    GuildOrientationSettings,
    GuildStaffMembership,
    Lease,
    Member,
    MemberEmail,
    MembershipPlan,
    MemberSkill,
    OrgFAQItem,
    OrgInfoPage,
    OrgLink,
    OrientationAvailability,
    OrientationBooking,
    OrientationSlot,
    Skill,
    SkillCategory,
    Space,
    VotePreference,
)


class MembershipPlanFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = MembershipPlan

    name = factory.Sequence(lambda n: f"Plan {n}")
    monthly_price = Decimal("150.00")


@mute_signals(post_save)
class MemberFactory(factory.django.DjangoModelFactory):
    # post_save is muted so the going-forward ``auto_provision_member_user`` signal
    # does NOT auto-provision a User for every factory-built ACTIVE member — the
    # default factory member stays unlinked (``user=None``), matching the behaviour
    # tests have always relied on. Specs that exercise auto-provisioning create the
    # member without this mute (via ``Member.objects.create``).
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
        django_get_or_create = ("name",)

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


class OrgInfoPageFactory(factory.django.DjangoModelFactory):
    """The Space & Org Info page is a pk=1 singleton — always returns/updates the one row."""

    class Meta:
        model = OrgInfoPage

    @classmethod
    def _create(cls, model_class, *args, **kwargs):
        obj = model_class.load()
        for field, value in kwargs.items():
            setattr(obj, field, value)
        obj.save()
        return obj


class OrgFAQItemFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = OrgFAQItem

    page = factory.SubFactory(OrgInfoPageFactory)
    question = factory.Sequence(lambda n: f"Org question {n}?")
    answer = "An answer."


class OrgLinkFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = OrgLink

    page = factory.SubFactory(OrgInfoPageFactory)
    label = factory.Sequence(lambda n: f"Org link {n}")
    url = "https://example.com"


class GuildAnnouncementFactory(factory.django.DjangoModelFactory):
    """A guild announcement. Defaults to PUBLISHED (a lead's direct post).

    Use the ``pending`` / ``changes_requested`` / ``declined`` traits for member-proposal
    states, and pass ``submitted_by=<user>`` for the proposer.
    """

    class Meta:
        model = GuildAnnouncement

    guild = factory.SubFactory(GuildFactory)
    title = factory.Sequence(lambda n: f"Announcement {n}")
    body = "Body text."

    class Params:
        pending = factory.Trait(moderation_state=GuildAnnouncement.ModerationState.PENDING)
        changes_requested = factory.Trait(moderation_state=GuildAnnouncement.ModerationState.CHANGES_REQUESTED)
        declined = factory.Trait(moderation_state=GuildAnnouncement.ModerationState.DECLINED)


class GuildMeetingNoteFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = GuildMeetingNote

    guild = factory.SubFactory(GuildFactory)
    meeting_date = date(2026, 6, 1)
    title = factory.Sequence(lambda n: f"Meeting {n}")
    body = ""


class GuildMeetingNoteAttachmentFactory(factory.django.DjangoModelFactory):
    """Defaults to a link attachment. Use the ``file`` trait for an uploaded file.

    Exactly one of file / url is ever set — the model's XOR constraint requires it.
    """

    class Meta:
        model = GuildMeetingNoteAttachment

    note = factory.SubFactory(GuildMeetingNoteFactory)
    label = factory.Sequence(lambda n: f"Attachment {n}")
    url = "https://docs.example.com/agenda"
    file = ""

    class Params:
        file_doc = factory.Trait(
            url="",
            file=factory.LazyFunction(lambda: SimpleUploadedFile("agenda.pdf", b"%PDF-1.4 test", "application/pdf")),
        )


class UserFactory(factory.django.DjangoModelFactory):
    """A bare auth User — for ``submitted_by`` / ``reviewed_by`` on proposals."""

    class Meta:
        model = User

    username = factory.Sequence(lambda n: f"eventuser{n}")
    email = factory.LazyAttribute(lambda o: f"{o.username}@example.com")


class CommunityEventFactory(factory.django.DjangoModelFactory):
    """A FOG-native community event. Defaults to a guild meeting (guild set).

    Use the ``community`` / ``lead_meeting`` traits for the site-wide variants (which
    null the guild to satisfy the type↔scope constraint); the ``pending`` / ``declined``
    traits for member-proposal moderation states.
    """

    class Meta:
        model = CommunityEvent

    guild = factory.SubFactory(GuildFactory)
    event_type = CommunityEvent.EventType.GUILD_MEETING
    title = factory.Sequence(lambda n: f"Event {n}")
    starts_at = factory.LazyFunction(lambda: timezone.now() + timedelta(days=7))
    ends_at = factory.LazyAttribute(lambda o: o.starts_at + timedelta(hours=2))
    recurrence = CommunityEvent.Recurrence.NONE

    class Params:
        guild_meeting = factory.Trait(event_type=CommunityEvent.EventType.GUILD_MEETING)
        community = factory.Trait(event_type=CommunityEvent.EventType.COMMUNITY, guild=None)
        lead_meeting = factory.Trait(event_type=CommunityEvent.EventType.LEAD_MEETING, guild=None)
        pending = factory.Trait(
            moderation_state=CommunityEvent.ModerationState.PENDING,
            submitted_by=factory.SubFactory(UserFactory),
        )
        declined = factory.Trait(
            moderation_state=CommunityEvent.ModerationState.DECLINED,
            submitted_by=factory.SubFactory(UserFactory),
            reviewed_by=factory.SubFactory(UserFactory),
            review_notes="Not a fit right now.",
        )


class GuildMembershipFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = GuildMembership

    guild = factory.SubFactory(GuildFactory)
    member = factory.SubFactory(MemberFactory)


class GuildStaffMembershipFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = GuildStaffMembership

    guild = factory.SubFactory(GuildFactory)
    member = factory.SubFactory(MemberFactory)
    role = GuildStaffMembership.Role.CO_LEAD

    class Params:
        # Pass ``custom=True`` for a free-text-title entry (override ``custom_title`` to set the text);
        # the default stays a preset role so existing callers are unaffected.
        custom = factory.Trait(role="", custom_title="Studio Technician")


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


class GuildOrientationSettingsFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = GuildOrientationSettings
        django_get_or_create = ("guild",)

    guild = factory.SubFactory(GuildFactory)
    is_enabled = True


class OrientationAvailabilityFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = OrientationAvailability

    guild = factory.SubFactory(GuildFactory)
    weekday = OrientationAvailability.Weekday.TUESDAY
    start_time = factory.LazyFunction(lambda: time(18, 0))
    end_time = factory.LazyFunction(lambda: time(19, 0))
    seats = 4


class OrientationSlotFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = OrientationSlot
        skip_postgeneration_save = True

    guild = factory.SubFactory(GuildFactory)
    starts_at = factory.LazyFunction(lambda: timezone.now() + timedelta(days=2))
    ends_at = factory.LazyFunction(lambda: timezone.now() + timedelta(days=2, hours=1))
    seats = 4

    @factory.post_generation
    def enabled_settings(obj, create, extracted, **kwargs):  # noqa: N805
        """Give the slot's guild enabled orientation settings so it's bookable by default.

        Pass ``enabled_settings=False`` to skip (e.g. to exercise the
        not-configured / disabled paths).
        """
        if not create or extracted is False:
            return
        GuildOrientationSettings.objects.get_or_create(guild=obj.guild, defaults={"is_enabled": True})


class OrientationBookingFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = OrientationBooking

    slot = factory.SubFactory(OrientationSlotFactory)
    member = factory.SubFactory(MemberFactory)
    # guild is denormalized from the slot in OrientationBooking.save().


class SkillCategoryFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = SkillCategory
        django_get_or_create = ("name",)

    name = factory.Sequence(lambda n: f"Category {n}")
    slug = factory.Sequence(lambda n: f"category-{n}")


class SkillFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Skill
        django_get_or_create = ("name",)

    name = factory.Sequence(lambda n: f"Skill {n}")
    slug = factory.Sequence(lambda n: f"skill-{n}")
    category = factory.SubFactory(SkillCategoryFactory)
    status = Skill.Status.APPROVED


class MemberSkillFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = MemberSkill

    member = factory.SubFactory(MemberFactory)
    skill = factory.SubFactory(SkillFactory)


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
