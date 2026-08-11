"""The member invite email arrives in the branded shell (branded-notification-emails).

The invite is the headline copy-mode email and the one that bypasses ``EmailAdapter``
entirely — it sends via the explicit ``email_to`` fan-out. These specs prove the wrap
reaches it there, that the plain-text body is untouched, and that the gold CTA button
reaches a *seeded* row (not only the ``copy.py`` fallback).

Email bodies are asserted on ``django.core.mail.outbox`` (the locmem backend the test
env installs) — ``TransactionalEmailLog`` stores no body, only the send metadata.
The HTML alternative is ``mail.outbox[0].alternatives[0][0]``.

Shell-presence is asserted with a SHELL-ONLY marker (footer ``because you have a … Makerspace account`` footer line / the
card ``#092E4C``), never the ``Past Lives`` wordmark — that also appears in the invite
body copy and would false-positive.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.core import mail
from django.core.management import call_command

from core.models import Invite, NotificationTemplate, TransactionalEmailLog

pytestmark = pytest.mark.django_db


def _invite(email: str = "invitee@example.com") -> Invite:
    admin = User.objects.create_user(username="inv_admin", email="admin@example.com")
    return Invite.objects.create(email=email, invited_by=admin)


def describe_invite_email_is_branded():
    def it_wraps_the_html_alternative_in_the_branded_shell():
        _invite().send_invite_email()
        assert len(mail.outbox) == 1
        html = mail.outbox[0].alternatives[0][0]
        # Shell-only markers prove the wrap.
        assert "because you have a Past Lives Makerspace account" in html
        assert "#092E4C" in html
        # The invite copy itself is still present inside the card.
        assert "Create your account" in html

    def it_records_the_send_in_the_audit_log():
        _invite("forced@example.com").send_invite_email()
        log = TransactionalEmailLog.objects.get(trigger_kind="member.invited")
        assert log.status == TransactionalEmailLog.Status.SENT
        assert log.to_email == "forced@example.com"

    def it_wraps_via_the_explicit_address_path_not_emailadapter():
        # The invitee has no account, so the event resolves to NO per-user recipient —
        # the email goes out through _explicit_email_fan_out (email_to), never touching
        # EmailAdapter. The wrap must still apply (the §4.3 regression guard).
        _invite("noaccount@example.com").send_invite_email()
        message = mail.outbox[0]
        assert message.to == ["noaccount@example.com"]
        assert "because you have a Past Lives Makerspace account" in message.alternatives[0][0]

    def it_leaves_the_plain_text_body_unwrapped():
        _invite().send_invite_email()
        text = mail.outbox[0].body
        assert "because you have a Past Lives Makerspace account" not in text
        assert "<html" not in text.lower()
        assert "#092E4C" not in text
        # The plain-text copy is intact.
        assert "create your account" in text.lower()


def describe_invite_gold_cta_on_a_seeded_row():
    def it_renders_the_gold_button_from_the_freshly_seeded_db_row():
        # Seed the catalogue: the member.invited email row is created from the current
        # copy.py fragment (the gold button). Asserting the button only on the unseeded
        # fallback would pass on a fresh test DB while a seeded prod still shipped a
        # plain link — so we assert on the SEEDED row.
        call_command("seed_notification_templates", "--quiet")
        assert NotificationTemplate.objects.filter(event_key="member.invited", channel="email").exists()

        _invite().send_invite_email()
        html = mail.outbox[0].alternatives[0][0]
        # The gold CTA button styling, pointing at the signup URL.
        assert "#EEB44B" in html
        assert "Create your account" in html
        assert "/accounts/signup/" in html
        # Still branded.
        assert "because you have a Past Lives Makerspace account" in html

    def it_keeps_a_plain_link_on_an_admin_overridden_row_but_still_brands_it():
        # An admin-overridden row keeps its hand-edited (old, plain-link) body after a
        # re-seed — the fragment edit doesn't reach overridden rows (accepted v1 limit).
        # It is STILL wrapped by the shell; only the button is absent.
        NotificationTemplate.objects.create(
            event_key="member.invited",
            channel="email",
            subject="You're invited",
            body_text="Join us: {{ signup_url }}",
            body_html='<p><a href="{{ signup_url }}">Create your account</a></p>',
            is_overridden=True,
        )
        call_command("seed_notification_templates", "--quiet")

        _invite().send_invite_email()
        html = mail.outbox[0].alternatives[0][0]
        # Branded by the shell (footer line)...
        assert "because you have a Past Lives Makerspace account" in html
        # ...and the admin's own body is preserved verbatim.
        assert "Create your account" in html
        # ...but no gold CTA *button* reached the overridden row — only the seeded copy
        # carries the button. (The bare link is made readable as gold text, and the
        # footer brand is gold text too, so we assert on the button background, not any
        # use of the accent color.)
        assert "background-color:#EEB44B" not in html
