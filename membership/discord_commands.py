"""Membership's Discord slash commands: ``/whats-on``, ``/info``, and ``/schedule-orientation``.

Autodiscovered by :func:`core.events.discord_commands.autodiscover`. Each handler stays thin
— it resolves a guild/window, calls an existing manager/service method, and hands the result
to a builder in :mod:`core.events.discord_replies`. The domain logic lives in ``membership``
models/managers and :mod:`membership.orientations`; nothing new lands in the handlers.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, cast

from core.events.discord_commands import SlashCommand, register
from core.events.discord_interactions import reply
from core.events.discord_replies import (
    format_local,
    guild_not_specified_reply,
    hub_url,
    option_value,
    resolve_command_guild,
    truncate,
)

if TYPE_CHECKING:
    from membership.models import Guild, GuildOrientationSettings, Member

# An interaction payload is Discord's JSON dict; the second arg is the resolved Member | None.
Interaction = dict

_SECTION_CAP = 8

_GUILD_OPTION = {
    "name": "guild",
    "description": "Which guild (name). Omit to use this channel's guild.",
    "type": 3,
    "required": False,
}


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


INFO = SlashCommand(
    name="info",
    description="Show a guild's rules, next meeting, FAQ, links, and staff.",
    handler=_info,
    options=[_GUILD_OPTION],
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


SCHEDULE_ORIENTATION = SlashCommand(
    name="schedule-orientation",
    description="Request an orientation for a guild.",
    handler=_schedule_orientation,
    options=[
        _GUILD_OPTION,
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
    ],
    requires_link=True,
    ephemeral=True,
    defer=True,
    scope="guild",
)

register(SCHEDULE_ORIENTATION)
