"""Pure helpers for the admin voting analyzer (live draft + stored snapshot modes).

Lifted out of ``plfog/admin_views.py`` so the hub voting views stay thin and the
logic lives inside the ``membership`` app's coverage scope. Everything here is
pure Python over lists of vote dicts — the only DB access is
``serialize_live_votes()`` reading the current ``VotePreference`` rows.

The analyzer serves two modes off the same context builder:

* **draft** (``snapshot=None``) — live ``VotePreference`` data, uncommitted.
* **stored** — a committed ``FundingSnapshot``'s frozen ``raw_votes``.

Filters run purely in memory over the list of vote dicts; they are a
visualization aid only — committing a snapshot always captures the full
unfiltered live state.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any

from django.utils import timezone

from membership.models import Member, VotePreference
from membership.vote_calculator import calculate_results

if TYPE_CHECKING:
    from django.http import QueryDict

    from membership.models import FundingSnapshot

MEMBER_TYPE_CHOICES = Member.MemberType.choices
FOG_ROLE_CHOICES = Member.FogRole.choices
DEFAULT_MINIMUM_POOL = Decimal("1000")


def serialize_live_votes() -> list[dict[str, Any]]:
    """Snapshot live ``VotePreference`` rows into the same shape as ``FundingSnapshot.raw_votes``.

    Only includes votes from signed-up members (those with a linked User) — see
    ``VotePreferenceQuerySet.from_signed_up_members``. Each row carries the
    member's current guild-lead / guild-staff status so the analyzer can filter
    on those dimensions.
    """
    preferences = (
        VotePreference.objects.from_signed_up_members()
        .with_role_flags()
        .select_related(
            "member",
            "guild_1st",
            "guild_2nd",
            "guild_3rd",
        )
    )
    return [
        {
            "member_id": pref.member_id,
            "member_name": pref.member.display_name,
            "member_type": pref.member.member_type,
            "fog_role": pref.member.fog_role,
            "is_paying": pref.member.is_paying,
            "is_guild_lead": pref.member_is_guild_lead,
            "is_guild_staff": pref.member_is_guild_staff,
            "guild_1st_id": pref.guild_1st_id,
            "guild_1st_name": pref.guild_1st.name,
            "guild_2nd_id": pref.guild_2nd_id,
            "guild_2nd_name": pref.guild_2nd.name if pref.guild_2nd else None,
            "guild_3rd_id": pref.guild_3rd_id,
            "guild_3rd_name": pref.guild_3rd.name if pref.guild_3rd else None,
        }
        for pref in preferences
    ]


def parse_is_paying(value: str) -> bool | None:
    """Normalize the is_paying filter query param. Empty string → None (both)."""
    if value == "yes":
        return True
    if value == "no":
        return False
    return None


def _parse_narrowing_flag(value: str | None) -> bool | None:
    """A checkbox-style narrowing filter: ``"yes"`` → True (only those who are), else None (don't filter)."""
    return True if value == "yes" else None


def parse_minimum_pool(raw: str | None, default: Decimal = DEFAULT_MINIMUM_POOL) -> Decimal:
    """Parse a minimum_pool string. Falls back to default on empty or invalid input."""
    if not raw:
        return default
    try:
        value = Decimal(raw)
    except (InvalidOperation, ValueError):
        return default
    if value < 0:
        return default
    return value


def apply_filters(
    raw_votes: list[dict[str, Any]],
    *,
    member_types: list[str],
    fog_roles: list[str],
    is_paying: bool | None,
    is_guild_lead: bool | None,
    is_guild_staff: bool | None,
) -> list[dict[str, Any]]:
    """Apply analyzer filters to a list of raw votes (in-memory, no DB work).

    ``is_paying`` is tri-state (None = both). ``is_guild_lead`` / ``is_guild_staff``
    are narrowing booleans (None = don't constrain; True = only those who are).
    Guild-lead/staff read ``.get(key, False)`` so snapshots taken before these
    keys were frozen simply never match those two filters.
    """
    filtered = raw_votes
    if member_types:
        filtered = [v for v in filtered if v["member_type"] in member_types]
    if fog_roles:
        filtered = [v for v in filtered if v["fog_role"] in fog_roles]
    if is_paying is not None:
        filtered = [v for v in filtered if v["is_paying"] is is_paying]
    if is_guild_lead:
        filtered = [v for v in filtered if v.get("is_guild_lead", False)]
    if is_guild_staff:
        filtered = [v for v in filtered if v.get("is_guild_staff", False)]
    return filtered


def build_analyzer_context(
    raw_votes: list[dict[str, Any]],
    *,
    snapshot: FundingSnapshot | None,
    get_params: QueryDict,
) -> dict[str, Any]:
    """Build the shared analyzer context for draft (snapshot=None) and stored modes.

    Returns the filtered rows, the dry-run ``calculate_results`` dict, paying /
    non-paying counts, the filter state, the choice lists, and the legacy flag.
    Does not touch the request/admin chrome — the calling hub view merges this
    into its own ``_get_hub_context``.
    """
    # Legacy pre-refactor snapshots have empty raw_votes; bypass filter/recalc.
    is_legacy = snapshot is not None and not raw_votes

    member_types = get_params.getlist("member_type")
    fog_roles = get_params.getlist("fog_role")
    is_paying = parse_is_paying(get_params.get("is_paying", ""))
    is_guild_lead = _parse_narrowing_flag(get_params.get("is_guild_lead"))
    is_guild_staff = _parse_narrowing_flag(get_params.get("is_guild_staff"))

    if snapshot is not None:
        minimum_pool = snapshot.minimum_pool
        title_default = snapshot.cycle_label
    else:
        minimum_pool = parse_minimum_pool(get_params.get("minimum_pool"))
        title_default = get_params.get("title", "").strip() or timezone.now().strftime("%B %Y")

    paying_count = 0
    non_paying_count = 0

    if is_legacy:
        assert snapshot is not None
        calc: dict[str, Any] = snapshot.results or {}
        filtered_votes: list[dict[str, Any]] = []
    else:
        filtered_votes = apply_filters(
            raw_votes,
            member_types=member_types,
            fog_roles=fog_roles,
            is_paying=is_paying,
            is_guild_lead=is_guild_lead,
            is_guild_staff=is_guild_staff,
        )
        paying_count = sum(1 for v in filtered_votes if v["is_paying"])
        non_paying_count = sum(1 for v in filtered_votes if not v["is_paying"])
        votes_for_calc = [
            {
                "guild_1st": v["guild_1st_name"],
                "guild_2nd": v["guild_2nd_name"],
                "guild_3rd": v["guild_3rd_name"],
            }
            for v in filtered_votes
        ]
        calc = calculate_results(
            votes_for_calc,
            paying_voter_count=paying_count,
            minimum_pool=minimum_pool,
        )

    any_filter_active = bool(member_types or fog_roles or is_paying is not None or is_guild_lead or is_guild_staff)

    return {
        "snapshot": snapshot,
        "mode": "stored" if snapshot is not None else "draft",
        "is_legacy": is_legacy,
        "title_default": title_default,
        "minimum_pool": minimum_pool,
        "calc": calc,
        "filtered_votes": sorted(filtered_votes, key=lambda v: v["member_name"].lower()),
        "filter_state": {
            "member_type": member_types,
            "fog_role": fog_roles,
            "is_paying": get_params.get("is_paying", ""),
            "is_guild_lead": is_guild_lead is True,
            "is_guild_staff": is_guild_staff is True,
        },
        "any_filter_active": any_filter_active,
        "has_raw_votes": bool(raw_votes),
        "member_type_choices": MEMBER_TYPE_CHOICES,
        "fog_role_choices": FOG_ROLE_CHOICES,
        "paying_count": paying_count,
        "non_paying_count": non_paying_count,
        "total_count": len(filtered_votes),
    }
