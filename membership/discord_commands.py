"""Membership's Discord slash commands: ``/whats-on``, ``/info``, ``/schedule-orientation``,
``/voting``, ``/members``, ``/create``, ``/cancel``, and ``/poll``.

Autodiscovered by :func:`core.events.discord_commands.autodiscover`. Each handler stays thin
— it resolves a guild/window, calls an existing manager/service method, and hands the result
to a builder in :mod:`core.events.discord_replies`. The domain logic lives in ``membership``
models/managers and :mod:`membership.orientations`; nothing new lands in the handlers.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta
from math import ceil
from typing import TYPE_CHECKING, cast

from core.events.discord_commands import ComponentHandler, SlashCommand, register, register_component
from core.events.discord_interactions import (
    ack_component_deferred,
    ack_deferred,
    error_reply,
    expire_poll,
    reply,
    send_followup,
    update_message,
)
from membership.when_text import WhenError
from core.events.discord_replies import (
    format_local,
    guild_not_specified_reply,
    hub_url,
    option_value,
    resolve_command_guild,
    truncate,
)

if TYPE_CHECKING:
    from django.contrib.auth.models import User

    from hub.forms import CommunityEventForm
    from membership.models import (
        CommunityEvent,
        CommunityEventDraft,
        Guild,
        GuildOrientationSettings,
        Member,
        MemberQuerySet,
    )
    from membership.vote_calculator import VoteStanding

logger = logging.getLogger(__name__)

# An interaction payload is Discord's JSON dict; the second arg is the resolved Member | None.
Interaction = dict

_SECTION_CAP = 8


# --- /whats-on ----------------------------------------------------------------


def _event_occurrences(frm: date, to: date) -> list[tuple[datetime, str, str]]:
    """Published community events expanded to concrete ``(datetime, title, url)`` in ``[frm, to]``.

    A recurring series carries no single future ``starts_at``, so each candidate row is asked
    for its concrete occurrences in the window (``occurrences_in``) — a plain ``starts_at``
    filter would silently drop a monthly series whose anchor is in the past.
    """
    from membership.models import CommunityEvent

    rows = CommunityEvent.objects.published().upcoming().candidates_for_window(frm, to)
    items = [(dt, row.title, row.public_url) for row in rows for dt in row.occurrences_in(frm, to)]
    items.sort(key=lambda item: item[0])
    return items


def _class_sessions(to: date) -> list[tuple[datetime, str, str]]:
    """Upcoming public class sessions starting on/before ``to`` as ``(datetime, title, url)``."""
    from classes.models import ClassSession

    sessions = ClassSession.objects.upcoming_public().filter(starts_at__date__lte=to).select_related("class_offering")
    items = [(s.starts_at, s.class_offering.title, s.class_offering.public_url) for s in sessions]
    items.sort(key=lambda item: item[0])
    return items


def _section_block(heading: str, items: list[tuple[datetime, str, str]], calendar_url: str) -> str:
    """One markdown section — a heading over linked, dated lines — or ``""`` when empty.

    Capped at :data:`_SECTION_CAP` items; an overflow appends a "…and more" line linking the
    full calendar so nothing is silently truncated.
    """
    if not items:
        return ""
    lines = [f"**{heading}**"]
    for dt, title, url in items[:_SECTION_CAP]:
        lines.append(f"• [{title}]({url}) — {format_local(dt)}")
    if len(items) > _SECTION_CAP:
        lines.append(f"…and more — full calendar: {calendar_url}")
    return "\n".join(lines)


def _whats_on(interaction: Interaction, member: Member | None) -> dict:
    """List community events + class sessions in the next 7 local days, each a linked, dated line."""
    from django.utils import timezone

    frm = timezone.localdate()
    to = frm + timedelta(days=7)
    calendar_url = hub_url("hub_community_calendar")

    blocks = [
        block
        for block in (
            _section_block("Events", _event_occurrences(frm, to), calendar_url),
            _section_block("Classes", _class_sessions(to), calendar_url),
        )
        if block
    ]
    if not blocks:
        return reply(f"Nothing scheduled in the next 7 days.\nBrowse the calendar: {calendar_url}", ephemeral=True)
    return reply("📅 **This week at Past Lives**\n\n" + "\n\n".join(blocks), ephemeral=True)


WHATS_ON = SlashCommand(
    name="whats-on",
    description="See community events and classes coming up in the next 7 days.",
    handler=_whats_on,
    requires_link=False,
    ephemeral=True,
    defer=False,
    scope="guild",
)

register(WHATS_ON)


# --- /info --------------------------------------------------------------------

_FAQ_LIMIT = 3
_ANSWER_LIMIT = 200
_FIELD_LIMIT = 1024


def _meeting_value(guild: Guild) -> str:
    """The guild's next-meeting text: the soonest upcoming occurrence, or the free-text schedule.

    Delegates to :meth:`Guild.next_meeting_occurrence` — the same authoritative method the guild
    page uses — so an event-based ``GUILD_MEETING`` is never dropped (the old reimplementation only
    understood the legacy cadence fields). Formats the returned :class:`NextMeeting` tuple; falls
    back to the free-text ``meeting_schedule`` only when there's no upcoming occurrence. Returns
    ``""`` when nothing is configured (caller omits the field).
    """
    occurrence = guild.next_meeting_occurrence()
    if occurrence is None:
        return guild.meeting_schedule
    parts = [occurrence.when.strftime("%A, %b %-d")]
    if occurrence.has_time:
        parts.append(occurrence.when.strftime("%-I:%M %p"))
    value = " · ".join(parts)
    if occurrence.location:
        value += f"\n{occurrence.location}"
    return value


def _staff_lines(guild: Guild) -> list[str]:
    """Lead-first lines of everyone with lead authority — each person once, with their titles."""
    lead = guild.guild_lead if guild.guild_lead_id else None
    lines: list[str] = []
    seen: set[int] = set()
    for staff_member, rows in guild.staff_by_member():
        titles = ", ".join(row.display_title for row in rows)
        if lead is not None and staff_member.pk == lead.pk:
            titles = f"Guild Lead, {titles}"
        lines.append(f"{staff_member.display_name} — {titles}")
        seen.add(staff_member.pk)
    if lead is not None and lead.pk not in seen:
        lines.insert(0, f"{lead.display_name} — Guild Lead")
    return lines


def _info(interaction: Interaction, member: Member | None) -> dict:
    """A guild summarized as one public embed — rules, next meeting, FAQ, links, staff.

    Every field is guarded so a heading never renders empty; a guild with no filled-in
    content gets a friendly nudge rather than a bare embed. The guild is resolved leniently
    (explicit ``guild`` option beats the channel map); an unresolved/inactive guild → the
    "which guild?" reply.
    """
    guild = resolve_command_guild(interaction)
    if guild is None:
        return guild_not_specified_reply()
    guild_url = hub_url("hub_guild_detail", guild.slug)

    fields: list[dict] = []
    if guild.about:
        fields.append(
            {
                "name": "About",
                "value": truncate(guild.about, _FIELD_LIMIT, suffix=" …more on the guild page"),
                "inline": False,
            }
        )
    if guild.essential_rules:
        fields.append(
            {"name": "Essential rules", "value": truncate(guild.essential_rules, _FIELD_LIMIT), "inline": False}
        )
    meeting = _meeting_value(guild)
    if meeting:
        fields.append({"name": "Next meeting", "value": meeting, "inline": False})
    faqs = list(guild.faq_items.all()[:_FAQ_LIMIT])
    if faqs:
        faq_value = "\n\n".join(f"**{item.question}**\n{truncate(item.answer, _ANSWER_LIMIT)}" for item in faqs)
        fields.append({"name": "FAQ", "value": truncate(faq_value, _FIELD_LIMIT), "inline": False})
    link_lines = [f"[{link.label}]({link.url})" for link in guild.links.all()]
    if guild.discord_url:
        link_lines.append(f"[Discord]({guild.discord_url})")
    if link_lines:
        fields.append({"name": "Links", "value": truncate("\n".join(link_lines), _FIELD_LIMIT), "inline": False})
    staff_lines = _staff_lines(guild)
    if staff_lines:
        fields.append({"name": "Staff", "value": truncate("\n".join(staff_lines), _FIELD_LIMIT), "inline": False})

    if not fields:
        return reply(f"This guild hasn't filled in its page yet — {guild_url}", ephemeral=True)

    fields.append({"name": "Full page", "value": f"[Open the {guild.name} page →]({guild_url})", "inline": False})
    embed = {"title": guild.name, "url": guild_url, "fields": fields}
    return reply(f"Here's **{guild.name}** on Past Lives:", ephemeral=False, embeds=[embed])


def _guild_dropdown_option() -> dict:
    """A ``guild`` slash-command option rendered as a dropdown of active guilds (optional).

    Shared by ``/info`` and ``/schedule-orientation`` (and mirrors ``/join-guild``'s picker):
    each choice's value is the guild ``slug`` (resolved by :func:`resolve_command_guild`), and it
    stays ``required=False`` so a member can omit it to use the current channel's guild. Built at
    *serialization* time (inside ``register_discord_commands`` → :meth:`SlashCommand.to_api_dict`),
    so the DB query is safe; capped at Discord's 25-choice limit; ships WITHOUT a ``choices`` key
    when there are no active guilds (an empty-choices option would 400 Discord's bulk command PUT,
    leaving the free-text field the lenient resolver still accepts).
    """
    from membership.models import Guild

    guilds = list(Guild.objects.filter(is_active=True).order_by("name"))[:25]
    option: dict = {
        "name": "guild",
        "description": "Which guild — pick one, or omit to use this channel's guild.",
        "type": 3,
        "required": False,
    }
    if guilds:
        option["choices"] = [{"name": g.name, "value": g.slug} for g in guilds]
    return option


def _info_guild_choices() -> list[dict]:
    """The ``/info`` options — just the guild dropdown."""
    return [_guild_dropdown_option()]


INFO = SlashCommand(
    name="info",
    description="Show a guild's rules, next meeting, FAQ, links, and staff.",
    handler=_info,
    options_builder=_info_guild_choices,
    requires_link=False,
    ephemeral=False,
    defer=False,
    scope="guild",
)

register(INFO)


# --- /schedule-orientation ----------------------------------------------------

_SLOT_LIST_CAP = 10
_PROPOSE_HINT = "propose your own with `date:` (YYYY-MM-DD) and `time:` (e.g. 5:30pm)"


def _slot_disambiguation(
    guild: Guild, settings_obj: GuildOrientationSettings, guild_url: str, *, prefix: str = ""
) -> dict:
    """List the guild's bookable slots (with pks) so the member can re-run with one chosen.

    Falls back to the custom-time hint when custom requests are allowed, or a guild-page
    pointer when neither posted times nor custom requests are available — never a dead end.
    """
    slots = list(guild.orientation_slots.bookable().order_by("starts_at")[:_SLOT_LIST_CAP])
    if slots:
        lines = [
            f"{prefix}Here are **{guild.name}**'s open orientation times — re-run "
            "`/schedule-orientation` with `slot:` set to one of these numbers:"
        ]
        for slot in slots:
            location = f" · {slot.location}" if slot.location else ""
            lines.append(f"`{slot.pk}` — {format_local(slot.starts_at)}{location}")
        if settings_obj.allow_custom_requests:
            lines.append(f"None of these work? {_PROPOSE_HINT[0].upper() + _PROPOSE_HINT[1:]}.")
        return reply("\n".join(lines), ephemeral=True)
    if settings_obj.allow_custom_requests:
        return reply(f"{prefix}No posted times right now — {_PROPOSE_HINT}.\n{guild_url}", ephemeral=True)
    return reply(f"{prefix}No orientation times are posted yet. Check {guild_url} for updates.", ephemeral=True)


def _requested_reply(guild: Guild, guild_url: str, detail: str) -> dict:
    """The shared "orientation requested" success copy (posted-slot or custom ``detail``)."""
    return reply(
        f"**Orientation requested — {guild.name}** ✅\n"
        f"{detail}\n"
        "Check your email for details — it's not official until a guild lead confirms.\n"
        f"View / cancel: {guild_url}",
        ephemeral=True,
    )


def _book_posted_slot(
    guild: Guild, member: Member, slot_opt: str, note: str, settings_obj: GuildOrientationSettings, guild_url: str
) -> dict:
    """Book a posted slot by its pk; a bad/unknown/unavailable pk re-lists the open times."""
    from membership import orientations
    from membership.models import OrientationError

    if not slot_opt.isdigit():
        return _slot_disambiguation(guild, settings_obj, guild_url, prefix="I didn't recognize that slot number. ")
    slot = guild.orientation_slots.filter(pk=int(slot_opt)).first()
    if slot is None or not slot.is_bookable:
        return _slot_disambiguation(guild, settings_obj, guild_url, prefix="That slot isn't available anymore. ")
    try:
        orientations.request_orientation(slot, member, note=note)
    except OrientationError as exc:
        return _slot_disambiguation(guild, settings_obj, guild_url, prefix=f"{exc} ")
    location = f" · {slot.location}" if slot.location else ""
    return _requested_reply(guild, guild_url, f"{format_local(slot.starts_at)}{location}")


def _book_custom(
    guild: Guild,
    member: Member,
    date_opt: str | None,
    time_opt: str | None,
    note: str,
    settings_obj: GuildOrientationSettings,
    guild_url: str,
) -> dict:
    """Propose a custom time — needs both date + time, guild must allow custom requests."""
    from membership import orientations
    from membership.models import OrientationError

    if not settings_obj.allow_custom_requests:
        return _slot_disambiguation(
            guild, settings_obj, guild_url, prefix=f"**{guild.name}** only takes posted times — pick one below. "
        )
    if not (date_opt and time_opt):
        return reply(
            f"I couldn't read that time — use both `date:` (YYYY-MM-DD) and `time:` (HH:MM).\n{guild_url}",
            ephemeral=True,
        )
    try:
        starts_at = orientations.parse_proposed_time(date_opt, time_opt)
        orientations.request_custom_orientation(guild, member, starts_at, note=note)
    except OrientationError as exc:
        return reply(f"{exc}\n{guild_url}", ephemeral=True)
    return _requested_reply(
        guild, guild_url, f"Proposed: {format_local(starts_at)} — the guild lead will confirm a time."
    )


def _schedule_orientation(interaction: Interaction, member: Member | None) -> dict:
    """Request an orientation: guard the guild + duplicates, then book a posted slot XOR a custom time.

    ``requires_link=True`` guarantees ``member`` is non-``None``; ``defer=True`` because this
    fans out the request email + lead notifications. Exactly one path runs — a posted ``slot``
    or a custom ``date`` + ``time``; both or neither shows the slot picker.
    """
    from membership.models import GuildOrientationSettings

    member = cast("Member", member)  # requires_link=True: dispatch resolved a linked member before this runs
    guild = resolve_command_guild(interaction)
    if guild is None:
        return guild_not_specified_reply()
    guild_url = hub_url("hub_guild_detail", guild.slug)

    settings_obj = GuildOrientationSettings.objects.filter(guild=guild).first()
    if settings_obj is None or not settings_obj.is_accepting:
        return reply(f"**{guild.name}** isn't taking orientation requests right now.\n{guild_url}", ephemeral=True)
    if member.is_oriented_for(guild):
        return reply(f"You're already oriented for **{guild.name}**. 🎉\n{guild_url}", ephemeral=True)
    if member.active_orientation_for(guild) is not None:
        return reply(
            f"You already have an orientation request in for **{guild.name}** — the lead will confirm it.\nSee {guild_url}",
            ephemeral=True,
        )

    slot_opt = option_value(interaction, "slot")
    date_opt = option_value(interaction, "date")
    time_opt = option_value(interaction, "time")
    note = option_value(interaction, "note") or ""

    has_slot = bool(slot_opt)
    has_custom = bool(date_opt) or bool(time_opt)
    if has_slot == has_custom:  # both given or neither → show the picker
        return _slot_disambiguation(guild, settings_obj, guild_url)
    if slot_opt:  # truthiness (not has_slot) so the str | None narrows for _book_posted_slot
        return _book_posted_slot(guild, member, slot_opt, note, settings_obj, guild_url)
    return _book_custom(guild, member, date_opt, time_opt, note, settings_obj, guild_url)


# The non-guild options for /schedule-orientation (static); the guild dropdown is prepended
# at registration time by _schedule_options() so its choices reflect the live guild list.
_SCHEDULE_EXTRA_OPTIONS: list[dict] = [
    {
        "name": "slot",
        "description": "A posted time's number (run with no options to see the list).",
        "type": 3,
        "required": False,
    },
    {
        "name": "date",
        "description": "Propose your own date, YYYY-MM-DD (needs a time too).",
        "type": 3,
        "required": False,
    },
    {
        "name": "time",
        "description": "Propose your own time, e.g. 17:30 or 5:30pm (needs a date too).",
        "type": 3,
        "required": False,
    },
    {
        "name": "note",
        "description": "Anything the orienter should know (optional).",
        "type": 3,
        "required": False,
    },
]


def _schedule_options() -> list[dict]:
    """The ``/schedule-orientation`` options — the guild dropdown, then slot/date/time/note."""
    return [_guild_dropdown_option(), *_SCHEDULE_EXTRA_OPTIONS]


SCHEDULE_ORIENTATION = SlashCommand(
    name="schedule-orientation",
    description="Request an orientation for a guild.",
    handler=_schedule_orientation,
    options_builder=_schedule_options,
    requires_link=True,
    ephemeral=True,
    defer=True,
    scope="guild",
)

register(SCHEDULE_ORIENTATION)


# --- /voting ------------------------------------------------------------------

_BAR_WIDTH = 12
_EMBED_DESCRIPTION_LIMIT = 4096
_MEDALS = ("🥇", "🥈", "🥉")


def _bar(bar_pct: float) -> str:
    """A fixed-width block bar for ``bar_pct`` — relative to the leader, exactly like the hub page.

    ``max(1, …)`` because standings only contain guilds with points > 0, so a tiny-but-nonzero
    guild always renders a sliver — mirroring the ``min-width: 2px`` bar in ``hub/_vote_bar.html``.
    """
    filled = max(1, round(bar_pct / 100 * _BAR_WIDTH))
    return "█" * filled + "░" * (_BAR_WIDTH - filled)


def _standings_block(standings: list[VoteStanding], zero_point_names: list[str]) -> str:
    """Every guild's line — ranked bars first, then the zero-point guilds with empty bars.

    Bars sit in inline code so the fixed width holds in Discord's proportional font. Ranked rows
    get medals + bold names for the top three, then plain ``4.`` numbering; the zero-point guilds
    (active, alphabetical, no votes yet) follow unranked — just an all-empty bar and ``— 0 pts`` —
    so the standings always show the whole field, exactly like the voting page's mission. An empty
    tally leads with the wide-open nudge instead of a bare stub.
    """
    lines: list[str] = []
    if not standings:
        lines.append("No votes yet this cycle — the standings are wide open. Be the first!")
    for rank, row in enumerate(standings, start=1):
        bar = f"`{_bar(row['bar_pct'])}`"
        if rank <= len(_MEDALS):
            lines.append(f"{_MEDALS[rank - 1]} {bar} **{row['guild_name']}** — {row['total_points']} pts")
        else:
            lines.append(f"`{rank}.` {bar} {row['guild_name']} — {row['total_points']} pts")
    empty_bar = "░" * _BAR_WIDTH
    lines.extend(f"`{empty_bar}` {name} — 0 pts" for name in zero_point_names)
    return "\n".join(lines)


def _voting(interaction: Interaction, member: Member | None) -> dict:
    """This month's live guild-funding standings as one **public** embed.

    Visible to everyone in the channel, so it carries no personal data: the member's own
    ballot is deliberately absent (a `/vote` nudge points at the private path instead —
    that reply is ephemeral). Read-only: the link button is the change-your-vote path
    (the page owns the form). No writes, no notifications.
    """
    from membership.cycle import get_cycle_context
    from membership.models import Guild
    from membership.vote_calculator import WEIGHTS, compute_live_standings

    standings = compute_live_standings()
    cycle = get_cycle_context()

    # The shared tally (hub page included) drops zero-point guilds; here every active guild
    # renders so the whole field is visible — the voteless ones follow the ranked rows.
    ranked_names = {row["guild_name"] for row in standings}
    active_names = Guild.objects.filter(is_active=True).order_by("name").values_list("name", flat=True)
    zero_point_names = [name for name in active_names if name not in ranked_names]

    description = truncate(
        "Your votes decide how the monthly funding pool is split. "
        f"This cycle closes **{cycle['cycle_closes_on']}**."
        f"\n\n{_standings_block(standings, zero_point_names)}"
        "\n\nSet or change your own picks with `/vote` — only you see that reply.",
        _EMBED_DESCRIPTION_LIMIT,
    )
    footer = f"Weighting: 1st = {WEIGHTS['1st']} pts · 2nd = {WEIGHTS['2nd']} pts · 3rd = {WEIGHTS['3rd']} pts"
    embed = {
        "title": f"Guild funding — {cycle['current_cycle_label']}",
        "description": description,
        "footer": {"text": footer},
    }
    button_row = {
        "type": 1,
        "components": [
            {"type": 2, "style": 5, "label": "Open the voting page", "url": hub_url("hub_guild_voting")},
        ],
    }
    return reply("", ephemeral=False, embeds=[embed], components=[button_row])


VOTING = SlashCommand(
    name="voting",
    description="See this month's live guild-funding standings.",
    handler=_voting,
    requires_link=True,
    ephemeral=False,
    defer=False,
    scope="guild",
)

register(VOTING)


# --- /members -----------------------------------------------------------------

_CARDS_PER_PAGE = 5
_CARD_TITLE_LIMIT = 80
_CARD_DESCRIPTION_LIMIT = 950
_SKILLS_SHOWN = 6
_CUSTOM_ID_LIMIT = 100
_SEARCH_LIMIT = 40
# The fixed characters of a members custom_id at a 4-digit page: "members:9999::".
_CUSTOM_ID_OVERHEAD = len("members:9999::")


def _search_budget(slug_token: str) -> int:
    """How many search characters fit a ``members:`` custom_id alongside ``slug_token``.

    Computed once per slash invocation (§5.3): the search is truncated to this budget
    *before* querying or encoding, so page 1 and every subsequent page run the same query
    and every generated id fits Discord's 100-char cap by construction (4-digit page
    headroom included).
    """
    return min(_SEARCH_LIMIT, _CUSTOM_ID_LIMIT - _CUSTOM_ID_OVERHEAD - len(slug_token))


def _members_queryset(guild: Guild | None, search: str) -> MemberQuerySet:
    """The directory queryset for one ``/members`` page — filter, prefetches, ordering.

    Built on :meth:`MemberQuerySet.directory_visible` unconditionally (privacy parity with
    the app directory by construction — no admin bypass), narrowed by the optional guild
    and search, with the hub view's exact efficiency block so a page build costs zero
    queries per card beyond the page fetch.
    """
    from allauth.account.models import EmailAddress
    from django.db.models import Prefetch

    from membership.models import Member, MemberContact

    qs = Member.objects.directory_visible()
    if guild is not None:
        qs = qs.filter(guild_memberships__guild=guild)
    if search:
        qs = qs.search_skills(search)
    return (
        qs.select_related("membership_plan", "user")
        .prefetch_related(
            Prefetch(
                "user__emailaddress_set",
                queryset=EmailAddress.objects.filter(primary=True),
                to_attr="_primary_emailaddresses",
            ),
            "guild_memberships__guild",
            "skills__skill__category",
            Prefetch(
                "contacts",
                queryset=MemberContact.objects.filter(show_in_directory=True),
                to_attr="visible_contacts",
            ),
        )
        .order_by("full_legal_name")
    )


def _skills_lines(member: Member) -> list[str]:
    """The 🎨 skills + 💼 commissions lines, both inside the ``is_public("skills")`` gate.

    Mirrors the directory card template exactly: the commissions block nests *inside* the
    skills visibility gate (there is no independent commissions key), so skills-hidden hides
    the commissions line too. Approved skills are filtered in Python from the prefetched
    rows (``skills__skill__category``) — the ``approved_skills`` property would re-query
    per card.
    """
    from membership.models import Skill

    if not member.is_public("skills"):
        return []
    lines: list[str] = []
    approved = [ms.skill.name for ms in member.skills.all() if ms.skill.status == Skill.Status.APPROVED]
    if approved:
        shown = ", ".join(approved[:_SKILLS_SHOWN])
        more = f", +{len(approved) - _SKILLS_SHOWN} more" if len(approved) > _SKILLS_SHOWN else ""
        lines.append(f"🎨 Skills: {shown}{more}")
    if member.open_for_commissions:
        note = f" — {member.commission_note}" if member.commission_note else ""
        lines.append(f"💼 Open for commissions!{note}")
    return lines


def _member_card(member: Member) -> dict:
    """One directory-card embed for ``member`` — every line honors the app's privacy gates.

    Line-for-line mirror of ``templates/hub/member_directory.html``: a line is omitted
    entirely when its data is empty or the member's ``is_public()`` toggle hides it — a
    card never shows an empty labeled row. ``about_me`` is deliberately omitted for the
    6000-char message budget; the footer's link button opens the full card in the app.
    """
    from membership.models import Member

    meta = [member.get_member_type_display()]
    if member.pronouns and member.pronouns != Member.Pronouns.PREFER_NOT and member.is_public("pronouns"):
        meta.append(member.pronouns)
    if member.join_date:
        meta.append(member.join_date.strftime("Joined %b %Y"))
    lines = [" · ".join(meta)]

    guild_names = [gm.guild.name for gm in member.guild_memberships.all()]
    if guild_names:
        lines.append("🛠️ Guilds: " + ", ".join(guild_names))
    lines.extend(_skills_lines(member))
    email = member.primary_email
    if email and member.is_public("email"):
        lines.append(f"✉️ {email}")
    if member.phone and member.is_public("phone"):
        lines.append(f"📞 {member.phone}")
    if member.discord_handle and member.is_public("discord_handle"):
        lines.append(f"💬 {member.discord_handle}")
    # Set by _members_queryset's Prefetch(to_attr=…) — rows already show_in_directory-filtered.
    for contact in member.visible_contacts:  # type: ignore[attr-defined]
        lines.append(f"🔗 {contact.label} — {contact.value}")

    card: dict = {
        "title": truncate(member.display_name, _CARD_TITLE_LIMIT),
        "description": truncate("\n".join(lines), _CARD_DESCRIPTION_LIMIT),
    }
    if member.profile_photo and member.is_public("profile_photo"):
        photo_url = member.profile_photo.url
        # Local FileSystemStorage yields relative /media/… URLs Discord can't fetch — skip
        # the thumbnail (text-only card) rather than ship a broken embed. Prod R2 URLs are
        # absolute, public, and unsigned.
        if photo_url.startswith("http"):
            card["thumbnail"] = {"url": photo_url}
    return card


def _members_footer(page: int, page_count: int, total: int, guild: Guild | None, search: str) -> dict:
    """The always-last footer embed — page position, filtered total, active filters, edit note."""
    parts = [f"Page {page} of {page_count} · {total} member{'' if total == 1 else 's'}"]
    if guild is not None:
        parts.append(guild.name)
    if search:
        parts.append(f"“{search}”")
    return {"description": " · ".join(parts) + "\nEdit what you share from the app — Settings → Directory."}


def _directory_link_button() -> dict:
    """The link button to the hub member directory — present in every state, including empty."""
    return {"type": 2, "style": 5, "label": "Open the full directory", "url": hub_url("hub_member_directory")}


def _members_components(page: int, page_count: int, slug_token: str, search: str) -> list[dict]:
    """The one action row: Prev/Next pagers (omitted on a single page) + the directory link.

    Prev/Next are secondary-style buttons disabled at the bounds (never a misfire), each
    carrying the stateless ``members:<page>:<slug|->:<search>`` custom_id of its target page.
    """
    buttons: list[dict] = []
    if page_count > 1:
        buttons.append(
            {
                "type": 2,
                "style": 2,
                "label": "◀ Prev",
                "custom_id": f"members:{page - 1}:{slug_token}:{search}",
                "disabled": page <= 1,
            }
        )
        buttons.append(
            {
                "type": 2,
                "style": 2,
                "label": "Next ▶",
                "custom_id": f"members:{page + 1}:{slug_token}:{search}",
                "disabled": page >= page_count,
            }
        )
    buttons.append(_directory_link_button())
    return [{"type": 1, "components": buttons}]


def _members_page(guild: Guild | None, search: str, page: int) -> dict:
    """Build one directory page: ``{"embeds": …, "components": …, "total": N}``.

    The single builder both entry points (slash + component click) call. Stateless: it
    re-counts and re-queries fresh every time, clamping the requested page to
    ``[1, page_count]`` so a click after the roster shifted lands on a real page.
    """
    qs = _members_queryset(guild, search)
    total = qs.count()
    page_count = max(1, ceil(total / _CARDS_PER_PAGE))
    page = min(max(1, page), page_count)
    embeds = [_member_card(m) for m in qs[(page - 1) * _CARDS_PER_PAGE : page * _CARDS_PER_PAGE]]
    embeds.append(_members_footer(page, page_count, total, guild, search))
    slug_token = guild.slug if guild is not None else "-"
    return {
        "embeds": embeds,
        "components": _members_components(page, page_count, slug_token, search),
        "total": total,
    }


def _members_empty_text(guild: Guild | None, search: str) -> str:
    """The no-matches copy — names the active filters and the fix, never a dead end."""
    scope = ""
    if guild is not None:
        scope += f" in **{guild.name}**"
    if search:
        scope += f" for **“{search}”**"
    return f"No members match{scope}. Try a broader search, or run `/members` without the filters to browse everyone."


def _members(interaction: Interaction, member: Member | None) -> dict:
    """Browse the member directory as ephemeral profile-card embeds with Prev/Next paging."""
    from membership.models import Guild

    member = cast("Member", member)  # requires_link=True: dispatch resolved a linked member before this runs
    guild: Guild | None = None
    slug = option_value(interaction, "guild")
    if slug:
        guild = Guild.objects.filter(is_active=True, slug=slug).first()
        if guild is None:  # only reachable via the zero-guild free-text edge
            return guild_not_specified_reply()
    slug_token = guild.slug if guild is not None else "-"
    search = (option_value(interaction, "search") or "").strip()[: _search_budget(slug_token)]

    page_data = _members_page(guild, search, 1)
    if page_data["total"] == 0:
        return reply(
            _members_empty_text(guild, search),
            ephemeral=True,
            components=[{"type": 1, "components": [_directory_link_button()]}],
        )
    return reply("", ephemeral=True, embeds=page_data["embeds"], components=page_data["components"])


def _members_component(interaction: Interaction, member: Member | None) -> dict:
    """A Prev/Next click: re-parse the stateless custom_id, rebuild the page, update in place."""
    from membership.models import Guild

    custom_id = interaction["data"]["custom_id"]
    parts = custom_id.split(":", 3)
    if len(parts) != 4 or not parts[1].isdigit() or int(parts[1]) < 1:
        logger.warning("Malformed members custom_id %r", custom_id)
        return error_reply()
    _prefix, page_str, slug_token, search = parts

    guild: Guild | None = None
    if slug_token != "-":
        guild = Guild.objects.filter(is_active=True, slug=slug_token).first()
        if guild is None:  # the guild vanished (or deactivated) mid-browse — a genuine edge
            logger.warning("members component: guild slug %r no longer resolves", slug_token)
            return error_reply()

    page_data = _members_page(guild, search, int(page_str))
    if page_data["total"] == 0:  # the roster emptied between clicks — replace the stale cards
        return update_message(
            "",
            embeds=[{"description": _members_empty_text(guild, search)}],
            components=[{"type": 1, "components": [_directory_link_button()]}],
        )
    return update_message("", embeds=page_data["embeds"], components=page_data["components"])


def _members_options() -> list[dict]:
    """The ``/members`` options — the guild filter dropdown + a free-text search."""
    return [
        {**_guild_dropdown_option(), "description": "Filter to one guild — omit to browse everyone."},
        {"name": "search", "description": "Match a name or skill.", "type": 3, "required": False},
    ]


MEMBERS = SlashCommand(
    name="members",
    description="Browse the member directory — profiles, skills, and contact info.",
    handler=_members,
    options_builder=_members_options,
    requires_link=True,
    ephemeral=True,
    defer=False,
    scope="guild",
)

register(MEMBERS)
register_component(ComponentHandler(prefix="members", handler=_members_component, requires_link=True))


# --- /create ------------------------------------------------------------------

# The "guild" choice value that means "no guild — a site-wide community event".
_GENERAL_VALUE = "__general__"
# Fallback event length in minutes when the `when` phrase has no explicit end time.
_DEFAULT_DURATION_MINUTES = 60
# The "email" option values that trigger an audience email blast (everything else is "no email").
_EMAIL_AUDIENCES = ("guild_members", "all_active")
# Per-member creation caps (counted only when a Confirm actually creates an event).
_CREATE_RATE_SCOPE = "discord_create"
_CREATE_HOURLY_LIMIT = 4
_CREATE_DAILY_LIMIT = 12
# How long a preview's Confirm button stays live before the draft is treated as expired.
_CONFIRM_WINDOW_MINUTES = 30
# The recurrence cadences the command exposes — the basic set; the rest stay web-only.
_RECURRENCE_VALUES = ("none", "weekly", "semi_monthly", "monthly")

_NOT_PERMITTED = (
    "Posting an event straight to the calendar is limited to guild leads and admins right now. "
    "Ask a lead to post it for you, or reach out to a Past Lives organizer."
)
_SETUP_INCOMPLETE = (
    "Your Past Lives account isn't fully set up yet, so I can't create an event under your name. "
    "Please reach out to a Past Lives organizer."
)
_EMAIL_NEEDS_GUILD = "Pick a guild to email its members, or choose the whole membership."
_PREVIEW_EXPIRED = "This preview expired or was already handled. Run /create again if you still want the event."
_PREVIEW_CANCELLED = "Cancelled. Nothing was created."
_CREATE_FANOUT_FAILED = (
    "Something went wrong on our side and the event was not fully posted. Please check the calendar or try again."
)


def _when_error_copy(error: WhenError) -> str:
    """The member-facing reply for each typed `when` rejection — every one names a fix."""
    copy = {
        WhenError.UNPARSEABLE: (
            "I could not read that date and time. Try one of these: next friday 6pm, "
            "tomorrow 7pm to 9pm, 2026-09-12 18:00."
        ),
        WhenError.NO_TIME: "I got the day but not a start time. Add one, like next friday 6pm.",
        WhenError.IN_PAST: (
            "That time has already passed. Events need a start in the future. Check the date and try again."
        ),
        WhenError.TOO_FAR: "That date is more than a year away. Double check the year and try again.",
    }
    return copy[error]


def _rate_limited_reply() -> dict:
    """The friendly per-member cap refusal, pointing at the hub as the fallback."""
    return reply(
        f"You have hit the limit for creating events from Discord ({_CREATE_HOURLY_LIMIT} per hour, "
        f"{_CREATE_DAILY_LIMIT} per day). Try again in a bit, or use the hub: {hub_url('hub_propose_event')}",
        ephemeral=True,
    )


def _duration_minutes(interaction: Interaction) -> int:
    """The ``duration_minutes`` option as an int, defaulting to :data:`_DEFAULT_DURATION_MINUTES`.

    Discord validates the integer option (``min_value=1``), so a supplied value is a positive int;
    an omitted / blank value falls back to the default one-hour length.
    """
    raw = option_value(interaction, "duration_minutes")
    return int(raw) if raw else _DEFAULT_DURATION_MINUTES


def _guild_not_found_reply(raw: str) -> dict:
    """The ephemeral "no such guild" nudge, listing active guilds so it's never a dead end."""
    from membership.models import Guild

    names = list(Guild.objects.filter(is_active=True).order_by("name").values_list("name", flat=True)[:25])
    content = (
        f"I couldn't find an active guild matching `{raw}`. Pick one from the dropdown, choose "
        "General, or run this in the guild's Discord channel."
    )
    if names:
        content += "\n\nGuilds: " + ", ".join(names) + "."
    return reply(content, ephemeral=True)


