"""BDD-style tests for core.email.send — the logged email wrapper."""

import json
from unittest.mock import patch

import icalendar
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

        def it_keeps_an_attachment_with_a_non_text_parameterised_type_a_plain_file():
            core_email.send(
                to="member@example.com",
                subject="Hi",
                trigger_kind="x",
                text_body="plain",
                attachments=[("a.bin", b"x", "application/octet-stream; name=a.bin")],
            )
            assert mail.outbox[0].attachments[0][0] == "a.bin"

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


def describe_calendar_invites():
    """#1287: an invite's ``method`` must reach the recipient, or calendars ignore a move or cancel."""

    def _send_invite(method: str = "CANCEL") -> TransactionalEmailLog:
        return core_email.send(
            to="sam@example.com",
            subject="Your tour is cancelled",
            trigger_kind="tours.visitor",
            text_body="Cancelled.",
            html_body="<p>Cancelled.</p>",
            attachments=[("tour.ics", b"BEGIN:VCALENDAR\r\nMETHOD:CANCEL\r\n", core_email.invite_mimetype(method))],
        )

    def it_names_the_method_in_the_content_type():
        assert core_email.invite_mimetype("REQUEST") == "text/calendar; method=REQUEST"

    def it_builds_a_message_whose_calendar_part_carries_the_method():
        _send_invite()
        built = mail.outbox[0].message()
        (part,) = [p for p in built.walk() if p.get_content_type() == "text/calendar"]
        assert part.get_param("method") == "CANCEL"
        assert part.get_filename() == "tour.ics"
        assert part.get_content_disposition() == "attachment"
        assert "METHOD:CANCEL" in part.get_content()

    def it_takes_an_invite_given_as_text():
        core_email.send(
            to="sam@example.com",
            subject="Booked",
            trigger_kind="tours.visitor",
            text_body="Booked.",
            attachments=[("tour.ics", "BEGIN:VCALENDAR", core_email.invite_mimetype("REQUEST"))],
        )
        (part,) = mail.outbox[0].attachments
        assert (part.get_param("method"), part.get_content()) == ("REQUEST", "BEGIN:VCALENDAR\n")

    def it_hands_resend_the_content_type_with_the_method(settings):
        settings.EMAIL_BACKEND = "anymail.backends.resend.EmailBackend"
        settings.ANYMAIL = {"RESEND_API_KEY": "test-key"}
        with patch("requests.Session.request") as request:
            request.return_value.status_code = 200
            request.return_value.json.return_value = {"id": "resend-id"}
            log = _send_invite()
        assert log.status == TransactionalEmailLog.Status.SENT
        (attachment,) = json.loads(request.call_args.kwargs["data"])["attachments"]
        assert attachment["filename"] == "tour.ics"
        assert attachment["content_type"].startswith("text/calendar;")
        assert 'method="CANCEL"' in attachment["content_type"]


def describe_add_invite_parties():
    def it_makes_the_sending_address_the_organizer_and_the_recipient_the_attendee(settings):
        settings.DEFAULT_FROM_EMAIL = "Past Lives <hello@pastlives.space>"
        event = icalendar.Event()
        core_email.add_invite_parties(event, attendee_email="sam@example.com", attendee_name="Sam")
        assert str(event["organizer"]) == "mailto:hello@pastlives.space"
        assert event["organizer"].params["cn"] == "Past Lives Makerspace"
        assert str(event["attendee"]) == "mailto:sam@example.com"
        assert event["attendee"].params["cn"] == "Sam"
