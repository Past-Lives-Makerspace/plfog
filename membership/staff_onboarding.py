"""Welcoming a new guild lead or staffer to Guild Staff Tools (#1076).

A grant (:meth:`Guild.assign_lead`, :meth:`Guild.add_staff`) records a ``GRANTED`` step and,
once the grant commits, emails the member a map of their tools and the rules for using them.
The hub then shows the rules popup and the tour button, each recording its own step, so
:meth:`Member.staff_onboarding_step` always reads one log.
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


def record_grant(member: Member, guild: Guild) -> GuildStaffOnboardingEvent:
    """Log a new lead or staff grant and send the welcome email once the grant commits."""
    from membership.models import GuildStaffOnboardingEvent

    event = GuildStaffOnboardingEvent.objects.create(
        member=member, guild=guild, action=GuildStaffOnboardingEvent.Action.GRANTED
    )
    transaction.on_commit(lambda: send_welcome(event))
    return event