def _resolve_target_guild(interaction: Interaction) -> tuple[Guild | None, dict | None]:
    """Resolve the target guild: ``(guild, None)`` on success, ``(None, error_reply)`` on failure.

    An explicit ``General`` choice or an omitted option with no channel match → a site-wide event
    (``guild=None``); an explicit guild slug that no longer resolves → the not-found reply.
    """
    from membership.models import Guild

    raw = option_value(interaction, "guild")
    if raw == _GENERAL_VALUE:
        return None, None
    if raw:
        guild = Guild.objects.filter(slug=raw, is_active=True).first()
        if guild is None:
            return None, _guild_not_found_reply(raw)
        return guild, None
    return Guild.objects.for_discord_channel(interaction.get("channel_id", "")), None


def _build_event_form(
    title: str,
    details: str,
    guild: Guild | None,
    start_naive: datetime,
    end_naive: datetime,
    calendar: str,
    *,
    location: str = "",
    video_url: str = "",
    recurrence: str = "none",
) -> CommunityEventForm:
    """Bind the shared :class:`~hub.forms.CommunityEventForm` (member mode) to the command's inputs.

    Reuses the web "Propose an event" form so date/time coercion (naive → aware), the
    end-after-start rule, and the URL validation are all enforced exactly once, in one
    place. ``details`` binds straight to the form's ``description`` field (a
    blank-friendly Textarea), so an omitted value is an empty string.
    """
    from hub.forms import CommunityEventForm

    data = {
        "title": title,
        "description": details,
        "starts_at": start_naive.strftime("%Y-%m-%dT%H:%M"),
        "ends_at": end_naive.strftime("%Y-%m-%dT%H:%M"),
        "location": location,
        "video_url": video_url,
        "recurrence": recurrence,
        "google_calendar_target": calendar,
    }
    if guild is not None:
        data["guild"] = str(guild.pk)
    return CommunityEventForm(data=data, as_member=True)


