"""BDD specs for the per-guild join welcome email (send path, model copy, silent join paths)."""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.core import mail

from core.models import Notification, TransactionalEmailLog
from membership import orientations
from membership.guild_welcome_copy import STANDARD_WELCOME_BODY, standard_welcome_subject
from membership.models import GuildMembership, GuildOrientationSettings
from tests.membership.factories import (
    GuildFactory,
    GuildOrientationSettingsFactory,
    MembershipPlanFactory,
)

pytestmark = pytest.mark.django_db


def _member(username: str = "welcome_member") -> object:
    MembershipPlanFactory()
    return User.objects.create_user(username=username, email=f"{username}@example.com").member


def describe_GuildOrientationSettings_welcome_copy():
    def it_returns_the_lead_override_when_set():
        guild = GuildFactory(name="Metal Guild")
        settings_obj = GuildOrientationSettingsFactory(
            guild=guild,
            welcome_email_subject="Hey there!",
            welcome_email_body="Custom note.",
        )
        assert settings_obj.welcome_email_subject_resolved == "Hey there!"
        assert settings_obj.welcome_email_body_resolved == "Custom note."

    def it_falls_back_to_the_standard_copy_when_blank():
        guild = GuildFactory(name="Fiber Guild")
        settings_obj = GuildOrientationSettingsFactory(guild=guild)
        assert settings_obj.welcome_email_subject_resolved == standard_welcome_subject("Fiber Guild")
        assert settings_obj.welcome_email_body_resolved == STANDARD_WELCOME_BODY

    def describe_welcome_email_ready():
        def it_tracks_the_enabled_flag():
            on = GuildOrientationSettingsFactory(guild=GuildFactory(), welcome_email_enabled=True)
            off = GuildOrientationSettingsFactory(guild=GuildFactory(), welcome_email_enabled=False)
            assert on.welcome_email_ready is True
            assert off.welcome_email_ready is False


def describe_send_guild_welcome():
    def it_sends_one_welcome_with_the_resolved_copy_and_banner_context():
        guild = GuildFactory(name="Wood Guild")
        GuildOrientationSettingsFactory(
            guild=guild,
            welcome_email_subject="Welcome, friend",
            welcome_email_body="Glad you joined.",
        )
        member = _member()

        orientations.send_guild_welcome(guild, member)

        assert len(mail.outbox) == 1
        sent = mail.outbox[0]
        assert sent.to == [member.primary_email]
        assert sent.subject == "Welcome, friend"
        assert "Glad you joined." in sent.body
        log = TransactionalEmailLog.objects.get(trigger_kind="guild_welcome")
        assert log.to_email == member.primary_email

    def it_includes_the_guild_banner_url_when_the_guild_has_a_banner():
        from django.core.files.uploadedfile import SimpleUploadedFile

        # 1x1 PNG so guild.banner_image is truthy and the banner branch of the context runs.
        png = bytes.fromhex(
            "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
            "890000000a49444154789c6300010000050001a5f645400000000049454e44ae426082"
        )
        guild = GuildFactory(
            name="Banner Guild",
            banner_image=SimpleUploadedFile("b.png", png, content_type="image/png"),
        )
        ctx = orientations._guild_welcome_context(guild, "Sam", "hi")
        assert ctx["banner_url"]
        assert guild.banner_image.url.split("?")[0] in ctx["banner_url"]

    def it_leaves_the_banner_url_blank_without_a_banner():
        guild = GuildFactory(name="Plain Guild")
        ctx = orientations._guild_welcome_context(guild, "Sam", "hi")
        assert ctx["banner_url"] == ""

    def it_sends_the_standard_welcome_when_no_custom_copy():
        guild = GuildFactory(name="Print Guild")
        GuildOrientationSettingsFactory(guild=guild)
        member = _member()

        orientations.send_guild_welcome(guild, member)

        assert mail.outbox[0].subject == standard_welcome_subject("Print Guild")
        assert STANDARD_WELCOME_BODY in mail.outbox[0].body

    def it_sends_nothing_when_the_guild_has_no_settings_row():
        guild = GuildFactory()
        member = _member()

        orientations.send_guild_welcome(guild, member)

        assert mail.outbox == []

    def it_sends_nothing_when_the_welcome_email_is_disabled():
        guild = GuildFactory()
        GuildOrientationSettingsFactory(guild=guild, welcome_email_enabled=False)
        member = _member()

        orientations.send_guild_welcome(guild, member)

        assert mail.outbox == []

    def it_is_idempotent_per_member_and_guild_forever():
        guild = GuildFactory()
        GuildOrientationSettingsFactory(guild=guild)
        member = _member()

        orientations.send_guild_welcome(guild, member)
        orientations.send_guild_welcome(guild, member)  # a leave-then-rejoin re-send

        assert len(mail.outbox) == 1

    def it_is_reachable_from_the_member_helper():
        guild = GuildFactory()
        GuildOrientationSettingsFactory(guild=guild)
        member = _member()

        member.send_guild_welcome(guild)

        assert len(mail.outbox) == 1


def describe_send_guild_welcome_test():
    def it_sends_even_when_disabled_so_a_lead_can_proof_a_draft():
        guild = GuildFactory()
        GuildOrientationSettingsFactory(guild=guild, welcome_email_enabled=False)
        lead = _member("proof_lead")

        orientations.send_guild_welcome_test(guild, lead)

        assert len(mail.outbox) == 1
        assert mail.outbox[0].to == [lead.primary_email]

    def it_creates_the_settings_row_when_missing():
        guild = GuildFactory()
        lead = _member("proof_lead2")

        orientations.send_guild_welcome_test(guild, lead)

        assert GuildOrientationSettings.objects.filter(guild=guild).exists()
        assert len(mail.outbox) == 1

    def it_can_send_repeated_proofs():
        guild = GuildFactory()
        GuildOrientationSettingsFactory(guild=guild)
        lead = _member("proof_lead3")

        orientations.send_guild_welcome_test(guild, lead)
        orientations.send_guild_welcome_test(guild, lead)

        assert len(mail.outbox) == 2


def describe_silent_join_paths():
    def it_does_not_send_a_welcome_from_subscribe_to_guild():
        # The Settings toggle and first-login picker both funnel through subscribe_to_guild;
        # neither should email (only the lead "New follower" notice fires).
        guild = GuildFactory()
        GuildOrientationSettingsFactory(guild=guild)
        member = _member()

        member.subscribe_to_guild(guild)

        assert mail.outbox == []
        assert GuildMembership.objects.filter(guild=guild, member=member).exists()

    def it_still_fires_the_lead_new_follower_notice_on_subscribe():
        lead = _member("notice_lead")
        guild = GuildFactory(guild_lead=lead)
        GuildOrientationSettingsFactory(guild=guild)
        member = _member("notice_member")

        member.subscribe_to_guild(guild)

        assert Notification.objects.filter(user=lead.user, trigger="guild_joined").exists()

    def it_does_not_send_a_welcome_from_the_first_login_picker():
        guild_a = GuildFactory()
        guild_b = GuildFactory()
        GuildOrientationSettingsFactory(guild=guild_a)
        GuildOrientationSettingsFactory(guild=guild_b)
        member = _member()

        member.answer_guild_updates_prompt([guild_a, guild_b])

        assert mail.outbox == []
