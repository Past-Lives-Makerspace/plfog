"""BDD specs for the guild welcome-email editor (Welcome Email tab).

The welcome email is the join-lifecycle email; its editor lives on its own Welcome Email
tab of the guild editor as a card + form (hidden ``form_id="welcome_email"``), posting to
the shared ``guild_emails_save`` view. Save, Preview, and Send-test are all covered here.
"""

from __future__ import annotations

import json

import pytest
from django.contrib.auth.models import User
from django.core import mail
from django.test import Client
from django.urls import reverse

from hub.forms import GuildWelcomeEmailForm
from membership.models import GuildOrientationSettings, Member
from tests.membership.factories import (
    GuildFactory,
    GuildOrientationSettingsFactory,
    MembershipPlanFactory,
)

pytestmark = pytest.mark.django_db


def _user_with_role(username: str, *, fog_role: str = Member.FogRole.MEMBER) -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, password="pass", email=f"{username}@example.com")
    member = user.member
    member.fog_role = fog_role
    member.save(update_fields=["fog_role"])
    member.sync_user_permissions()
    return user


def _form_data_from(instance: GuildOrientationSettings, **overrides: object) -> dict[str, str]:
    data: dict[str, str] = {}
    for name in GuildWelcomeEmailForm.Meta.fields:
        value = getattr(instance, name)
        if isinstance(value, bool):
            if value:
                data[name] = "on"
        else:
            data[name] = "" if value is None else str(value)
    for name, value in overrides.items():
        if value is None:
            data.pop(name, None)
        else:
            data[name] = str(value)
    return data


def describe_GuildWelcomeEmailForm():
    def it_stamps_updated_at_when_a_welcome_field_changes():
        settings_obj = GuildOrientationSettingsFactory()
        form = GuildWelcomeEmailForm(
            data=_form_data_from(settings_obj, welcome_email_subject="Hey!", welcome_email_body="Come in."),
            instance=settings_obj,
        )
        assert form.is_valid(), form.errors
        saved = form.save()
        assert saved.welcome_email_updated_at is not None

    def it_stamps_nothing_when_nothing_changes():
        settings_obj = GuildOrientationSettingsFactory()
        form = GuildWelcomeEmailForm(data=_form_data_from(settings_obj), instance=settings_obj)
        assert form.is_valid(), form.errors
        saved = form.save()
        assert saved.welcome_email_updated_at is None

    def it_allows_enabling_with_no_subject_or_body():
        settings_obj = GuildOrientationSettingsFactory()
        form = GuildWelcomeEmailForm(
            data=_form_data_from(
                settings_obj, welcome_email_enabled="on", welcome_email_subject="", welcome_email_body=""
            ),
            instance=settings_obj,
        )
        assert form.is_valid(), form.errors
        saved = form.save()
        assert saved.welcome_email_enabled is True

    def it_sanitizes_the_body_and_strips_script():
        settings_obj = GuildOrientationSettingsFactory()
        form = GuildWelcomeEmailForm(
            data=_form_data_from(settings_obj, welcome_email_body="<p>Hi!</p><script>a()</script>"),
            instance=settings_obj,
        )
        assert form.is_valid(), form.errors
        assert "<script" not in form.cleaned_data["welcome_email_body"]
        assert "Hi!" in form.cleaned_data["welcome_email_body"]

    def it_rejects_an_over_length_subject():
        settings_obj = GuildOrientationSettingsFactory()
        form = GuildWelcomeEmailForm(
            data=_form_data_from(settings_obj, welcome_email_subject="x" * 201),
            instance=settings_obj,
        )
        assert not form.is_valid()
        assert "welcome_email_subject" in form.errors


