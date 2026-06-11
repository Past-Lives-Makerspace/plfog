"""BDD-style tests for core.email.send — the logged email wrapper."""

from unittest.mock import patch

import pytest

from core import email as core_email
from core.models import TransactionalEmailLog

pytestmark = pytest.mark.django_db


def describe_send():
    def it_sends_and_logs_a_sent_row():
        with patch("core.email.send_mail") as mock_send:
            log = core_email.send(
                to="member@example.com",
                subject="Receipt",
                trigger_kind="billing.receipt",
                text_body="body",
                html_body="<p>body</p>",
            )
        mock_send.assert_called_once()
        assert log.status == TransactionalEmailLog.Status.SENT
        assert log.to_email == "member@example.com"
        assert TransactionalEmailLog.objects.count() == 1

    def it_joins_multiple_recipients_into_one_row():
        with patch("core.email.send_mail"):
            log = core_email.send(
                to=["a@example.com", "b@example.com"],
                subject="Hi",
                trigger_kind="x",
                text_body="b",
            )
        assert log.to_email == "a@example.com, b@example.com"

    def describe_when_send_mail_raises():
        def it_logs_failed_and_reraises_by_default():
            with patch("core.email.send_mail", side_effect=RuntimeError("SMTP down")):
                with pytest.raises(RuntimeError):
                    core_email.send(
                        to="m@example.com",
                        subject="Hi",
                        trigger_kind="x",
                        text_body="b",
                    )
            log = TransactionalEmailLog.objects.get()
            assert log.status == TransactionalEmailLog.Status.FAILED
            assert "SMTP down" in log.error_message

        def it_swallows_when_best_effort():
            with patch("core.email.send_mail", side_effect=RuntimeError("SMTP down")):
                log = core_email.send(
                    to="m@example.com",
                    subject="Hi",
                    trigger_kind="x",
                    text_body="b",
                    best_effort=True,
                )
            assert log.status == TransactionalEmailLog.Status.FAILED
