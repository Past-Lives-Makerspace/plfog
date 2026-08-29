"""Tests for airtable_sync.config — field mapping transforms."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock

from airtable_sync.config import (
    LEASE_TYPE_FROM_AT,
    LEASE_TYPE_TO_AT,
    MEMBER_TYPE_FROM_AT,
    MEMBER_TYPE_TO_AT,
    MEMBER_STATUS_FROM_AT,
    MEMBER_STATUS_TO_AT,
    SPACE_STATUS_FROM_AT,
    SPACE_STATUS_TO_AT,
    funding_snapshot_to_airtable,
    lease_from_airtable,
    lease_to_airtable,
    member_from_airtable,
    member_to_airtable,
    space_from_airtable,
    space_to_airtable,
    vote_preference_to_airtable,
)


def describe_enum_mappings():
    def it_has_bidirectional_member_status_mapping():
        for django_val, at_val in MEMBER_STATUS_TO_AT.items():
            assert MEMBER_STATUS_FROM_AT[at_val] == django_val

    def it_has_bidirectional_member_role_mapping():
        for django_val, at_val in MEMBER_TYPE_TO_AT.items():
            assert MEMBER_TYPE_FROM_AT[at_val] == django_val

    def it_has_bidirectional_space_status_mapping():
        for django_val, at_val in SPACE_STATUS_TO_AT.items():
            assert SPACE_STATUS_FROM_AT[at_val] == django_val

    def it_has_bidirectional_lease_type_mapping():
        for django_val, at_val in LEASE_TYPE_TO_AT.items():
            assert LEASE_TYPE_FROM_AT[at_val] == django_val


def describe_member_to_airtable():
    def it_converts_member_with_preferred_name():
        member = MagicMock()
        member.preferred_name = "JD"
        member.full_legal_name = "John Doe"
        member._pre_signup_email = "jd@example.com"
        member.phone = "555-1234"
        member.status = "active"
        member.member_type = "standard"
        member.join_date = date(2024, 1, 15)
        member.cancellation_date = None
        member.notes = "Test member"
        member.emergency_contact_name = "Jane Doe"
        member.emergency_contact_phone = "555-5678"
        member.emergency_contact_relationship = "Spouse"
        member.membership_plan.name = "Standard"
        member.membership_plan.monthly_price = Decimal("150.00")

        fields = member_to_airtable(member)

        assert fields["Member Name"] == "JD"
        assert fields["Legal name (if different)"] == "John Doe"
        assert fields["Email"] == "jd@example.com"
        assert fields["Status"] == "Active"
        assert fields["Role"] == "Standard Member"
        assert fields["Join Date"] == "2024-01-15"
        assert fields["Cancellation Date"] is None
        assert fields["Monthly Membership $"] == 150.0
        assert fields["Emergency Contact"] == "Jane Doe"

    def it_converts_member_without_preferred_name():
        member = MagicMock()
        member.preferred_name = ""
        member.full_legal_name = "John Doe"
        member._pre_signup_email = "jd@example.com"
        member.phone = ""
        member.status = "invited"
        member.member_type = "guild_lead"
        member.join_date = None
        member.cancellation_date = None
        member.notes = ""
        member.emergency_contact_name = ""
        member.emergency_contact_phone = ""
        member.emergency_contact_relationship = ""
        member.membership_plan.name = "Standard"
        member.membership_plan.monthly_price = Decimal("130.00")

        fields = member_to_airtable(member)

        assert fields["Member Name"] == "John Doe"
        assert fields["Legal name (if different)"] == ""
        assert fields["Status"] == "Pending"
        assert fields["Role"] == "Guild Lead"


def describe_member_from_airtable():
    def it_converts_at_fields_with_legal_name():
        fields = {
            "Member Name": "JD",
            "Legal name (if different)": "John Doe",
            "Email": "jd@example.com",
            "Phone": "555-1234",
            "Status": "Active",
            "Role": "Standard Member",
            "Join Date": "2024-01-15",
            "Cancellation Date": None,
            "Notes": "Test",
            "Emergency Contact": "Jane",
            "Emergency Contact Phone": "555-5678",
            "Emergency Contact Relationship": "Spouse",
        }

        result = member_from_airtable(fields)

        assert result["full_legal_name"] == "John Doe"
        assert result["preferred_name"] == "JD"
        assert result["_pre_signup_email"] == "jd@example.com"
        assert result["status"] == "active"
        assert result["member_type"] == "standard"
        assert result["join_date"] == date(2024, 1, 15)

    def it_converts_at_fields_without_legal_name():
        fields = {
            "Member Name": "John Doe",
            "Email": "jd@example.com",
            "Status": "Former",
            "Role": "Work Trade",
        }

        result = member_from_airtable(fields)

        assert result["full_legal_name"] == "John Doe"
        assert result["preferred_name"] == ""
        assert result["status"] == "former"
        assert result["member_type"] == "work_trade"

    def it_handles_unknown_status_gracefully():
        fields = {"Member Name": "Test", "Status": "Unknown"}

        result = member_from_airtable(fields)

        assert "status" not in result

    def it_handles_unknown_role_gracefully():
        fields = {"Member Name": "Test", "Role": "Unknown"}

        result = member_from_airtable(fields)

        assert "member_type" not in result

    def it_handles_empty_status_and_role():
        fields = {"Member Name": "Test", "Status": "", "Role": ""}

        result = member_from_airtable(fields)

        assert "status" not in result
        assert "member_type" not in result


def describe_member_to_airtable_without_plan():
    def it_skips_plan_fields_when_no_membership_plan():
        member = MagicMock(spec=[])  # no attributes by default
        member.preferred_name = "Test"
        member.full_legal_name = "Test User"
        member._pre_signup_email = "test@example.com"
        member.phone = ""
        member.status = "active"
        member.member_type = "standard"
        member.join_date = None
        member.cancellation_date = None
        member.notes = ""
        member.emergency_contact_name = ""
        member.emergency_contact_phone = ""
        member.emergency_contact_relationship = ""

        fields = member_to_airtable(member)

        assert "Membership Plan" not in fields
        assert "Monthly Membership $" not in fields


def describe_space_to_airtable():
    def it_converts_space():
        space = MagicMock()
        space.space_id = "A1"
        space.name = "Studio A1"
        space.size_sqft = Decimal("200.00")
        space.manual_price = Decimal("750.00")
        space.status = "occupied"
        space.notes = "Nice studio"

        fields = space_to_airtable(space)

        assert fields["Space Code"] == "A1"
        assert fields["Designation"] == "Studio A1"
        assert fields["Size (sq ft)"] == 200.0
        assert fields["Manual Price"] == 750.0
        assert fields["Status"] == "Occupied"


def describe_space_from_airtable():
    def it_converts_at_fields():
        fields = {
            "Space Code": "A1",
            "Designation": "Studio A1",
            "Size (sq ft)": 200,
            "Manual Price": 750.0,
            "Status": "Available",
            "Notes": "Nice studio",
        }

        result = space_from_airtable(fields)

        assert result["space_id"] == "A1"
        assert result["name"] == "Studio A1"
        assert result["size_sqft"] == Decimal("200")
        assert result["manual_price"] == Decimal("750.0")
        assert result["status"] == "available"

    def it_handles_missing_optional_fields():
        fields = {
            "Space Code": "B1",
            "Designation": "Studio B1",
            "Notes": "",
        }

        result = space_from_airtable(fields)

        assert result["space_id"] == "B1"
        assert "size_sqft" not in result
        assert "manual_price" not in result
        assert "status" not in result

    def it_handles_unknown_status():
        fields = {"Space Code": "B1", "Status": "Unknown"}

        result = space_from_airtable(fields)

        assert "status" not in result


def describe_lease_to_airtable():
    def it_converts_lease_with_linked_records():
        tenant = MagicMock()
        tenant.airtable_record_id = "recMEMBER123"

        space = MagicMock()
        space.airtable_record_id = "recSPACE456"

        lease = MagicMock()
        lease.tenant = tenant
        lease.space = space
        lease.monthly_rent = Decimal("500.00")
        lease.deposit_required = Decimal("500.00")
        lease.deposit_paid_date = date(2024, 1, 1)
        lease.start_date = date(2024, 1, 1)
        lease.end_date = None
        lease.lease_type = "month_to_month"
        lease.notes = ""

        fields = lease_to_airtable(lease)

        assert fields["Member"] == ["recMEMBER123"]
        assert fields["Space"] == ["recSPACE456"]
        assert fields["Monthly Rent"] == 500.0
        assert fields["Lease Type"] == "Month-to-month"

    def it_skips_linked_records_without_at_ids():
        tenant = MagicMock()
        tenant.airtable_record_id = None

        space = MagicMock()
        space.airtable_record_id = None

        lease = MagicMock()
        lease.tenant = tenant
        lease.space = space
        lease.monthly_rent = Decimal("500.00")
        lease.deposit_required = None
        lease.deposit_paid_date = None
        lease.start_date = date(2024, 1, 1)
        lease.end_date = None
        lease.lease_type = "annual"
        lease.notes = ""

        fields = lease_to_airtable(lease)

        assert "Member" not in fields
        assert "Space" not in fields
        assert fields["Lease Type"] == "Annual"


def describe_lease_from_airtable():
    def it_converts_at_fields():
        fields = {
            "Monthly Rent": 500.0,
            "Deposit Required": 500.0,
            "Start Date": "2024-01-01",
            "End Date": None,
            "Lease Type": "Month-to-month",
            "Notes": "Test lease",
            "Member": ["recMEM123"],
            "Space": ["recSPC456"],
        }

        result = lease_from_airtable(fields)

        assert result["monthly_rent"] == Decimal("500.0")
        assert result["start_date"] == date(2024, 1, 1)
        assert result["lease_type"] == "month_to_month"
        assert result["_member_record_ids"] == ["recMEM123"]
        assert result["_space_record_ids"] == ["recSPC456"]

    def it_handles_missing_optional_fields():
        fields = {
            "Notes": "",
            "Start Date": "2024-01-01",
        }

        result = lease_from_airtable(fields)

        assert result["start_date"] == date(2024, 1, 1)
        assert "monthly_rent" not in result
        assert "deposit_required" not in result
        assert "lease_type" not in result

    def it_handles_unknown_lease_type():
        fields = {"Lease Type": "Unknown"}

        result = lease_from_airtable(fields)

        assert "lease_type" not in result


def describe_vote_preference_to_airtable():
    def it_converts_vote():
        vote = MagicMock()
        vote.member.display_name = "John"
        vote.member.airtable_record_id = "recMEM123"
        vote.guild_1st.name = "Glass Guild"
        vote.guild_2nd.name = "Tech Guild"
        vote.guild_3rd.name = "Art Guild"
        vote.updated_at.isoformat.return_value = "2024-01-15T10:00:00+00:00"

        fields = vote_preference_to_airtable(vote)

        assert fields["Member Name"] == "John"
        assert fields["Guild 1st"] == "Glass Guild"
        assert fields["Guild 2nd"] == "Tech Guild"
        assert fields["Guild 3rd"] == "Art Guild"

    def it_blanks_missing_optional_choices():
        # 2nd/3rd are optional now; a partial ballot must sync as "" rather than raise
        # AttributeError (which the broad sync except would swallow, dropping the row).
        vote = MagicMock()
        vote.member.display_name = "Ada"
        vote.member.airtable_record_id = "recABC"
        vote.guild_1st.name = "Fiber"
        vote.guild_2nd = None
        vote.guild_3rd = None
        vote.updated_at = None

        fields = vote_preference_to_airtable(vote)

        assert fields["Guild 1st"] == "Fiber"
        assert fields["Guild 2nd"] == ""
        assert fields["Guild 3rd"] == ""


def describe_funding_snapshot_to_airtable():
    def it_converts_snapshot():
        snapshot = MagicMock()
        snapshot.cycle_label = "March 2026"
        snapshot.snapshot_at.date.return_value.isoformat.return_value = "2026-03-15"
        snapshot.contributor_count = 10
        snapshot.funding_pool = Decimal("100.00")
        snapshot.results = {
            "guilds": [
                {"name": "Glass", "amount": 50, "percentage": 50},
                {"name": "Tech", "amount": 50, "percentage": 50},
            ]
        }

        fields = funding_snapshot_to_airtable(snapshot)

        assert fields["Name"] == "March 2026"
        assert fields["Status"] == "Calculated"
        assert fields["Eligible Member Count"] == 10
        assert "Glass" in fields["Results Summary"]
