"""Airtable sync service — push/pull/delete for each synced model.

All functions short-circuit when AIRTABLE_SYNC_ENABLED is False.
Errors are logged but never raised — AT is a secondary data store.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from django.conf import settings
from django.contrib.contenttypes.models import ContentType

from airtable_sync.client import get_table
from airtable_sync.config import (
    GUILD_VOTES_TABLE_ID,
    LEASES_TABLE_ID,
    MEMBERS_TABLE_ID,
    SPACES_TABLE_ID,
    VOTING_SESSIONS_TABLE_ID,
    funding_snapshot_to_airtable,
    lease_to_airtable,
    member_to_airtable,
    space_to_airtable,
    vote_preference_to_airtable,
)

if TYPE_CHECKING:
    from membership.models import FundingSnapshot, Lease, Member, Space, VotePreference

logger = logging.getLogger("airtable_sync")


def _sync_enabled() -> bool:
    return bool(settings.AIRTABLE_SYNC_ENABLED)


# ---------------------------------------------------------------------------
# Member
# ---------------------------------------------------------------------------


def sync_member_to_airtable(member: Member) -> str | None:
    """Push a Member to Airtable. Returns the AT record ID, or None on failure/disabled."""
    if not _sync_enabled():
        return None
    try:
        table = get_table(MEMBERS_TABLE_ID)
        fields = member_to_airtable(member)
        if member.airtable_record_id:
            table.update(member.airtable_record_id, fields)
            logger.info("Updated Airtable Member %s (pk=%s)", member.airtable_record_id, member.pk)
            return member.airtable_record_id
        else:
            record = table.create(fields)
            record_id = record["id"]
            # Save the record ID back without triggering another sync
            type(member).objects.filter(pk=member.pk).update(airtable_record_id=record_id)
            member.airtable_record_id = record_id
            logger.info("Created Airtable Member %s (pk=%s)", record_id, member.pk)
            return record_id
    except Exception:
        logger.exception("Airtable sync failed for Member pk=%s", member.pk)
        return None


def delete_member_from_airtable(record_id: str) -> None:
    """Delete a Member record from Airtable."""
    if not _sync_enabled():
        return
    try:
        table = get_table(MEMBERS_TABLE_ID)
        table.delete(record_id)
        logger.info("Deleted Airtable Member %s", record_id)
    except Exception:
        logger.exception("Airtable delete failed for Member %s", record_id)


# ---------------------------------------------------------------------------
# Space
# ---------------------------------------------------------------------------


def sync_space_to_airtable(space: Space) -> str | None:
    """Push a Space to Airtable. Returns the AT record ID, or None on failure/disabled."""
    if not _sync_enabled():
        return None
    try:
        table = get_table(SPACES_TABLE_ID)
        fields = space_to_airtable(space)
        if space.airtable_record_id:
            table.update(space.airtable_record_id, fields)
            logger.info("Updated Airtable Space %s (pk=%s)", space.airtable_record_id, space.pk)
            return space.airtable_record_id
        else:
            record = table.create(fields)
            record_id = record["id"]
            type(space).objects.filter(pk=space.pk).update(airtable_record_id=record_id)
            space.airtable_record_id = record_id
            logger.info("Created Airtable Space %s (pk=%s)", record_id, space.pk)
            return record_id
    except Exception:
        logger.exception("Airtable sync failed for Space pk=%s", space.pk)
        return None


def delete_space_from_airtable(record_id: str) -> None:
    """Delete a Space record from Airtable."""
    if not _sync_enabled():
        return
    try:
        table = get_table(SPACES_TABLE_ID)
        table.delete(record_id)
        logger.info("Deleted Airtable Space %s", record_id)
    except Exception:
        logger.exception("Airtable delete failed for Space %s", record_id)


# ---------------------------------------------------------------------------
# Lease
# ---------------------------------------------------------------------------


def _is_member_tenant(lease: Lease) -> bool:
    """Return True if the lease tenant is a Member (not a Guild)."""
    from membership.models import Member

    return lease.content_type == ContentType.objects.get_for_model(Member)


def sync_lease_to_airtable(lease: Lease) -> str | None:
    """Push a Lease to Airtable. Only syncs Member-tenant leases."""
    if not _sync_enabled():
        return None
    if not _is_member_tenant(lease):
        return None
    try:
        table = get_table(LEASES_TABLE_ID)
        fields = lease_to_airtable(lease)
        if lease.airtable_record_id:
            table.update(lease.airtable_record_id, fields)
            logger.info("Updated Airtable Lease %s (pk=%s)", lease.airtable_record_id, lease.pk)
            return lease.airtable_record_id
        else:
            record = table.create(fields)
            record_id = record["id"]
            type(lease).objects.filter(pk=lease.pk).update(airtable_record_id=record_id)
            lease.airtable_record_id = record_id
            logger.info("Created Airtable Lease %s (pk=%s)", record_id, lease.pk)
            return record_id
    except Exception:
        logger.exception("Airtable sync failed for Lease pk=%s", lease.pk)
        return None


def delete_lease_from_airtable(record_id: str) -> None:
    """Delete a Lease record from Airtable."""
    if not _sync_enabled():
        return
    try:
        table = get_table(LEASES_TABLE_ID)
        table.delete(record_id)
        logger.info("Deleted Airtable Lease %s", record_id)
    except Exception:
        logger.exception("Airtable delete failed for Lease %s", record_id)


# ---------------------------------------------------------------------------
# VotePreference -> Guild Votes (Django -> AT only)
# ---------------------------------------------------------------------------


def delete_vote_from_airtable(record_id: str) -> None:
    """Delete a VotePreference record from Airtable Guild Votes table."""
    if not _sync_enabled():
        return
    try:
        table = get_table(GUILD_VOTES_TABLE_ID)
        table.delete(record_id)
        logger.info("Deleted Airtable VotePreference %s", record_id)
    except Exception:
        logger.exception("Airtable delete failed for VotePreference %s", record_id)


def sync_vote_to_airtable(vote: VotePreference) -> str | None:
    """Push a VotePreference to Airtable Guild Votes table."""
    if not _sync_enabled():
        return None
    try:
        table = get_table(GUILD_VOTES_TABLE_ID)
        fields = vote_preference_to_airtable(vote)
        if vote.airtable_record_id:
            table.update(vote.airtable_record_id, fields)
            logger.info("Updated Airtable VotePreference %s (pk=%s)", vote.airtable_record_id, vote.pk)
            return vote.airtable_record_id
        else:
            record = table.create(fields)
            record_id = record["id"]
            type(vote).objects.filter(pk=vote.pk).update(airtable_record_id=record_id)
            vote.airtable_record_id = record_id
            logger.info("Created Airtable VotePreference %s (pk=%s)", record_id, vote.pk)
            return record_id
    except Exception:
        logger.exception("Airtable sync failed for VotePreference pk=%s", vote.pk)
        return None


# ---------------------------------------------------------------------------
# FundingSnapshot -> Voting Sessions (Django -> AT only)
# ---------------------------------------------------------------------------


def delete_snapshot_from_airtable(record_id: str) -> None:
    """Delete a FundingSnapshot record from the Airtable Voting Sessions table."""
    if not _sync_enabled():
        return
    try:
        table = get_table(VOTING_SESSIONS_TABLE_ID)
        table.delete(record_id)
        logger.info("Deleted Airtable FundingSnapshot %s", record_id)
    except Exception:
        logger.exception("Airtable delete failed for FundingSnapshot %s", record_id)


def sync_snapshot_to_airtable(snapshot: FundingSnapshot) -> str | None:
    """Push a FundingSnapshot to Airtable Voting Sessions table."""
    if not _sync_enabled():
        return None
    try:
        table = get_table(VOTING_SESSIONS_TABLE_ID)
        fields = funding_snapshot_to_airtable(snapshot)
        if snapshot.airtable_record_id:
            table.update(snapshot.airtable_record_id, fields)
            logger.info("Updated Airtable FundingSnapshot %s (pk=%s)", snapshot.airtable_record_id, snapshot.pk)
            return snapshot.airtable_record_id
        else:
            record = table.create(fields)
            record_id = record["id"]
            type(snapshot).objects.filter(pk=snapshot.pk).update(airtable_record_id=record_id)
            snapshot.airtable_record_id = record_id
            logger.info("Created Airtable FundingSnapshot %s (pk=%s)", record_id, snapshot.pk)
            return record_id
    except Exception:
        logger.exception("Airtable sync failed for FundingSnapshot pk=%s", snapshot.pk)
        return None
