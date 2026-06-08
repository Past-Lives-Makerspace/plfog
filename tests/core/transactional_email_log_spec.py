"""BDD-style tests for core.models.TransactionalEmailLog."""

import pytest

from core.models import TransactionalEmailLog

pytestmark = pytest.mark.django_db


def describe_TransactionalEmailLog():
    def it_records_a_sent_email():
        log = TransactionalEmailLog.objects.create(
            to_email="member@example.com",
            subject="Receipt for $20",
            trigger_kind="billing.receipt",
            status=TransactionalEmailLog.Status.SENT,
        )
        assert log.status == "sent"
        assert log.error_message == ""

    def it_records_a_failed_email_with_error():
        log = TransactionalEmailLog.objects.create(
            to_email="member@example.com",
            subject="Receipt for $20",
            trigger_kind="billing.receipt",
            status=TransactionalEmailLog.Status.FAILED,
            error_message="SMTP timeout",
        )
        assert log.status == "failed"
        assert "timeout" in log.error_message

    def it_has_a_readable_str():
        log = TransactionalEmailLog.objects.create(
            to_email="m@example.com",
            subject="Hi",
            trigger_kind="core.invite",
            status=TransactionalEmailLog.Status.SENT,
        )
        assert "m@example.com" in str(log)
