"""Welcoming a new guild lead or staffer to Guild Staff Tools (#1076).

A grant (:meth:`Guild.assign_lead`, :meth:`Guild.add_staff`) records a ``GRANTED`` step and,
once the grant commits, emails the member a map of their tools and the rules for using them.
The hub then shows the rules popup and the tour button from :attr:`Member.staff_onboarding_step`,
which every function here keeps in step with the log, so a page knows what to show without a query.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from django.conf import settings
from django.db import transaction
from django.urls import reverse

if TYPE_CHECKING:
    from membership.models import Guild, GuildStaffOnboardingEvent, Member

WELCOME_EVENT = "guild_staff.welcome"
RULES_PAGE_TITLE = "Guild Staff Rules and Tools"
# The walkthrough the onboarding button offers; its take or exit ends the onboarding.
ONBOARDING_TOUR = "guild-lead"


def _absolute_url(path: str) -> str:
    return f"{settings.MEMBER_BASE_URL.rstrip('/')}{path}"


def rules_page_path() -> str:
    """The rules wiki article (``seed_wiki_staff_rules``), or the wiki home before it is seeded."""
    from membership.models import WikiPage

    page = (
        WikiPage.objects.filter(title__iexact=RULES_PAGE_TITLE, guild__isnull=True).not_archived().only("slug").first()
    )
    if page is None:
        return reverse("hub_wiki_home")
    return reverse("hub_wiki_page", args=[page.slug])


def tool_links(guild: Guild) -> list[tuple[str, str, str]]:
    """``(name, what it is for, relative URL)`` for each Guild Staff Tool, in the email's order."""
    edit = reverse("hub_guild_edit", args=[guild.pk])
    return [
        ("Guild Settings", "Your guild page: about, studio hours, images, FAQ and links.", edit),
        (
            "Orientations",
            "Orientation types, your hours and every booking.",
            reverse("hub_guild_orientations", args=[guild.pk]),
        ),
        ("Reservations", "The rooms, spaces and tools members reserve from your guild.", f"{edit}?tab=reservations"),
        ("Equipment", "Your guild's equipment, its agreements and who is signed off.", reverse("hub_equipment_index")),
        (
            "Manage Bookings",
            "Every orientation and reservation request waiting on you.",
            reverse("hub_manage_bookings"),
        ),
        ("Email Members", "Announcements to your members and your guild's email lists.", f"{edit}?tab=announcements"),
        ("Meetings and Events", "Your meetings, notes and guild events on the calendar.", f"{edit}?tab=meetings"),
        ("Staff", "Who runs the guild with you and what each of you does.", f"{edit}?tab=staff"),
        ("Guild Tools", "Everything above for every guild you run, in one place.", reverse("hub_admin_tools")),
    ]


def email_context(member: Member, guild: Guild) -> dict[str, Any]:
    """What ``membership/emails/guild_staff_welcome.{txt,html}`` reads."""
    return {
        "first_name": member.display_name.split(" ")[0],
        "guild": guild,
        "guild_url": _absolute_url(reverse("hub_guild_detail", args=[guild.slug])),
        "tools": [(name, purpose, _absolute_url(path)) for name, purpose, path in tool_links(guild)],
        "rules_url": _absolute_url(rules_page_path()),
        "tour_url": _absolute_url(f"{reverse('hub_guild_edit', args=[guild.pk])}?tour={ONBOARDING_TOUR}"),
    }


def welcome_subject(guild: Guild) -> str:
    """The email subject and the bell title."""
    return f"Welcome to Guild Staff Tools for {guild.name}"


