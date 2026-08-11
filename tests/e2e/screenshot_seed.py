"""Shared demo-data seeding for the screenshot capture specs.

Factored out of ``screenshots_spec.py`` so both the CMS copy-review capture and
the help-center screenshot capture (``help_screenshots_spec.py``) build on the
same representative dataset. Not a spec file — pytest never collects it.
"""

from __future__ import annotations

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.utils import timezone

from classes.factories import (
    CategoryFactory,
    ClassOfferingFactory,
    ClassSessionFactory,
    DiscountCodeFactory,
    RegistrationFactory,
)
from classes.models import ClassOffering, Registration, RegistrationQuestion
from membership.models import Member, MembershipPlan

ADMIN_EMAIL = "studio.lead@example.com"


def _seed() -> dict[str, object]:
    """Create a representative slice of CMS data and return the handles we need.

    One member is the admin *and* the instructor of the seeded classes, so a
    single login reaches every dashboard. Pages are populated (a published
    class with sign-ups, one awaiting approval, a category, discount codes, a
    registration question) so the copy is reviewed against realistic content
    rather than empty states.
    """
    plan, _ = MembershipPlan.objects.get_or_create(name="Standard", defaults={"monthly_price": "50.00"})

    user, _ = get_user_model().objects.get_or_create(username=ADMIN_EMAIL, defaults={"email": ADMIN_EMAIL})
    member, _ = Member.objects.update_or_create(
        user=user,
        defaults={
            "full_legal_name": "Robin Maker",
            "membership_plan": plan,
            "status": Member.Status.ACTIVE,
            "fog_role": Member.FogRole.ADMIN,
            "instructor_slug": "robin-maker",
            "about_me": "Longtime metalsmith and studio lead. Teaches casting and fabrication.",
        },
    )

    category = CategoryFactory(name="Metalworking", slug="metalworking")

    published = ClassOfferingFactory(
        title="Intro to Lost-Wax Casting",
        slug="intro-to-lost-wax-casting",
        category=category,
        instructor=member,
        status=ClassOffering.Status.PUBLISHED,
        is_private=False,
        price_cents=4500,
        capacity=8,
        description=(
            "Carve a model in wax, invest it, burn it out, and pour molten bronze to cast your own "
            "small sculpture. No experience needed — all tools and materials provided."
        ),
    )
    base = timezone.now() + timedelta(days=10)
    ClassSessionFactory(class_offering=published, starts_at=base, ends_at=base + timedelta(hours=3))

    # A class awaiting approval, so the admin overview / classes list show a
    # non-published state with its own copy.
    ClassOfferingFactory(
        title="Beginner Forge Welding",
        slug="beginner-forge-welding",
        category=category,
        instructor=member,
        status=ClassOffering.Status.PENDING,
        is_private=False,
        price_cents=6000,
        description="Heat, hammer, and join steel at the forge. Safety gear and steel stock included.",
    )

    # A spread of registration states populates the registration list pages.
    RegistrationFactory(
        class_offering=published, first_name="Avery", last_name="Lim", status=Registration.Status.CONFIRMED
    )
    RegistrationFactory(
        class_offering=published, first_name="Sam", last_name="Cole", status=Registration.Status.CONFIRMED
    )
    waitlisted = RegistrationFactory(
        class_offering=published, first_name="Dana", last_name="Reyes", status=Registration.Status.WAITLISTED
    )

    # One global code and one class-scoped code (the latter is editable from the
    # teaching portal because it belongs to this instructor's class).
    global_code = DiscountCodeFactory(code="WELCOME10", description="10% off any class", discount_pct=10)
    class_code = DiscountCodeFactory(
        code="CASTING20", description="20% off lost-wax casting", discount_pct=20, class_offering=published
    )

    question = RegistrationQuestion.objects.create(
        prompt="Do you have any allergies or access needs we should know about?",
        is_active=True,
    )

    confirmed = published.registrations.filter(status=Registration.Status.CONFIRMED).first()

    return {
        "instructor": member,
        "category": category,
        "published": published,
        "registration": confirmed or waitlisted,
        "global_code": global_code,
        "class_code": class_code,
        "question": question,
    }


def _seed_member_hub(member: Member) -> None:
    """Populate the member-hub feature pages so each screenshot shows a good state.

    Guilds (for the directory + My Guilds toggles), two official memberships for the
    signed-in member, a published announcement (for the home dashboard), a filled-in
    Space & Org Info page, a few notifications (for the Notifications page), a published
    upcoming community event (for the Community Calendar), and a few more members for the
    directory. This seeding is the bulk of the capture effort and grows with each
    FeaturePage in the registry.
    """
    from core.models import Notification
    from tests.membership.factories import (
        CommunityEventFactory,
        GuildAnnouncementFactory,
        GuildFactory,
        GuildFAQItemFactory,
        GuildMembershipFactory,
        MemberFactory,
        OrgInfoPageFactory,
    )

    guilds = [
        GuildFactory(name="Ceramics Guild", about="Wheel-throwing, glazing, and kiln firings — all skill levels."),
        GuildFactory(name="Textiles Guild", about="Weaving, dyeing, and sewing in the fiber studio."),
        GuildFactory(name="Woodshop Guild", about="Hand tools, the lathe, and safe machine time."),
    ]
    for guild in guilds[:2]:
        GuildMembershipFactory(guild=guild, member=member)
    GuildAnnouncementFactory(
        guild=guilds[0],
        title="Spring glaze restock is in",
        body="New celadons and a fresh batch of clay just landed. Come make something.",
    )
    # A couple of FAQ items so the guild detail page (the "guild pages" feature shot) shows a real FAQ.
    GuildFAQItemFactory(
        guild=guilds[0],
        question="Do I need to bring my own tools?",
        answer="Nope — the studio stocks wheels, tools, and glazes. Just bring an apron.",
    )
    GuildFAQItemFactory(
        guild=guilds[0],
        question="How do I get oriented?",
        answer="Book a guild orientation from the guild page and a lead will show you the ropes.",
    )
    OrgInfoPageFactory(
        intro="Everything you need to know about how our space and our guilds work.",
        parking="Free lot on the north side; street parking is open after 6pm.",
        who_to_contact="Front desk for access, your guild lead for studio-specific questions.",
        code_of_conduct="Be kind, clean your station, and ask before borrowing tools.",
    )
    # Notifications page (0.21.3): a couple bell rows — one unread (highlighted), one read.
    if member.user is not None:
        Notification.objects.create(
            user=member.user,
            trigger="class_published",
            title="New class: Intro to Lost-Wax Casting",
            body="A new class just went live in the Metalworking guild — grab a seat.",
            url="/classes/",
        )
        Notification.objects.create(
            user=member.user,
            trigger="guild_announcement",
            title="Ceramics Guild: Spring glaze restock is in",
            body="New celadons and a fresh batch of clay just landed.",
            url="/guilds/",
            read_at=timezone.now(),
        )
    # Community Calendar (0.21.1): a published, upcoming site-wide event for the Events tab.
    CommunityEventFactory(
        community=True,
        title="Open Studio Night",
        starts_at=timezone.now() + timedelta(days=5),
    )
    MemberFactory.create_batch(4)