def _form_error_reply(form: CommunityEventForm) -> dict:
    """Surface the form's own validation message.

    Reachable here: the end-before-start rule, a ``title`` longer than the model's
    200-char limit, and a malformed ``video_url``. All surface as the ephemeral
    "adjust and try again" reply — nothing is created.
    """
    message = " ".join(str(error) for errors in form.errors.values() for error in errors)
    return reply(f"{message} Nothing was created — adjust and try again.", ephemeral=True)


def _published_reply(event: CommunityEvent, emailed: int) -> dict:
    """The success reply for a live event — the hub link is the edit affordance (v1)."""
    content = "Your event is live on the Community Calendar. ✅"
    if emailed:
        content += f"\nEmailed {emailed} member{'' if emailed == 1 else 's'}."
    button_row = {
        "type": 1,
        "components": [{"type": 2, "style": 5, "label": "Open the event", "url": event.public_url}],
    }
    return reply(content, ephemeral=True, components=[button_row])


def _pending_reply() -> dict:
    """The reply for a proposal that entered the review queue (APPROVAL policy)."""
    return reply(
        "Thanks — your event was submitted for review. A lead or admin will take a look, and "
        "you'll get a note when they respond.",
        ephemeral=True,
    )


def _finalize_event(
    member: Member,
    guild: Guild | None,
    form: CommunityEventForm,
    policy: str,
    authored: bool,
    email_choice: str,
) -> dict:
    """Author or propose the validated event, optionally email the audience, and build the reply.

    ``authored`` (a lead/admin) publishes straight to the calendar via
    :meth:`CommunityEvent.schedule_or_go_live` (mirroring the web guild/admin event views);
    everyone else routes through :meth:`CommunityEvent.propose`, whose OPEN/APPROVAL branch
    decides publish-now vs the review queue. The email fan-out runs only on a published event,
    and its own try/except keeps a post-publish email failure from reporting failure on an event
    that is already live: the address fan-out is logged and the reply still reports it live
    (``emailed = 0``). Once the event has a pk, the reply is always the published/pending one — the
    caller's outer guard then only catches genuine publish/propose failures (nothing created).
    """
    from membership.models import CommunityEvent

    event = form.save(commit=False)
    if authored:
        event.guild = guild
        event.event_type = (
            CommunityEvent.EventType.GUILD_MEETING if guild is not None else CommunityEvent.EventType.COMMUNITY
        )
        event.created_by = member.user
        event.save()
        event.schedule_or_go_live(actor=member.user)
        published = True
    else:
        published = event.propose(by=member.user, guild=guild, policy=policy, editing=False)

    if not published:
        return _pending_reply()
    emailed = 0
    if email_choice in _EMAIL_AUDIENCES:
        try:
            emailed = event.email_announcement(email_choice, actor=member.user)
        except Exception:
            logger.exception("create: email announcement failed after the event was published")
    return _published_reply(event, emailed)


