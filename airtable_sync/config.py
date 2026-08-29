"""Declarative field mappings between Django models and Airtable tables.

Each mapping defines:
- table_id: The Airtable table ID
- to_airtable(instance): Convert a Django model instance to AT writable fields
- from_airtable(fields): Convert AT fields to Django field kwargs
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

# ---------------------------------------------------------------------------
# Table IDs (PLM Members & Studios 2026 base)
# ---------------------------------------------------------------------------

MEMBERS_TABLE_ID = "tbllpqGB2XXuRt6lg"
SPACES_TABLE_ID = "tblzUObU6ENi4md3H"
LEASES_TABLE_ID = "tblFmX4O4ZoEbFINB"
GUILD_VOTES_TABLE_ID = "tblpefgQUIMdwbLZX"
VOTING_SESSIONS_TABLE_ID = "tblGW2Bo1Mb09qT2y"

# ---------------------------------------------------------------------------
# Enum mappings
# ---------------------------------------------------------------------------

MEMBER_STATUS_TO_AT: dict[str, str] = {
    "invited": "Pending",
    "active": "Active",
    "former": "Former",
    "suspended": "Paused",
}
MEMBER_STATUS_FROM_AT: dict[str, str] = {v: k for k, v in MEMBER_STATUS_TO_AT.items()}

MEMBER_TYPE_TO_AT: dict[str, str] = {
    "standard": "Standard Member",
    "guild_lead": "Guild Lead",
    "work_trade": "Work Trade",
    "employee": "Employee",
    "contractor": "Contractor",
    "volunteer": "Volunteer",
}
MEMBER_TYPE_FROM_AT: dict[str, str] = {v: k for k, v in MEMBER_TYPE_TO_AT.items()}

SPACE_STATUS_TO_AT: dict[str, str] = {
    "available": "Available",
    "occupied": "Occupied",
    "maintenance": "Facility/PLM",
}
SPACE_STATUS_FROM_AT: dict[str, str] = {v: k for k, v in SPACE_STATUS_TO_AT.items()}

LEASE_TYPE_TO_AT: dict[str, str] = {
    "month_to_month": "Month-to-month",
    "annual": "Annual",
}
LEASE_TYPE_FROM_AT: dict[str, str] = {v: k for k, v in LEASE_TYPE_TO_AT.items()}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _date_to_str(d: date | None) -> str | None:
    """Convert a date to ISO string for Airtable, or None."""
    if d is None:
        return None
    return d.isoformat()


def _decimal_to_float(d: Decimal | None) -> float | None:
    """Convert Decimal to float for Airtable JSON, or None."""
    if d is None:
        return None
    return float(d)


def _str_to_date(s: str | None) -> date | None:
    """Parse an ISO date string from Airtable, or None."""
    if not s:
        return None
    return date.fromisoformat(s)


# ---------------------------------------------------------------------------
# Member mapping
# ---------------------------------------------------------------------------


def member_to_airtable(member: Any) -> dict[str, Any]:
    """Convert a Member instance to Airtable writable fields."""
    fields: dict[str, Any] = {
        "Member Name": member.preferred_name or member.full_legal_name,
        # NOTE: We read the stored _pre_signup_email field here, not member.primary_email,
        # because Airtable is the source of truth for unlinked members. The primary_email
        # property would re-enter allauth for linked members and we don't want to change
        # what we push upstream. See specs/2026-04-07-user-email-aliases-design.md.
        "Email": member._pre_signup_email,
        "Phone": member.phone,
        "Status": MEMBER_STATUS_TO_AT[member.status],
        "Role": MEMBER_TYPE_TO_AT[member.member_type],
        "Join Date": _date_to_str(member.join_date),
        "Cancellation Date": _date_to_str(member.cancellation_date),
        "Notes": member.notes,
        "Emergency Contact": member.emergency_contact_name,
        "Emergency Contact Phone": member.emergency_contact_phone,
        "Emergency Contact Relationship": member.emergency_contact_relationship,
    }
    # Legal name (if different) — AT stores legal name separately when preferred_name is the display
    if member.preferred_name:
        fields["Legal name (if different)"] = member.full_legal_name
    else:
        fields["Legal name (if different)"] = ""

    # MembershipPlan → select field + currency
    if hasattr(member, "membership_plan") and member.membership_plan:
        fields["Membership Plan"] = member.membership_plan.name
        fields["Monthly Membership $"] = _decimal_to_float(member.membership_plan.monthly_price)

    return fields


def member_from_airtable(fields: dict[str, Any]) -> dict[str, Any]:
    """Convert Airtable Member fields to Django field kwargs.

    Note: Does NOT set membership_plan (requires lookup). Caller must handle that.
    """
    display_name = fields.get("Member Name", "")
    legal_name = fields.get("Legal name (if different)", "")

    result: dict[str, Any] = {
        "_pre_signup_email": fields.get("Email", ""),
        "phone": fields.get("Phone", ""),
        "notes": fields.get("Notes", ""),
        "emergency_contact_name": fields.get("Emergency Contact", ""),
        "emergency_contact_phone": fields.get("Emergency Contact Phone", ""),
        "emergency_contact_relationship": fields.get("Emergency Contact Relationship", ""),
        "join_date": _str_to_date(fields.get("Join Date")),
        "cancellation_date": _str_to_date(fields.get("Cancellation Date")),
    }

    # If legal name is set, display name is preferred_name; otherwise display name is legal name
    if legal_name:
        result["full_legal_name"] = legal_name
        result["preferred_name"] = display_name
    else:
        result["full_legal_name"] = display_name
        result["preferred_name"] = ""

    at_status = fields.get("Status", "")
    if at_status and at_status in MEMBER_STATUS_FROM_AT:
        result["status"] = MEMBER_STATUS_FROM_AT[at_status]

    at_role = fields.get("Role", "")
    if at_role and at_role in MEMBER_TYPE_FROM_AT:
        result["member_type"] = MEMBER_TYPE_FROM_AT[at_role]

    return result


# ---------------------------------------------------------------------------
# Space mapping
# ---------------------------------------------------------------------------


def space_to_airtable(space: Any) -> dict[str, Any]:
    """Convert a Space instance to Airtable writable fields."""
    return {
        "Space Code": space.space_id,
        "Designation": space.name,
        "Size (sq ft)": _decimal_to_float(space.size_sqft),
        "Manual Price": _decimal_to_float(space.manual_price),
        "Status": SPACE_STATUS_TO_AT[space.status],
        "Notes": space.notes,
    }


def space_from_airtable(fields: dict[str, Any]) -> dict[str, Any]:
    """Convert Airtable Space fields to Django field kwargs."""
    result: dict[str, Any] = {
        "space_id": fields.get("Space Code", ""),
        "name": fields.get("Designation", ""),
        "notes": fields.get("Notes", ""),
    }

    sqft = fields.get("Size (sq ft)")
    if sqft is not None:
        result["size_sqft"] = Decimal(str(sqft))

    manual_price = fields.get("Manual Price")
    if manual_price is not None:
        result["manual_price"] = Decimal(str(manual_price))

    at_status = fields.get("Status", "")
    if at_status and at_status in SPACE_STATUS_FROM_AT:
        result["status"] = SPACE_STATUS_FROM_AT[at_status]

    return result


# ---------------------------------------------------------------------------
# Lease mapping
# ---------------------------------------------------------------------------


def lease_to_airtable(lease: Any) -> dict[str, Any]:
    """Convert a Lease instance to Airtable writable fields.

    Linked record fields (Member, Space) require the related object's airtable_record_id.
    """
    fields: dict[str, Any] = {
        "Monthly Rent": _decimal_to_float(lease.monthly_rent),
        "Deposit Required": _decimal_to_float(lease.deposit_required),
        "Deposit Paid Date": _date_to_str(lease.deposit_paid_date),
        "Start Date": _date_to_str(lease.start_date),
        "End Date": _date_to_str(lease.end_date),
        "Lease Type": LEASE_TYPE_TO_AT[lease.lease_type],
        "Notes": lease.notes or "",
    }

    # Linked record: Member
    tenant = lease.tenant
    if tenant and hasattr(tenant, "airtable_record_id") and tenant.airtable_record_id:
        fields["Member"] = [tenant.airtable_record_id]

    # Linked record: Space
    if lease.space and lease.space.airtable_record_id:
        fields["Space"] = [lease.space.airtable_record_id]

    return fields


def lease_from_airtable(fields: dict[str, Any]) -> dict[str, Any]:
    """Convert Airtable Lease fields to Django field kwargs.

    Note: Does NOT resolve linked records (Member, Space). Caller must handle FK resolution.
    """
    result: dict[str, Any] = {
        "notes": fields.get("Notes", ""),
        "start_date": _str_to_date(fields.get("Start Date")),
        "end_date": _str_to_date(fields.get("End Date")),
        "deposit_paid_date": _str_to_date(fields.get("Deposit Paid Date")),
    }

    rent = fields.get("Monthly Rent")
    if rent is not None:
        result["monthly_rent"] = Decimal(str(rent))

    deposit = fields.get("Deposit Required")
    if deposit is not None:
        result["deposit_required"] = Decimal(str(deposit))

    at_type = fields.get("Lease Type", "")
    if at_type and at_type in LEASE_TYPE_FROM_AT:
        result["lease_type"] = LEASE_TYPE_FROM_AT[at_type]

    # Store raw linked record IDs for caller to resolve
    result["_member_record_ids"] = fields.get("Member", [])
    result["_space_record_ids"] = fields.get("Space", [])

    return result


# ---------------------------------------------------------------------------
# VotePreference -> Guild Votes mapping (Django -> AT only)
# ---------------------------------------------------------------------------


def vote_preference_to_airtable(vote: Any) -> dict[str, Any]:
    """Convert a VotePreference instance to Airtable Guild Votes fields."""
    return {
        "Member Name": vote.member.display_name,
        "Member Airtable ID": vote.member.airtable_record_id or "",
        "Guild 1st": vote.guild_1st.name,
        "Guild 2nd": vote.guild_2nd.name if vote.guild_2nd else "",
        "Guild 3rd": vote.guild_3rd.name if vote.guild_3rd else "",
        "Voted At": vote.updated_at.isoformat() if vote.updated_at else None,
    }


# ---------------------------------------------------------------------------
# FundingSnapshot -> Voting Sessions mapping (Django -> AT only)
# ---------------------------------------------------------------------------


def funding_snapshot_to_airtable(snapshot: Any) -> dict[str, Any]:
    """Convert a FundingSnapshot to Airtable Voting Sessions fields."""
    # Build a summary from the results JSON
    results_summary = ""
    if snapshot.results:
        guilds = snapshot.results.get("guilds", [])
        lines = [f"Pool: ${snapshot.funding_pool}"]
        for g in guilds:
            name = g.get("name", "?")
            amount = g.get("amount", 0)
            pct = g.get("percentage", 0)
            lines.append(f"  {name}: ${amount} ({pct}%)")
        results_summary = "\n".join(lines)

    return {
        "Name": snapshot.cycle_label,
        "Close Date": snapshot.snapshot_at.date().isoformat() if snapshot.snapshot_at else None,
        "Status": "Calculated",
        "Eligible Member Count": snapshot.contributor_count,
        "Votes Cast": snapshot.contributor_count,
        "Results Summary": results_summary,
    }
