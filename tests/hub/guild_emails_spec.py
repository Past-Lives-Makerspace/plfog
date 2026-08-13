"""BDD specs for the guild follow-up emails editor (Announcements/Emails tab).

The thank-you + welcome emails moved off the Orientations tab onto the new
Announcements/Emails tab. The data still lives on ``GuildOrientationSettings``; only
the editing UI relocated — ``GuildEmailsForm`` and the ``guild_emails_save`` view.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from hub.forms import GuildEmailsForm
from membership.models import GuildOrientationSettings, Member
from tests.membership.factories import (
    GuildFactory,
    GuildOrientationSettingsFactory,
    MembershipPlanFactory,
)

pytestmark = pytest.mark.django_db


def _user_with_role(username: str, *, fog_role: str = Member.FogRole.MEMBER) -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, password="pass")
    member = user.member
    member.fog_role = fog_role
    member.save(update_fields=["fog_role"])
    member.sync_user_permissions()
    return user


def _form_data_from(instance: GuildOrientationSettings, **overrides: object) -> dict[str, str]:
    """Build a complete bound payload mirroring ``instance``, with ``overrides`` applied.

    Mirroring every field means ``changed_data`` contains only the overridden fields —
    exactly what the timestamp gating keys off.
    """
    data: dict[str, str] = {}
    for name in GuildEmailsForm.Meta.fields:
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


def describe_GuildEmailsForm_email_timestamps():
    def it_stamps_thankyou_only_when_a_thankyou_field_changes():
        settings_obj = GuildOrientationSettingsFactory()
        form = GuildEmailsForm(
            data=_form_data_from(
                settings_obj,
                thankyou_email_enabled="on",
                thankyou_email_subject="Thanks!",
                thankyou_email_body="Next steps.",
            ),
            instance=settings_obj,
        )
        assert form.is_valid(), form.errors
        saved = form.save()

        assert saved.thankyou_email_updated_at is not None
        assert saved.join_email_updated_at is None

    def it_stamps_join_only_when_a_join_field_changes():
        settings_obj = GuildOrientationSettingsFactory()
        form = GuildEmailsForm(
            data=_form_data_from(
                settings_obj,
                join_email_enabled="on",
                join_email_subject="Welcome!",
                join_email_body="Glad you joined.",
            ),
            instance=settings_obj,
        )
        assert form.is_valid(), form.errors
        saved = form.save()

        assert saved.join_email_updated_at is not None
        assert saved.thankyou_email_updated_at is None

    def it_stamps_neither_when_nothing_changes():
        settings_obj = GuildOrientationSettingsFactory()
        form = GuildEmailsForm(data=_form_data_from(settings_obj), instance=settings_obj)
        assert form.is_valid(), form.errors
        saved = form.save()

        assert saved.thankyou_email_updated_at is None
        assert saved.join_email_updated_at is None

    def it_does_not_disturb_an_existing_join_timestamp_on_an_unrelated_save():
        original = timezone.now() - timedelta(days=3)
        settings_obj = GuildOrientationSettingsFactory(join_email_updated_at=original)
        form = GuildEmailsForm(
            data=_form_data_from(
                settings_obj,
                thankyou_email_enabled="on",
                thankyou_email_subject="Thanks!",
                thankyou_email_body="Next steps.",
            ),
            instance=settings_obj,
        )
        assert form.is_valid(), form.errors
        saved = form.save()

        assert saved.join_email_updated_at == original


def describe_GuildEmailsForm_validation():
    def it_allows_enabling_the_thankyou_email_with_no_subject_or_body():
        # Blank thank-you subject/body is now valid — it just falls back to the
        # standard copy (membership.orientation_copy) instead of raising.
        settings_obj = GuildOrientationSettingsFactory()
        form = GuildEmailsForm(
            data=_form_data_from(
                settings_obj, thankyou_email_enabled="on", thankyou_email_subject="", thankyou_email_body=""
            ),
            instance=settings_obj,
        )
        assert form.is_valid(), form.errors
        saved = form.save()
        assert saved.thankyou_email_enabled is True
        assert saved.thankyou_email_subject == ""
        assert saved.thankyou_email_body == ""

    def it_rejects_a_welcome_email_with_no_subject():
        settings_obj = GuildOrientationSettingsFactory()
        form = GuildEmailsForm(
            data=_form_data_from(settings_obj, join_email_enabled="on", join_email_body="Glad you joined."),
            instance=settings_obj,
        )
        assert not form.is_valid()
        assert "join_email_subject" in form.errors

    def it_rejects_a_welcome_email_with_no_body():
        settings_obj = GuildOrientationSettingsFactory()
        form = GuildEmailsForm(
            data=_form_data_from(settings_obj, join_email_enabled="on", join_email_subject="Welcome!"),
            instance=settings_obj,
        )
        assert not form.is_valid()
        assert "join_email_body" in form.errors

    def it_treats_an_empty_quill_doc_as_a_missing_body_when_enabling_the_welcome_email():
        # The welcome/join email still has no standard-copy fallback, so it still
        # requires a real subject and body — a Quill doc with only an empty paragraph
        # must still count as "no body".
        settings_obj = GuildOrientationSettingsFactory()
        form = GuildEmailsForm(
            data=_form_data_from(
                settings_obj,
                join_email_enabled="on",
                join_email_subject="Welcome",
                join_email_body="<p><br></p>",
            ),
            instance=settings_obj,
        )
        assert not form.is_valid()
        assert "join_email_body" in form.errors


def describe_GuildEmailsForm_sanitization():
    def it_sanitizes_both_email_bodies_and_strips_script():
        settings_obj = GuildOrientationSettingsFactory()
        form = GuildEmailsForm(
            data=_form_data_from(
                settings_obj,
                thankyou_email_body="<p>Thanks!</p><script>a()</script>",
                join_email_body="<p>Welcome!</p><script>b()</script>",
            ),
            instance=settings_obj,
        )
        assert form.is_valid(), form.errors
        assert "<script" not in form.cleaned_data["thankyou_email_body"]
        assert "Thanks!" in form.cleaned_data["thankyou_email_body"]
        assert "<script" not in form.cleaned_data["join_email_body"]
        assert "Welcome!" in form.cleaned_data["join_email_body"]


def describe_guild_emails_save():
    def it_saves_the_emails_and_redirects_to_the_announcements_tab(client: Client):
        _user_with_role("em_save", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        client.login(username="em_save", password="pass")
        response = client.post(
            reverse("hub_guild_emails_save", args=[guild.pk]),
            {
                "thankyou_email_enabled": "on",
                "thankyou_email_subject": "Thanks for coming",
                "thankyou_email_body": "Next steps inside.",
                "join_email_subject": "",
                "join_email_body": "",
            },
        )
        assert response.status_code == 302
        assert response["Location"] == f"{reverse('hub_guild_edit', args=[guild.pk])}?tab=announcements"
        settings_obj = GuildOrientationSettings.objects.get(guild=guild)
        assert settings_obj.thankyou_email_enabled is True
        assert settings_obj.thankyou_email_subject == "Thanks for coming"
        assert settings_obj.thankyou_email_updated_at is not None

    def it_allows_saving_a_thankyou_email_with_no_subject_or_body(client: Client):
        # Blank thank-you subject/body is valid now — it falls back to the standard
        # copy, so enabling it no longer requires the guild to write their own.
        _user_with_role("em_nosub", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        client.login(username="em_nosub", password="pass")
        response = client.post(
            reverse("hub_guild_emails_save", args=[guild.pk]),
            {"thankyou_email_enabled": "on"},
        )
        assert response.status_code == 302
        settings_obj = GuildOrientationSettings.objects.get(guild=guild)
        assert settings_obj.thankyou_email_enabled is True
        assert settings_obj.thankyou_email_subject == ""

    def it_rejects_a_welcome_email_with_no_subject(client: Client):
        _user_with_role("em_join_nosub", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        client.login(username="em_join_nosub", password="pass")
        response = client.post(
            reverse("hub_guild_emails_save", args=[guild.pk]),
            {"join_email_enabled": "on", "join_email_body": "Glad you joined."},
        )
        assert response.status_code == 200
        assert "join_email_subject" in response.context["emails_form"].errors
        assert GuildOrientationSettings.objects.get(guild=guild).join_email_enabled is False

    def it_rejects_a_welcome_email_with_no_body(client: Client):
        _user_with_role("em_nobody", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        client.login(username="em_nobody", password="pass")
        response = client.post(
            reverse("hub_guild_emails_save", args=[guild.pk]),
            {"join_email_enabled": "on", "join_email_subject": "Welcome!"},
        )
        assert response.status_code == 200
        assert "join_email_body" in response.context["emails_form"].errors

    def it_redirects_a_get_to_the_announcements_tab(client: Client):
        _user_with_role("em_get", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        client.login(username="em_get", password="pass")
        response = client.get(reverse("hub_guild_emails_save", args=[guild.pk]))
        assert response.status_code == 302
        assert response["Location"] == f"{reverse('hub_guild_edit', args=[guild.pk])}?tab=announcements"

    def it_lets_the_guild_lead_save(client: Client):
        user = _user_with_role("em_lead", fog_role=Member.FogRole.MEMBER)
        guild = GuildFactory(guild_lead=user.member)
        client.login(username="em_lead", password="pass")
        response = client.post(
            reverse("hub_guild_emails_save", args=[guild.pk]),
            {"join_email_enabled": "on", "join_email_subject": "Welcome!", "join_email_body": "Glad you're here."},
        )
        assert response.status_code == 302
        assert GuildOrientationSettings.objects.get(guild=guild).join_email_enabled is True

    def it_forbids_a_regular_member(client: Client):
        _user_with_role("em_reg", fog_role=Member.FogRole.MEMBER)
        guild = GuildFactory()
        client.login(username="em_reg", password="pass")
        response = client.post(reverse("hub_guild_emails_save", args=[guild.pk]), {})
        assert response.status_code == 403

    def it_requires_login(client: Client):
        guild = GuildFactory()
        response = client.get(reverse("hub_guild_emails_save", args=[guild.pk]))
        assert response.status_code == 302
        assert "/accounts/login/" in response["Location"]