def _local_naive(dt: datetime) -> datetime:
    """An aware datetime as naive site-local — the form's expected input shape."""
    from django.utils import timezone as django_tz

    return django_tz.localtime(dt).replace(tzinfo=None)


def _preview_branch_line(*, authored: bool, guild: Guild | None, policy: str, emails: bool) -> str:
    """The preview's what-happens-on-confirm line (§6.C) — exactly one branch."""
    from core.models import SiteConfiguration

    if authored:
        if guild is not None:
            return "You can post for this guild, so this will publish right away."
        return "You can post site wide events, so this will publish right away."
    if policy == SiteConfiguration.MemberEventPolicy.OPEN:
        return "This will publish right away."
    line = (
        "This will go to the review queue. A lead or admin will take a look, and you will hear back when they decide."
    )
    if emails:
        line += " The email option only applies when an event publishes, so it will not be sent for a proposal."
    return line


def _create_preview_reply(draft: CommunityEventDraft, *, authored: bool, policy: str) -> dict:
    """The ephemeral preview: every chosen value, the publish-vs-propose branch, Confirm / Cancel."""
    from membership.models import CommunityEvent

    guild = draft.guild
    lines = [
        "**Here is your event. Please confirm.**",
        f"**{draft.title}**",
        f"{format_local(draft.starts_at)} to {format_local(draft.ends_at)} (Pacific)",
        f"Guild: {guild.name}" if guild is not None else "Guild: Whole makerspace",
    ]
    if draft.location:
        lines.append(f"Location: {draft.location}")
    if draft.video_url:
        lines.append(f"Join online: {draft.video_url}")
    if draft.recurrence != CommunityEvent.Recurrence.NONE:
        lines.append(f"Repeats: {CommunityEvent.Recurrence(draft.recurrence).label}")
    lines.append(f"Calendar: {CommunityEvent.GoogleCalendarTarget(draft.google_calendar_target).label}")
    emails = draft.email_choice in _EMAIL_AUDIENCES
    if emails:
        audience = "this guild's members" if draft.email_choice == "guild_members" else "the whole membership"
        lines.append(f"Also emails: {audience}")
    lines.append("")
    lines.append(_preview_branch_line(authored=authored, guild=guild, policy=policy, emails=emails))
    row = {
        "type": 1,
        "components": [
            {"type": 2, "style": 3, "label": "Create event", "custom_id": f"create:confirm:{draft.pk}"},
            {"type": 2, "style": 4, "label": "Cancel", "custom_id": f"create:cancel:{draft.pk}"},
        ],
    }
    return reply("\n".join(lines), ephemeral=True, components=[row])


