"""Tests for airtable_sync module."""

from datetime import date
from unittest.mock import MagicMock, patch

from membership.airtable_sync import (
    get_eligible_members,
    get_member,
    get_voteable_guilds,
    sync_session_to_airtable,
    sync_vote_to_airtable,
)


def _mock_api():
    """Create a mock Api instance with a mock table."""
    api = MagicMock()
    table = MagicMock()
    api.table.return_value = table
    return api, table


def describe_api_factory():
    @patch("membership.airtable_sync.Api")
    def it_creates_api_with_settings_key(mock_api_cls):
        from membership.airtable_sync import _api

        _api()
        mock_api_cls.assert_called_once()


def describe_get_eligible_members():
    @patch("membership.airtable_sync._api")
    def it_fetches_and_transforms_records(mock_api_fn):
        api, table = _mock_api()
        mock_api_fn.return_value = api
        table.all.return_value = [
            {
                "id": "rec001",
                "fields": {
                    "Member Name": "Alice",
                    "Email": "alice@example.com",
                    "Phone": "555-1234",
                    "Role": "Standard",
                    "Monthly Membership $": 150,
                },
            },
        ]
        result = get_eligible_members()
        assert len(result) == 1
        assert result[0]["record_id"] == "rec001"
        assert result[0]["name"] == "Alice"
        assert result[0]["email"] == "alice@example.com"
        assert result[0]["monthly_amount"] == 150
        table.all.assert_called_once()


def describe_get_member():
    @patch("membership.airtable_sync._api")
    def it_fetches_single_member(mock_api_fn):
        api, table = _mock_api()
        mock_api_fn.return_value = api
        table.get.return_value = {
            "id": "recXYZ",
            "fields": {
                "Member Name": "Bob",
                "Email": "bob@example.com",
                "Status": "Active",
                "Role": "Guild Lead",
                "Monthly Membership $": 200,
            },
        }
        result = get_member("recXYZ")
        assert result["record_id"] == "recXYZ"
        assert result["name"] == "Bob"
        assert result["status"] == "Active"
        table.get.assert_called_once_with("recXYZ")


def describe_get_voteable_guilds():
    @patch("membership.airtable_sync._api")
    def it_fetches_guilds_from_old_base(mock_api_fn):
        api, table = _mock_api()
        mock_api_fn.return_value = api
        table.all.return_value = [
            {"id": "recG1", "fields": {"Guild": "Ceramics"}},
            {"id": "recG2", "fields": {"Guild": "Glass"}},
        ]
        result = get_voteable_guilds()
        assert len(result) == 2
        assert result[0]["name"] == "Ceramics"
        assert result[1]["name"] == "Glass"


def describe_sync_session_to_airtable():
    @patch("membership.airtable_sync._api")
    def it_creates_new_session(mock_api_fn):
        api, table = _mock_api()
        mock_api_fn.return_value = api
        table.create.return_value = {"id": "recSESS1"}

        result = sync_session_to_airtable(
            session_id=1,
            name="March 2026",
            open_date=date(2026, 3, 1),
            close_date=date(2026, 3, 8),
            status="draft",
        )
        assert result == "recSESS1"
        table.create.assert_called_once()

    @patch("membership.airtable_sync._api")
    def it_updates_existing_session(mock_api_fn):
        api, table = _mock_api()
        mock_api_fn.return_value = api

        result = sync_session_to_airtable(
            session_id=1,
            name="March 2026",
            open_date=date(2026, 3, 1),
            close_date=date(2026, 3, 8),
            status="open",
            airtable_record_id="recEXIST",
        )
        assert result == "recEXIST"
        table.update.assert_called_once()

    @patch("membership.airtable_sync._api")
    def it_returns_empty_on_error(mock_api_fn):
        api, table = _mock_api()
        mock_api_fn.return_value = api
        table.create.side_effect = Exception("API error")

        result = sync_session_to_airtable(
            session_id=1,
            name="Fail",
            open_date=date(2026, 3, 1),
            close_date=date(2026, 3, 8),
            status="draft",
        )
        assert result == ""  # fallback to empty airtable_record_id

    @patch("membership.airtable_sync._api")
    def it_includes_results_summary_when_provided(mock_api_fn):
        api, table = _mock_api()
        mock_api_fn.return_value = api
        table.create.return_value = {"id": "recWithResults"}

        result = sync_session_to_airtable(
            session_id=1,
            name="With Results",
            open_date=date(2026, 3, 1),
            close_date=date(2026, 3, 8),
            status="calculated",
            results_summary='{"total_pool": 100}',
        )
        assert result == "recWithResults"
        fields = table.create.call_args[0][0]
        assert fields["Results Summary"] == '{"total_pool": 100}'


def describe_sync_vote_to_airtable():
    @patch("membership.airtable_sync._api")
    def it_creates_vote_record(mock_api_fn):
        api, table = _mock_api()
        mock_api_fn.return_value = api
        table.create.return_value = {"id": "recVOTE1"}

        result = sync_vote_to_airtable(
            member_name="Alice",
            member_airtable_id="rec001",
            guild_1st="Ceramics",
            guild_2nd="Glass",
            guild_3rd="Wood",
            session_name="March 2026",
        )
        assert result == "recVOTE1"

    @patch("membership.airtable_sync._api")
    def it_returns_empty_on_error(mock_api_fn):
        api, table = _mock_api()
        mock_api_fn.return_value = api
        table.create.side_effect = Exception("API error")

        result = sync_vote_to_airtable(
            member_name="Alice",
            member_airtable_id="rec001",
            guild_1st="Ceramics",
            guild_2nd="Glass",
            guild_3rd="Wood",
            session_name="March 2026",
        )
        assert result == ""
