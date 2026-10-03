"""BDD specs for the guild settings autosave contract (#575).

Every save view on the guild edit page answers an ``X-Autosave: 1`` POST with JSON: 200
``{"saved": true, "rows": {...}}`` with one pk per formset row in posted order, or 422
``{"errors": {...}}`` keyed by html field name plus the error toast in ``HX-Trigger``. The
same posts without the header keep the redirect or the bound re-render they always had, the
edit gate still answers 403, and the side effects (slot regeneration, the Google push) still
run on the autosave path. The helpers in ``hub/autosave.py`` get their own block.
"""

from __future__ import annotations

import json
import re
from unittest.mock import patch

import pytest
from django import forms
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from hub.autosave import autosave_refused, formset_rows
from hub.forms import GuildLinkFormSet
from membership.models import (
    CommunityEvent,
    GuildFAQItem,
    GuildLink,
    GuildMailingListEmail,
    GuildOrientationSettings,
    Member,
    OrientationAvailability,
    OrientationType,
)
from tests.membership.factories import (
    GuildFactory,
    GuildFAQItemFactory,
    GuildLinkFactory,
    GuildMailingListEmailFactory,
    GuildOrientationSettingsFactory,
    MembershipPlanFactory,
    OrientationAvailabilityFactory,
    OrientationTypeFactory,
)

pytestmark = pytest.mark.django_db

AUTOSAVE = {"HTTP_X_AUTOSAVE": "1"}


def _user_with_role(username: str, *, fog_role: str = Member.FogRole.MEMBER) -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, password="pass")
    member = user.member
    member.fog_role = fog_role
    member.save(update_fields=["fog_role"])
    member.sync_user_permissions()
    return user


def _lead(client: Client, username: str):
    """A guild led by a fresh user who is logged in."""
    user = _user_with_role(username)
    guild = GuildFactory(guild_lead=user.member)
    client.login(username=username, password="pass")
    return user, guild


def _admin(client: Client, username: str):
    user = _user_with_role(username, fog_role=Member.FogRole.ADMIN)
    guild = GuildFactory()
    client.login(username=username, password="pass")
    return user, guild


def _formset(prefix: str, rows: list[dict[str, str]], *, initial: int) -> dict[str, str]:
    data = {
        f"{prefix}-TOTAL_FORMS": str(len(rows)),
        f"{prefix}-INITIAL_FORMS": str(initial),
        f"{prefix}-MIN_NUM_FORMS": "0",
        f"{prefix}-MAX_NUM_FORMS": "1000",
    }
    for index, row in enumerate(rows):
        for key, value in row.items():
            data[f"{prefix}-{index}-{key}"] = value
    return data


def _toast(response) -> dict[str, str]:
    return json.loads(response["HX-Trigger"])["showToast"]


def _refused(response, key: str) -> list[str]:
    """Assert the 422 shape and return the errors under ``key``."""
    assert response.status_code == 422
    body = response.json()
    assert key in body["errors"], body
    assert _toast(response)["type"] == "error"
    assert _toast(response)["message"].startswith("Couldn't save that. ")
    return body["errors"][key]


