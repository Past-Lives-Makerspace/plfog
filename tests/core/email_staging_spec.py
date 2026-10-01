"""BDD specs for what core.email.send does on staging (settings.IS_STAGING).

The policy itself is specified in email_policy_spec; these cover its application at the
choke point: the marking on what goes out, the suppressed rows for what does not, and that
no transport call happens when nobody is left.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.core import mail

from core import email as core_email
from core.models import TransactionalEmailLog

pytestmark = pytest.mark.django_db

_ALLOWED = "lee@example.com"
_ALLOWED_TOO = "josh@example.com"
_UNKNOWN = "member@example.com"


@pytest.fixture
def staging(settings):
    settings.IS_STAGING = True
    settings.MEMBER_HOST = "staging.pastlives.space"
    settings.EMAIL_DELIVERY_ALLOWLIST = frozenset({_ALLOWED, _ALLOWED_TOO})
    return settings


def _send(**overrides):
    kwargs = dict(to=_ALLOWED, subject="Your login code", trigger_kind="auth.login_code", text_body="Code: 1234")
    kwargs.update(overrides)
    return core_email.send(**kwargs)


def describe_send_on_staging():
    def it_marks_the_subject_text_and_html_of_a_delivered_email(staging):
        log = _send(html_body="<html><body><p>Code: 1234</p></body></html>")
        message = mail.outbox[0]
        assert message.subject == "[STAGING] Your login code"
        assert message.body.startswith("STAGING. This came from staging.pastlives.space")
        html, mimetype = message.alternatives[0]
        assert mimetype == "text/html"
        assert html.startswith("<html><body><div style=")
        assert "Nothing here is real." in html
        assert log.status == TransactionalEmailLog.Status.SENT
        assert log.subject == "[STAGING] Your login code"

    def it_writes_a_suppressed_row_and_sends_nothing_when_the_only_recipient_is_refused(staging):
        with patch("core.email.send_mail") as mock_send:
            log = _send(to=_UNKNOWN)
        mock_send.assert_not_called()
        assert mail.outbox == []
        assert log.status == TransactionalEmailLog.Status.SUPPRESSED
        assert log.to_email == _UNKNOWN
        assert log.error_message == "staging: recipient not on the delivery allowlist"
        assert TransactionalEmailLog.objects.count() == 1

    def it_delivers_to_the_allowed_recipients_and_logs_each_refused_one(staging):
        log = _send(to=[_ALLOWED, _UNKNOWN, "other@example.com"])
        assert mail.outbox[0].to == [_ALLOWED]
        assert log.status == TransactionalEmailLog.Status.SENT
        assert log.to_email == _ALLOWED
        suppressed = TransactionalEmailLog.objects.filter(status=TransactionalEmailLog.Status.SUPPRESSED)
        assert sorted(suppressed.values_list("to_email", flat=True)) == [_UNKNOWN, "other@example.com"]

    def it_applies_the_policy_to_bcc_as_well(staging):
        log = _send(bcc=[_ALLOWED_TOO, _UNKNOWN])
        message = mail.outbox[0]
        assert message.to == [_ALLOWED]
        assert message.bcc == [_ALLOWED_TOO]
        assert log.to_email == f"{_ALLOWED}, {_ALLOWED_TOO}"
        assert TransactionalEmailLog.objects.filter(status=TransactionalEmailLog.Status.SUPPRESSED).count() == 1

    def it_still_sends_when_only_a_bcc_recipient_survives(staging):
        log = _send(to=_UNKNOWN, bcc=_ALLOWED_TOO)
        message = mail.outbox[0]
        assert message.to == []
        assert message.bcc == [_ALLOWED_TOO]
        assert log.status == TransactionalEmailLog.Status.SENT
        assert log.to_email == _ALLOWED_TOO

    def it_returns_the_first_suppressed_row_when_everyone_was_refused(staging):
        log = _send(to=[_UNKNOWN, "second@example.com"])
        rows = list(TransactionalEmailLog.objects.order_by("pk"))
        assert [r.to_email for r in rows] == [_UNKNOWN, "second@example.com"]
        assert log.pk == rows[0].pk


def describe_send_off_staging():
    def it_delivers_to_anyone_and_marks_nothing(settings):
        settings.IS_STAGING = False
        log = _send(to=_UNKNOWN, html_body="<body><p>Code</p></body>")
        message = mail.outbox[0]
        assert message.subject == "Your login code"
        assert message.body == "Code: 1234"
        assert message.alternatives[0][0] == "<body><p>Code</p></body>"
        assert log.status == TransactionalEmailLog.Status.SENT
        assert TransactionalEmailLog.objects.count() == 1