def _create_event(interaction: Interaction, member: Member | None) -> dict:
    """Stage a Community Calendar event from Discord: validate cheaply, then preview + confirm.

    Every guard returns an immediate ephemeral reply — nothing slow happens here and
    nothing is published: the setup-incomplete check, the per-member rate-limit peek,
    an unknown guild, an unreadable ``when`` phrase, a guild-less guild-members email
    choice, the shared form's validation, and the not-permitted gate (a non-lead/admin
    under the DISABLED member-event policy). All green → the member's older unconfirmed
    drafts are dropped, a fresh :class:`~membership.models.CommunityEventDraft` is
    written, and the preview with Confirm / Cancel buttons goes back. The publish /
    propose fan-out runs only on the Confirm click (:func:`_create_component`).

    ``requires_link=True`` guarantees ``member`` is non-``None`` (dispatch shows the
    connect prompt for an unlinked caller before this runs).
    """
    from django.utils import timezone as django_tz

    from core.abuse_limits import keyed_within_limits
    from core.models import SiteConfiguration
    from membership.models import CommunityEvent, CommunityEventDraft
    from membership.when_text import parse_when

    member = cast("Member", member)
    if member.user is None:
        return reply(_SETUP_INCOMPLETE, ephemeral=True)
    if not keyed_within_limits(
        _CREATE_RATE_SCOPE, str(member.pk), hourly_limit=_CREATE_HOURLY_LIMIT, daily_limit=_CREATE_DAILY_LIMIT
    ):
        return _rate_limited_reply()

    guild, guild_error = _resolve_target_guild(interaction)
    if guild_error is not None:
        return guild_error

    when = parse_when(
        option_value(interaction, "when") or "",
        duration_minutes=_duration_minutes(interaction),
        now=django_tz.localtime(django_tz.now()).replace(tzinfo=None),
    )
    if when.error is not None:
        return reply(_when_error_copy(when.error), ephemeral=True)

    email_choice = option_value(interaction, "email") or "none"
    if email_choice == "guild_members" and guild is None:
        return reply(_EMAIL_NEEDS_GUILD, ephemeral=True)

    title = (option_value(interaction, "title") or "").strip()
    details = (option_value(interaction, "details") or "").strip()
    location = (option_value(interaction, "location") or "").strip()
    video_url = (option_value(interaction, "video_url") or "").strip()
    calendar = option_value(interaction, "calendar") or CommunityEvent.GoogleCalendarTarget.MEMBER
    recurrence = option_value(interaction, "recurrence") or CommunityEvent.Recurrence.NONE
    form = _build_event_form(
        title,
        details,
        guild,
        cast("datetime", when.start),
        cast("datetime", when.end),
        calendar,
        location=location,
        video_url=video_url,
        recurrence=recurrence,
    )
    if not form.is_valid():
        return _form_error_reply(form)

    policy = SiteConfiguration.load().member_event_policy
    authored = member.is_fog_admin or (guild is not None and member.can_edit_guild(guild))
    if not authored and policy == SiteConfiguration.MemberEventPolicy.DISABLED:
        return reply(_NOT_PERMITTED, ephemeral=True)

    CommunityEventDraft.objects.claimable_for(member.user).delete()
    cleaned = form.cleaned_data
    draft = CommunityEventDraft.objects.create(
        author=member.user,
        guild=guild,
        title=cleaned["title"],
        starts_at=cleaned["starts_at"],
        ends_at=cleaned["ends_at"],
        location=cleaned["location"],
        video_url=cleaned["video_url"],
        description=cleaned["description"],
        recurrence=cleaned["recurrence"],
        google_calendar_target=cleaned["google_calendar_target"] or CommunityEvent.GoogleCalendarTarget.MEMBER,
        email_choice=email_choice,
    )
    return _create_preview_reply(draft, authored=authored, policy=policy)


def _confirm_create(interaction: Interaction, member: Member, draft: CommunityEventDraft) -> dict:
    """The Confirm click: re-check, atomically claim, ack type 6, then publish / propose.

    Authority and site policy are re-checked (state can shift between preview and click),
    the per-member rate limit is re-peeked, and the draft is claimed with a single
    conditional ``UPDATE … WHERE confirmed_at IS NULL`` — the one point that resolves a
    double-click race: the loser updates 0 rows and must NOT create a second event. The
    fan-out (announce + Google + Discord push, optional email) far exceeds Discord's
    3-second window, so the click is acked with a type-6 deferred update and the preview
    is then PATCHed in place — ``components`` falls back to ``[]`` on purpose, so the
    Confirm / Cancel row is always replaced (by the success reply's own link button, or
    by nothing).
    """
    from django.utils import timezone as django_tz

    from core.abuse_limits import keyed_within_limits, record_keyed_attempt
    from core.models import SiteConfiguration
    from membership.models import CommunityEventDraft

    policy = SiteConfiguration.load().member_event_policy
    guild = draft.guild
    authored = member.is_fog_admin or (guild is not None and member.can_edit_guild(guild))
    if not authored and policy == SiteConfiguration.MemberEventPolicy.DISABLED:
        draft.delete()
        return update_message(_NOT_PERMITTED)
    if not keyed_within_limits(
        _CREATE_RATE_SCOPE, str(member.pk), hourly_limit=_CREATE_HOURLY_LIMIT, daily_limit=_CREATE_DAILY_LIMIT
    ):
        draft.delete()
        return update_message(_rate_limited_reply()["data"]["content"])

    claimed = CommunityEventDraft.objects.filter(pk=draft.pk, author=member.user, confirmed_at__isnull=True).update(
        confirmed_at=django_tz.now()
    )
    if not claimed:
        return update_message(_PREVIEW_EXPIRED)

    ack_component_deferred(interaction["id"], interaction["token"])
    form = _build_event_form(
        draft.title,
        draft.description,
        guild,
        _local_naive(draft.starts_at),
        _local_naive(draft.ends_at),
        draft.google_calendar_target,
        location=draft.location,
        video_url=draft.video_url,
        recurrence=draft.recurrence,
    )
    try:
        if not form.is_valid():  # the same data validated at preview time — a failure here is a bug
            raise ValueError(f"Draft {draft.pk} failed re-validation on confirm: {form.errors.as_json()}")
        followup = _finalize_event(member, guild, form, policy, authored, draft.email_choice)
        record_keyed_attempt(
            _CREATE_RATE_SCOPE, str(member.pk), hourly_limit=_CREATE_HOURLY_LIMIT, daily_limit=_CREATE_DAILY_LIMIT
        )
    except Exception:
        logger.exception("create: publish/propose fan-out failed after claim")
        followup = reply(_CREATE_FANOUT_FAILED, ephemeral=True)
    data = followup["data"]
    send_followup(
        interaction["token"],
        content=data.get("content", ""),
        embeds=data.get("embeds"),
        components=data.get("components") or [],
    )
    return {}


def _create_component(interaction: Interaction, member: Member | None) -> dict:
    """The Confirm / Cancel click on a ``/create`` preview — the only place an event is created.

    Parses ``create:<action>:<draft_pk>``, reloads the caller's own unconfirmed draft
    (missing / foreign / already-claimed → the friendly expired reply), enforces the
    confirm window, and routes Cancel (delete + in-place replace) or Confirm
    (:func:`_confirm_create`).
    """
    from django.utils import timezone as django_tz

    from membership.models import CommunityEventDraft

    member = cast("Member", member)
    if member.user is None:
        return update_message(_SETUP_INCOMPLETE)
    custom_id = interaction["data"]["custom_id"]
    parts = custom_id.split(":")
    if len(parts) != 3 or parts[1] not in ("confirm", "cancel") or not parts[2].isdigit():
        logger.warning("Malformed create custom_id %r", custom_id)
        return error_reply()
    _prefix, action, pk_str = parts

    draft = CommunityEventDraft.objects.claimable_for(member.user).filter(pk=int(pk_str)).first()
    if draft is None:
        return update_message(_PREVIEW_EXPIRED)
    if django_tz.now() - draft.created_at > timedelta(minutes=_CONFIRM_WINDOW_MINUTES):
        draft.delete()
        return update_message(_PREVIEW_EXPIRED)

    if action == "cancel":
        draft.delete()
        return update_message(_PREVIEW_CANCELLED)
    return _confirm_create(interaction, member, draft)


