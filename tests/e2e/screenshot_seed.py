"""Shared demo-data seeding for the screenshot capture specs.

Factored out of ``screenshots_spec.py`` so both the CMS copy-review capture and
the help-center screenshot capture (``help_screenshots_spec.py``) build on the
same representative dataset. Not a spec file — pytest never collects it.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from django.contrib.auth import get_user_model
from django.utils import timezone

from classes.factories import (
    CategoryFactory,
    ClassApprovalFactory,
    ClassOfferingFactory,
    ClassSessionFactory,
    DiscountCodeFactory,
    RegistrationFactory,
)
from classes.models import Category, ClassApproval, ClassOffering, Registration, RegistrationQuestion
from membership.models import Member, MembershipPlan

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractBaseUser

ADMIN_EMAIL = "studio.lead@example.com"
GUILD_LEAD_EMAIL = "guild.lead@example.com"


def _seed() -> dict[str, object]:
    """Create a representative slice of CMS data and return the handles we need.

    One member is the admin *and* the instructor of the seeded classes, so a
    single login reaches every dashboard. Pages are populated (a published
    class with sign-ups, three classes in review — one per state the two review
    lanes can be in — a category, discount codes, a registration question) so the
    copy is reviewed against realistic content rather than empty states.
    """
    plan, _ = MembershipPlan.objects.get_or_create(name="Standard", defaults={"monthly_price": "50.00"})

    # First and last name matter: the review strip names whoever decided a lane, and with
    # a nameless user it falls back to printing their email address.
    user, _ = get_user_model().objects.get_or_create(
        username=ADMIN_EMAIL, defaults={"email": ADMIN_EMAIL, "first_name": "Robin", "last_name": "Maker"}
    )
    member, _ = Member.objects.update_or_create(
        user=user,
        defaults={
            "full_legal_name": "Robin Maker",
            "membership_plan": plan,
            "status": Member.Status.ACTIVE,
            "fog_role": Member.FogRole.ADMIN,
            "instructor_slug": "robin-maker",
            # The teach portal is gated on an admin granting teaching; this stamp is that grant.
            "instructor_oriented_at": timezone.now(),
            "about_me": "Longtime metalsmith and studio lead. Teaches casting and fabrication.",
        },
    )

    category = CategoryFactory(name="Metalworking", slug="metalworking")
    lead = _seed_guild_lead(category, plan)

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

    # The strip on a live class reads its approval rows, so give the published one the two
    # decided lanes a class actually goes live with. Without rows both lanes still tick, but
    # with no "Approved by" detail behind them.
    ClassApprovalFactory(
        class_offering=published,
        role=ClassApproval.Role.GUILD_LEAD,
        decision=ClassApproval.Decision.APPROVED,
        decided_by=lead.user,
    )
    ClassApprovalFactory(
        class_offering=published,
        role=ClassApproval.Role.ADMIN,
        decision=ClassApproval.Decision.APPROVED,
        decided_by=user,
    )

    in_review = _seed_classes_in_review(category, member, admin_user=user, lead=lead)

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
        "guild_lead": lead,
        "category": category,
        "published": published,
        "registration": confirmed or waitlisted,
        "global_code": global_code,
        "class_code": class_code,
        "question": question,
        **in_review,
    }


def _seed_guild_lead(category: Category, plan: MembershipPlan) -> Member:
    """Give the seeded category a guild with a lead, and return that lead.

    A class opens a guild-lead review lane only when its category links a guild that has a
    lead (``ClassOffering.required_review_roles``). Without one, every seeded class reviewed
    on the admin lane alone and the pipeline strip drew a single line, so no screenshot could
    show the two lanes at all. The lead is a real member with a user account because the
    strip names whoever decided a lane.
    """
    from tests.membership.factories import GuildFactory

    user, _ = get_user_model().objects.get_or_create(
        username=GUILD_LEAD_EMAIL,
        defaults={"email": GUILD_LEAD_EMAIL, "first_name": "Wren", "last_name": "Alvarez"},
    )
    lead, _ = Member.objects.update_or_create(
        user=user,
        defaults={
            "full_legal_name": "Wren Alvarez",
            "membership_plan": plan,
            "status": Member.Status.ACTIVE,
            "about_me": "Leads the Metalworking Guild. Forge, foundry, and the fabrication bay.",
        },
    )
    guild = GuildFactory(
        name="Metalworking Guild",
        about="Forge, foundry, and the fabrication bay.",
        guild_lead=lead,
    )
    category.guild = guild
    category.save(update_fields=["guild"])
    return lead


def _pending_class(
    category: Category,
    instructor: Member,
    *,
    title: str,
    slug: str,
    price_cents: int,
    description: str,
    days_out: int,
) -> ClassOffering:
    """One PENDING class with a real date on it, ready for its approval rows."""
    offering = ClassOfferingFactory(
        title=title,
        slug=slug,
        category=category,
        instructor=instructor,
        status=ClassOffering.Status.PENDING,
        is_private=False,
        price_cents=price_cents,
        description=description,
    )
    start = timezone.now() + timedelta(days=days_out)
    ClassSessionFactory(class_offering=offering, starts_at=start, ends_at=start + timedelta(hours=3))
    return offering


def _seed_classes_in_review(
    category: Category, instructor: Member, *, admin_user: "AbstractBaseUser", lead: Member
) -> dict[str, ClassOffering]:
    """One PENDING class per state the two review lanes can be in.

    Both lanes open together at submit and neither waits for the other, so a class in review
    is in one of three shapes: nobody has answered yet, the admin said yes and held it for the
    guild lead's room check, or the guild lead cleared the room and the admin has yet to look.
    Seeding all three is what lets the admin overview, the classes list and a class screen
    show a difference at all — the single row-less pending class this replaced rendered
    identically before and after the two-lane change.

    Rows are written directly rather than through ``submit_for_review`` so seeding sends no
    review email and logs no submission activity.
    """
    both = _pending_class(
        category,
        instructor,
        title="Beginner Forge Welding",
        slug="beginner-forge-welding",
        price_cents=6000,
        description="Heat, hammer, and join steel at the forge. Safety gear and steel stock included.",
        days_out=21,
    )
    ClassApprovalFactory(class_offering=both, role=ClassApproval.Role.GUILD_LEAD)
    ClassApprovalFactory(class_offering=both, role=ClassApproval.Role.ADMIN)

    held = _pending_class(
        category,
        instructor,
        title="Bronze Sand Casting",
        slug="bronze-sand-casting",
        price_cents=7500,
        description="Pack the flask, pull the pattern, and pour bronze into your own sand mold.",
        days_out=28,
    )
    ClassApprovalFactory(class_offering=held, role=ClassApproval.Role.GUILD_LEAD)
    ClassApprovalFactory(
        class_offering=held,
        role=ClassApproval.Role.ADMIN,
        decision=ClassApproval.Decision.APPROVED,
        decided_by=admin_user,
    )

    cleared = _pending_class(
        category,
        instructor,
        title="Chasing and Repoussé",
        slug="chasing-and-repousse",
        price_cents=5500,
        description="Raise a relief in sheet copper with punches, pitch, and a chasing hammer.",
        days_out=35,
    )
    ClassApprovalFactory(
        class_offering=cleared,
        role=ClassApproval.Role.GUILD_LEAD,
        decision=ClassApproval.Decision.APPROVED,
        decided_by=lead.user,
    )
    ClassApprovalFactory(class_offering=cleared, role=ClassApproval.Role.ADMIN)

    return {"pending_both": both, "pending_held": held, "pending_guild_cleared": cleared}


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
            trigger="class_reminder",
            title="Tomorrow: Intro to Lost-Wax Casting",
            body="Your class in the Metalworking guild starts tomorrow — see you there.",
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