def describe_autosave_helpers():
    def it_reports_a_deleted_row_as_none_and_a_kept_row_by_pk():
        guild = GuildFactory()
        kept = GuildLinkFactory(guild=guild)
        gone = GuildLinkFactory(guild=guild)
        data = _formset(
            "links",
            [
                {"id": str(kept.pk), "label": "Kept", "url": "https://example.com/kept", "sort_order": "0"},
                {
                    "id": str(gone.pk),
                    "label": "Gone",
                    "url": "https://example.com/gone",
                    "sort_order": "1",
                    "DELETE": "on",
                },
            ],
            initial=2,
        )
        formset = GuildLinkFormSet(data, instance=guild, prefix="links")
        assert formset.is_valid()
        formset.save()
        assert formset_rows(formset) == [kept.pk, None]

    def it_keys_a_row_error_by_its_html_name_and_a_form_error_under_all():
        guild = GuildFactory()
        data = _formset("links", [{"id": "", "label": "Docs", "url": "not a url", "sort_order": "0"}], initial=0)
        formset = GuildLinkFormSet(data, instance=guild, prefix="links")
        assert not formset.is_valid()

        class Plain(forms.Form):
            name = forms.CharField()

            def clean(self):
                raise forms.ValidationError("Nope.")

        plain = Plain({"name": "x"})
        assert not plain.is_valid()
        response = autosave_refused(formset, plain)
        body = json.loads(response.content)
        assert "links-0-url" in body["errors"]
        assert body["errors"]["__all__"] == ["Nope."]
        assert _toast(response)["message"] == f"Couldn't save that. {body['errors']['links-0-url'][0]}"

    def it_leaves_a_deleted_rows_errors_out():
        guild = GuildFactory()
        saved = GuildLinkFactory(guild=guild)
        data = _formset(
            "links",
            [
                {"id": str(saved.pk), "label": "", "url": "", "sort_order": "0", "DELETE": "on"},
                {"id": "", "label": "Docs", "url": "not a url", "sort_order": "1"},
            ],
            initial=1,
        )
        formset = GuildLinkFormSet(data, instance=guild, prefix="links")
        assert not formset.is_valid()
        body = json.loads(autosave_refused(formset).content)
        assert "links-0-label" not in body["errors"]
        assert "links-1-url" in body["errors"]

    def it_reports_formset_errors_under_the_prefix():
        guild = GuildFactory()
        data = _formset("links", [], initial=0)
        # The management form is tampered so the formset itself carries an error.
        data["links-TOTAL_FORMS"] = "x"
        formset = GuildLinkFormSet(data, instance=guild, prefix="links")
        assert not formset.is_valid()
        body = json.loads(autosave_refused(formset).content)
        assert list(body["errors"]) == ["links-__all__"]

    def it_refuses_to_answer_422_with_nothing_to_report():
        class Plain(forms.Form):
            name = forms.CharField()

        valid = Plain({"name": "x"})
        assert valid.is_valid()
        with pytest.raises(ValueError):
            autosave_refused(valid)


def describe_main_form_autosave():
    def it_answers_saved_with_no_rows_and_keeps_the_redirect_without_the_header(client: Client):
        _user, guild = _lead(client, "main_ok")
        url = reverse("hub_guild_edit", args=[guild.pk])
        response = client.post(url, {"name": "Autosaved", "about": "Typed"}, **AUTOSAVE)
        assert response.status_code == 200
        assert response.json() == {"saved": True, "rows": {}}
        guild.refresh_from_db()
        assert guild.about == "Typed"
        plain = client.post(url, {"name": "Autosaved", "about": "Again"})
        assert plain.status_code == 302

    def it_answers_422_keyed_by_field_and_re_renders_without_the_header(client: Client):
        _user, guild = _lead(client, "main_bad")
        url = reverse("hub_guild_edit", args=[guild.pk])
        response = client.post(url, {"name": "", "about": "x"}, **AUTOSAVE)
        assert _refused(response, "name") == ["This field is required."]
        guild.refresh_from_db()
        assert guild.about != "x"
        plain = client.post(url, {"name": "", "about": "x"})
        assert plain.status_code == 200
        assert plain.context["form"].errors["name"]

    def it_still_answers_403_to_someone_who_cannot_edit_the_guild(client: Client):
        _user_with_role("main_nope")
        guild = GuildFactory()
        client.login(username="main_nope", password="pass")
        response = client.post(reverse("hub_guild_edit", args=[guild.pk]), {"name": "Hacked"}, **AUTOSAVE)
        assert response.status_code == 403


def describe_visibility_autosave():
    def it_answers_saved_for_an_admin_and_403_for_a_lead(client: Client):
        _user, guild = _admin(client, "vis_admin")
        url = reverse("hub_guild_visibility_save", args=[guild.pk])
        response = client.post(url, {}, **AUTOSAVE)
        assert response.status_code == 200
        assert response.json()["saved"] is True
        guild.refresh_from_db()
        assert guild.is_active is False
        assert client.post(url, {"is_active": "on"}).status_code == 302
        client.logout()
        _lead_user, led = _lead(client, "vis_lead")
        assert client.post(reverse("hub_guild_visibility_save", args=[led.pk]), {}, **AUTOSAVE).status_code == 403