def describe_guild_emails_save_welcome_branch():
    def it_saves_the_welcome_email_and_redirects_to_the_welcome_tab(client: Client):
        _user_with_role("we_save", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        client.login(username="we_save", password="pass")
        response = client.post(
            reverse("hub_guild_emails_save", args=[guild.pk]),
            {
                "form_id": "welcome_email",
                "welcome_email_enabled": "on",
                "welcome_email_subject": "Welcome aboard",
                "welcome_email_body": "Glad you joined.",
            },
        )
        assert response.status_code == 302
        assert response["Location"] == f"{reverse('hub_guild_edit', args=[guild.pk])}?tab=welcome_email"
        settings_obj = GuildOrientationSettings.objects.get(guild=guild)
        assert settings_obj.welcome_email_subject == "Welcome aboard"
        assert settings_obj.welcome_email_updated_at is not None

    def it_re_renders_on_the_welcome_tab_when_invalid(client: Client):
        _user_with_role("we_invalid", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        client.login(username="we_invalid", password="pass")
        response = client.post(
            reverse("hub_guild_emails_save", args=[guild.pk]),
            {"form_id": "welcome_email", "welcome_email_subject": "x" * 201},
        )
        assert response.status_code == 200
        assert response.context["active_tab"] == "welcome_email"
        assert response.context["welcome_email_form"].errors

    def it_lets_the_guild_lead_save(client: Client):
        user = _user_with_role("we_lead", fog_role=Member.FogRole.MEMBER)
        guild = GuildFactory(guild_lead=user.member)
        client.login(username="we_lead", password="pass")
        response = client.post(
            reverse("hub_guild_emails_save", args=[guild.pk]),
            {"form_id": "welcome_email", "welcome_email_enabled": "on"},
        )
        assert response.status_code == 302
        assert GuildOrientationSettings.objects.get(guild=guild).welcome_email_enabled is True

    def it_forbids_a_regular_member(client: Client):
        _user_with_role("we_reg", fog_role=Member.FogRole.MEMBER)
        guild = GuildFactory()
        client.login(username="we_reg", password="pass")
        response = client.post(reverse("hub_guild_emails_save", args=[guild.pk]), {"form_id": "welcome_email"})
        assert response.status_code == 403

    def it_renders_the_welcome_tab_on_the_editor(client: Client):
        _user_with_role("we_tab", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        client.login(username="we_tab", password="pass")
        response = client.get(reverse("hub_guild_edit", args=[guild.pk]))
        assert b"Welcome Packet" in response.content
        assert b'value="welcome_email"' in response.content


def describe_guild_welcome_test():
    def it_sends_a_test_to_the_editing_lead(client: Client):
        user = _user_with_role("wt_lead", fog_role=Member.FogRole.MEMBER)
        guild = GuildFactory(guild_lead=user.member)
        GuildOrientationSettingsFactory(guild=guild)
        client.login(username="wt_lead", password="pass")
        response = client.post(reverse("hub_guild_welcome_test", args=[guild.pk]))
        assert response.status_code == 204
        assert len(mail.outbox) == 1
        assert mail.outbox[0].to == [user.member.primary_email]
        assert "Test sent" in json.loads(response["HX-Trigger"])["showToast"]["message"]

    def it_forbids_a_regular_member(client: Client):
        _user_with_role("wt_reg", fog_role=Member.FogRole.MEMBER)
        guild = GuildFactory()
        client.login(username="wt_reg", password="pass")
        response = client.post(reverse("hub_guild_welcome_test", args=[guild.pk]))
        assert response.status_code == 403
        assert mail.outbox == []

    def it_reports_an_unlinked_editor_without_sending(client: Client):
        # An admin (passes the edit gate via is_staff) whose account has no linked Member:
        # the test-send bails with an error toast, sends nothing.
        user = _user_with_role("wt_unlinked", fog_role=Member.FogRole.ADMIN)
        Member.objects.filter(user=user).delete()
        guild = GuildFactory()
        GuildOrientationSettingsFactory(guild=guild)
        client.login(username="wt_unlinked", password="pass")
        response = client.post(reverse("hub_guild_welcome_test", args=[guild.pk]))
        assert response.status_code == 204
        assert mail.outbox == []
        assert "not linked" in json.loads(response["HX-Trigger"])["showToast"]["message"]


def describe_guild_welcome_preview():
    def it_renders_the_preview_without_sending(client: Client):
        _user_with_role("wp_lead", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory(name="Preview Guild")
        client.login(username="wp_lead", password="pass")
        response = client.get(reverse("hub_guild_welcome_preview", args=[guild.pk]))
        assert response.status_code == 200
        assert b"Preview Guild" in response.content
        assert mail.outbox == []

    def it_forbids_a_regular_member(client: Client):
        _user_with_role("wp_reg", fog_role=Member.FogRole.MEMBER)
        guild = GuildFactory()
        client.login(username="wp_reg", password="pass")
        response = client.get(reverse("hub_guild_welcome_preview", args=[guild.pk]))
        assert response.status_code == 403
