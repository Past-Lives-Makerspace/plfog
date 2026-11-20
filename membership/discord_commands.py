"""Membership's Discord slash commands, ``/info``, ``/voting`` and ``/poll``, plus the
#calendar announcement buttons (RSVP, the ⚙ Manage card and its Cancel confirm).

Autodiscovered by :func:`core.events.discord_commands.autodiscover`. Each handler stays thin
— it resolves a guild/window, calls an existing manager/service method, and hands the result
to a builder in :mod:`core.events.discord_replies`. The domain logic lives in ``membership``
models/managers and :mod:`membership.orientations`; nothing new lands in the handlers.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, cast

from core.events.discord_commands import (
    ComponentHandler,
    ModalHandler,
    SlashCommand,
    register,
    register_component,
    register_modal,
)
from core.events.discord_interactions import (
    ack_component_deferred,
    ack_deferred,
    checkbox,
    error_reply,
    expire_poll,
    modal,
    modal_label,
    parse_modal_values,
    reply,
    send_followup,
    string_select,
    text_display,
    text_input,
    update_message,
)
from core.events.discord_replies import (
    format_local,
    guild_not_specified_reply,
    hub_url,
    resolve_command_guild,
    truncate,
)

if TYPE_CHECKING:
    from membership.models import (
        CommunityEvent,
        Guild,
        Member,
    )
    from membership.vote_calculator import VoteStanding

logger = logging.getLogger(__name__)

# An interaction payload is Discord's JSON dict; the second arg is the resolved Member | None.
Interaction = dict

# --- /info --------------------------------------------------------------------

_FAQ_LIMIT = 3
_ANSWER_LIMIT = 200
_FIELD_LIMIT = 1024


def _meeting_value(guild: Guild) -> str:
    """The guild's next-meeting text: the soonest upcoming occurrence, or the free-text schedule.

    Delegates to :meth:`Guild.next_meeting_occurrence` — the same authoritative method the guild
    page uses — so an event-based ``GUILD_MEETING`` is never dropped (the old reimplementation only
    understood the legacy cadence fields). Formats the returned :class:`NextMeeting` tuple, its
    title first when it comes from an event (#953); falls
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
    if occurrence.title:
        value = f"{occurrence.title}\n{value}"
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

    Used by ``/info``: each choice's value is the guild ``slug`` (resolved by :func:`resolve_command_guild`), and it
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
    ballot is deliberately absent. Read-only: the link button is the change-your-vote path
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
        "\n\nSet or change your own picks on the voting page.",
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


# --- event component (RSVP toggle + ⚙ Manage) ---------------------------------

_RSVP_CLOSED = "This event has already ended, so RSVPs are closed."
_EVENT_GONE = "This event is no longer on the calendar."
_MANAGE_CREATOR_ONLY = (
    "Editing and cancelling a published event is handled by a guild lead or admin. "
    "Ask a lead if this event needs a change."
)
_SETUP_INCOMPLETE = (
    "Your Past Lives account isn't fully set up yet, so I can't change an event under your name. "
    "Please reach out to a Past Lives organizer."
)
_CANCEL_GONE = "That event was already handled. Nothing more to do."
_CANCEL_NO_AUTH = "You can no longer cancel that event from here. Ask a lead or admin to remove it."
_CANCEL_KEPT = "Kept. Nothing changed."
_CANCEL_DELETED = "Event cancelled and removed from the calendar, Google Calendar, and Discord."
_CANCEL_FAILED = (
    "Something went wrong on our side and the event was not fully removed. Please check the calendar or try again."
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


def _can_cancel(member: Member, event: CommunityEvent) -> bool:
    """Whether ``member`` may cancel ``event`` from Discord, the hub's exact delete authority.

    A published or scheduled event only: a fog admin may cancel any (mirroring the admin-only
    ``event_delete`` view); anyone else only a guild event whose guild they can edit
    (mirroring ``guild_event_delete``).
    """
    from membership.models import CommunityEvent

    delete_states = (CommunityEvent.ModerationState.PUBLISHED, CommunityEvent.ModerationState.SCHEDULED)
    if event.moderation_state not in delete_states:
        return False
    return member.is_fog_admin or (event.guild is not None and member.can_edit_guild(event.guild))


def _manage_card(event: CommunityEvent, member: Member) -> dict:
    """The ephemeral ⚙ Manage card.

    A member who may cancel the event gets Edit + Cancel; a creator without that authority
    gets an honest "ask a lead" card with a plain link to the event page.
    """
    when = format_local(event.next_occurrence_start())
    if _can_cancel(member, event):
        row = [
            {"type": 2, "style": 5, "label": "Edit on the hub", "url": _event_edit_url(event)},
            {"type": 2, "style": 4, "label": "Cancel this event", "custom_id": f"event:cancelcard:{event.pk}"},
        ]
        content = f"**Managing {truncate(event.title, 100)}** ({when})"
    else:
        row = [{"type": 2, "style": 5, "label": "Open the event page", "url": event.public_url}]
        content = f"**Managing {truncate(event.title, 100)}** ({when})\n\n{_MANAGE_CREATOR_ONLY}"
    return reply(content, ephemeral=True, components=[{"type": 1, "components": row}])


def _cancel_confirm_card(event: CommunityEvent) -> dict:
    """The in-place confirm card; states plainly what confirming does."""
    from membership.models import CommunityEvent

    content = (
        f"Cancel **{truncate(event.title, 100)}** ({format_local(event.starts_at)})? It will be removed from the "
        "Calendar, Google Calendar, and Discord. Members will not be notified automatically."
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


def _confirm_cancel(interaction: Interaction, event: CommunityEvent) -> dict:
    """Delete the event behind a type-6 deferred ack, then PATCH the confirm card.

    Unwinding Google and Discord over REST is slow, so the click is acked type-6 and the
    confirm card PATCHed afterward (buttons stripped via ``components=[]``). The remove calls
    are best-effort by design (mirroring the hub's ``event_delete``): each logs its own
    failure and the row still goes away.
    """
    ack_component_deferred(interaction["id"], interaction["token"])
    try:
        event.remove_from_google()
        event.remove_from_discord()
        event.strip_discord_announcement_buttons()
        event.delete()
        content = _CANCEL_DELETED
    except Exception:
        logger.exception("cancel: delete fan-out failed for event %s", event.pk)
        content = _CANCEL_FAILED
    send_followup(interaction["token"], content=content, components=[])
    return {}


def _cancel_component(interaction: Interaction, member: Member | None) -> dict:
    """The confirm card's Yes, cancel it / Keep it clicks (``cancel:<confirm|keep>:<pk>``).

    Authority is re-resolved from the live row on the click (someone else may have deleted
    or changed it since the card opened): a vanished row or a lost authority never
    stacktraces, it lands on the friendly gone / no-authority copy.
    """
    from membership.models import CommunityEvent

    member = cast("Member", member)
    if member.user is None:
        return update_message(_SETUP_INCOMPLETE)
    custom_id = interaction["data"]["custom_id"]
    parts = custom_id.split(":")
    if len(parts) != 3 or parts[1] not in ("confirm", "keep") or not parts[2].isdigit():
        logger.warning("Malformed cancel custom_id %r", custom_id)
        return error_reply()
    if parts[1] == "keep":
        return update_message(_CANCEL_KEPT)

    event = CommunityEvent.objects.filter(pk=int(parts[2])).first()
    if event is None:
        return update_message(_CANCEL_GONE)
    if not _can_cancel(member, event):
        return update_message(_CANCEL_NO_AUTH)
    return _confirm_cancel(interaction, event)


def _event_component(interaction: Interaction, member: Member | None) -> dict:
    """The announcement buttons: RSVP toggle, ⚙ Manage card, and the Cancel jump.

    Parses ``event:<action>:<pk>``; a malformed id or unknown action lands on ``error_reply``.
    A missing/unpublished event is the friendly "no longer on the calendar" ephemeral.
    ``requires_link=True`` guarantees a linked ``member``.
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
    # manage card into the confirm card whose buttons route to _cancel_component.
    if not _can_cancel(member, event):
        return update_message(_CANCEL_NO_AUTH)
    return _cancel_confirm_card(event)


register_component(ComponentHandler(prefix="event", handler=_event_component, requires_link=True))
register_component(ComponentHandler(prefix="cancel", handler=_cancel_component, requires_link=True))


# --- /poll --------------------------------------------------------------------

# Discord native-poll limits (verified against the API docs, 2026-08-26).
_POLL_QUESTION_MAX = 300
_POLL_ANSWER_MAX = 55
_POLL_MIN_ANSWERS = 2
_POLL_MAX_ANSWERS = 10
_POLL_DEFAULT_DURATION_HOURS = 24
# The "Voting Stays Open For" modal choices — (hours, label), in menu order. The default
# (24 Hours) is preselected; the header line reads its label from the same source.
_POLL_DURATIONS: list[tuple[int, str]] = [
    (1, "1 Hour"),
    (4, "4 Hours"),
    (8, "8 Hours"),
    (24, "24 Hours"),
    (72, "3 Days"),
    (168, "1 Week"),
    (336, "2 Weeks"),
]
_POLL_DURATION_LABELS: dict[int, str] = dict(_POLL_DURATIONS)

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


def _split_answers(raw: str) -> list[str]:
    """Split the Answers textarea into trimmed, non-empty answers — one per line."""
    return [line.strip() for line in raw.splitlines() if line.strip()]


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


def _poll_header(display_name: str, hours: int, multiselect: bool) -> str:
    """The styled content line above the native poll widget — attribution, no ping."""
    pick = "pick any" if multiselect else "pick one"
    return f"📊 **Poll from {display_name}**  ·  open for {_POLL_DURATION_LABELS[hours]}  ·  {pick}"


_POLL_MODAL_PREFIX = "pollform"
_POLL_EDIT_PREFIX = "polledit"
# A failed submission is cached this long so its "Edit Poll" button can reopen it prefilled.
_POLL_EDIT_TTL_SECONDS = 900
_POLL_MODAL_INTRO = (
    "Submitting posts the poll in this channel right away. Polls cannot be edited after they post. "
    "Need a change? End it early with the gear and run /poll again."
)
# Per-member poll caps (counted only when a poll is actually posted), the same caps the old /create used.
_POLL_RATE_SCOPE = "discord_poll"
_POLL_HOURLY_LIMIT = 4
_POLL_DAILY_LIMIT = 12


def _poll_rate_limited_reply() -> dict:
    """The friendly per-member cap refusal for /poll (no hub alternative — polls are Discord only)."""
    return reply(
        f"You have hit the limit for polls from Discord ({_POLL_HOURLY_LIMIT} per hour, "
        f"{_POLL_DAILY_LIMIT} per day). Try again in a bit.",
        ephemeral=True,
    )


def _poll_duration_options(selected_hours: int) -> list[dict]:
    """The duration-select options (hour ints as string values), the current one preselected."""
    return [
        {"label": label, "value": str(hours), "default": hours == selected_hours} for hours, label in _POLL_DURATIONS
    ]


def _poll_modal(
    *, question: str = "", answers: str = "", hours: int = _POLL_DEFAULT_DURATION_HOURS, multiselect: bool = False
) -> dict:
    """The Create-a-Poll modal, optionally prefilled from a failed submission (the edit reopen)."""
    return modal(
        _POLL_MODAL_PREFIX,
        "Create a Poll",
        [
            modal_label(
                "Question",
                text_input(
                    "question", placeholder="What should we vote on?", value=question, max_length=_POLL_QUESTION_MAX
                ),
                description="Posted exactly as written.",
            ),
            modal_label(
                "Answers",
                text_input("answers", style=2, placeholder="🔨 Option one\n🎨 Option two", value=answers),
                description="One per line, 2 to 10 answers. Start a line with an emoji to use it as that answer's icon.",
            ),
            modal_label(
                "Voting Stays Open For",
                string_select("duration", _poll_duration_options(hours)),
                description="The poll closes on its own and shows the winner.",
            ),
            modal_label(
                "Multiple Choice",
                checkbox("multiselect", default=multiselect),
                description="Members can vote for more than one answer.",
            ),
            text_display(_POLL_MODAL_INTRO),
        ],
    )


def _poll(interaction: Interaction, member: Member | None) -> dict:
    """Open the Create-a-Poll modal (the slash command carries no options).

    The per-member rate limit is peeked here at invoke time (recorded only on a successfully
    posted poll in :func:`_poll_submit`); an over-cap member gets the friendly refusal instead
    of the form. ``requires_link=True`` guarantees a linked ``member``.
    """
    from core.abuse_limits import keyed_within_limits

    member = cast("Member", member)
    if not keyed_within_limits(
        _POLL_RATE_SCOPE, str(member.pk), hourly_limit=_POLL_HOURLY_LIMIT, daily_limit=_POLL_DAILY_LIMIT
    ):
        return _poll_rate_limited_reply()
    return _poll_modal()


def _poll_selected_hours(values: dict[str, object]) -> int:
    """The chosen duration in hours from the submitted select, defaulting to one day."""
    raw = values.get("duration")
    if isinstance(raw, list) and raw:
        return int(str(raw[0]))
    return _POLL_DEFAULT_DURATION_HOURS


def _poll_build_media(pieces: list[str]) -> tuple[list[dict], str | None]:
    """Validate the answers and build their ``poll_media``, or return ``(_, error copy)``.

    Reuses :func:`_answer_media`'s leading-emoji extraction. The per-answer 55-char cap is
    enforced here (not by the modal) because every answer shares one textarea; the count and
    uniqueness rules name the number and quote the offending answer.
    """
    count = len(pieces)
    if count < _POLL_MIN_ANSWERS:
        return [], f"A poll needs at least 2 answers. You gave {count}. Put each answer on its own line."
    if count > _POLL_MAX_ANSWERS:
        return [], f"Discord caps polls at 10 answers and you gave {count}. Trim the list and resubmit."
    media = [_answer_media(piece) for piece in pieces]
    for index, item in enumerate(media, start=1):
        if len(item["text"]) > _POLL_ANSWER_MAX:
            shown = truncate(item["text"], _POLL_ANSWER_MAX)
            return media, (
                f"Answer {index} is too long. Each answer has to fit in {_POLL_ANSWER_MAX} characters. "
                f'Shorten "{shown}" and resubmit.'
            )
    seen: set[str] = set()
    for item in media:
        text = item["text"]
        if text in seen:
            return media, (
                f'Two answers come out identical: "{text}". Every answer has to be unique. Edit the list and resubmit.'
            )
        seen.add(text)
    return media, None


def _cache_poll_submission(*, question: str, answers: str, hours: int, multiselect: bool) -> str:
    """Stash a failed submission under a short token so the Edit Poll button can reopen it."""
    import secrets

    from django.core.cache import cache

    token = secrets.token_urlsafe(9)
    cache.set(
        f"{_POLL_MODAL_PREFIX}:{token}",
        {"question": question, "answers": answers, "hours": hours, "multiselect": multiselect},
        _POLL_EDIT_TTL_SECONDS,
    )
    return token


def _poll_error_reply(error: str, *, question: str, answers: str, hours: int, multiselect: bool) -> dict:
    """The ephemeral validation error with a blurple Edit Poll button that reopens the form."""
    token = _cache_poll_submission(question=question, answers=answers, hours=hours, multiselect=multiselect)
    button_row = {
        "type": 1,
        "components": [{"type": 2, "style": 1, "label": "Edit Poll", "custom_id": f"{_POLL_EDIT_PREFIX}:{token}"}],
    }
    return reply(error, ephemeral=True, components=[button_row])


def _poll_submit(interaction: Interaction, member: Member | None) -> dict:
    """The Create-a-Poll modal submit: validate, then post the native poll (or the edit reopen).

    A validation failure returns an ephemeral error naming the fix plus an Edit Poll button
    that reopens the modal prefilled from this exact submission. A valid submit posts the
    public poll (flags 0) with the attribution header, the mass-ping gate, and the ⚙ gear row.
    ``requires_link=True`` guarantees a linked ``member``.
    """
    member = cast("Member", member)
    values = parse_modal_values(interaction)
    question = str(values.get("question", "")).strip()
    answers = str(values.get("answers", ""))
    hours = _poll_selected_hours(values)
    multiselect = bool(values.get("multiselect"))

    media, error = _poll_build_media(_split_answers(answers))
    if error is not None:
        return _poll_error_reply(error, question=question, answers=answers, hours=hours, multiselect=multiselect)

    poll = {
        "question": {"text": question},
        "answers": [{"poll_media": item} for item in media],
        "duration": hours,
        "allow_multiselect": multiselect,
    }
    # The creator pk rides statelessly in the gear's custom_id; reply() carries a poll and
    # components together and already pins allowed_mentions on the poll branch.
    gear_row = {"type": 1, "components": [{"type": 2, "style": 2, "label": "⚙", "custom_id": f"poll:end:{member.pk}"}]}
    # Count the cap only on a poll that actually posts — validation failures above cost nothing.
    from core.abuse_limits import record_keyed_attempt

    record_keyed_attempt(
        _POLL_RATE_SCOPE, str(member.pk), hourly_limit=_POLL_HOURLY_LIMIT, daily_limit=_POLL_DAILY_LIMIT
    )
    return reply(
        _poll_header(member.display_name, hours, multiselect), ephemeral=False, poll=poll, components=[gear_row]
    )


def _poll_edit_component(interaction: Interaction, member: Member | None) -> dict:
    """The Edit Poll click on a validation error — reopen the modal prefilled from the cache.

    An expired/evicted token reopens a blank modal so the member can still start over."""
    from django.core.cache import cache

    _prefix, _, token = interaction["data"]["custom_id"].partition(":")
    data = cache.get(f"{_POLL_MODAL_PREFIX}:{token}") if token else None
    if data is None:
        return _poll_modal()
    return _poll_modal(
        question=data["question"], answers=data["answers"], hours=data["hours"], multiselect=data["multiselect"]
    )


POLL = SlashCommand(
    name="poll",
    description="Post a poll for the channel to vote on.",
    handler=_poll,
    requires_link=True,
    ephemeral=True,
    defer=False,
    scope="guild",
)

register(POLL)
register_modal(ModalHandler(prefix=_POLL_MODAL_PREFIX, handler=_poll_submit, requires_link=True))
register_component(ComponentHandler(prefix=_POLL_EDIT_PREFIX, handler=_poll_edit_component, requires_link=True))


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