def describe_orientation_settings_autosave():
    def it_saves_and_still_regenerates_slots(client: Client):
        _user, guild = _lead(client, "os_ok")
        url = reverse("hub_guild_orientation_edit", args=[guild.pk])
        with patch("membership.orientations.generate_slots") as generate:
            response = client.post(url, {"is_enabled": "on", "info": "Bring shoes"}, **AUTOSAVE)
        assert response.status_code == 200
        assert response.json()["saved"] is True
        generate.assert_called_once_with(guild=guild)
        assert GuildOrientationSettings.objects.get(guild=guild).info == "Bring shoes"
        assert client.post(url, {"is_enabled": "on", "info": "Plain"}).status_code == 302

    def it_answers_422_for_a_bad_signup_link_and_re_renders_without_the_header(client: Client):
        _user, guild = _lead(client, "os_bad")
        url = reverse("hub_guild_orientation_edit", args=[guild.pk])
        response = client.post(url, {"external_signup_url": "not a url"}, **AUTOSAVE)
        assert _refused(response, "external_signup_url")
        plain = client.post(url, {"external_signup_url": "not a url"})
        assert plain.status_code == 200
        assert plain.context["orientation_form"].errors["external_signup_url"]


def describe_orientation_types_autosave():
    def it_returns_the_pk_of_an_existing_and_a_new_row(client: Client):
        _user, guild = _lead(client, "ot_ok")
        existing = OrientationTypeFactory(guild=guild, name="Shop Basics")
        url = reverse("hub_guild_orientation_types_save", args=[guild.pk])
        data = _formset(
            "otypes",
            [
                {
                    "id": str(existing.pk),
                    "name": "Shop Basics",
                    "duration_minutes": "60",
                    "default_seats": "4",
                    "sort_order": "0",
                    "is_active": "on",
                },
                {
                    "id": "",
                    "name": "Lathe",
                    "duration_minutes": "90",
                    "default_seats": "2",
                    "sort_order": "1",
                    "is_active": "on",
                },
            ],
            initial=1,
        )
        with patch("membership.orientations.generate_slots") as generate:
            response = client.post(url, data, **AUTOSAVE)
        assert response.status_code == 200
        lathe = OrientationType.objects.get(guild=guild, name="Lathe")
        assert response.json()["rows"] == {"otypes": [existing.pk, lathe.pk]}
        generate.assert_called_once_with(guild=guild)

    def it_answers_422_with_the_formset_error_under_the_prefix(client: Client):
        _user, guild = _lead(client, "ot_bad")
        url = reverse("hub_guild_orientation_types_save", args=[guild.pk])
        data = _formset(
            "otypes",
            [
                {
                    "id": "",
                    "name": "Same",
                    "duration_minutes": "60",
                    "default_seats": "4",
                    "sort_order": "0",
                    "is_active": "on",
                },
                {
                    "id": "",
                    "name": "same",
                    "duration_minutes": "60",
                    "default_seats": "4",
                    "sort_order": "1",
                    "is_active": "on",
                },
            ],
            initial=0,
        )
        response = client.post(url, data, **AUTOSAVE)
        assert "share the name" in _refused(response, "otypes-__all__")[0]
        assert not OrientationType.objects.filter(guild=guild).exists()
        assert client.post(url, data).status_code == 200


def describe_emails_autosave():
    def it_saves_the_thank_you_and_welcome_emails(client: Client):
        _user, guild = _lead(client, "em_ok")
        url = reverse("hub_guild_emails_save", args=[guild.pk])
        thanks = client.post(
            url,
            {"form_id": "thankyou_email", "thankyou_email_enabled": "on", "thankyou_email_subject": "Thanks"},
            **AUTOSAVE,
        )
        assert thanks.status_code == 200 and thanks.json()["saved"] is True
        welcome = client.post(
            url,
            {"form_id": "welcome_email", "welcome_email_enabled": "on", "welcome_email_subject": "Hello"},
            **AUTOSAVE,
        )
        assert welcome.status_code == 200 and welcome.json()["saved"] is True
        settings_obj = GuildOrientationSettings.objects.get(guild=guild)
        assert settings_obj.thankyou_email_subject == "Thanks"
        assert settings_obj.welcome_email_subject == "Hello"
        assert client.post(url, {"form_id": "welcome_email", "welcome_email_subject": "Plain"}).status_code == 302

    def it_answers_422_for_a_subject_that_is_too_long_and_re_renders_without_the_header(client: Client):
        _user, guild = _lead(client, "em_bad")
        url = reverse("hub_guild_emails_save", args=[guild.pk])
        too_long = "x" * 201
        thanks = client.post(url, {"form_id": "thankyou_email", "thankyou_email_subject": too_long}, **AUTOSAVE)
        assert _refused(thanks, "thankyou_email_subject")
        welcome = client.post(url, {"form_id": "welcome_email", "welcome_email_subject": too_long}, **AUTOSAVE)
        assert _refused(welcome, "welcome_email_subject")
        plain = client.post(url, {"form_id": "welcome_email", "welcome_email_subject": too_long})
        assert plain.status_code == 200
        assert plain.context["welcome_email_form"].errors["welcome_email_subject"]


