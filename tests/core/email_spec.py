"""BDD-style tests for core.email.send — the logged email wrapper."""

from unittest.mock import patch

import pytest
from django.core import mail

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

    def describe_with_attachments():
        def it_sends_a_multipart_message_carrying_the_attachment():
            log = core_email.send(
                to="member@example.com",
                subject="Your orientation",
                trigger_kind="orientations.confirmed",
                text_body="See attached invite.",
                attachments=[("orientation.ics", b"BEGIN:VCALENDAR", "text/calendar")],
            )
            assert log.status == TransactionalEmailLog.Status.SENT
            assert len(mail.outbox) == 1
            message = mail.outbox[0]
            assert message.subject == "Your orientation"
            assert message.to == ["member@example.com"]
            assert message.body == "See attached invite."
            filename, content, mimetype = message.attachments[0]
            assert filename == "orientation.ics"
            assert mimetype == "text/calendar"
            assert (content if isinstance(content, str) else content.decode()) == "BEGIN:VCALENDAR"

        def it_keeps_the_html_alternative_alongside_the_attachment():
            core_email.send(
                to="member@example.com",
                subject="Hi",
                trigger_kind="orientations.confirmed",
                text_body="plain",
                html_body="<p>fancy</p>",
                attachments=[("orientation.ics", "BEGIN:VCALENDAR", "text/calendar")],
            )
            message = mail.outbox[0]
            assert message.alternatives == [("<p>fancy</p>", "text/html")]
            assert message.attachments[0][0] == "orientation.ics"

        def it_omits_the_html_alternative_when_none_is_given():
            core_email.send(
                to="member@example.com",
                subject="Hi",
                trigger_kind="orientations.confirmed",
                text_body="plain",
                attachments=[("orientation.ics", "x", "text/calendar")],
            )
            assert mail.outbox[0].alternatives == []

        def describe_when_delivery_fails():
            def it_logs_failed_and_reraises_by_default():
                with patch.object(core_email.EmailMultiAlternatives, "send", side_effect=RuntimeError("SMTP down")):
                    with pytest.raises(RuntimeError):
                        core_email.send(
                            to="m@example.com",
                            subject="Hi",
                            trigger_kind="orientations.confirmed",
                            text_body="b",
                            attachments=[("a.ics", "x", "text/calendar")],
                        )
                assert TransactionalEmailLog.objects.get().status == TransactionalEmailLog.Status.FAILED

            def it_swallows_when_best_effort():
                with patch.object(core_email.EmailMultiAlternatives, "send", side_effect=RuntimeError("SMTP down")):
                    log = core_email.send(
                        to="m@example.com",
                        subject="Hi",
                        trigger_kind="orientations.confirmed",
                        text_body="b",
                        attachments=[("a.ics", "x", "text/calendar")],
                        best_effort=True,
                    )
                assert log.status == TransactionalEmailLog.Status.FAILED