def _create_options() -> list[dict]:
    """The ``/create`` options, guild dropdown built from the live active-guild list.

    Required options (title, when) come first, as Discord requires — every optional
    option must follow them. The guild dropdown always carries at least the ``General``
    choice, so it never ships an empty ``choices`` list (which would 400 the bulk
    command PUT); active guilds are capped so the total stays within Discord's
    25-choice limit.
    """
    from membership.models import Guild

    guilds = list(Guild.objects.filter(is_active=True).order_by("name"))[:24]
    guild_choices = [{"name": "General (whole makerspace)", "value": _GENERAL_VALUE}]
    guild_choices += [{"name": g.name, "value": g.slug} for g in guilds]
    return [
        {
            "name": "title",
            "description": "The event name shown on the calendar, like Monthly Potluck.",
            "type": 3,
            "required": True,
        },
        {
            "name": "when",
            "description": "When it happens, like next friday 6pm, tomorrow 7pm to 9pm, or 2026-09-12 18:00.",
            "type": 3,
            "required": True,
        },
        {
            "name": "duration_minutes",
            "description": "How long in minutes when your when has no end time. Default 60.",
            "type": 4,
            "required": False,
            "min_value": 1,
        },
        {
            "name": "guild",
            "description": "Which guild this is for. Pick General, or skip it to use this channel's guild.",
            "type": 3,
            "required": False,
            "choices": guild_choices,
        },
        {
            "name": "details",
            "description": "More about it. What to bring, the agenda, who it is for.",
            "type": 3,
            "required": False,
        },
        {
            "name": "location",
            "description": "Where it happens. A room, an address, or leave blank.",
            "type": 3,
            "required": False,
        },
        {
            "name": "video_url",
            "description": "A link to join online, like a Google Meet URL.",
            "type": 3,
            "required": False,
        },
        {
            "name": "recurrence",
            "description": "Whether it repeats. Default is a one time event.",
            "type": 3,
            "required": False,
            "choices": [
                {"name": "Does not repeat", "value": "none"},
                {"name": "Every week", "value": "weekly"},
                {"name": "Twice a month", "value": "semi_monthly"},
                {"name": "Every month", "value": "monthly"},
            ],
        },
        {
            "name": "calendar",
            "description": "Which Google calendar it posts to. Default is members only.",
            "type": 3,
            "required": False,
            "choices": [
                {"name": "Members only (default)", "value": "member"},
                {"name": "Public", "value": "public"},
            ],
        },
        {
            "name": "email",
            "description": "Also email members about it. Off by default.",
            "type": 3,
            "required": False,
            "choices": [
                {"name": "Don't email", "value": "none"},
                {"name": "This guild's members", "value": "guild_members"},
                {"name": "The whole membership", "value": "all_active"},
            ],
        },
    ]


CREATE = SlashCommand(
    name="create",
    description="Create a Community Calendar event.",
    handler=_create_event,
    options_builder=_create_options,
    requires_link=True,
    ephemeral=True,
    defer=False,
    scope="guild",
)

register(CREATE)
register_component(ComponentHandler(prefix="create", handler=_create_component, requires_link=True))


# --- /cancel ------------------------------------------------------------------

_CANCEL_EMPTY = (
    "You have no upcoming events you can cancel from here. "
    "If one of your published events needs to come down, ask a lead or admin."
)
_CANCEL_GONE = "That event was already handled. Nothing more to do."
_CANCEL_NO_AUTH = "You can no longer cancel that event from here. Ask a lead or admin to remove it."
_CANCEL_KEPT = "Kept. Nothing changed."
_CANCEL_WITHDRAWN = "Proposal withdrawn."
_CANCEL_DELETED = "Event cancelled and removed from the calendar, Google Calendar, and Discord."
# Discord's per-select-menu option cap.
_CANCEL_PICKER_CAP = 25


def _withdrawable_events(member: Member) -> "list[CommunityEvent]":
    """The member's own not-yet-published proposals (the states :meth:`withdraw` accepts)."""
    from membership.models import CommunityEvent

    states = (CommunityEvent.ModerationState.PENDING, CommunityEvent.ModerationState.CHANGES_REQUESTED)
    return list(CommunityEvent.objects.filter(submitted_by=member.user, moderation_state__in=states))


def _deletable_events(member: Member) -> "list[CommunityEvent]":
    """Upcoming published/scheduled events this member may delete — the hub's exact authority.

    A fog admin may delete any (mirroring the admin-only ``event_delete`` view); anyone
    else only a guild event whose guild they can edit (mirroring ``guild_event_delete``).
    "Upcoming" is an end in the future or a recurring series (whose anchor may be past).
    The per-guild ``can_edit_guild`` check runs in Python — the candidate set is small
    and the object-level check is the single source of authority truth.
    """
    from django.db.models import Q
    from django.utils import timezone as django_tz

    from membership.models import CommunityEvent

    states = (CommunityEvent.ModerationState.PUBLISHED, CommunityEvent.ModerationState.SCHEDULED)
    candidates = (
        CommunityEvent.objects.filter(moderation_state__in=states)
        .filter(Q(ends_at__gte=django_tz.now()) | ~Q(recurrence=CommunityEvent.Recurrence.NONE))
        .select_related("guild")  # the per-event can_edit_guild check below reads event.guild
    )
    if member.is_fog_admin:
        return list(candidates)
    # `exclude(guild=None)` guarantees a guild at the query level; the cast narrows for mypy
    # without adding a runtime branch the tests could never take.
    return [event for event in candidates.exclude(guild=None) if member.can_edit_guild(cast("Guild", event.guild))]


def _cancellable_events(member: Member) -> "list[CommunityEvent]":
    """Everything the member may cancel, soonest-starting first, capped for the picker.

    The withdraw and delete sets can't overlap (their moderation states are disjoint),
    so a plain concatenation is dedupe-free.
    """
    events = _withdrawable_events(member) + _deletable_events(member)
    events.sort(key=lambda event: event.starts_at)
    return events[:_CANCEL_PICKER_CAP]


def _cancel_authority(member: Member, event: CommunityEvent) -> str | None:
    """Which cancel branch applies: ``"withdraw"``, ``"delete"``, or ``None`` (no authority)."""
    from membership.models import CommunityEvent

    withdraw_states = (CommunityEvent.ModerationState.PENDING, CommunityEvent.ModerationState.CHANGES_REQUESTED)
    if event.moderation_state in withdraw_states and event.submitted_by_id == getattr(member.user, "pk", None):
        return "withdraw"
    delete_states = (CommunityEvent.ModerationState.PUBLISHED, CommunityEvent.ModerationState.SCHEDULED)
    if event.moderation_state in delete_states and (
        member.is_fog_admin or (event.guild is not None and member.can_edit_guild(event.guild))
    ):
        return "delete"
    return None


def _cancel_picker_reply(events: "list[CommunityEvent]") -> dict:
    """The ephemeral Step-1 select menu of cancellable events (soonest first)."""
    options = [
        {"label": truncate(event.title, 100), "value": str(event.pk), "description": format_local(event.starts_at)}
        for event in events
    ]
    row = {
        "type": 1,
        "components": [
            {"type": 3, "custom_id": "cancel:pick", "placeholder": "Pick an event", "options": options},
        ],
    }
    content = "Which event do you want to cancel?"
    if len(events) == _CANCEL_PICKER_CAP:
        content += f"\nOnly your next {_CANCEL_PICKER_CAP} are listed. The rest are on the hub."
    return reply(content, ephemeral=True, components=[row])


def _cancel_confirm_card(event: CommunityEvent, branch: str) -> dict:
    """The Step-2 in-place confirm card — states plainly what confirming does."""
    from membership.models import CommunityEvent

    when_display = format_local(event.starts_at)
    if branch == "withdraw":
        content = (
            f"Withdraw your proposal **{truncate(event.title, 100)}** ({when_display})? "
            "It was never published, so it just comes off the review queue."
        )
    else:
        content = (
            f"Cancel **{truncate(event.title, 100)}** ({when_display})? It will be removed from the "
            "Community Calendar, Google Calendar, and Discord. Members will not be notified automatically."
        )
        if event.recurrence != CommunityEvent.Recurrence.NONE:
            content += " This removes the whole repeating series."
    row = {
        "type": 1,
        "components": [
            {"type": 2, "style": 4, "label": "Yes, cancel it", "custom_id": f"cancel:confirm:{event.pk}"},
            {"type": 2, "style": 2, "label": "Keep it", "custom_id": f"cancel:keep:{event.pk}"},
        ],
    }
    return update_message(content, components=[row])


def _cancel(interaction: Interaction, member: Member | None) -> dict:
    """List the caller's cancellable events as a select menu (or the friendly empty state)."""
    member = cast("Member", member)
    if member.user is None:
        return reply(_SETUP_INCOMPLETE, ephemeral=True)
    events = _cancellable_events(member)
    if not events:
        return reply(_CANCEL_EMPTY, ephemeral=True)
    return _cancel_picker_reply(events)


def _confirm_cancel(interaction: Interaction, member: Member, event: CommunityEvent, branch: str) -> dict:
    """Execute the confirmed cancel — withdraw in-band, delete behind a type-6 deferred ack.

    Withdraw is one DB delete (nothing was ever pushed), so it answers in-band. Delete
    unwinds Google and Discord over REST first — slow, so the click is acked type-6 and
    the confirm card PATCHed afterward (buttons stripped via ``components=[]``). The
    remove calls are best-effort by design (mirroring the hub's ``event_delete``): each
    logs its own failure and the row still goes away.
    """
    from membership.models import InvalidEventTransition

    if branch == "withdraw":
        try:
            event.withdraw(by=cast("User", member.user))
        except InvalidEventTransition:
            return update_message(_CANCEL_GONE)
        return update_message(_CANCEL_WITHDRAWN)

    ack_component_deferred(interaction["id"], interaction["token"])
    try:
        event.remove_from_google()
        event.remove_from_discord()
        event.strip_discord_announcement_buttons()
        event.delete()
        content = _CANCEL_DELETED
    except Exception:
        logger.exception("cancel: delete fan-out failed for event %s", event.pk)
        content = _CREATE_FANOUT_FAILED
    send_followup(interaction["token"], content=content, components=[])
    return {}


