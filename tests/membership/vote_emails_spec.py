"""Tests for vote_emails module."""

from datetime import date
from unittest.mock import patch

import pytest
from django.core import mail

from membership.vote_emails import send_results_email, send_voting_emails

pytestmark = pytest.mark.django_db


MOCK_MEMBERS = [
    {"record_id": "rec001", "name": "Alice", "email": "alice@example.com"},
    {"record_id": "rec002", "name": "Bob", "email": "bob@example.com"},
    {"record_id": "rec003", "name": "No Email", "email": ""},
]


def describe_send_voting_emails():
    def it_sends_to_members_with_email():
        result = send_voting_emails(
            members=MOCK_MEMBERS[:2],
            session_id=1,
            session_name="March 2026",
            close_date=date(2026, 3, 15),
            base_url="http://localhost:8000",
        )
        assert result["sent_count"] == 2
        assert result["errors"] == []
        assert len(mail.outbox) == 2
        assert "Vote for your guilds" in mail.outbox[0].subject

    def it_skips_members_without_email():
        result = send_voting_emails(
            members=MOCK_MEMBERS,
            session_id=1,
            session_name="March 2026",
            close_date=date(2026, 3, 15),
            base_url="http://localhost:8000",
        )
        assert result["sent_count"] == 2
        assert len(result["errors"]) == 1
        assert "No Email" in result["errors"][0]

    def it_includes_voting_url_in_email():
        send_voting_emails(
            members=MOCK_MEMBERS[:1],
            session_id=42,
            session_name="Test",
            close_date=date(2026, 3, 15),
            base_url="http://localhost:8000",
        )
        body = mail.outbox[0].body
        assert "http://localhost:8000/voting/vote/" in body

    @patch("membership.vote_emails.send_mail", side_effect=Exception("SMTP error"))
    def it_catches_smtp_errors(mock_send):
        result = send_voting_emails(
            members=MOCK_MEMBERS[:1],
            session_id=1,
            session_name="Test",
            close_date=date(2026, 3, 15),
            base_url="http://localhost:8000",
        )
        assert result["sent_count"] == 0
        assert len(result["errors"]) == 1
        assert "SMTP error" in result["errors"][0]


def describe_send_results_email():
    def it_sends_results_to_recipients():
        results_data = {
            "total_pool": 100,
            "votes_cast": 5,
            "results": [
                {"guild_name": "Ceramics", "votes_1st": 3, "votes_2nd": 1,
                 "votes_3rd": 0, "weighted_amount": 18, "disbursement": 36.0},
            ],
        }
        send_results_email(
            recipients=["lead@example.com"],
            session_name="March 2026",
            results_data=results_data,
        )
        assert len(mail.outbox) == 1
        assert "Results" in mail.outbox[0].subject
        assert "lead@example.com" in mail.outbox[0].to

    def it_raises_on_smtp_failure():
        with patch("membership.vote_emails.send_mail", side_effect=Exception("fail")):
            with pytest.raises(Exception, match="fail"):
                send_results_email(
                    recipients=["x@example.com"],
                    session_name="Test",
                    results_data={"total_pool": 0, "votes_cast": 0, "results": []},
                )
