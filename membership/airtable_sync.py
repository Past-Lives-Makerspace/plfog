"""Airtable API client for guild voting — dual-write sync layer.

Reads members from new base (PLM Members & Studios 2026).
Reads guilds from old base (Past Lives Information).
Writes votes and sessions to both Django DB and new base.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from django.conf import settings
from pyairtable import Api

logger = logging.getLogger(__name__)


def _api() -> Api:
    return Api(settings.AIRTABLE_API_KEY)


# ---------------------------------------------------------------------------
# Members (read-only from NEW base)
# ---------------------------------------------------------------------------


def get_eligible_members() -> list[dict[str, Any]]:
    """Fetch paying, active members from the new Airtable base.

    Filters: Status=Active AND Role != 'Work Trade' AND Monthly Membership $ > 0.
    """
    table = _api().table(settings.AIRTABLE_NEW_BASE_ID, settings.AIRTABLE_MEMBERS_TABLE)
    formula = "AND({Status}='Active', {Role}!='Work Trade', {Monthly Membership $}>0)"
    records = table.all(formula=formula)
    return [
        {
            "record_id": r["id"],
            "name": r["fields"].get("Member Name", ""),
            "email": r["fields"].get("Email", ""),
            "phone": r["fields"].get("Phone", ""),
            "role": r["fields"].get("Role", ""),
            "monthly_amount": r["fields"].get("Monthly Membership $", 0),
        }
        for r in records
    ]


def get_member(record_id: str) -> dict[str, Any]:
    """Fetch a single member by Airtable record ID."""
    table = _api().table(settings.AIRTABLE_NEW_BASE_ID, settings.AIRTABLE_MEMBERS_TABLE)
    r = table.get(record_id)
    return {
        "record_id": r["id"],
        "name": r["fields"].get("Member Name", ""),
        "email": r["fields"].get("Email", ""),
        "status": r["fields"].get("Status", ""),
        "role": r["fields"].get("Role", ""),
        "monthly_amount": r["fields"].get("Monthly Membership $", 0),
    }


# ---------------------------------------------------------------------------
# Guilds (read-only from OLD base)
# ---------------------------------------------------------------------------


def get_voteable_guilds() -> list[dict[str, Any]]:
    """Fetch all official voteable guilds from the old Airtable base."""
    table = _api().table(settings.AIRTABLE_OLD_BASE_ID, settings.AIRTABLE_GUILDS_TABLE)
    records = table.all(formula="{Official Guild and Voteable?}=TRUE()")
    return [
        {
            "record_id": r["id"],
            "name": r["fields"].get("Guild", ""),
        }
        for r in records
    ]


# ---------------------------------------------------------------------------
# Voting Sessions (write to NEW base)
# ---------------------------------------------------------------------------


def sync_session_to_airtable(
    session_id: int,
    name: str,
    open_date: date,
    close_date: date,
    status: str,
    eligible_member_count: int = 0,
    votes_cast: int = 0,
    results_summary: str = "",
    airtable_record_id: str = "",
) -> str:
    """Create or update a voting session in Airtable. Returns the Airtable record ID."""
    table = _api().table(settings.AIRTABLE_NEW_BASE_ID, settings.AIRTABLE_SESSIONS_TABLE)
    fields: dict[str, Any] = {
        "Name": name,
        "Open Date": open_date.isoformat(),
        "Close Date": close_date.isoformat(),
        "Status": status.capitalize(),
        "Eligible Member Count": eligible_member_count,
        "Votes Cast": votes_cast,
    }
    if results_summary:
        fields["Results Summary"] = results_summary

    try:
        if airtable_record_id:
            table.update(airtable_record_id, fields)  # type: ignore[arg-type]
            return airtable_record_id
        else:
            result = table.create(fields)  # type: ignore[arg-type]
            return result["id"]
    except Exception:
        logger.exception("Failed to sync session %s to Airtable", session_id)
        return airtable_record_id


# ---------------------------------------------------------------------------
# Votes (write to NEW base)
# ---------------------------------------------------------------------------


def sync_vote_to_airtable(
    member_name: str,
    member_airtable_id: str,
    guild_1st: str,
    guild_2nd: str,
    guild_3rd: str,
    session_name: str,
) -> str:
    """Create a vote record in Airtable. Returns the Airtable record ID."""
    table = _api().table(settings.AIRTABLE_NEW_BASE_ID, settings.AIRTABLE_VOTES_TABLE)
    fields: dict[str, Any] = {
        "Member Name": member_name,
        "Member Airtable ID": member_airtable_id,
        "Guild 1st": guild_1st,
        "Guild 2nd": guild_2nd,
        "Guild 3rd": guild_3rd,
        "Session": session_name,
    }
    try:
        result = table.create(fields)  # type: ignore[arg-type]
        return result["id"]
    except Exception:
        logger.exception("Failed to sync vote for %s to Airtable", member_name)
        return ""
