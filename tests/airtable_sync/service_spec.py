"""Tests for airtable_sync.service — sync operations with mocked Airtable.

Member/Space/Lease sync functions exist for backfill commands but are NOT
called from model save()/delete(). VotePreference and FundingSnapshot push
to Airtable on save.
"""

from __future__ import annotations

from tests.membership.factories import (
    FundingSnapshotFactory,
    GuildFactory,
    LeaseFactory,
    MemberFactory,
    SpaceFactory,
    VotePreferenceFactory,
)

from airtable_sync.service import (
    delete_lease_from_airtable,
    delete_member_from_airtable,
    delete_snapshot_from_airtable,
    delete_space_from_airtable,
    delete_vote_from_airtable,
    sync_lease_to_airtable,
    sync_member_to_airtable,
    sync_snapshot_to_airtable,
    sync_space_to_airtable,
    sync_vote_to_airtable,
)


# ---------------------------------------------------------------------------
# Member (used by backfill, not by model save)
# ---------------------------------------------------------------------------


def describe_sync_member_to_airtable():
    def it_short_circuits_when_disabled(db):
        member = MemberFactory()
        assert sync_member_to_airtable(member) is None

    def it_creates_new_airtable_record(db, enable_airtable_sync, mock_airtable_table):
        member = MemberFactory(airtable_record_id=None)
        result = sync_member_to_airtable(member)
        assert result == "recTEST123456789"
        mock_airtable_table.create.assert_called_once()

    def it_updates_existing_airtable_record(db, enable_airtable_sync, mock_airtable_table):
        member = MemberFactory(airtable_record_id="recEXISTING12345")
        result = sync_member_to_airtable(member)
        assert result == "recEXISTING12345"
        mock_airtable_table.update.assert_called_once()

    def it_logs_and_returns_none_on_error(db, enable_airtable_sync, mock_airtable_table):
        mock_airtable_table.create.side_effect = Exception("API error")
        member = MemberFactory(airtable_record_id=None)
        assert sync_member_to_airtable(member) is None


def describe_delete_member_from_airtable():
    def it_short_circuits_when_disabled():
        delete_member_from_airtable("recTEST123")

    def it_deletes_the_record(enable_airtable_sync, mock_airtable_table):
        delete_member_from_airtable("recTEST123")
        mock_airtable_table.delete.assert_called_once_with("recTEST123")

    def it_logs_on_error(enable_airtable_sync, mock_airtable_table):
        mock_airtable_table.delete.side_effect = Exception("API error")
        delete_member_from_airtable("recTEST123")


# ---------------------------------------------------------------------------
# Space (used by backfill, not by model save)
# ---------------------------------------------------------------------------


def describe_sync_space_to_airtable():
    def it_short_circuits_when_disabled(db):
        space = SpaceFactory()
        assert sync_space_to_airtable(space) is None

    def it_creates_new_airtable_record(db, enable_airtable_sync, mock_airtable_table):
        space = SpaceFactory(airtable_record_id=None)
        result = sync_space_to_airtable(space)
        assert result == "recTEST123456789"
        mock_airtable_table.create.assert_called_once()

    def it_updates_existing_airtable_record(db, enable_airtable_sync, mock_airtable_table):
        space = SpaceFactory(airtable_record_id="recSPACE12345678")
        result = sync_space_to_airtable(space)
        assert result == "recSPACE12345678"
        mock_airtable_table.update.assert_called_once()

    def it_logs_and_returns_none_on_error(db, enable_airtable_sync, mock_airtable_table):
        mock_airtable_table.create.side_effect = Exception("API error")
        space = SpaceFactory(airtable_record_id=None)
        assert sync_space_to_airtable(space) is None


def describe_delete_space_from_airtable():
    def it_short_circuits_when_disabled():
        delete_space_from_airtable("recSPACE123")

    def it_deletes_the_record(enable_airtable_sync, mock_airtable_table):
        delete_space_from_airtable("recSPACE123")
        mock_airtable_table.delete.assert_called_once_with("recSPACE123")

    def it_logs_on_error(enable_airtable_sync, mock_airtable_table):
        mock_airtable_table.delete.side_effect = Exception("API error")
        delete_space_from_airtable("recSPACE123")


# ---------------------------------------------------------------------------
# Lease (used by backfill, not by model save)
# ---------------------------------------------------------------------------


