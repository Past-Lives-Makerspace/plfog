"""No-double-wrap guards for the branded-notification-email shell (§4.4).

The branded shell wrap lives *inside* ``rendered_message`` (copy mode only). The two
``message_for`` branches that carry an already-complete branded document — the
``emit_with_email_shell`` override and the explicit ``html_body`` fixed message — never
call ``rendered_message``, so they must be delivered verbatim with no card-inside-a-card.

Shell-presence is asserted with the SHELL-ONLY footer marker ``Do It Together`` (unique
to ``_base.html``), never the ``Past Lives`` wordmark.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.core import mail
from django.template.loader import render_to_string

from core.events.emit import emit
from core.events.senders import emit_with_email_shell

pytestmark = pytest.mark.django_db


def describe_email_shell_override_is_not_double_wrapped():
    def it_keeps_the_structural_document_with_one_branded_card():
        # orientation_confirmed.html already {% extends %} _base.html (one card + one
        # footer). Routed as a Channel.EMAIL override, it must NOT be re-wrapped.
        emit_with_email_shell(
            "site_announcement",
            context={},
            subject="Hello",
            text_template="membership/emails/orientation_confirmed.txt",
            html_template="membership/emails/orientation_confirmed.html",
            template_context={},
            email_to="dest@example.com",
        )
        html = mail.outbox[0].alternatives[0][0]
        # Exactly one footer → exactly one card → no card-inside-a-card.
        assert html.count("Do It Together") == 1


def describe_fixed_html_body_is_not_wrapped():
    def it_delivers_an_explicit_full_document_verbatim():
        # The login-code email renders a standalone full document and passes it to emit
        # as html_body (the fixed-message path) — it must arrive byte-identical.
        user = User.objects.create_user(username="nl", email="nl@example.com")
        doc = render_to_string("account/email/login_code_message.html", {"code": "123456"})
        emit(
            "member.login_invite",
            actor=user,
            context={"user": user},
            title="Sign in",
            body="b",
            html_body=doc,
        )
        html = mail.outbox[0].alternatives[0][0]
        assert html == doc
        assert html.count("Do It Together") == 1