def _cancel_component(interaction: Interaction, member: Member | None) -> dict:
    """The ``/cancel`` select + button clicks: pick → confirm card → withdraw / delete / keep.

    Authority is re-resolved from the live row on every click (state can shift between
    steps — someone else may approve, publish, or delete first): a vanished row or a
    lost authority never stacktraces, it lands on the friendly gone / no-authority copy.
    """
    from membership.models import CommunityEvent

    member = cast("Member", member)
    if member.user is None:
        return update_message(_SETUP_INCOMPLETE)
    custom_id = interaction["data"]["custom_id"]
    parts = custom_id.split(":")
    if len(parts) == 2 and parts[1] == "pick":
        values = interaction["data"].get("values") or []
        pk_str = values[0] if values else ""
    elif len(parts) == 3 and parts[1] in ("confirm", "keep"):
        pk_str = parts[2]
    else:
        logger.warning("Malformed cancel custom_id %r", custom_id)
        return error_reply()
    if not pk_str.isdigit():
        logger.warning("Malformed cancel target %r in %r", pk_str, custom_id)
        return error_reply()

    if len(parts) == 3 and parts[1] == "keep":
        return update_message(_CANCEL_KEPT)

    event = CommunityEvent.objects.filter(pk=int(pk_str)).first()
    if event is None:
        return update_message(_CANCEL_GONE)
    branch = _cancel_authority(member, event)
    if branch is None:
        return update_message(_CANCEL_NO_AUTH)

    if parts[1] == "pick":
        return _cancel_confirm_card(event, branch)
    return _confirm_cancel(interaction, member, event, branch)


CANCEL = SlashCommand(
    name="cancel",
    description="Withdraw or cancel one of your upcoming events.",
    handler=_cancel,
    requires_link=True,
    ephemeral=True,
    defer=False,
    scope="guild",
)

register(CANCEL)
register_component(ComponentHandler(prefix="cancel", handler=_cancel_component, requires_link=True))


# --- event component (RSVP toggle + ⚙ Manage) ---------------------------------

_RSVP_CLOSED = "This event has already ended, so RSVPs are closed."
_EVENT_GONE = "This event is no longer on the calendar."
_MANAGE_CREATOR_ONLY = (
    "Editing and cancelling a published event is handled by a guild lead or admin. "
    "Ask a lead if this event needs a change."
)


def _manage_no_auth_reply(event: CommunityEvent) -> dict:
    """The friendly ephemeral refusal for a non-manager clicking ⚙ — never a dead end."""
    return reply(
        f"Only the organizer or a guild lead can manage this event. You can see the details here: {event.public_url}",
        ephemeral=True,
    )


def _event_edit_url(event: CommunityEvent) -> str:
    """The absolute hub edit URL for the manage card's link button.

    Mirrors ``templates/hub/event_detail.html``: a guild event edits via
    ``hub_guild_event_edit``, a site-wide event via ``hub_event_edit``. ``hub_url`` prefixes
    ``MEMBER_BASE_URL`` so Discord's link button gets an absolute https URL (it rejects
    relative paths).
    """
    guild = event.guild
    if guild is not None:
        return hub_url("hub_guild_event_edit", guild.pk, event.pk)
    return hub_url("hub_event_edit", event.pk)


def _manage_card(event: CommunityEvent, member: Member) -> dict:
    """The ephemeral ⚙ Manage card.

    An authorized manager (``_cancel_authority`` yields a branch) gets Edit + Cancel; a
    creator without edit/cancel authority gets an honest "ask a lead" card with a plain link
    to the event page — the same pre-existing gap ``/cancel``'s empty state acknowledges, not
    widened and not hidden.
    """
    branch = _cancel_authority(member, event)
    when = format_local(event.next_occurrence_start())
    if branch is not None:
        row = [
            {"type": 2, "style": 5, "label": "Edit on the hub", "url": _event_edit_url(event)},
            {"type": 2, "style": 4, "label": "Cancel this event", "custom_id": f"event:cancelcard:{event.pk}"},
        ]
        content = f"**Managing {truncate(event.title, 100)}** ({when})"
    else:
        row = [{"type": 2, "style": 5, "label": "Open the event page", "url": event.public_url}]
        content = f"**Managing {truncate(event.title, 100)}** ({when})\n\n{_MANAGE_CREATOR_ONLY}"
    return reply(content, ephemeral=True, components=[{"type": 1, "components": row}])


def _event_component(interaction: Interaction, member: Member | None) -> dict:
    """The announcement buttons: RSVP toggle, ⚙ Manage card, and the Cancel jump.

    Parses ``event:<action>:<pk>``; a malformed id or unknown action lands on ``error_reply``
    (the ``members`` pattern). A missing/unpublished event is the friendly "no longer on the
    calendar" ephemeral. ``requires_link=True`` guarantees a linked ``member``.
    """
    from membership.models import CommunityEvent

    member = cast("Member", member)
    custom_id = interaction["data"]["custom_id"]
    parts = custom_id.split(":")
    if len(parts) != 3 or parts[1] not in ("rsvp", "manage", "cancelcard") or not parts[2].isdigit():
        logger.warning("Malformed event custom_id %r", custom_id)
        return error_reply()
    action, pk = parts[1], int(parts[2])
    event = CommunityEvent.objects.published().filter(pk=pk).first()
    if event is None:
        return reply(_EVENT_GONE, ephemeral=True)

    if action == "rsvp":
        if event.rsvps_closed:
            return reply(_RSVP_CLOSED, ephemeral=True)
        event.toggle_rsvp(member)
        # Type-7 rebuild from the DB (components omitted → Discord keeps the existing buttons):
        # your name visibly appears in / disappears from the Attendees field.
        return update_message("", embeds=[event.discord_announcement_embed()])
    if action == "manage":
        if not event.can_manage_from_discord(member):
            return _manage_no_auth_reply(event)
        return _manage_card(event, member)
    # cancelcard: re-resolve the authority (state may have shifted), then edit the ephemeral
    # manage card into the EXISTING confirm card whose buttons route to _cancel_component.
    branch = _cancel_authority(member, event)
    if branch is None:
        return update_message(_CANCEL_NO_AUTH)
    return _cancel_confirm_card(event, branch)


register_component(ComponentHandler(prefix="event", handler=_event_component, requires_link=True))


# --- /poll --------------------------------------------------------------------

# Discord native-poll limits (verified against the API docs, 2026-08-26).
_POLL_QUESTION_MAX = 300
_POLL_ANSWER_MAX = 55
_POLL_MIN_ANSWERS = 2
_POLL_MAX_ANSWERS = 10
_POLL_DEFAULT_DURATION_HOURS = 24
# Duration options in hours → the label shown in the styled header. 768h is Discord's max.
_POLL_DURATION_LABELS: dict[int, str] = {
    1: "1 hour",
    4: "4 hours",
    8: "8 hours",
    24: "1 day",
    72: "3 days",
    168: "1 week",
    336: "2 weeks",
    768: "32 days",
}

# A Discord custom-emoji token at the start of an answer: <:name:id> or <a:name:id> (animated).
_CUSTOM_EMOJI_RE = re.compile(r"^<a?:(\w+):(\d+)>")
# Leading-emoji extraction is deliberately CONSERVATIVE: an answer's leading emoji is
# pulled into the poll answer's icon only when it is confidently a single, well-formed,
# Discord-acceptable emoji. A missed icon is cosmetic; a malformed poll_media.emoji makes
# Discord reject the whole interaction, so anything ambiguous (a non-emoji symbol, a lone
# or doubled flag, a tag-sequence flag, a dangling joiner) keeps the answer text untouched.
_ZWJ = 0x200D
_VS16 = 0xFE0F  # variation selector 16 — forces emoji presentation
_SKIN_TONES = frozenset(range(0x1F3FB, 0x1F400))
_REGIONAL = frozenset(range(0x1F1E6, 0x1F1FF + 1))  # regional indicators; a flag is exactly a pair
_TAG_RANGE = range(0xE0020, 0xE007F + 1)  # tag characters — only appear inside tag-sequence flags
# Unambiguous default-emoji SMP blocks. The non-emoji SMP blocks below 0x1F300 (mahjong,
# dominoes, playing cards, enclosed alphanumerics) are intentionally excluded.
_EMOJI_BASE_RANGES: tuple[tuple[int, int], ...] = (
    (0x1F300, 0x1F5FF),  # miscellaneous symbols & pictographs (🔥 🎬 🎲 🏴 …)
    (0x1F600, 0x1F64F),  # emoticons
    (0x1F680, 0x1F6FF),  # transport & map symbols
    (0x1F7E0, 0x1F7EB),  # large colored circles and squares
    (0x1F900, 0x1F9FF),  # supplemental symbols & pictographs
    (0x1FA70, 0x1FAFF),  # symbols & pictographs extended-A
)
# Curated default-emoji-presentation BMP codepoints. Their text-presentation neighbours
# (☀ U+2600, ✏ U+270F, ⌘ U+2318, …) are deliberately absent — bare, they are not emoji.
_EMOJI_BASE_BMP: frozenset[int] = frozenset(
    {
        0x231A, 0x231B, 0x23E9, 0x23EA, 0x23EB, 0x23EC, 0x23F0, 0x23F3,
        0x25FD, 0x25FE, 0x2614, 0x2615, *range(0x2648, 0x2654), 0x267F,
        0x2693, 0x26A1, 0x26AA, 0x26AB, 0x26BD, 0x26BE, 0x26C4, 0x26C5,
        0x26CE, 0x26D4, 0x26EA, 0x26F2, 0x26F3, 0x26F5, 0x26FA, 0x26FD,
        0x2705, 0x270A, 0x270B, 0x2728, 0x274C, 0x274E, 0x2753, 0x2754,
        0x2755, 0x2757, 0x2795, 0x2796, 0x2797, 0x27B0, 0x27BF, 0x2B1B,
        0x2B1C, 0x2B50, 0x2B55,
    }
)  # fmt: skip

_POLL_TOO_FEW = "Give me at least 2 answers, separated by semicolons. Like: Alien; Clue; The Thing"
_POLL_TOO_MANY = "Discord polls allow at most 10 answers. Trim the list and try again."
_POLL_QUESTION_TOO_LONG = "The question has to fit in 300 characters. Shorten it and try again."
_POLL_DUPLICATE = "You have the same answer twice. Make each one different and try again."


def _split_answers(raw: str) -> list[str]:
    """Split the ``answers`` option into trimmed, non-empty answer strings.

    Semicolons win: when the input contains any ``;`` it splits on ``;`` (so an answer may
    carry a literal ``|``); otherwise it splits on ``|``. Each piece is trimmed and empty
    pieces are dropped.
    """
    separator = ";" if ";" in raw else "|"
    return [piece.strip() for piece in raw.split(separator) if piece.strip()]


