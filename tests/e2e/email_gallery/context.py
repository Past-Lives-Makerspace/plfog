"""Sample-context builders for the email gallery — factory-built, realistic data.

One builder per email (or per small family), each returning exactly what its
renderer needs. Seed values follow ``screenshots_spec._seed`` (Robin Vale,
"Intro to Lost-Wax Casting", Ceramics Guild) so every card reads against
realistic content. Absolute URLs use the app's own ``_absolute_url`` helpers so
links are never bare paths. Everything is created inside the capture spec's
transactional ``db`` fixture, so nothing ever touches a real database.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Any

from django.contrib.sites.models import Site
from django.urls import reverse
from django.utils import timezone

MEMBER_NAME = "Robin Vale"
MEMBER_EMAIL = "robin.vale@example.com"
SITE_DOMAIN = "members.pastlives.space"


@dataclass
class SampleData:
    """The seeded objects every builder draws from."""

    member: Any  # membership.models.Member — the registrant / orientee / tab owner
    instructor: Any  # membership.models.Member — the class's instructor
    lead: Any  # membership.models.Member — the guild's lead
    guild: Any  # membership.models.Guild
    offering: Any  # classes.models.ClassOffering (published, welcome email authored)
    in_review: Any  # classes.models.ClassOffering (pending, both review lanes open)
    lead_approved: Any  # classes.models.ClassOffering (pending, guild lead done, admin still open)
    registration: Any  # classes.models.Registration (confirmed)
    waitlisted: Any  # classes.models.Registration (waitlisted)
    approval_pending: Any  # classes.models.ClassApproval (guild-lead gate, pending)
    approval_decided: Any  # classes.models.ClassApproval (admin gate, approved)
    in_review_lead: Any  # classes.models.ClassApproval (in_review's guild-lead lane, open)
    in_review_admin: Any  # classes.models.ClassApproval (in_review's admin lane, open)
    lead_approved_admin: Any  # classes.models.ClassApproval (lead_approved's admin lane, open)
    booking: Any  # membership.models.OrientationBooking
    charge: Any  # billing.models.TabCharge (with entries)


def build_sample_data() -> SampleData:
    """Seed one representative slice of data for every email family."""
    from classes.factories import CategoryFactory, ClassSessionFactory, InstructorFactory, RegistrationFactory
    from classes.models import ClassApproval, ClassOffering, Registration
    from tests.billing.factories import TabChargeFactory, TabEntryFactory, TabFactory
    from tests.membership.factories import (
        GuildFactory,
        GuildOrientationSettingsFactory,
        MemberFactory,
        OrientationBookingFactory,
        OrientationSlotFactory,
    )

    # Realistic host for the find_account login link (built from the current Site).
    site = Site.objects.get_current()
    site.domain = SITE_DOMAIN
    site.name = "Past Lives Makerspace"
    site.save()

    member = MemberFactory(
        full_legal_name=MEMBER_NAME,
        preferred_name="Robin",
        _pre_signup_email=MEMBER_EMAIL,
    )
    lead = MemberFactory(full_legal_name="Mara Quill", _pre_signup_email="mara.quill@example.com")
    guild = GuildFactory(
        name="Ceramics Guild",
        about="Wheel-throwing, glazing, and kiln firings — all skill levels.",
    )
    guild.guild_lead = lead
    guild.save()
    GuildOrientationSettingsFactory(
        guild=guild,
        # Left blank on purpose so the gallery renders the STANDARD thank-you (on by
        # default). A guild that writes its own subject/body overrides it.
        thankyou_email_enabled=True,
        thankyou_email_subject="",
        thankyou_email_body="",
    )

    instructor = InstructorFactory(
        full_legal_name="Robin Vale",
        _pre_signup_email=MEMBER_EMAIL,
        instructor_slug="robin-vale",
        about_me="Longtime metalsmith and studio lead. Teaches casting and fabrication.",
    )
    # Guild-linked on purpose: the review emails read ``category.guild`` to decide whether the
    # class has a guild-lead lane at all, so a guild-less category would draw every review card
    # as a one-lane admin-only flow and hide the shape these cards exist to show.
    category = CategoryFactory(name="Ceramics", slug="ceramics", guild=guild)
    offering = _published_offering(category=category, instructor=instructor)
    base = timezone.now() + timedelta(days=10)
    ClassSessionFactory(class_offering=offering, starts_at=base, ends_at=base + timedelta(hours=3))

    registration = RegistrationFactory(
        class_offering=offering,
        first_name="Avery",
        last_name="Lim",
        email="avery.lim@example.com",
        status=Registration.Status.CONFIRMED,
        amount_paid_cents=4500,
        member=member,
    )
    waitlisted = RegistrationFactory(
        class_offering=offering,
        first_name="Dana",
        last_name="Reyes",
        email="dana.reyes@example.com",
        status=Registration.Status.WAITLISTED,
        member=None,
    )

    approval_pending = ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.GUILD_LEAD)
    approval_decided = ClassApproval.objects.create(
        class_offering=offering,
        role=ClassApproval.Role.ADMIN,
        decision=ClassApproval.Decision.APPROVED,
        notes="Looks great — clear materials list and sensible safety notes.",
        decided_at=timezone.now(),
    )
    # The decided card renders the "fully approved / your class is live" outcome.
    offering.status = ClassOffering.Status.PUBLISHED
    offering.save(update_fields=["status"])

    # Review is parallel: both lanes open at submit and neither waits for the other. The strip
    # is drawn from the offering's OWN status and rows, so a published class can only ever draw
    # a finished flow. These two carry the states the review emails actually go out in — both
    # lanes open, and one lane done with the other still open — so the cards show the branch
    # rather than a row of ticks.
    in_review = _review_offering(
        category=category,
        instructor=instructor,
        title="Raku Firing Weekend",
        slug="raku-firing-weekend",
    )
    in_review_lead = ClassApproval.objects.create(class_offering=in_review, role=ClassApproval.Role.GUILD_LEAD)
    in_review_admin = ClassApproval.objects.create(class_offering=in_review, role=ClassApproval.Role.ADMIN)

    lead_approved = _review_offering(
        category=category,
        instructor=instructor,
        title="Slab-Built Planters",
        slug="slab-built-planters",
    )
    ClassApproval.objects.create(
        class_offering=lead_approved,
        role=ClassApproval.Role.GUILD_LEAD,
        decision=ClassApproval.Decision.APPROVED,
        decided_at=timezone.now(),
    )
    lead_approved_admin = ClassApproval.objects.create(class_offering=lead_approved, role=ClassApproval.Role.ADMIN)

    slot = OrientationSlotFactory(
        guild=guild,
        starts_at=timezone.now() + timedelta(days=4),
        ends_at=timezone.now() + timedelta(days=4, hours=1),
        location="Ceramics studio — meet by the kilns",
    )
    booking = OrientationBookingFactory(slot=slot, member=member)

    tab = TabFactory(member=member)
    charge = TabChargeFactory(tab=tab, amount=Decimal("47.50"), charged_at=timezone.now())
    TabEntryFactory(tab=tab, tab_charge=charge, description="Community clay — 25 lb bag", amount=Decimal("32.50"))
    TabEntryFactory(tab=tab, tab_charge=charge, description="Kiln firing — cone 6 shelf", amount=Decimal("15.00"))

    return SampleData(
        member=member,
        instructor=instructor,
        lead=lead,
        guild=guild,
        offering=offering,
        in_review=in_review,
        lead_approved=lead_approved,
        registration=registration,
        waitlisted=waitlisted,
        approval_pending=approval_pending,
        approval_decided=approval_decided,
        in_review_lead=in_review_lead,
        in_review_admin=in_review_admin,
        lead_approved_admin=lead_approved_admin,
        booking=booking,
        charge=charge,
    )


def _review_offering(**kwargs: Any) -> Any:
    """A PENDING class in the Ceramics Guild, for the cards that draw a review in flight."""
    from classes.factories import ClassOfferingFactory
    from classes.models import ClassOffering

    return ClassOfferingFactory(
        status=ClassOffering.Status.PENDING,
        is_private=False,
        price_cents=6500,
        capacity=10,
        description=(
            "Build, glaze and fire your own pieces over one weekend. Bring an apron; everything else is provided."
        ),
        **kwargs,
    )


def _published_offering(**kwargs: Any) -> Any:
    """A published "Intro to Lost-Wax Casting" with an instructor-authored welcome email (M4)."""
    from classes.factories import ClassOfferingFactory
    from classes.models import ClassOffering

    return ClassOfferingFactory(
        title="Intro to Lost-Wax Casting",
        slug="intro-to-lost-wax-casting",
        status=ClassOffering.Status.PUBLISHED,
        is_private=False,
        price_cents=4500,
        capacity=8,
        description=(
            "Carve a model in wax, invest it, burn it out, and pour molten bronze to cast your own "
            "small sculpture. No experience needed — all tools and materials provided."
        ),
        welcome_email_enabled=True,
        welcome_email_subject="Welcome to Lost-Wax Casting — a few things before we start",
        welcome_email_body=(
            "So glad you're joining us! Please wear closed-toe shoes and bring an apron. We provide "
            "all wax, tools, and metal. Doors open 15 minutes early — come find me at the casting "
            "bench. — Robin"
        ),
        **kwargs,
    )


# --- Classes / Teaching --------------------------------------------------------


def _class_urls(data: SampleData, registration: Any) -> dict[str, str]:
    from classes.emails import _absolute_url

    return {
        "self_serve_url": _absolute_url(
            reverse("classes:my_registration", kwargs={"token": registration.self_serve_token})
        ),
        "class_url": _absolute_url(reverse("classes:public_class_detail", kwargs={"slug": data.offering.slug})),
    }


def _upcoming_sessions(data: SampleData) -> list[Any]:
    return list(data.offering.sessions.filter(starts_at__gte=timezone.now()).order_by("starts_at"))


def confirmation_context(data: SampleData) -> dict[str, Any]:
    """Mirrors ``classes.emails.send_registration_confirmation``."""
    from classes.models import ClassSettings

    registration = data.registration
    urls = _class_urls(data, registration)
    return {
        "subject": f"You're confirmed for {data.offering.title}",
        "template_context": {
            "registration": registration,
            "offering": data.offering,
            "upcoming_sessions": _upcoming_sessions(data),
            "amount_paid_cents": registration.amount_paid_cents,
            "amount_paid_dollars": f"{registration.amount_paid_cents / 100:.2f}",
            "footer": ClassSettings.load().confirmation_email_footer,
            **urls,
        },
    }


def welcome_context(data: SampleData) -> dict[str, Any]:
    """Inputs for ``classes.emails._welcome_email_bodies`` (the real send's renderer)."""
    urls = _class_urls(data, data.registration)
    return {
        "offering": data.offering,
        "greeting_name": data.registration.first_name,
        "self_serve_url": urls["self_serve_url"],
    }


def instructor_new_registration_context(data: SampleData) -> dict[str, Any]:
    """Mirrors ``classes.emails.emit_instructor_new_registration``."""
    from classes.emails import _absolute_url

    registration = data.registration
    offering = data.offering
    return {
        "subject": f"New registration: {registration.first_name} {registration.last_name} for {offering.title}",
        "template_context": {
            "registration": registration,
            "offering": offering,
            "class_url": _class_urls(data, registration)["class_url"],
            "manage_url": _absolute_url(reverse("classes:teach_class_detail", kwargs={"pk": offering.pk})),
            "amount_paid": f"{registration.amount_paid_cents / 100:.2f}",
            "spots_filled": offering.registrations.count(),
            "capacity": offering.capacity,
        },
    }


def review_request_context(data: SampleData) -> dict[str, Any]:
    """Mirrors ``classes.emails.send_guild_lead_review_request`` — the guild-lead lane at submit.

    Drawn against the class with both lanes open, which is the state this email actually goes
    out in, so the card shows the guild lead and the admin side by side rather than a finished
    row of ticks.
    """
    from classes.emails import _token_review_url

    row = data.in_review_lead
    return {
        "subject": f"Review request: {data.in_review.title}",
        "template_context": {
            "offering": data.in_review,
            "approval": row,
            "review_url": _token_review_url(row),
            "role_label": "Guild Lead",
            "guild_lead_status": "",
        },
    }


def admin_review_request_context(data: SampleData) -> dict[str, Any]:
    """Mirrors ``classes.emails.send_admin_review_request`` — the admin lane at submit.

    Same shell as the guild lead's card, addressed to the other lane: no bearer token, a
    logged-in ``/classes/admin/<pk>/review/`` link, and the line naming where the guild lead's
    room check stands.
    """
    from classes.emails import _absolute_url, _admin_review_url, _guild_lead_lane_status

    return {
        "subject": f"Review request: {data.in_review.title}",
        "template_context": {
            "offering": data.in_review,
            "approval": data.in_review_admin,
            "review_url": _absolute_url(_admin_review_url(data.in_review)),
            "role_label": "Admin",
            "guild_lead_status": _guild_lead_lane_status(data.in_review),
        },
    }


def review_submitted_instructor_context(data: SampleData) -> dict[str, Any]:
    """Mirrors ``classes.emails._emit_instructor_review_explainer`` — one email, both reviewers."""
    from classes.emails import _absolute_url
    from classes.models import ClassApproval

    return {
        "subject": f"Your class '{data.in_review.title}' is in review",
        "template_context": {
            "offering": data.in_review,
            "approvals": [data.in_review_admin],
            "instructor_url": _absolute_url(reverse("classes:teach_class_edit", kwargs={"pk": data.in_review.pk})),
            "has_guild_lead_lane": ClassApproval.Role.GUILD_LEAD in data.in_review.required_review_roles,
        },
    }


def admin_validation_request_context(data: SampleData) -> dict[str, Any]:
    """Mirrors ``classes.emails.send_admin_validation_request`` — the guild lead has signed off.

    Drawn against the class whose guild-lead lane is done and whose admin lane is still open,
    so the strip shows one half of the review column settled.
    """
    from classes.emails import _absolute_url, _admin_review_url

    return {
        "subject": f"Last approval needed: {data.lead_approved.title}",
        "template_context": {
            "offering": data.lead_approved,
            "approval": data.lead_approved_admin,
            "review_url": _absolute_url(_admin_review_url(data.lead_approved)),
            "guild_lead_name": data.lead.display_name,
            "instructor_name": data.instructor.display_name,
        },
    }


def review_decision_context(data: SampleData) -> dict[str, Any]:
    """Mirrors ``classes.emails.send_class_review_decision`` — the fully-approved outcome."""
    from classes.emails import _absolute_url

    public_url = _absolute_url(reverse("classes:public_class_detail", kwargs={"slug": data.offering.slug}))
    return {
        "subject": f"Your class '{data.offering.title}' is live!",
        "template_context": {
            "offering": data.offering,
            "approval": data.approval_decided,
            "edit_url": public_url,
            "public_url": public_url,
            "fully_approved": True,
            "pending_rows": [],
            "held_for_room_check": False,
        },
    }


def waitlist_joined_context(data: SampleData) -> dict[str, Any]:
    """Mirrors ``classes.emails.send_waitlist_joined_confirmation``."""
    registration = data.waitlisted
    return {
        "subject": f"You're on the waitlist for {data.offering.title}",
        "template_context": {
            "registration": registration,
            "offering": data.offering,
            "position": registration.waitlist_position,
            **_class_urls(data, registration),
        },
    }


def waitlist_spot_opened_context(data: SampleData) -> dict[str, Any]:
    """Mirrors ``classes.emails.send_waitlist_spot_opened``."""
    from classes.emails import _absolute_url
    from classes.models import ClassSettings

    registration = data.waitlisted
    register_url = _absolute_url(
        reverse("classes:register", kwargs={"slug": data.offering.slug})
        + f"?waitlist_token={registration.self_serve_token}"
    )
    return {
        "subject": f"A spot opened in {data.offering.title}!",
        "template_context": {
            "registration": registration,
            "offering": data.offering,
            "register_url": register_url,
            "class_url": _class_urls(data, registration)["class_url"],
            "claim_window_hours": ClassSettings.load().waitlist_claim_window_hours,
        },
    }


def _promoted_context(data: SampleData) -> dict[str, Any]:
    """Mirrors ``classes.emails._promoted_template_context`` for the promoted pair."""
    from classes.emails import _absolute_url

    registration = data.waitlisted
    return {
        "registration": registration,
        "offering": data.offering,
        "upcoming_sessions": _upcoming_sessions(data),
        "pay_url": _absolute_url(
            reverse("classes:my_registration_pay", kwargs={"token": registration.self_serve_token})
        ),
        "amount_due_dollars": "45.00",
        **_class_urls(data, registration),
    }


def promoted_context(data: SampleData) -> dict[str, Any]:
    """Mirrors ``classes.emails.send_waitlist_promoted``."""
    return {
        "subject": f"You're in! {data.offering.title}",
        "template_context": _promoted_context(data),
    }


def promoted_pay_context(data: SampleData) -> dict[str, Any]:
    """Mirrors ``classes.emails.send_payment_link_email``."""
    return {
        "subject": f"You're in! Complete your payment for {data.offering.title}",
        "template_context": _promoted_context(data),
    }


def removed_context(data: SampleData) -> dict[str, Any]:
    """Mirrors ``classes.emails.send_removal_notice`` — the paid seat-holder variant."""
    from classes.emails import _absolute_url

    registration = data.registration
    return {
        "subject": f"Your registration for {data.offering.title} was cancelled",
        "template_context": {
            "registration": registration,
            "offering": data.offering,
            "was_waitlisted": False,
            "show_refund_note": True,
            "amount_paid_dollars": "45.00",
            "class_url": _class_urls(data, registration)["class_url"],
            "browse_url": _absolute_url(reverse("classes:public_list")),
        },
    }


def moved_context(data: SampleData) -> dict[str, Any]:
    """Mirrors ``classes.emails.send_registration_moved`` — the confirmed seat-holder variant.

    ``source`` is the class they came FROM. The template reads only its title, so a plain
    mapping stands in rather than seeding a second offering just to render one line.
    """
    registration = data.registration
    return {
        "subject": f"You've been moved to {data.offering.title}",
        "template_context": {
            "registration": registration,
            "offering": data.offering,
            "source": {"title": "Introduction to Wheel Throwing"},
            "upcoming_sessions": _upcoming_sessions(data),
            **_class_urls(data, registration),
            "is_waitlisted": False,
        },
    }


def duplicate_payment_alert_context(data: SampleData) -> dict[str, Any]:
    """Reproduces ``classes.emails.send_duplicate_payment_alert`` exactly."""
    from classes.emails import _absolute_url

    registration = data.registration
    offering = data.offering
    detail_url = _absolute_url(reverse("classes:admin_registration_detail", kwargs={"pk": registration.pk}))
    stripe_url = "https://dashboard.stripe.com/payments/pi_sample_duplicate"
    name = f"{registration.first_name} {registration.last_name}".strip() or registration.email
    return {
        "subject": f"Duplicate payment: {name}, {offering.title}",
        "text_body": (
            f"{name} ({registration.email}) paid $45.00 online for "
            f'"{offering.title}" AFTER the balance was already settled (marked paid by staff, '
            f"or an earlier payment landed first).\n\n"
            f"A refund is owed for one of the two payments.\n\n"
            f"Registration: {detail_url}\n"
            f"Stripe payment: {stripe_url}\n"
            f"Checkout session: cs_sample_duplicate"
        ),
    }


def orientation_orphan_payment_alert_context(data: SampleData) -> dict[str, Any]:
    """Reproduces ``membership.webhook_handlers._send_orphan_payment_alert`` exactly."""
    booking = data.booking
    stripe_url = "https://dashboard.stripe.com/payments/pi_sample_orphan"
    return {
        "subject": "Orphaned orientation payment needs a manual refund",
        "text_body": (
            f"A paid orientation Checkout landed with no booking to credit.\n\n"
            f"Booking {booking.pk} no longer exists.\n\n"
            f"The member paid $15.00 and has nothing in the app to show for it. "
            f"Refund the payment from the Stripe dashboard.\n\n"
            f"Stripe payment: {stripe_url}\n"
            f"Checkout session: cs_sample_orphan\n"
            f"Customer email: {data.member.primary_email}"
        ),
    }


def reminder_context(data: SampleData) -> dict[str, Any]:
    """Mirrors ``classes.emails.build_class_reminder_occurrence``."""
    session = _upcoming_sessions(data)[0]
    return {
        "subject": f"Reminder: {data.offering.title} — {session.starts_at:%a %b %-d at %-I:%M %p}",
        "template_context": {
            "registration": data.registration,
            "session": session,
            "offering": data.offering,
            **_class_urls(data, data.registration),
        },
    }


# --- Guilds & Orientations -----------------------------------------------------


def _orientation_context(data: SampleData, **extra: Any) -> dict[str, Any]:
    """Reuses the real send helper's own context assembly (byte-identical cards)."""
    from membership.orientations import _context

    return _context(data.booking, **extra)


def orientation_member_context(data: SampleData) -> dict[str, Any]:
    """One builder for the request / confirmed / declined / cancelled member emails.

    The subject varies per template; the build step overrides it per entry key.
    """
    return {
        "subject": f"Orientation request received — {data.guild.name}",
        "template_context": _orientation_context(data),
    }


# Per-template subjects for the orientation member family (mirroring each send site).
ORIENTATION_SUBJECTS: dict[str, str] = {
    "orientation_request": "Orientation request received — {guild}",
    "orientation_confirmed": "Orientation confirmed — {guild}",
    "orientation_declined": "About your orientation request — {guild}",
    "orientation_cancelled": "Orientation cancelled — {guild}",
}


def orientation_thankyou_context(data: SampleData) -> dict[str, Any]:
    """Mirrors ``membership.orientations.complete_orientation``.

    The sample guild leaves the thank-you copy blank, so this renders the STANDARD
    thank-you (the on-by-default copy) via the resolved_* fallbacks — what most members
    actually receive.
    """
    from membership.models import GuildOrientationSettings

    settings_obj = GuildOrientationSettings.objects.get(guild=data.guild)
    return {
        "subject": settings_obj.resolved_thankyou_subject,
        "template_context": _orientation_context(data, body=settings_obj.resolved_thankyou_body),
    }


def orientation_lead_request_context(data: SampleData) -> dict[str, Any]:
    """Mirrors ``membership.orientations._emit_lead_request``."""
    from membership.orientations import _absolute_url, _action_url

    return {
        "subject": f"New orientation request — {data.guild.name}",
        "template_context": _orientation_context(
            data,
            respond_url=_absolute_url(reverse("hub_orientation_respond", args=[data.booking.pk])),
            confirm_url=_action_url(data.booking, "confirm"),
            decline_url=_action_url(data.booking, "decline"),
        ),
    }


def discord_guilds_imported_context(data: SampleData) -> dict[str, Any]:
    """Mirrors ``membership.discord_sync._send_import_confirmation``."""
    from membership.orientations import _absolute_url

    return {
        "subject": "Your Past Lives guilds are set up",
        "template_context": {
            "greeting_name": data.member.display_name,
            "guilds": [
                {"name": data.guild.name, "url": _absolute_url(reverse("hub_guild_detail", args=[data.guild.slug]))}
            ],
            "manage_url": _absolute_url(f"{reverse('hub_user_settings')}?tab=guilds"),
            "complete": True,
        },
    }


def guild_welcome_context(data: SampleData) -> dict[str, Any]:
    """Mirrors ``membership.orientations.send_guild_welcome``.

    The sample guild leaves the welcome copy blank, so this renders the STANDARD welcome
    (the on-by-default copy) via the resolved_* fallbacks — what most members receive.
    """
    from membership.models import GuildOrientationSettings
    from membership.orientations import _guild_welcome_context

    settings_obj, _created = GuildOrientationSettings.objects.get_or_create(guild=data.guild)
    return {
        "subject": settings_obj.welcome_email_subject_resolved,
        "template_context": _guild_welcome_context(
            data.guild, data.member.display_name, settings_obj.welcome_email_body_resolved
        ),
    }


# --- Member wiki ----------------------------------------------------------------


def _sample_wiki_page(data: SampleData) -> Any:
    """An unsaved WikiPage with a slug, which is all both templates read off it.

    Unsaved on purpose: ``get_absolute_url`` only needs the slug, and the gallery should
    not leave rows behind in a database it shares with the other builders.
    """
    from membership.models import WikiPage

    return WikiPage(title="SawStop Table Saw", slug="sawstop-table-saw", kind=WikiPage.Kind.MACHINE)


def wiki_page_archived_context(data: SampleData) -> dict[str, Any]:
    """Mirrors ``membership.models.WikiPage.send_archive_notice``."""
    from django.conf import settings as django_settings

    page = _sample_wiki_page(data)
    return {
        "subject": f'Your wiki page "{page.title}" was archived',
        "template_context": {
            "page": page,
            "author_name": data.member.display_name,
            "archiver": data.lead,
            "archiver_name": data.lead.display_name,
            "archiver_email": data.lead.primary_email,
            "reason": "The information here was replaced by the Bandsaw Safety page.",
            "page_url": f"{django_settings.MEMBER_BASE_URL}{page.get_absolute_url()}",
            "redirect_page": None,
            "redirect_url": "",
        },
    }


def wiki_proposal_declined_context(data: SampleData) -> dict[str, Any]:
    """Mirrors ``membership.models.WikiPage.decline_proposal``."""
    from django.conf import settings as django_settings

    page = _sample_wiki_page(data)
    return {
        "subject": f'Your safety page "{page.title}" needs one change',
        "template_context": {
            "page": page,
            "author_name": data.member.display_name,
            "reviewer_name": data.lead.display_name,
            "reviewer_email": data.lead.primary_email,
            "note": "Add the dust mask requirement and say who to ask when the blade needs changing.",
            "edit_url": f"{django_settings.MEMBER_BASE_URL}{reverse('hub_wiki_edit', args=[page.slug])}",
            "page_url": f"{django_settings.MEMBER_BASE_URL}{page.get_absolute_url()}",
        },
    }


def wiki_guild_digest_context(data: SampleData) -> dict[str, Any]:
    """Mirrors ``send_wiki_guild_digest``, over one seeded page and one seeded miss.

    Seeded here rather than drawn from :class:`SampleData`, because the wiki is a later
    feature than the sample slice and no other card needs a wiki page.
    """
    from django.utils import timezone

    from membership.models import WikiPage, WikiSearchMiss
    from membership.wiki_guild import digest_subject, guild_digest_payload, previous_month_window

    month_start, month_end = previous_month_window()
    page = WikiPage.objects.create(
        title="SawStop Table Saw",
        kind=WikiPage.Kind.MACHINE,
        guild=data.guild,
        created_by=data.member,
        updated_by=data.member,
        body="<p>How to use the table saw safely.</p>",
        body_edited_at=timezone.now(),
    )
    WikiPage.objects.filter(pk=page.pk).update(created_at=month_start)
    WikiSearchMiss.objects.create(
        query="epoxy cure time",
        query_normalized="epoxy cure time",
        guild=data.guild,
        member=data.member,
    )
    WikiSearchMiss.objects.filter(guild=data.guild).update(created_at=month_start)
    payload = guild_digest_payload(data.guild, month_start=month_start, month_end=month_end)
    assert payload is not None
    return {"subject": digest_subject(data.guild, payload), "template_context": payload}


# --- Billing --------------------------------------------------------------------


def receipt_context(data: SampleData) -> dict[str, Any]:
    """Mirrors ``billing.notifications.send_receipt``."""
    from billing.notifications import _member_url

    charge = data.charge
    return {
        "subject": f"Past Lives Makerspace — Receipt for ${charge.amount}",
        "template_context": {
            "member": data.member,
            "charge": charge,
            "entries": charge.entries.all().order_by("created_at"),
            "charged_at": charge.charged_at or timezone.now(),
            "billing_history_url": _member_url(reverse("hub_tab_history")),
        },
    }


def charge_failed_admin_context(data: SampleData) -> dict[str, Any]:
    """Mirrors ``billing.notifications.notify_admin_charge_failed``."""
    from billing.notifications import _member_url

    charge = data.charge
    return {
        "subject": f"[Billing] Failed charge for {data.member.display_name} — ${charge.amount}",
        "template_context": {
            "member": data.member,
            "charge": charge,
            "dashboard_url": _member_url(reverse("billing_admin_dashboard")),
        },
    }


# --- Announcements & Release ----------------------------------------------------


def release_context(data: SampleData) -> dict[str, Any]:
    """Kwargs for ``core.release_email.render_release_email`` (sample cards)."""
    from core.release_email import Card
    from plfog.version import VERSION

    return {
        "version": VERSION,
        "subject": "What's new at Past Lives",
        "preheader": "A quick tour of what just shipped.",
        "intro": "<p>We've just shipped an update — here's what's new.</p>",
        "cards": [
            Card(
                title="Book guild orientations from the guild page",
                bullets=[
                    "Pick a slot that works for you and get a calendar invite automatically.",
                    "Your guild's lead confirms with one click.",
                ],
            ),
            Card(
                title="Calendar",
                bullets=["Every guild meeting and community event, in one place."],
            ),
        ],
    }


def announcement_context(data: SampleData) -> dict[str, Any]:
    """Inputs for ``membership.models.build_announcement_email_html`` (composer sample)."""
    return {
        "title": "Studio closed for deep clean this Friday",
        "body": (
            "<p>The whole space is closed <strong>Friday from 9am to 3pm</strong> while we deep-clean "
            "the studios and service the kilns.</p>"
            "<p>Open studio resumes Friday evening — see you then!</p>"
        ),
    }


# --- System/Auth ----------------------------------------------------------------


def _auth_urls() -> dict[str, str]:
    domain = Site.objects.get_current().domain
    return {
        "login_url": f"https://{domain}/accounts/login/code/",
        "find_account_url": f"https://{domain}/find-account/",
        "signup_url": f"https://{domain}/accounts/signup/",
    }


def login_code_context(data: SampleData) -> dict[str, Any]:
    return {"email": MEMBER_EMAIL, "context": {"code": "824113"}}


def unknown_account_context(data: SampleData) -> dict[str, Any]:
    urls = _auth_urls()
    return {
        "email": "someone.new@example.com",
        "context": {
            "email": "someone.new@example.com",
            "find_account_url": urls["find_account_url"],
            "signup_url": urls["signup_url"],
        },
    }


def account_already_exists_context(data: SampleData) -> dict[str, Any]:
    urls = _auth_urls()
    return {
        "email": MEMBER_EMAIL,
        "context": {"email": MEMBER_EMAIL, "login_url": urls["login_url"]},
    }


def find_account_context(data: SampleData) -> dict[str, Any]:
    """Reproduces ``core.forms.FindAccountForm.send_login_email`` exactly (B2)."""
    member = data.member
    login_url = f"https://{Site.objects.get_current().domain}/accounts/login/"
    return {
        "subject": "Your Past Lives Account",
        "text_body": (
            f"Hi {member.preferred_name or member.full_legal_name},\n\n"
            f"Your account email is: {member.primary_email}\n\n"
            f"You can log in here:\n{login_url}\n\n"
            f"If you didn't request this, you can safely ignore this email."
        ),
    }
