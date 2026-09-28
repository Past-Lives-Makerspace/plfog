"""The Member Directory and Guild Pages launch email: one announcement, two doors in.

Every active member hears that the Member Directory and Guild Pages are open, with a
screenshot and a few lines on each, and what unlocks in the weeks after. Which door they
get depends on whether their account has ever signed in:

- A member who has signed in before gets the announcement with a button to their profile
  settings, as one direct :func:`core.email.send` per member: email only, no bell, no push,
  and no opt-out applies, because a launch notice is not a notification anyone chose to mute.
  The audit log is its ledger: an address already logged SENT for this launch is skipped on
  a re-run.
- A member who has never signed in gets the same announcement as an activation invite: the
  forced ``member.login_invite`` email with a button to the login-code page and their address
  pre-filled. That event is email-only and forced by definition, and the delivery ledger
  under :data:`LAUNCH_PERIOD` makes a re-run skip whoever it already reached.

Both variants render from ``membership/emails/launch_announcement.html``, which reuses the
release email's hero, button and footer partials plus the rollout table. Each feature section
carries a framed member-view screenshot that drops out silently when it is missing from
object storage, the same rule the release email's cards follow. The plain-text part is built
here so the two never drift, as ``core.release_email`` does.

Either way a re-run reaches only whoever was missed, and Discord is left alone: the
launch post there is written by a person.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING

from django.conf import settings
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.dateformat import format as date_format

from core.release_email import resolve_feature_shot_url

if TYPE_CHECKING:
    from core.events.emit import EmitResult
    from membership.models import Member

#: The day the Member Directory and Guild Pages opened. Labels the hero band and keys the delivery ledger.
LAUNCH_DATE = date(2026, 9, 28)

#: The invite emit's idempotency bucket: a re-run skips everyone already delivered.
LAUNCH_PERIOD = f"directory_guilds_launch:{LAUNCH_DATE.isoformat()}"

#: The audit label of the direct announcement send; a SENT row under it is the send-once ledger.
ANNOUNCEMENT_TRIGGER_KIND = "directory_guilds_launch.announcement"

ANNOUNCEMENT_SUBJECT = "Find your people: the Member Directory and Guild Pages are live"
INVITE_SUBJECT = "Find your people at Past Lives: your Member Portal account is ready"

HERO_BADGE = "New this week"
HERO_TITLE = "Member Directory & Guild Pages"

ANNOUNCEMENT_CTA = "Set up your directory profile"
INVITE_CTA = "Activate your account"

_INTRO = (
    "Two new parts of the Past Lives Member Portal opened today. The Member Directory helps "
    "you find other members by name, skill or guild, and every guild now has its own page "
    "with its announcements, studio hours, meetings and the people who run it."
)
_ACTIVATE = (
    "Your account is already set up. To activate it, click the button below and we'll email "
    "you a one-time login code. There's no password to remember."
)

#: Object-storage slugs of the two section screenshots (``email/features/<slug>.png``), captured as a
#: plain member against seeded demo data so no real member appears. Their own slugs, not the release
#: email's ``member-directory`` and ``guild-pages``, so a later harness run cannot swap the approved images.
DIRECTORY_SHOT_SLUG = "launch2-directory"
GUILD_SHOT_SLUG = "launch2-guild"


@dataclass(frozen=True)
class FeatureSection:
    """One feature in the email body: a heading, its screenshot and what a member can do there."""

    title: str
    path: str
    shot_slug: str
    shot_alt: str
    points: tuple[str, ...]


SECTIONS: tuple[FeatureSection, ...] = (
    FeatureSection(
        title="The Member Directory",
        path="/members/",
        shot_slug=DIRECTORY_SHOT_SLUG,
        shot_alt="The Member Directory in the Member Portal",
        points=(
            "Search members by name or skill, or filter by guild and by who's open for commissions.",
            "Your profile stays hidden until you choose to share it. Turn it on under Settings, Profile, "
            "and pick which details show: pronouns, bio, photo, contact info and skills.",
            "Add your skills and flag yourself as open for commissions so other members can find you.",
        ),
    ),
    FeatureSection(
        title="Guild Pages",
        path="/guilds/",
        shot_slug=GUILD_SHOT_SLUG,
        shot_alt="A guild page in the Member Portal",
        points=(
            "Every guild has a page with its announcements, studio hours, upcoming classes, next meeting "
            "and guild lead.",
            "Join a guild from its page to get its updates by email, in the portal or on Discord, and to "
            "add its badge to your directory profile.",
            "Find every guild in the sidebar under Guilds.",
        ),
    ),
)

SCHEDULE_NOTE = "Dates are tentative. We'll announce each release in the portal and on Discord as it lands."


@dataclass(frozen=True)
class RolloutWeek:
    """One row of the rollout table: which week, when it starts, what unlocks."""

    label: str
    starts: str
    features: tuple[str, ...]


#: What is still to come, as the email shows it.
ROLLOUT_SCHEDULE: tuple[RolloutWeek, ...] = (
    RolloutWeek("Week 3", "Mon Oct 5", ("Member Wiki", "Meetings & Spaces")),
    RolloutWeek("Week 4", "Mon Oct 12", ("Classes & Workshops",)),
    RolloutWeek("Week 5", "Mon Oct 19", ("Equipment (or Space Rentals)",)),
)


def profile_settings_url() -> str:
    """The absolute Settings, Profile URL: the announcement's button, where the directory opt-in lives."""
    return f"{settings.MEMBER_BASE_URL}{reverse('hub_user_settings')}?tab=profile"