def _is_emoji_base(code: int) -> bool:
    """Whether ``code`` unambiguously starts a default-emoji grapheme (curated ranges + BMP set)."""
    return code in _EMOJI_BASE_BMP or any(low <= code <= high for low, high in _EMOJI_BASE_RANGES)


def _emoji_prefix(text: str) -> str:
    """A leading, confidently well-formed single emoji of ``text``, or ``""`` when not confident.

    Recognizes a flag as exactly one regional-indicator pair, and a base emoji plus its
    VS16 / skin-tone / ZWJ-joined sequence. Bails (returns ``""``) on anything ambiguous — a
    lone or doubled flag, a tag-sequence flag, a dangling ZWJ, or a base outside the curated
    emoji ranges — so :func:`_answer_media` keeps the original answer text rather than risk a
    malformed ``poll_media.emoji``. Custom Discord tokens are matched separately.
    """
    if not text:
        return ""
    first = ord(text[0])

    # A flag is exactly two regional indicators; a lone one, or a third that would merge two
    # flags, is not a confident single emoji.
    if first in _REGIONAL:
        pair = len(text) >= 2 and ord(text[1]) in _REGIONAL
        tripled = len(text) >= 3 and ord(text[2]) in _REGIONAL
        return text[:2] if pair and not tripled else ""

    if not _is_emoji_base(first):
        return ""

    end = 1
    while end < len(text):
        code = ord(text[end])
        if code in _TAG_RANGE:
            return ""  # a tag-sequence flag (e.g. Scotland) — keep the whole answer untouched
        if code == _VS16 or code in _SKIN_TONES:
            end += 1
        elif code == _ZWJ and end + 1 < len(text) and _is_emoji_base(ord(text[end + 1])):
            end += 2  # a ZWJ plus the emoji base it joins (family / profession sequences)
        else:
            break  # a lone trailing ZWJ is left out of the prefix, never leaked into the icon
    return text[:end]


def _clean_remainder(text: str) -> str:
    """Trim whitespace plus any stray zero-width joiner / variation selector from a remainder."""
    return text.strip().strip("\u200d\ufe0f").strip()


def _answer_media(answer: str) -> dict:
    """The ``poll_media`` object for one trimmed answer, pulling a leading emoji into its icon.

    A leading custom-emoji token (``<:name:id>`` / ``<a:name:id>``) becomes ``{"id": <id>}``
    with the token stripped from the text, falling back to the token's name when nothing else
    remains (Discord rejects empty answer text). A confidently well-formed leading unicode
    emoji becomes ``{"name": <emoji>}`` with the emoji stripped; an emoji-only answer, or one
    whose leading glyph is not confidently a single emoji, keeps its original text and carries
    no emoji field.
    """
    custom = _CUSTOM_EMOJI_RE.match(answer)
    if custom is not None:
        name, emoji_id = custom.group(1), custom.group(2)
        remainder = answer[custom.end() :].strip()
        return {"text": remainder or name, "emoji": {"id": emoji_id}}
    prefix = _emoji_prefix(answer)
    if prefix:
        remainder = _clean_remainder(answer[len(prefix) :])
        if remainder:
            return {"text": remainder, "emoji": {"name": prefix}}
        return {"text": answer}  # emoji-only: keep the original string, no emoji field
    return {"text": answer}


def _poll_duration_hours(interaction: Interaction) -> int:
    """The ``duration`` option in hours, defaulting to :data:`_POLL_DEFAULT_DURATION_HOURS`.

    Discord constrains the integer option to the registered choices, so a supplied value is
    always one of :data:`_POLL_DURATION_LABELS`; an omitted value falls back to one day.
    """
    raw = option_value(interaction, "duration")
    return int(raw) if raw else _POLL_DEFAULT_DURATION_HOURS


def _poll_multiselect(interaction: Interaction) -> bool:
    """The raw boolean ``multiselect`` option (default ``False`` when omitted).

    Read straight from the interaction, not via :func:`option_value`, which stringifies
    every value — ``str(False)`` is truthy. Discord sends a JSON boolean for a type-5 option.
    """
    for option in interaction.get("data", {}).get("options", []):
        if option.get("name") == "multiselect":
            return bool(option.get("value"))
    return False


def _answer_too_long_reply(answer_text: str) -> dict:
    """The ephemeral "shorten this answer" reply, quoting the first over-long answer."""
    shown = truncate(answer_text, _POLL_ANSWER_MAX)
    return reply(
        f'Each answer has to fit in {_POLL_ANSWER_MAX} characters. Shorten "{shown}" and try again.', ephemeral=True
    )


def _poll_header(display_name: str, hours: int, multiselect: bool) -> str:
    """The styled content line above the native poll widget — attribution, no ping."""
    pick = "pick any" if multiselect else "pick one"
    return f"📊 **Poll from {display_name}**  ·  open for {_POLL_DURATION_LABELS[hours]}  ·  {pick}"


def _poll(interaction: Interaction, member: Member | None) -> dict:
    """Post a native Discord poll in the channel, credited to the asker in the header line.

    Every guard returns an immediate ephemeral reply naming the fix and posts nothing; a
    poll can't be edited once live, so validation is airtight up front. The happy path
    returns a public (flags 0) type-4 reply carrying the poll object itself — no extra REST
    call. ``requires_link=True`` guarantees ``member`` is non-``None`` (dispatch shows the
    connect prompt for an unlinked caller first), and ``defer=False`` because the deferred
    followup path cannot carry a poll.
    """
    member = cast("Member", member)  # requires_link=True: dispatch resolved a linked member before this runs
    question = (option_value(interaction, "question") or "").strip()
    if len(question) > _POLL_QUESTION_MAX:
        return reply(_POLL_QUESTION_TOO_LONG, ephemeral=True)

    pieces = _split_answers(option_value(interaction, "answers") or "")
    if len(pieces) < _POLL_MIN_ANSWERS:
        return reply(_POLL_TOO_FEW, ephemeral=True)
    if len(pieces) > _POLL_MAX_ANSWERS:
        return reply(_POLL_TOO_MANY, ephemeral=True)

    media = [_answer_media(piece) for piece in pieces]
    for item in media:
        if len(item["text"]) > _POLL_ANSWER_MAX:
            return _answer_too_long_reply(item["text"])
    texts = [item["text"] for item in media]
    if len(set(texts)) != len(texts):
        return reply(_POLL_DUPLICATE, ephemeral=True)

    hours = _poll_duration_hours(interaction)
    multiselect = _poll_multiselect(interaction)
    poll = {
        "question": {"text": question},
        "answers": [{"poll_media": item} for item in media],
        "duration": hours,
        "allow_multiselect": multiselect,
    }
    # A one-button ⚙ row so the asker (or an admin) can end the poll early. The creator pk
    # rides statelessly in the custom_id; reply() carries a poll and components together, and
    # already pins allowed_mentions on the poll branch.
    gear_row = {"type": 1, "components": [{"type": 2, "style": 2, "label": "⚙", "custom_id": f"poll:end:{member.pk}"}]}
    return reply(
        _poll_header(member.display_name, hours, multiselect), ephemeral=False, poll=poll, components=[gear_row]
    )


def _poll_options() -> list[dict]:
    """The ``/poll`` options — required ``question`` + ``answers`` first, then duration + multiselect.

    ``duration`` is a type-4 INTEGER choice option with integer choice values (a string
    option with int values fails Discord's command registration); ``multiselect`` is a
    type-5 BOOLEAN. Neither touches the DB, so this is a pure, import-safe builder.
    """
    duration_choices = [
        {"name": f"{label} (default)" if hours == _POLL_DEFAULT_DURATION_HOURS else label, "value": hours}
        for hours, label in _POLL_DURATION_LABELS.items()
    ]
    return [
        {
            "name": "question",
            "description": "What you are asking, like Which movie should we watch?",
            "type": 3,
            "required": True,
        },
        {
            "name": "answers",
            "description": "The choices, separated by semicolons, like Alien; Clue; The Thing. Between 2 and 10.",
            "type": 3,
            "required": True,
        },
        {
            "name": "duration",
            "description": "How long voting stays open. Default is 1 day.",
            "type": 4,
            "required": False,
            "choices": duration_choices,
        },
        {
            "name": "multiselect",
            "description": "Let people pick more than one answer. Off by default.",
            "type": 5,
            "required": False,
        },
    ]


POLL = SlashCommand(
    name="poll",
    description="Post a poll for the channel to vote on.",
    handler=_poll,
    options_builder=_poll_options,
    requires_link=True,
    ephemeral=False,
    defer=False,
    scope="guild",
)

register(POLL)


# --- poll component (⚙ End poll) ----------------------------------------------

_POLL_END_NO_AUTH = "Only the person who started this poll or an admin can end it."
_POLL_ENDED = "Poll closed."
_POLL_ALREADY_ENDED = "This poll has already ended."


def _poll_component(interaction: Interaction, member: Member | None) -> dict:
    """The ⚙ End-poll click: the asker or a fog admin ends the poll early.

    Parses ``poll:end:<creator_member_pk>``; a malformed id lands on ``error_reply``, a
    stranger on the friendly refusal. An authorized clicker gets a **type-5 ephemeral** ack
    (NOT type 6 — Discord refuses to edit a message carrying a poll, so a type-6 ack's
    ``@original`` followups would fail silently), then the poll is expired via one bot REST
    call. On success the clicker sees "Poll closed."; on any failure (already expired, deleted)
    the followup says "This poll has already ended." Never a stacktrace.
    """
    member = cast("Member", member)
    custom_id = interaction["data"]["custom_id"]
    parts = custom_id.split(":")
    if len(parts) != 3 or parts[1] != "end" or not parts[2].isdigit():
        logger.warning("Malformed poll custom_id %r", custom_id)
        return error_reply()
    creator_pk = int(parts[2])
    if member.pk != creator_pk and not member.is_fog_admin:
        return reply(_POLL_END_NO_AUTH, ephemeral=True)

    ack_deferred(interaction["id"], interaction["token"], ephemeral=True)
    ended = expire_poll(interaction["channel_id"], interaction["message"]["id"])
    send_followup(
        interaction["token"],
        content=_POLL_ENDED if ended else _POLL_ALREADY_ENDED,
        allowed_mentions={"parse": []},
    )
    return {}


register_component(ComponentHandler(prefix="poll", handler=_poll_component, requires_link=True))
