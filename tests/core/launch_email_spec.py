"""BDD specs for the Member Portal launch email renderer and its two send paths."""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.core import mail
from django.db.models.signals import post_save
from django.test import override_settings
from django.utils import timezone
from django.utils.html import escape
from factory.django import mute_signals

from core.events.channels import Channel
from core.launch_email import (
    ANNOUNCEMENT_CTA,
    ANNOUNCEMENT_SUBJECT,
    INVITE_CTA,
    INVITE_SUBJECT,
    LAUNCH_PERIOD,
    ROLLOUT_SCHEDULE,
    portal_home_url,
    render_launch_announcement,
    render_launch_invite,
    send_launch_announcement,
    send_launch_invite,
    send_launch_previews,
)
from core.models import EventDelivery, Notification, TransactionalEmailLog
from membership.models import Member
from membership.services.provisioning import provision_user_for_member
from tests.membership.factories import MemberFactory

pytestmark = pytest.mark.django_db


def _activated(email: str) -> Member:
    """An active member whose account has signed in: the ``site_announcement`` audience."""
    member = MemberFactory(status=Member.Status.ACTIVE)
    with mute_signals(post_save):
        user = User.objects.create_user(username=f"u{member.pk}", email=email, last_login=timezone.now())
    member.user = user
    member.save(update_fields=["user"])
    return member


def _html_part(message) -> str:
    return next((content for content, mime in message.alternatives if mime == "text/html"), "")


def describe_render_launch_announcement():
    def it_renders_the_hero_the_rollout_table_and_the_sign_in_button():
        html, text = render_launch_announcement(cta_url="https://members.example/home/")

        assert "Now live" in html
        assert "The Member Portal is open" in html
        assert "What you can do today" in html
        for week in ROLLOUT_SCHEDULE:
            assert week.label in html
            assert week.starts in html
            for feature in week.features:
                assert escape(feature) in html  # "Meetings &amp; Spaces"
        assert "Equipment (or Space Rentals)" in html
        assert 'href="https://members.example/home/"' in html
        assert f">{ANNOUNCEMENT_CTA}<" in html
        assert "because you have a Past Lives Makerspace account" in html  # the shared footer

    def it_carries_no_greeting_and_no_sign_in_note_because_one_message_reaches_everyone():
        html, text = render_launch_announcement(cta_url="https://members.example/home/")

        assert "Hi " not in html
        assert "This link is for" not in html
        assert "This link is for" not in text
        assert not text.startswith("Hi ")

    def it_mirrors_the_html_in_the_text_part():
        html, text = render_launch_announcement(cta_url="https://members.example/home/")

        assert text.startswith(ANNOUNCEMENT_SUBJECT)
        assert "What you can do today" in text
        assert "Week 2, starting Mon Sep 28: Guild Pages; Orientations" in text
        assert f"{ANNOUNCEMENT_CTA}: https://members.example/home/" in text
        assert "Manage your email preferences or unsubscribe" in text  # the shared text footer

    def it_keeps_the_release_email_hero_byte_identical():
        """The hero partial gained two optional params; the release shell passes neither."""
        from django.template.loader import render_to_string

        hero = render_to_string("membership/emails/_hero.html", {"minor_label": "v0.21", "release_date": "July 1"})

        assert ">v0.21<" in hero
        assert "Heads-Up: New Member Portal Features" in hero
        assert "July 1" in hero


def describe_render_launch_invite():
    def it_greets_the_member_and_points_the_button_at_their_login_code_page():
        html, text = render_launch_invite(
            member_name="Robin Vale",
            login_url="https://members.example/accounts/login/code/?email=robin%40example.com",
            email="robin@example.com",
        )

        assert "Hi Robin Vale," in html
        assert 'href="https://members.example/accounts/login/code/?email=robin%40example.com"' in html
        assert f">{INVITE_CTA}<" in html
        assert "This link is for robin@example.com." in html
        assert "one-time login code" in html
        assert "Week 5" in html  # the same rollout table as the announcement

    def it_mirrors_the_html_in_the_text_part():
        html, text = render_launch_invite(
            member_name="Robin Vale",
            login_url="https://members.example/accounts/login/code/?email=robin%40example.com",
            email="robin@example.com",
        )

        assert text.startswith(INVITE_SUBJECT)
        assert "Hi Robin Vale," in text
        assert f"{INVITE_CTA}: https://members.example/accounts/login/code/?email=robin%40example.com" in text
        assert "This link is for robin@example.com." in text


def describe_portal_home_url():
    @override_settings(MEMBER_BASE_URL="https://members.example")
    def it_is_the_hub_home_on_the_member_host():
        assert portal_home_url() == "https://members.example/home/"


