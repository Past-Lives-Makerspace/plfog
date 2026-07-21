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
    registration: Any  # classes.models.Registration (confirmed)
    waitlisted: Any  # classes.models.Registration (waitlisted)
    approval_pending: Any  # classes.models.ClassApproval (guild-lead gate, pending)
    approval_decided: Any  # classes.models.ClassApproval (admin gate, approved)
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
        thankyou_email_enabled=True,
        thankyou_email_subject="Thanks for getting oriented with the Ceramics Guild!",
        thankyou_email_body=(
            "It was lovely meeting you today. Your studio access is live — come throw a pot any "
            "open-studio night, and ask in the guild channel if you get stuck. — Mara"
        ),
        join_email_enabled=True,
        join_email_subject="Welcome to the Ceramics Guild!",
        join_email_body=(
            "We're so glad you've joined us. Glazes and community clay live on the shelves by the "
            "kilns; book an orientation from the guild page and we'll show you around. — Mara"
        ),
    )

    instructor = InstructorFactory(
        full_legal_name="Robin Vale",
        _pre_signup_email=MEMBER_EMAIL,
        instructor_slug="robin-vale",
        about_me="Longtime metalsmith and studio lead. Teaches casting and fabrication.",
    )
    category = CategoryFactory(name="Ceramics", slug="ceramics")
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
        registration=registration,
        waitlisted=waitlisted,
        approval_pending=approval_pending,
        approval_decided=approval_decided,
        booking=booking,
        charge=charge,
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
    """Mirrors ``classes.emails._emit_review_request`` (guild-lead gate)."""
    from classes.emails import _absolute_url

    row = data.approval_pending
    return {
        "subject": f"Review request: {data.offering.title}",
        "template_context": {
            "offering": data.offering,
            "approval": row,
            "review_url": _absolute_url(reverse("classes:class_review", kwargs={"token": row.token})),
            "role_label": "Guild Lead",
        },
    }


def review_submitted_instructor_context(data: SampleData) -> dict[str, Any]:
    """Mirrors ``classes.emails._emit_instructor_review_explainer``."""
    from classes.emails import _absolute_url

    return {
        "subject": f"Your class '{data.offering.title}' is in review",
        "template_context": {
            "offering": data.offering,
            "approvals": [data.approval_pending],
            "instructor_url": _absolute_url(reverse("classes:teach_class_edit", kwargs={"pk": data.offering.pk})),
        },
    }


def admin_validation_request_context(data: SampleData) -> dict[str, Any]:
    """Mirrors ``classes.emails.send_admin_validation_request``."""
    from classes.emails import _absolute_url

    row = data.approval_pending
    return {
        "subject": f"Executive validation needed: {data.offering.title}",
        "template_context": {
            "offering": data.offering,
            "approval": row,
            "review_url": _absolute_url(reverse("classes:class_review", kwargs={"token": row.token})),
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
    """Mirrors ``membership.orientations.complete_orientation`` (guild-authored)."""
    from membership.models import GuildOrientationSettings

    settings_obj = GuildOrientationSettings.objects.get(guild=data.guild)
    return {
        "subject": settings_obj.thankyou_email_subject,
        "template_context": _orientation_context(data, body=settings_obj.thankyou_email_body),
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


def guild_welcome_context(data: SampleData) -> dict[str, Any]:
    """Mirrors ``membership.orientations.member_joined_guild`` (guild-authored)."""
    from membership.models import GuildOrientationSettings
    from membership.orientations import _absolute_url

    settings_obj = GuildOrientationSettings.objects.get(guild=data.guild)
    return {
        "subject": settings_obj.join_email_subject,
        "template_context": {
            "guild": data.guild,
            "greeting_name": data.member.display_name,
            "body": settings_obj.join_email_body,
            "guild_url": _absolute_url(reverse("hub_guild_detail", args=[data.guild.slug])),
        },
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
                title="Community Calendar",
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