def _section_context() -> list[dict[str, object]]:
    """The sections with absolute links and resolved screenshot URLs (blank drops the image)."""
    return [
        {
            "title": section.title,
            "url": f"{settings.MEMBER_BASE_URL}{section.path}",
            "shot_url": resolve_feature_shot_url(section.shot_slug),
            "shot_alt": section.shot_alt,
            "points": list(section.points),
        }
        for section in SECTIONS
    ]


def _render(
    *,
    subject: str,
    preheader: str,
    greeting: str,
    intro_paragraphs: list[str],
    cta_url: str,
    cta_label: str,
    signin_note: str,
) -> tuple[str, str]:
    """Assemble ``(html, text)`` for one variant from the shared context."""
    context = {
        "preheader": preheader,
        "hero_badge": HERO_BADGE,
        "hero_title": HERO_TITLE,
        "hero_subtitle": date_format(LAUNCH_DATE, "F j, Y"),
        "greeting": greeting,
        "intro_paragraphs": intro_paragraphs,
        "sections": _section_context(),
        "weeks": ROLLOUT_SCHEDULE,
        "schedule_note": SCHEDULE_NOTE,
        "cta_url": cta_url,
        "cta_label": cta_label,
        "signin_note": signin_note,
    }
    # Coerce SafeString to plain str, as the release renderer does, so an admin preview's
    # srcdoc attribute escapes it correctly.
    html = "" + render_to_string("membership/emails/launch_announcement.html", context)
    text = _text(
        subject=subject,
        greeting=greeting,
        intro_paragraphs=intro_paragraphs,
        cta_label=cta_label,
        cta_url=cta_url,
        signin_note=signin_note,
    )
    return html, text


def _text(
    *,
    subject: str,
    greeting: str,
    intro_paragraphs: list[str],
    cta_label: str,
    cta_url: str,
    signin_note: str,
) -> str:
    """The plain-text part, mirroring the HTML section for section, ending in the shared footer."""
    lines: list[str] = [subject, "", greeting, ""]
    for paragraph in intro_paragraphs:
        lines += [paragraph, ""]
    for section in SECTIONS:
        lines += [f"{section.title}: {settings.MEMBER_BASE_URL}{section.path}"]
        lines += [*[f"• {point}" for point in section.points], ""]
    lines += ["What's coming next"]
    for week in ROLLOUT_SCHEDULE:
        lines.append(f"{week.label}, starting {week.starts}: {'; '.join(week.features)}")
    lines += [SCHEDULE_NOTE, "", f"{cta_label}: {cta_url}"]
    if signin_note:
        lines += [signin_note]
    lines.append("")
    return "\n".join(lines) + render_to_string("membership/emails/_footer.txt")


def render_launch_announcement(*, member_name: str, cta_url: str) -> tuple[str, str]:
    """The variant for a member who has signed in before: a greeting and the profile settings button."""
    return _render(
        subject=ANNOUNCEMENT_SUBJECT,
        preheader="Find members by skill or guild, and see what every guild is up to.",
        greeting=f"Hi {member_name},",
        intro_paragraphs=[_INTRO],
        cta_url=cta_url,
        cta_label=ANNOUNCEMENT_CTA,
        signin_note="",
    )