def describe_sync_lease_to_airtable():
    def it_short_circuits_when_disabled(db):
        lease = LeaseFactory()
        assert sync_lease_to_airtable(lease) is None

    def it_creates_new_airtable_record_for_member_tenant(db, enable_airtable_sync, mock_airtable_table):
        lease = LeaseFactory(airtable_record_id=None)
        result = sync_lease_to_airtable(lease)
        assert result == "recTEST123456789"
        mock_airtable_table.create.assert_called_once()

    def it_updates_existing_lease(db, enable_airtable_sync, mock_airtable_table):
        lease = LeaseFactory(airtable_record_id="recLEASE123456789")
        result = sync_lease_to_airtable(lease)
        assert result == "recLEASE123456789"
        mock_airtable_table.update.assert_called_once()

    def it_skips_guild_tenant_leases(db, enable_airtable_sync, mock_airtable_table):
        guild = GuildFactory()
        lease = LeaseFactory(tenant_obj=guild, airtable_record_id=None)
        result = sync_lease_to_airtable(lease)
        assert result is None
        mock_airtable_table.create.assert_not_called()

    def it_logs_and_returns_none_on_error(db, enable_airtable_sync, mock_airtable_table):
        mock_airtable_table.create.side_effect = Exception("API error")
        lease = LeaseFactory(airtable_record_id=None)
        assert sync_lease_to_airtable(lease) is None


def describe_delete_lease_from_airtable():
    def it_short_circuits_when_disabled():
        delete_lease_from_airtable("recLEASE123")

    def it_deletes_the_record(enable_airtable_sync, mock_airtable_table):
        delete_lease_from_airtable("recLEASE123")
        mock_airtable_table.delete.assert_called_once_with("recLEASE123")

    def it_logs_on_error(enable_airtable_sync, mock_airtable_table):
        mock_airtable_table.delete.side_effect = Exception("API error")
        delete_lease_from_airtable("recLEASE123")


# ---------------------------------------------------------------------------
# VotePreference (pushes to Airtable on model save)
# ---------------------------------------------------------------------------


def describe_sync_vote_to_airtable():
    def it_short_circuits_when_disabled(db):
        vote = VotePreferenceFactory()
        assert sync_vote_to_airtable(vote) is None

    def it_creates_new_airtable_record(db, enable_airtable_sync, mock_airtable_table):
        vote = VotePreferenceFactory(airtable_record_id=None)
        assert mock_airtable_table.create.called
        vote.refresh_from_db()
        assert vote.airtable_record_id == "recTEST123456789"

    def it_updates_existing_vote(db, enable_airtable_sync, mock_airtable_table):
        vote = VotePreferenceFactory(airtable_record_id="recVOTE1234567890")
        mock_airtable_table.reset_mock()
        sync_vote_to_airtable(vote)
        mock_airtable_table.update.assert_called_once()

    def it_logs_and_returns_none_on_error(db, enable_airtable_sync, mock_airtable_table):
        mock_airtable_table.create.side_effect = Exception("API error")
        vote = VotePreferenceFactory(airtable_record_id=None)
        assert vote.pk is not None


def describe_delete_vote_from_airtable():
    def it_short_circuits_when_disabled():
        delete_vote_from_airtable("recVOTE123")

    def it_deletes_the_record(enable_airtable_sync, mock_airtable_table):
        delete_vote_from_airtable("recVOTE123")
        mock_airtable_table.delete.assert_called_once_with("recVOTE123")

    def it_logs_on_error(enable_airtable_sync, mock_airtable_table):
        mock_airtable_table.delete.side_effect = Exception("API error")
        delete_vote_from_airtable("recVOTE123")


# ---------------------------------------------------------------------------
# FundingSnapshot (pushes to Airtable on model save)
# ---------------------------------------------------------------------------


def describe_delete_snapshot_from_airtable():
    def it_short_circuits_when_disabled():
        delete_snapshot_from_airtable("recSNAP123")

    def it_deletes_the_record(enable_airtable_sync, mock_airtable_table):
        delete_snapshot_from_airtable("recSNAP123")
        mock_airtable_table.delete.assert_called_once_with("recSNAP123")

    def it_logs_on_error(enable_airtable_sync, mock_airtable_table):
        mock_airtable_table.delete.side_effect = Exception("API error")
        delete_snapshot_from_airtable("recSNAP123")


def describe_sync_snapshot_to_airtable():
    def it_short_circuits_when_disabled(db):
        snapshot = FundingSnapshotFactory()
        assert sync_snapshot_to_airtable(snapshot) is None

    def it_creates_new_airtable_record(db, enable_airtable_sync, mock_airtable_table):
        snapshot = FundingSnapshotFactory(airtable_record_id=None)
        assert mock_airtable_table.create.called
        snapshot.refresh_from_db()
        assert snapshot.airtable_record_id == "recTEST123456789"

    def it_updates_existing_snapshot(db, enable_airtable_sync, mock_airtable_table):
        snapshot = FundingSnapshotFactory(airtable_record_id="recSNAP12345678")
        mock_airtable_table.reset_mock()
        sync_snapshot_to_airtable(snapshot)
        mock_airtable_table.update.assert_called_once()

    def it_logs_and_returns_none_on_error(db, enable_airtable_sync, mock_airtable_table):
        mock_airtable_table.create.side_effect = Exception("API error")
        snapshot = FundingSnapshotFactory(airtable_record_id=None)
        assert snapshot.pk is not None
