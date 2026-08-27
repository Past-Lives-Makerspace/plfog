"""BDD specs for the guild thank-you email editor (Orientations tab).

The thank-you email is the orientation-lifecycle email; its editor lives on the
Orientations tab of the guild editor as its own card + form (hidden
``form_id="thankyou_email"``), posting to the ``guild_emails_save`` view. The data lives
on ``GuildOrientationSettings``. The welcome email was deleted entirely, so there is only
one email form now.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from hub.forms import GuildThankyouEmailForm
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
    for name in GuildThankyouEmailForm.Meta.fields:
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


def describe_GuildThankyouEmailForm():
    def it_stamps_updated_at_when_a_thankyou_field_changes():
        settings_obj = GuildOrientationSettingsFactory()
        form = GuildThankyouEmailForm(
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

    def it_stamps_nothing_when_nothing_changes():
        settings_obj = GuildOrientationSettingsFactory()
        form = GuildThankyouEmailForm(data=_form_data_from(settings_obj), instance=settings_obj)
        assert form.is_valid(), form.errors
        saved = form.save()
        assert saved.thankyou_email_updated_at is None

    def it_allows_enabling_with_no_subject_or_body():
        # Blank thank-you subject/body is valid — it falls back to the standard copy
        # (membership.orientation_copy) instead of raising.
        settings_obj = GuildOrientationSettingsFactory()
        form = GuildThankyouEmailForm(
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

    def it_sanitizes_the_body_and_strips_script():
        settings_obj = GuildOrientationSettingsFactory()
        form = GuildThankyouEmailForm(
            data=_form_data_from(settings_obj, thankyou_email_body="<p>Thanks!</p><script>a()</script>"),
            instance=settings_obj,
        )
        assert form.is_valid(), form.errors
        assert "<script" not in form.cleaned_data["thankyou_email_body"]
        assert "Thanks!" in form.cleaned_data["thankyou_email_body"]

    def it_rejects_an_over_length_subject():
        # The realistic invalid path (no enable-requires-subject rule survives): a subject
        # longer than the model's 200-char cap fails the ModelForm's max-length validator.
        settings_obj = GuildOrientationSettingsFactory()
        form = GuildThankyouEmailForm(
            data=_form_data_from(settings_obj, thankyou_email_subject="x" * 201),
            instance=settings_obj,
        )
        assert not form.is_valid()
        assert "thankyou_email_subject" in form.errors


def describe_guild_emails_save():
    def it_saves_the_thankyou_email_and_redirects_to_the_orientations_tab(client: Client):
        _user_with_role("em_save", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        client.login(username="em_save", password="pass")
        response = client.post(
            reverse("hub_guild_emails_save", args=[guild.pk]),
            {
                "form_id": "thankyou_email",
                "thankyou_email_enabled": "on",
                "thankyou_email_subject": "Thanks for coming",
                "thankyou_email_body": "Next steps inside.",
            },
        )
        assert response.status_code == 302
        assert response["Location"] == f"{reverse('hub_guild_edit', args=[guild.pk])}?tab=orientations"
        settings_obj = GuildOrientationSettings.objects.get(guild=guild)
        assert settings_obj.thankyou_email_enabled is True
        assert settings_obj.thankyou_email_subject == "Thanks for coming"
        assert settings_obj.thankyou_email_updated_at is not None

    def it_allows_a_thankyou_email_with_no_subject_or_body(client: Client):
        _user_with_role("em_nosub", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        client.login(username="em_nosub", password="pass")
        response = client.post(
            reverse("hub_guild_emails_save", args=[guild.pk]),
            {"form_id": "thankyou_email", "thankyou_email_enabled": "on"},
        )
        assert response.status_code == 302
        settings_obj = GuildOrientationSettings.objects.get(guild=guild)
        assert settings_obj.thankyou_email_enabled is True
        assert settings_obj.thankyou_email_subject == ""

    def it_re_renders_on_the_orientations_tab_when_invalid(client: Client):
        # An invalid POST must land the lead back on the Orientations tab (where the card
        # lives), not on Basic Information with the errors hidden on another tab.
        _user_with_role("em_invalid", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        client.login(username="em_invalid", password="pass")
        response = client.post(
            reverse("hub_guild_emails_save", args=[guild.pk]),
            {"form_id": "thankyou_email", "thankyou_email_subject": "x" * 201},
        )
        assert response.status_code == 200
        assert response.context["active_tab"] == "orientations"
        assert response.context["thankyou_email_form"].errors

    def it_404s_on_a_post_with_an_unknown_form_id(client: Client):
        _user_with_role("em_bogus", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        client.login(username="em_bogus", password="pass")
        response = client.post(reverse("hub_guild_emails_save", args=[guild.pk]), {"form_id": "bogus"})
        assert response.status_code == 404

    def it_404s_on_a_post_with_no_form_id(client: Client):
        _user_with_role("em_noform", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        client.login(username="em_noform", password="pass")
        response = client.post(reverse("hub_guild_emails_save", args=[guild.pk]), {})
        assert response.status_code == 404

    def it_redirects_a_get_to_the_orientations_tab(client: Client):
        _user_with_role("em_get", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        client.login(username="em_get", password="pass")
        response = client.get(reverse("hub_guild_emails_save", args=[guild.pk]))
        assert response.status_code == 302
        assert response["Location"] == f"{reverse('hub_guild_edit', args=[guild.pk])}?tab=orientations"

    def it_lets_the_guild_lead_save(client: Client):
        user = _user_with_role("em_lead", fog_role=Member.FogRole.MEMBER)
        guild = GuildFactory(guild_lead=user.member)
        client.login(username="em_lead", password="pass")
        response = client.post(
            reverse("hub_guild_emails_save", args=[guild.pk]),
            {"form_id": "thankyou_email", "thankyou_email_enabled": "on", "thankyou_email_subject": "Thanks!"},
        )
        assert response.status_code == 302
        assert GuildOrientationSettings.objects.get(guild=guild).thankyou_email_enabled is True

    def it_forbids_a_regular_member(client: Client):
        _user_with_role("em_reg", fog_role=Member.FogRole.MEMBER)
        guild = GuildFactory()
        client.login(username="em_reg", password="pass")
        response = client.post(reverse("hub_guild_emails_save", args=[guild.pk]), {"form_id": "thankyou_email"})
        assert response.status_code == 403

    def it_requires_login(client: Client):
        guild = GuildFactory()
        response = client.get(reverse("hub_guild_emails_save", args=[guild.pk]))
        assert response.status_code == 302
        assert "/accounts/login/" in response["Location"]


def describe_guild_edit_email_cards():
    def it_shows_the_thankyou_card_on_orientations_and_no_welcome_card(client: Client):
        _user_with_role("em_cards", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        client.login(username="em_cards", password="pass")
        response = client.get(f"{reverse('hub_guild_edit', args=[guild.pk])}?tab=orientations")
        assert response.status_code == 200
        content = response.content
        assert b"Thank-you Email" in content
        # The welcome email and the old combined follow-up-emails block are gone.
        assert b"Welcome email" not in content
        assert b"Save emails" not in content