def render_launch_invite(*, member_name: str, login_url: str, email: str) -> tuple[str, str]:
    """The variant for a member who has never signed in: personal, with the activation button."""
    return _render(
        subject=INVITE_SUBJECT,
        preheader="Activate your account with one click, then find members by skill or guild.",
        greeting=f"Hi {member_name},",
        intro_paragraphs=[_INTRO, _ACTIVATE],
        cta_url=login_url,
        cta_label=INVITE_CTA,
        signin_note=f"This link is for {email}. Sign in with that same address whenever you come back.",
    )


def announcement_address(member: Member) -> str:
    """Where the announcement goes: the member's chosen notification address, else their primary."""
    from core.events.channels import notification_email_for

    user = member.user
    if user is None:
        raise ValueError(f"Member {member.pk} has no account; the announcement audience is signed-in members only.")
    return notification_email_for(user) or member.primary_email


def send_launch_announcement(member: Member) -> str:
    """Email one signed-in member the announcement, straight through ``core.email.send``.

    Deliberately not the notification spine: no in-app row, no push, no preference gate.
    The audit log is the ledger, so the same address is never sent twice.

    Returns:
        ``"sent"``, ``"already"`` (a SENT row exists for this address) or ``"failed"``
        (the provider rejected it; logged FAILED and left for a re-run).
    """
    from core.email import send
    from core.models import TransactionalEmailLog

    address = announcement_address(member)
    if TransactionalEmailLog.objects.filter(
        trigger_kind=ANNOUNCEMENT_TRIGGER_KIND, to_email=address, status=TransactionalEmailLog.Status.SENT
    ).exists():
        return "already"
    html, text = render_launch_announcement(member_name=member.display_name, cta_url=profile_settings_url())
    log = send(
        to=address,
        subject=ANNOUNCEMENT_SUBJECT,
        trigger_kind="directory_guilds_launch.announcement",
        text_body=text,
        html_body=html,
        best_effort=True,
    )
    return "sent" if log.status == TransactionalEmailLog.Status.SENT else "failed"


def send_launch_invite(member: Member) -> EmitResult:
    """Email one never-signed-in member the launch as an activation invite.

    Provisions their account first (:meth:`Member.first_sign_in_url`), then emits the forced
    ``member.login_invite`` email with the launch body in place of the stock copy.

    Raises:
        ValueError: if the member has no email on file (nothing to send to).
    """
    from core.events.channels import Channel, Message
    from core.events.emit import emit

    login_url = member.first_sign_in_url()
    html, text = render_launch_invite(member_name=member.display_name, login_url=login_url, email=member.primary_email)
    email = Message(title=INVITE_SUBJECT, body=text, url=login_url, html_body=html, trigger_kind="member.login_invite")
    return emit(
        "member.login_invite",
        target=member,
        context={"user": member.user, "member_name": member.display_name, "login_url": login_url},
        period=LAUNCH_PERIOD,
        messages={Channel.EMAIL: email},
    )


def _preview_name(to: str) -> str:
    """The member behind ``to`` for the preview's greeting, or "there" for an outside address."""
    from core.email_prefs import user_for_email
    from membership.models import Member

    user = user_for_email(to)
    member = Member.objects.filter(user_id=user.pk).first() if user is not None else None
    return member.display_name if member is not None else "there"


def send_launch_previews(to: str) -> None:
    """Send both variants to one inbox, straight through ``core.email.send``.

    Deliberately not the notification spine: a preview must not claim a member's
    :data:`LAUNCH_PERIOD` ledger slot, or the real send would skip them. A provider
    failure raises, so the command reports it instead of a preview that never arrived.
    """
    from core.email import send
    from membership.models import login_code_url

    name = _preview_name(to)
    html, text = render_launch_announcement(member_name=name, cta_url=profile_settings_url())
    # The trigger_kind is a literal on purpose: the email gallery's send-site lint reads it.
    send(to=to, subject=ANNOUNCEMENT_SUBJECT, trigger_kind="directory_guilds_launch.test", text_body=text, html_body=html)
    html, text = render_launch_invite(member_name=name, login_url=login_code_url(to), email=to)
    send(to=to, subject=INVITE_SUBJECT, trigger_kind="directory_guilds_launch.test", text_body=text, html_body=html)
