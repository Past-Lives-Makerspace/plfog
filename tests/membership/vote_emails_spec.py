"""Tests for vote_emails module."""

from unittest.mock import patch

import pytest
from django.core import mail

from membership.vote_emails import send_results_email

pytestmark = pytest.mark.django_db


def describe_send_results_email():
    def it_sends_results_to_recipients():
        results_data = {
            "total_pool": 100,
            "votes_cast": 5,
            "results": [
                {
                    "guild_name": "Ceramics",
                    "votes_1st": 3,
                    "votes_2nd": 1,
                    "votes_3rd": 0,
                    "weighted_amount": 18,
                    "disbursement": 36.0,
                },
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