def describe_send_launch_announcement():
    def it_emails_every_activated_member_the_announcement_and_rings_their_bell():
        _activated("a@x.com")
        _activated("b@x.com")
        never = MemberFactory(_pre_signup_email="never@x.com")  # unlinked: not in the broadcast audience
        mail.outbox.clear()

        result = send_launch_announcement()

        assert sorted(m.to[0] for m in mail.outbox) == ["a@x.com", "b@x.com"]
        assert mail.outbox[0].subject == ANNOUNCEMENT_SUBJECT
        html = _html_part(mail.outbox[0])
        assert "Now live" in html
        assert "&lt;table" not in html  # the pre-rendered HTML rides the EMAIL override unescaped
        assert Notification.objects.count() == 2  # the in-app row for each activated member
        assert never.user_id is None
        assert Channel.DISCORD not in result.broadcast_channels  # off unless asked

    def it_is_idempotent_across_runs_through_the_launch_period():
        _activated("a@x.com")
        mail.outbox.clear()

        send_launch_announcement()
        send_launch_announcement()

        assert len(mail.outbox) == 1
        assert EventDelivery.objects.filter(event_key="site_announcement", period=LAUNCH_PERIOD).exists()


def describe_send_launch_invite():
    def it_provisions_the_member_and_emails_the_invite_variant_to_them():
        member = MemberFactory(_pre_signup_email="newbie@example.com", preferred_name="Newbie")
        assert member.user_id is None
        mail.outbox.clear()

        result = send_launch_invite(member)

        member.refresh_from_db()
        assert member.user_id is not None
        assert [m.to for m in mail.outbox] == [["newbie@example.com"]]
        assert mail.outbox[0].subject == INVITE_SUBJECT
        html = _html_part(mail.outbox[0])
        assert "Hi Newbie," in html
        assert "/accounts/login/code/?email=newbie%40example.com" in html
        assert "Week 2" in html
        assert (member.user.pk, Channel.EMAIL) in result.delivered

    def it_is_idempotent_across_runs_through_the_launch_period():
        member = MemberFactory(_pre_signup_email="newbie@example.com")
        mail.outbox.clear()

        send_launch_invite(member)
        second = send_launch_invite(member)

        assert len(mail.outbox) == 1
        assert second.delivered == []
        assert (member.user.pk, Channel.EMAIL) in second.skipped_duplicates

    def it_raises_when_the_member_has_no_email():
        member = MemberFactory(_pre_signup_email="")

        with pytest.raises(ValueError, match="no email on file"):
            send_launch_invite(member)

        assert mail.outbox == []


def describe_send_launch_previews():
    def it_sends_both_variants_to_one_inbox_outside_the_spine():
        mail.outbox.clear()

        send_launch_previews("outsider@example.com")

        assert [m.subject for m in mail.outbox] == [ANNOUNCEMENT_SUBJECT, INVITE_SUBJECT]
        assert all(m.to == ["outsider@example.com"] for m in mail.outbox)
        invite_html = _html_part(mail.outbox[1])
        assert "Hi there," in invite_html  # not a member: the neutral greeting
        assert "/accounts/login/code/?email=outsider%40example.com" in invite_html
        # No ledger slot claimed, so the real send still reaches this person.
        assert not EventDelivery.objects.filter(period=LAUNCH_PERIOD).exists()
        assert TransactionalEmailLog.objects.filter(trigger_kind="portal_launch.test").count() == 2

    def it_greets_a_member_by_name_when_the_address_is_theirs():
        member = MemberFactory(_pre_signup_email="felix@example.com", preferred_name="Felix")
        provision_user_for_member(member)
        mail.outbox.clear()

        send_launch_previews("felix@example.com")

        assert "Hi Felix," in _html_part(mail.outbox[1])

    def it_falls_back_to_the_neutral_greeting_for_an_account_with_no_member():
        with mute_signals(post_save):
            User.objects.create_user(username="loose", email="loose@example.com")
        mail.outbox.clear()

        send_launch_previews("loose@example.com")

        assert "Hi there," in _html_part(mail.outbox[1])

    def it_raises_when_the_provider_rejects_the_send(monkeypatch):
        from core import email as core_email

        def _boom(*args, **kwargs):
            raise RuntimeError("provider said no")

        monkeypatch.setattr(core_email, "_deliver", _boom)

        with pytest.raises(RuntimeError, match="provider said no"):
            send_launch_previews("outsider@example.com")

        log = TransactionalEmailLog.objects.get(trigger_kind="portal_launch.test")
        assert log.status == TransactionalEmailLog.Status.FAILED
