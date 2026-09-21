"""BDD specs for the ``send_portal_launch_email`` management command.

The launch-day send: the announcement to every member who has signed in before, the
activation invite to every active member who never has. Guarded behind ``--confirm``;
``--test`` previews both variants to one inbox. Tests use the locmem outbox and factory
members, never a real send.
"""

from __future__ import annotations

import io

import pytest
from django.contrib.auth.models import User
from django.core import mail
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db.models.signals import post_save
from django.utils import timezone
from factory.django import mute_signals

from core.launch_email import ANNOUNCEMENT_SUBJECT, INVITE_SUBJECT
from membership.models import Member
from membership.services.provisioning import provision_user_for_member
from tests.membership.factories import MemberFactory

pytestmark = pytest.mark.django_db


def _activated(email: str) -> Member:
    """An active member whose account has signed in: gets the announcement."""
    member = MemberFactory(status=Member.Status.ACTIVE)
    with mute_signals(post_save):
        user = User.objects.create_user(username=f"u{member.pk}", email=email, last_login=timezone.now())
    member.user = user
    member.save(update_fields=["user"])
    return member


def _run(**options) -> tuple[str, str]:
    out, err = io.StringIO(), io.StringIO()
    call_command("send_portal_launch_email", stdout=out, stderr=err, **options)
    return out.getvalue(), err.getvalue()


def describe_send_portal_launch_email():
    def describe_without_confirm():
        def it_sends_nothing_and_prints_both_audience_counts():
            _activated("a@x.com")
            _activated("b@x.com")
            MemberFactory(_pre_signup_email="never@x.com")
            MemberFactory(_pre_signup_email="former@x.com", status=Member.Status.FORMER)
            mail.outbox.clear()

            out, _err = _run()

            assert mail.outbox == []
            assert "DRY RUN" in out
            assert "Announcement (signed in before):     2 member(s)" in out
            assert "Activation invite (never signed in): 1 member(s)" in out
            assert "Discord: off" in out

    def describe_with_confirm():
        def it_sends_each_member_the_variant_for_their_door():
            _activated("a@x.com")
            never = MemberFactory(_pre_signup_email="never@x.com", preferred_name="Never")
            mail.outbox.clear()

            out, err = _run(confirm=True)

            by_recipient = {m.to[0]: m for m in mail.outbox}
            assert set(by_recipient) == {"a@x.com", "never@x.com"}
            assert by_recipient["a@x.com"].subject == ANNOUNCEMENT_SUBJECT
            assert by_recipient["never@x.com"].subject == INVITE_SUBJECT
            assert "Hi Never," in by_recipient["never@x.com"].alternatives[0][0]
            assert "Announcement: emailed 1 of 1 signed-in member(s); 0 already sent. Discord: off." in out
            assert "Activation invites: sent 1; 0 already sent; 0 not delivered; skipped 0." in out
            assert err == ""
            never.refresh_from_db()
            assert never.user_id is not None  # provisioned on the way
            assert never.welcome_email_sent_at is not None  # the welcome automation will not repeat it

        def it_emails_nobody_twice_on_a_re_run():
            _activated("a@x.com")
            MemberFactory(_pre_signup_email="never@x.com")
            mail.outbox.clear()

            _run(confirm=True)
            out, _err = _run(confirm=True)

            assert len(mail.outbox) == 2
            assert "Announcement: emailed 0 of 1 signed-in member(s); 1 already sent." in out
            assert "Activation invites: sent 0; 1 already sent; 0 not delivered; skipped 0." in out

        def it_skips_a_member_who_cannot_be_provisioned_without_aborting_the_batch():
            owner = MemberFactory(_pre_signup_email="taken@example.com")
            provision_user_for_member(owner)  # a User now owns taken@example.com; owner has never signed in
            MemberFactory(_pre_signup_email="taken@example.com")  # a second member claiming the same address
            healthy = MemberFactory(_pre_signup_email="ok@example.com")
            mail.outbox.clear()

            out, err = _run(confirm=True)

            assert sorted(m.to[0] for m in mail.outbox) == ["ok@example.com", "taken@example.com"]
            assert "✗ invite" in err
            assert "Activation invites: sent 2; 0 already sent; 0 not delivered; skipped 1." in out
            healthy.refresh_from_db()
            assert healthy.welcome_email_sent_at is not None

        def it_reports_an_invite_the_provider_rejected_and_leaves_it_for_a_retry(monkeypatch):
            from core import email as core_email

            member = MemberFactory(_pre_signup_email="undelivered@example.com")
            calls: list[str] = []

            def _boom(*args, **kwargs):
                calls.append(kwargs["recipients"][0])
                raise RuntimeError("provider said no")

            monkeypatch.setattr(core_email, "_deliver", _boom)

            out, err = _run(confirm=True)

            assert calls == ["undelivered@example.com"]
            assert "not delivered, re-run to retry" in err
            assert "Activation invites: sent 0; 0 already sent; 1 not delivered; skipped 0." in out
            member.refresh_from_db()
            assert member.welcome_email_sent_at is None  # not stamped: nothing landed

        def it_posts_to_discord_only_when_asked(monkeypatch):
            posted: list[str] = []
            from core.events import discord as discord_module

            monkeypatch.setattr(discord_module, "post_embed", lambda *a, **k: posted.append("posted") or True)
            _activated("a@x.com")
            mail.outbox.clear()

            out, _err = _run(confirm=True, discord=True)

            assert "Discord: on." in out

    def describe_test_mode():
        def it_sends_both_variants_to_one_address_and_nothing_else():
            _activated("a@x.com")
            MemberFactory(_pre_signup_email="never@x.com")
            mail.outbox.clear()

            out, _err = _run(test="reviewer@example.com")

            assert [m.subject for m in mail.outbox] == [ANNOUNCEMENT_SUBJECT, INVITE_SUBJECT]
            assert all(m.to == ["reviewer@example.com"] for m in mail.outbox)
            assert "Both launch email variants sent to reviewer@example.com." in out

        def it_rejects_a_value_that_is_not_an_address():
            with pytest.raises(CommandError, match="--test must be an email address"):
                _run(test="not-an-address")