def send_welcome(event: GuildStaffOnboardingEvent) -> None:
    """``guild_staff.welcome``: the bell and a forced email to the member just granted, once per grant."""
    from core.events.senders import emit_with_email_shell

    member, guild = event.member, event.guild
    if guild is None:
        raise ValueError(f"Onboarding step {event.pk} is a grant with no guild")
    subject = welcome_subject(guild)
    email = member.primary_email
    emit_with_email_shell(
        WELCOME_EVENT,
        target=guild,
        context={"user": member.user},
        subject=subject,
        text_template="membership/emails/guild_staff_welcome.txt",
        html_template="membership/emails/guild_staff_welcome.html",
        template_context=email_context(member, guild),
        in_app_title=subject,
        in_app_body="Your guild's tools and the rules for running it through the Member Portal.",
        url=rules_page_path(),
        # A lead appointed before they have an account still gets the email; it is how they find out.
        email_to=email or None,
        period=f"guild_staff_welcome:{event.pk}",
    )


def welcome(member: Member, guild: Guild) -> GuildStaffOnboardingEvent | None:
    """Welcome ``member`` to Guild Staff Tools once in their life: log it, owe the popup, email after commit.

    The ``GRANTED`` step is the record: someone who already has one (a new grant before, or the
    backfill) is never welcomed again, and None comes back. The one welcome path both callers share.
    """
    from membership.models import GuildStaffOnboardingEvent, Member

    if member.staff_onboarding_events.filter(action=GuildStaffOnboardingEvent.Action.GRANTED).exists():
        return None
    event = GuildStaffOnboardingEvent.objects.create(
        member=member, guild=guild, action=GuildStaffOnboardingEvent.Action.GRANTED
    )
    member.staff_onboarding_step = Member.StaffOnboardingStep.RULES
    member.save(update_fields=["staff_onboarding_step"])
    transaction.on_commit(lambda: send_welcome(event))
    return event


def record_grant(member: Member, guild: Guild) -> GuildStaffOnboardingEvent | None:
    """A new lead or a first staff role on ``guild``: welcome them, unless they were welcomed before."""
    return welcome(member, guild)


def backfill_candidates() -> list[tuple[Member, Guild]]:
    """Every current guild lead and staffer never welcomed, each with the first guild they run by name.

    Live guilds only (not soft deleted), hidden ones included: a hidden guild's staff run it too.
    """
    from membership.models import Guild, GuildStaffOnboardingEvent

    welcomed = GuildStaffOnboardingEvent.objects.filter(action=GuildStaffOnboardingEvent.Action.GRANTED).values(
        "member_id"
    )
    first: dict[int, tuple[Member, Guild]] = {}
    for guild in (
        Guild.objects.order_by("name").select_related("guild_lead").prefetch_related("staff_memberships__member")
    ):
        people = [guild.guild_lead] if guild.guild_lead is not None else []
        people += [row.member for row in guild.staff_memberships.all()]
        for person in people:
            first.setdefault(person.pk, (person, guild))
    skip = set(welcomed.values_list("member_id", flat=True))
    return sorted((pair for pk, pair in first.items() if pk not in skip), key=lambda pair: pair[0].display_name)


def confirm_rules(member: Member) -> None:
    """The member confirmed they read the rules: log it, and offer the onboarding tour next."""
    from membership.models import GuildStaffOnboardingEvent, Member

    GuildStaffOnboardingEvent.objects.create(member=member, action=GuildStaffOnboardingEvent.Action.CONFIRMED)
    if member.staff_onboarding_step == Member.StaffOnboardingStep.RULES:
        member.staff_onboarding_step = Member.StaffOnboardingStep.TOUR
        member.save(update_fields=["staff_onboarding_step"])


def record_tour_choice(member: Member, tour_key: str, *, taken: bool) -> None:
    """The member took or exited a walkthrough: log it; the onboarding tour's choice ends the welcome."""
    from membership.models import GuildStaffOnboardingEvent, Member

    Action = GuildStaffOnboardingEvent.Action
    GuildStaffOnboardingEvent.objects.create(
        member=member, action=Action.TOUR_TAKEN if taken else Action.TOUR_EXITED, tour_key=tour_key
    )
    if tour_key == ONBOARDING_TOUR and member.staff_onboarding_step == Member.StaffOnboardingStep.TOUR:
        member.staff_onboarding_step = ""
        member.save(update_fields=["staff_onboarding_step"])