def describe_studio_hours_autosave():
    def it_returns_row_pks_and_still_pushes_to_google(client: Client):
        _user, guild = _lead(client, "sh_ok")
        url = reverse("hub_guild_studio_hours_save", args=[guild.pk])
        data = _formset(
            "studio_hours",
            [
                {
                    "id": "",
                    "weekday": "1",
                    "start_time": "14:00",
                    "end_time": "17:00",
                    "location": "Kiln room",
                    "note": "",
                }
            ],
            initial=0,
        )
        pushed: list[CommunityEvent] = []
        with patch.object(CommunityEvent, "push_to_google", lambda self: pushed.append(self)):
            response = client.post(url, data, **AUTOSAVE)
        assert response.status_code == 200
        event = guild.events.studio_hours().get()
        assert response.json()["rows"] == {"studio_hours": [event.pk]}
        assert pushed == [event]

    def it_returns_null_for_a_deleted_row_and_removes_it(client: Client):
        _user, guild = _lead(client, "sh_del")
        url = reverse("hub_guild_studio_hours_save", args=[guild.pk])
        first = client.post(
            url,
            _formset(
                "studio_hours",
                [{"id": "", "weekday": "1", "start_time": "14:00", "end_time": "17:00", "location": "", "note": ""}],
                initial=0,
            ),
            **AUTOSAVE,
        )
        pk = first.json()["rows"]["studio_hours"][0]
        gone = client.post(
            url,
            _formset(
                "studio_hours",
                [
                    {
                        "id": str(pk),
                        "weekday": "1",
                        "start_time": "14:00",
                        "end_time": "17:00",
                        "location": "",
                        "note": "",
                        "DELETE": "on",
                    }
                ],
                initial=1,
            ),
            **AUTOSAVE,
        )
        assert gone.json()["rows"] == {"studio_hours": [None]}
        assert not guild.events.studio_hours().exists()

    def it_answers_422_for_an_end_before_the_start_and_re_renders_without_the_header(client: Client):
        _user, guild = _lead(client, "sh_bad")
        url = reverse("hub_guild_studio_hours_save", args=[guild.pk])
        data = _formset(
            "studio_hours",
            [{"id": "", "weekday": "1", "start_time": "17:00", "end_time": "14:00", "location": "", "note": ""}],
            initial=0,
        )
        response = client.post(url, data, **AUTOSAVE)
        assert _refused(response, "studio_hours-0-end_time") == ["End time must be after start time."]
        assert not guild.events.studio_hours().exists()
        plain = client.post(url, data)
        assert plain.status_code == 200
        assert plain.context["studio_hours_formset"].errors[0]["end_time"]


def describe_guild_hours_autosave():
    def it_returns_the_row_pk_for_the_shared_hours_form(client: Client):
        _user, guild = _lead(client, "gh_ok")
        shared = OrientationAvailabilityFactory(guild=guild)
        url = reverse("hub_guild_orientation_hours_save", args=[guild.pk])
        data = {
            "orienter_scope": "",
            **_formset(
                "guild_rules",
                [
                    {
                        "id": str(shared.pk),
                        "orientation_type": str(shared.orientation_type_id),
                        "weekday": str(shared.weekday),
                        "start_time": "18:00",
                        "end_time": "20:00",
                        "seats": "4",
                        "is_active": "on",
                    }
                ],
                initial=1,
            ),
        }
        with patch("membership.orientations.generate_slots") as generate:
            response = client.post(url, data, **AUTOSAVE)
        assert response.status_code == 200
        assert response.json()["rows"] == {"guild_rules": [shared.pk]}
        generate.assert_called_once_with(guild=guild)
        shared.refresh_from_db()
        assert shared.end_time.hour == 20
        assert client.post(url, data).status_code == 302

    def it_answers_422_keyed_by_the_row_field(client: Client):
        _user, guild = _lead(client, "gh_bad")
        shared = OrientationAvailabilityFactory(guild=guild)
        url = reverse("hub_guild_orientation_hours_save", args=[guild.pk])
        data = {
            "orienter_scope": "",
            **_formset(
                "guild_rules",
                [
                    {
                        "id": str(shared.pk),
                        "orientation_type": str(shared.orientation_type_id),
                        "weekday": str(shared.weekday),
                        "start_time": "19:00",
                        "end_time": "18:00",
                        "seats": "4",
                        "is_active": "on",
                    }
                ],
                initial=1,
            ),
        }
        response = client.post(url, data, **AUTOSAVE)
        assert _refused(response, "guild_rules-0-end_time")
        assert OrientationAvailability.objects.get(pk=shared.pk).end_time.hour == 19
        assert client.post(url, data).status_code == 200


