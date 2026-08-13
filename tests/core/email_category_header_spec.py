"""BDD specs for the X-Category header fix in core.email._deliver/send.

``category=`` used to only affect the multipart decision when combined with
attachments/bcc; a category-only send must switch to the ``EmailMultiAlternatives``
path (the plain ``send_mail`` helper cannot carry custom headers) and stamp
``X-Category`` on the outgoing message — even with no attachments and no bcc.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.core import mail

from core import email as core_email

pytestmark = pytest.mark.django_db


def describe_send():
    def describe_with_a_category():
        def it_uses_the_multipart_path_instead_of_plain_send_mail():
            with patch("core.email.send_mail") as mock_plain:
                core_email.send(
                    to="member@example.com",
                    subject="Renewal reminder",
                    trigger_kind="billing.receipt",
                    text_body="Renew now.",
                    category="Billing",
                )
            mock_plain.assert_not_called()

        def it_sets_the_x_category_header_on_the_sent_message():
            core_email.send(
                to="member@example.com",
                subject="Renewal reminder",
                trigger_kind="billing.receipt",
                text_body="Renew now.",
                category="Billing",
            )
            assert len(mail.outbox) == 1
            assert mail.outbox[0].extra_headers["X-Category"] == "Billing"

        def it_sets_the_header_even_with_no_attachments_and_no_bcc():
            # The exact regression case: category is the ONLY reason to go multipart.
            core_email.send(
                to="member@example.com",
                subject="Hi",
                trigger_kind="x",
                text_body="body",
                category="Voting",
                attachments=None,
                bcc=None,
            )
            message = mail.outbox[0]
            assert message.attachments == []
            assert message.bcc == []
            assert message.extra_headers["X-Category"] == "Voting"

    def describe_without_a_category():
        def it_uses_the_plain_send_mail_path():
            with patch("core.email.EmailMultiAlternatives") as mock_multi:
                core_email.send(
                    to="member@example.com",
                    subject="Hi",
                    trigger_kind="x",
                    text_body="body",
                    category=None,
                )
            mock_multi.assert_not_called()

        def it_omits_the_x_category_header():
            core_email.send(
                to="member@example.com",
                subject="Hi",
                trigger_kind="x",
                text_body="body",
            )
            assert len(mail.outbox) == 1
            assert "X-Category" not in mail.outbox[0].extra_headers