def describe_faq_autosave():
    def it_returns_both_pks_for_an_existing_and_a_new_row(client: Client):
        _user, guild = _lead(client, "faq_ok")
        existing = GuildFAQItemFactory(guild=guild, question="Old?")
        url = reverse("hub_guild_faq_save", args=[guild.pk])
        data = _formset(
            "faq",
            [
                {"id": str(existing.pk), "question": "Old?", "answer": "Yes.", "sort_order": "0"},
                {"id": "", "question": "New?", "answer": "Also yes.", "sort_order": "1"},
            ],
            initial=1,
        )
        response = client.post(url, data, **AUTOSAVE)
        assert response.status_code == 200
        new = GuildFAQItem.objects.get(guild=guild, question="New?")
        assert response.json()["rows"] == {"faq": [existing.pk, new.pk]}

    def it_skips_a_new_row_posted_as_rendered_and_saves_the_rest(client: Client):
        # The script posts a half typed new row with its rendered defaults, so Django reads
        # it as unchanged and the existing row's edit still lands.
        _user, guild = _lead(client, "faq_skip")
        existing = GuildFAQItemFactory(guild=guild, question="Old?")
        url = reverse("hub_guild_faq_save", args=[guild.pk])
        data = _formset(
            "faq",
            [
                {"id": str(existing.pk), "question": "Edited?", "answer": "Yes.", "sort_order": "0"},
                {"id": "", "question": "", "answer": "", "video_url": "", "document_url": "", "sort_order": "0"},
            ],
            initial=1,
        )
        response = client.post(url, data, **AUTOSAVE)
        assert response.json()["rows"] == {"faq": [existing.pk, None]}
        assert list(GuildFAQItem.objects.filter(guild=guild).values_list("question", flat=True)) == ["Edited?"]

    def it_returns_null_for_a_deleted_row_and_the_row_is_gone(client: Client):
        _user, guild = _lead(client, "faq_del")
        existing = GuildFAQItemFactory(guild=guild)
        url = reverse("hub_guild_faq_save", args=[guild.pk])
        data = _formset(
            "faq",
            [{"id": str(existing.pk), "question": "Bye?", "answer": "Bye.", "sort_order": "0", "DELETE": "on"}],
            initial=1,
        )
        response = client.post(url, data, **AUTOSAVE)
        assert response.json()["rows"] == {"faq": [None]}
        assert not GuildFAQItem.objects.filter(pk=existing.pk).exists()

    def it_answers_422_inline_where_the_plain_post_only_flashes(client: Client):
        _user, guild = _lead(client, "faq_bad")
        url = reverse("hub_guild_faq_save", args=[guild.pk])
        data = _formset(
            "faq",
            [{"id": "", "question": "Q?", "answer": "A.", "video_url": "https://example.com/clip", "sort_order": "0"}],
            initial=0,
        )
        response = client.post(url, data, **AUTOSAVE)
        assert _refused(response, "faq-0-video_url")
        assert not GuildFAQItem.objects.filter(guild=guild).exists()
        plain = client.post(url, data)
        assert plain.status_code == 302


def describe_links_autosave():
    def it_returns_row_pks_and_422_for_a_bad_url(client: Client):
        _user, guild = _lead(client, "lnk")
        url = reverse("hub_guild_links_save", args=[guild.pk])
        good = client.post(
            url,
            _formset(
                "links", [{"id": "", "label": "Docs", "url": "https://example.com/docs", "sort_order": "0"}], initial=0
            ),
            **AUTOSAVE,
        )
        link = GuildLink.objects.get(guild=guild)
        assert good.json()["rows"] == {"links": [link.pk]}
        bad = _formset(
            "links", [{"id": str(link.pk), "label": "Docs", "url": "not a url", "sort_order": "0"}], initial=1
        )
        response = client.post(url, bad, **AUTOSAVE)
        assert _refused(response, "links-0-url")
        link.refresh_from_db()
        assert link.url == "https://example.com/docs"
        assert client.post(url, bad).status_code == 302


def describe_mailing_list_autosave():
    def it_returns_row_pks_and_422_for_a_bad_address(client: Client):
        _user, guild = _lead(client, "ml")
        existing = GuildMailingListEmailFactory(guild=guild)
        url = reverse("hub_guild_mailing_list_save", args=[guild.pk])
        good = client.post(
            url,
            _formset(
                "mailing_list",
                [
                    {"id": str(existing.pk), "email": existing.email, "label": "", "sort_order": "0"},
                    {"id": "", "email": "booster@example.com", "label": "Booster", "sort_order": "1"},
                ],
                initial=1,
            ),
            **AUTOSAVE,
        )
        booster = GuildMailingListEmail.objects.get(guild=guild, email="booster@example.com")
        assert good.json()["rows"] == {"mailing_list": [existing.pk, booster.pk]}
        bad = _formset("mailing_list", [{"id": "", "email": "nope", "label": "", "sort_order": "0"}], initial=0)
        response = client.post(url, bad, **AUTOSAVE)
        assert _refused(response, "mailing_list-0-email")
        plain = client.post(url, bad)
        assert plain.status_code == 200
        assert plain.context["mailing_list_formset"].errors[0]["email"]


def describe_announcement_settings_autosave():
    def it_answers_saved_and_keeps_the_redirect_without_the_header(client: Client):
        _user, guild = _lead(client, "ann")
        url = reverse("hub_guild_announcement_settings_save", args=[guild.pk])
        response = client.post(url, {"allow_member_announcement_suggestions": "on"}, **AUTOSAVE)
        assert response.status_code == 200
        assert response.json()["saved"] is True
        guild.refresh_from_db()
        assert guild.allow_member_announcement_suggestions is True
        assert client.post(url, {}).status_code == 302


def describe_guild_edit_page_autosave_markup():
    def it_renders_the_pill_the_lead_line_and_no_save_button_on_any_autosave_section(client: Client):
        _user, guild = _admin(client, "markup")
        GuildOrientationSettingsFactory(guild=guild)
        OrientationAvailabilityFactory(guild=guild)
        content = client.get(reverse("hub_guild_edit", args=[guild.pk])).content.decode()
        assert "data-save-pill" in content
        assert "Every change saves as you make it." in content
        assert 'x-data="plGuildAutosave(' in content
        for label in (
            "Save Changes",
            "Save Studio Hours",
            "Save orientation settings",
            "Save FAQ",
            "Save Links",
            "Save mailing list",
        ):
            assert label not in content, label
        # The one off slot form keeps its Save; nothing else on the page submits.
        assert content.count('type="submit" class="pl-btn pl-btn--primary">Save</button>') == 1
        assert "requestSubmit()" not in content.replace(
            "document.getElementById('times-bulk-form').requestSubmit();", ""
        )

    def it_marks_every_saving_form_and_names_each_formsets_required_fields(client: Client):
        _user, guild = _admin(client, "markup2")
        GuildOrientationSettingsFactory(guild=guild)
        OrientationAvailabilityFactory(guild=guild)
        content = client.get(reverse("hub_guild_edit", args=[guild.pk])).content.decode()
        for action in (
            reverse("hub_guild_visibility_save", args=[guild.pk]),
            reverse("hub_guild_orientation_edit", args=[guild.pk]),
            reverse("hub_guild_orientation_types_save", args=[guild.pk]),
            reverse("hub_guild_emails_save", args=[guild.pk]),
            reverse("hub_guild_studio_hours_save", args=[guild.pk]),
            reverse("hub_guild_orientation_hours_save", args=[guild.pk]),
            reverse("hub_guild_faq_save", args=[guild.pk]),
            reverse("hub_guild_links_save", args=[guild.pk]),
            reverse("hub_guild_mailing_list_save", args=[guild.pk]),
            reverse("hub_guild_announcement_settings_save", args=[guild.pk]),
        ):
            assert re.search(rf'<form[^>]*action="{re.escape(action)}"[^>]*data-autosave', content), action
        assert 'data-formset="faq" data-formset-required="question answer"' in content
        assert 'data-formset="links" data-formset-required="label url"' in content
        assert 'data-formset="mailing_list" data-formset-required="email"' in content
        assert 'data-formset="studio_hours" data-formset-required="weekday start_time end_time"' in content
        assert 'data-formset="otypes" data-formset-required="name duration_minutes default_seats sort_order"' in content
        assert 'data-formset="guild_rules"' in content
        assert 'data-autosave-confirm="delete-studio-hours"' in content
        assert 'data-autosave-confirm="delete-guild-hours"' in content
        assert "leave-while-saving" in content
