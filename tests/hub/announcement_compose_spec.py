"""The announcement compose wizard views (/announcements/compose/) — page, drafts, send, mention.

Covers: GET renders the three steps + the send path (the drafts UI is hidden as of 2026-07-13 —
backend intact); audience gating (admins see site-wide, leads see their guilds, plain members get
bounced to propose); resume robustness (foreign/sent pk → 404); save-draft upsert + error toast;
the recipient-count HTMX endpoint; the direct test-send (never the spine); send permission
re-checks + the guild materialization; delete via confirm.
"""

from __future__ import annotations

import json
import types

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from django.test import RequestFactory

from core.models import EventDelivery
from hub.forms import AnnouncementComposeForm, discord_channel_choices, split_audience
from hub.views import _compose_count_for, _compose_editable_guilds, _compose_first_error
from membership.models import AnnouncementDraft, GuildAnnouncement
from tests.membership.factories import GuildFactory, MembershipPlanFactory

pytestmark = pytest.mark.django_db


def _login_admin(client: Client, username: str = "admin") -> User:
    user = User.objects.create_superuser(username=username, email=f"{username}@x.com", password="p")
    client.login(username=username, password="p")
    return user


def _login_lead(client: Client, guild, username: str = "lead"):
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, email=f"{username}@x.com", password="p")
    member = user.member
    guild.guild_lead = member
    guild.save(update_fields=["guild_lead"])
    client.login(username=username, password="p")
    return user, member


def _login_plain(client: Client, username: str = "plain") -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, email=f"{username}@x.com", password="p")
    client.login(username=username, password="p")
    return user


def _trigger(response) -> dict:
    return json.loads(response["HX-Trigger"])


def _valid_send_data(**overrides) -> dict:
    data = {
        "audience": "site",
        "title": "Heads up",
        "body": "<p>Hello everyone.</p>",
        "discord_channel": "none",
        "mention": "none",
        "draft_pk": "",
    }
    data.update(overrides)
    return data


def describe_hub_compose_page():
    def it_renders_the_three_steps_and_the_send_path_for_an_admin(client: Client):
        _login_admin(client)
        response = client.get(reverse("hub_compose"))
        assert response.status_code == 200
        content = response.content.decode()
        # All three wizard steps render: audience & message → email → Discord.
        assert 'name="audience"' in content
        assert "Audience &amp; message" in content
        assert "Email" in content
        assert "Discord" in content
        assert "Reaches" in content
        # The send path is intact: one form posting to send, with a Send submit.
        assert f'action="{reverse("hub_compose_send")}"' in content
        assert "Send announcement" in content

    def it_hides_the_drafts_panel_and_the_save_draft_button(client: Client):
        # The drafts UI was hidden on 2026-07-13 (backend intact); neither the "Save draft"
        # button nor the "Your drafts" panel include should reach the composer any more.
        _login_admin(client)
        content = client.get(reverse("hub_compose")).content.decode()
        assert reverse("hub_compose_save_draft") not in content
        assert ">Save draft<" not in content
        assert 'id="compose-drafts"' not in content
        assert "Your drafts" not in content
        assert "No saved drafts yet" not in content
        assert "+ New announcement" not in content

    def it_offers_the_site_option_to_an_admin(client: Client):
        _login_admin(client)
        content = client.get(reverse("hub_compose")).content.decode()
        assert 'value="site"' in content

    def it_hides_the_site_option_from_a_guild_lead(client: Client):
        guild = GuildFactory()
        _login_lead(client, guild)
        content = client.get(reverse("hub_compose")).content.decode()
        assert 'value="site"' not in content
        assert f'value="guild:{guild.pk}"' in content

    def it_bounces_a_plain_member_to_the_propose_flow(client: Client):
        _login_plain(client)
        response = client.get(reverse("hub_compose"))
        assert response.status_code == 302
        assert response.url == reverse("hub_guild_announcement_propose")

    def it_has_exactly_one_wizard_form_and_only_send_is_a_submit(client: Client):
        _login_admin(client)
        content = client.get(reverse("hub_compose")).content.decode()
        # One wizard form posting to send.
        assert content.count(f'action="{reverse("hub_compose_send")}"') == 1
        start = content.index(f'action="{reverse("hub_compose_send")}"')
        form_html = content[start : content.index("</form>", start)]
        assert form_html.count('type="submit"') == 1  # only Send

    def it_pre_scopes_from_the_audience_query_param(client: Client):
        guild = GuildFactory()
        _login_lead(client, guild)
        content = client.get(reverse("hub_compose") + f"?audience=guild:{guild.pk}").content.decode()
        # The pre-scoped option is selected.
        assert f'value="guild:{guild.pk}"' in content

    def it_resumes_a_draft_you_own(client: Client):
        admin = _login_admin(client)
        draft = AnnouncementDraft.objects.create(author=admin, title="Resume me", body="<p>hi</p>")
        content = client.get(reverse("hub_compose_resume", args=[draft.pk])).content.decode()
        assert "Resume me" in content

    def it_404s_resuming_another_users_draft(client: Client):
        other = User.objects.create_user(username="other", password="p")
        draft = AnnouncementDraft.objects.create(author=other, title="Not yours")
        _login_admin(client)
        assert client.get(reverse("hub_compose_resume", args=[draft.pk])).status_code == 404

    def it_404s_resuming_an_already_sent_draft(client: Client):
        admin = _login_admin(client)
        draft = AnnouncementDraft.objects.create(author=admin, title="Gone", sent_at=timezone.now())
        assert client.get(reverse("hub_compose_resume", args=[draft.pk])).status_code == 404


def describe_hub_compose_save_draft():
    def it_saves_a_new_draft_and_returns_a_toast(client: Client):
        admin = _login_admin(client)
        response = client.post(
            reverse("hub_compose_save_draft"), _valid_send_data(title="Draft one", body="<p>body</p>")
        )
        assert response.status_code == 200
        assert AnnouncementDraft.objects.filter(author=admin, title="Draft one").exists()
        assert "Draft saved." in _trigger(response)["showToast"]["message"]

    def it_upserts_the_same_row_on_a_second_save(client: Client):
        admin = _login_admin(client)
        first = client.post(reverse("hub_compose_save_draft"), _valid_send_data(title="v1"))
        draft = AnnouncementDraft.objects.get(author=admin)
        client.post(reverse("hub_compose_save_draft"), _valid_send_data(title="v2", draft_pk=str(draft.pk)))
        assert AnnouncementDraft.objects.filter(author=admin).count() == 1
        draft.refresh_from_db()
        assert draft.title == "v2"
        assert 'id="compose-draft-pk"' in first.content.decode()

    def it_returns_an_error_toast_and_no_row_on_a_blank_title(client: Client):
        admin = _login_admin(client)
        response = client.post(reverse("hub_compose_save_draft"), _valid_send_data(title=""))
        assert response.status_code == 204
        assert "subject" in _trigger(response)["showToast"]["message"].lower()
        assert not AnnouncementDraft.objects.filter(author=admin).exists()

    def it_403s_a_lead_saving_a_site_wide_draft(client: Client):
        guild = GuildFactory()
        _login_lead(client, guild)
        response = client.post(reverse("hub_compose_save_draft"), _valid_send_data(audience="site"))
        assert response.status_code == 403


def describe_hub_compose_send():
    def it_sends_a_site_announcement_and_redirects_with_a_message(client: Client):
        _login_admin(client)
        response = client.post(reverse("hub_compose_send"), _valid_send_data())
        assert response.status_code == 302
        assert response.url == reverse("hub_compose")

    def it_re_renders_on_a_blank_body(client: Client):
        _login_admin(client)
        response = client.post(reverse("hub_compose_send"), _valid_send_data(body="<p><br></p>"))
        assert response.status_code == 200
        assert b"Add a message before sending." in response.content

    def it_403s_a_lead_sending_site_wide(client: Client):
        guild = GuildFactory()
        _login_lead(client, guild)
        response = client.post(reverse("hub_compose_send"), _valid_send_data(audience="site"))
        assert response.status_code == 403

    def it_403s_a_lead_sending_to_a_guild_they_do_not_edit(client: Client):
        own = GuildFactory(name="Own")
        other = GuildFactory(name="Other")
        _login_lead(client, own)
        response = client.post(reverse("hub_compose_send"), _valid_send_data(audience=f"guild:{other.pk}"))
        assert response.status_code == 403

    def it_lets_a_lead_send_to_their_own_guild_and_materializes_a_post(client: Client):
        guild = GuildFactory()
        _login_lead(client, guild)
        response = client.post(reverse("hub_compose_send"), _valid_send_data(audience=f"guild:{guild.pk}"))
        assert response.status_code == 302
        assert GuildAnnouncement.objects.filter(guild=guild, moderation_state="published").exists()


def describe_hub_compose_count():
    def it_pushes_the_count_and_returns_the_rescoped_picker(client: Client):
        _login_admin(client)
        response = client.get(reverse("hub_compose_count"), {"audience": "site"})
        assert response.status_code == 200
        assert "compose-count" in _trigger(response)
        assert b"compose-discord-picker" in response.content

    def it_403s_a_lead_asking_for_the_site_count(client: Client):
        guild = GuildFactory()
        _login_lead(client, guild)
        response = client.get(reverse("hub_compose_count"), {"audience": "site"})
        assert response.status_code == 403


def describe_hub_compose_test():
    def it_sends_a_test_to_the_author_only_and_never_the_spine(client: Client, mailoutbox):
        _login_admin(client, username="tester")
        response = client.post(reverse("hub_compose_test"), _valid_send_data())
        assert response.status_code == 204
        assert [m.to for m in mailoutbox] == [["tester@x.com"]]
        assert not EventDelivery.objects.exists()  # a test never touches the ledger

    def it_403s_a_plain_member(client: Client):
        _login_plain(client)
        assert client.post(reverse("hub_compose_test"), _valid_send_data()).status_code == 403


def describe_hub_compose_delete_draft():
    def it_deletes_the_draft_and_returns_the_list_with_a_toast(client: Client):
        admin = _login_admin(client)
        draft = AnnouncementDraft.objects.create(author=admin, title="Bye")
        response = client.post(reverse("hub_compose_delete_draft", args=[draft.pk]))
        assert response.status_code == 200
        assert not AnnouncementDraft.objects.filter(pk=draft.pk).exists()
        assert "Draft deleted." in _trigger(response)["showToast"]["message"]

    def it_404s_deleting_another_users_draft(client: Client):
        other = User.objects.create_user(username="other", password="p")
        draft = AnnouncementDraft.objects.create(author=other, title="Not yours")
        _login_admin(client)
        assert client.post(reverse("hub_compose_delete_draft", args=[draft.pk])).status_code == 404


def describe_hub_compose_preview():
    def it_returns_the_branded_iframe_preview(client: Client):
        _login_admin(client)
        response = client.post(reverse("hub_compose_preview"), _valid_send_data(title="Preview", body="<p>hi</p>"))
        assert response.status_code == 200
        assert b"<iframe" in response.content
        assert b"Preview" in response.content

    def it_403s_a_plain_member(client: Client):
        _login_plain(client)
        assert client.post(reverse("hub_compose_preview"), _valid_send_data()).status_code == 403


def describe_compose_edge_cases():
    def it_403s_an_unrecognized_audience_value(client: Client):
        _login_admin(client)
        response = client.post(reverse("hub_compose_save_draft"), _valid_send_data(audience="bogus"))
        assert response.status_code == 403

    def it_toasts_the_first_field_error_for_a_non_title_problem(client: Client):
        admin = _login_admin(client)
        response = client.post(reverse("hub_compose_save_draft"), _valid_send_data(discord_channel="bogus"))
        assert response.status_code == 204
        assert _trigger(response)["showToast"]["type"] == "error"
        assert not AnnouncementDraft.objects.filter(author=admin).exists()

    def it_re_renders_send_on_an_invalid_form(client: Client):
        _login_admin(client)
        response = client.post(reverse("hub_compose_send"), _valid_send_data(discord_channel="bogus"))
        assert response.status_code == 200

    def it_sends_a_resumed_draft_by_pk(client: Client):
        admin = _login_admin(client)
        draft = AnnouncementDraft.objects.create(author=admin, title="Resume then send", body="<p>hi</p>")
        response = client.post(reverse("hub_compose_send"), _valid_send_data(title="R", draft_pk=str(draft.pk)))
        assert response.status_code == 302
        draft.refresh_from_db()
        assert draft.sent_at is not None

    def it_error_toasts_a_test_when_the_author_has_no_email(client: Client):
        MembershipPlanFactory()
        guild = GuildFactory()
        user, member = _login_lead(client, guild, username="noemail")
        user.email = ""
        user.save(update_fields=["email"])
        response = client.post(reverse("hub_compose_test"), _valid_send_data(audience=f"guild:{guild.pk}"))
        assert response.status_code == 204
        assert _trigger(response)["showToast"]["type"] == "error"

    def it_counts_a_guild_audience(client: Client):
        guild = GuildFactory()
        _login_lead(client, guild)
        response = client.get(reverse("hub_compose_count"), {"audience": f"guild:{guild.pk}"})
        assert response.status_code == 200
        assert _trigger(response)["compose-count"]["count"] == 0

    def it_resumes_a_guild_scoped_draft(client: Client):
        guild = GuildFactory()
        user, member = _login_lead(client, guild)
        draft = AnnouncementDraft.objects.create(
            author=user, audience=AnnouncementDraft.Audience.GUILD, guild=guild, title="Guild draft"
        )
        content = client.get(reverse("hub_compose_resume", args=[draft.pk])).content.decode()
        assert f'value="guild:{guild.pk}"' in content


def describe_AnnouncementComposeForm():
    def it_splits_the_audience_value():
        guild = GuildFactory()
        assert split_audience("site") == ("site", None)
        assert split_audience(f"guild:{guild.pk}") == ("guild", guild)
        assert split_audience("guild:abc") == ("guild", None)
        assert split_audience("bogus") == ("", None)

    def it_scopes_the_channel_choices_by_audience():
        assert ("guild", "Our Guild Channel") in discord_channel_choices("guild")
        assert all(value != "guild" for value, _ in discord_channel_choices("site"))
        # The shared #guild-officers channel is offered to both guild and site-wide audiences.
        assert ("officers", "#guild-officers") in discord_channel_choices("guild")
        assert ("officers", "#guild-officers") in discord_channel_choices("site")

    def it_rejects_an_unconfigured_guild_channel():
        guild = GuildFactory()  # no webhook configured
        form = AnnouncementComposeForm(
            {"audience": f"guild:{guild.pk}", "title": "T", "body": "<p>x</p>", "discord_channel": "guild"},
            is_admin=True,
            editable_guilds=[guild],
        )
        assert not form.is_valid()
        assert "discord_channel" in form.errors

    def it_defaults_a_blank_channel_to_none():
        form = AnnouncementComposeForm(
            {"audience": "site", "title": "T", "body": "<p>x</p>"}, is_admin=True, editable_guilds=[]
        )
        assert form.is_valid(), form.errors
        assert form.cleaned_data["discord_channel"] == "none"

    def it_rejects_an_empty_audience():
        form = AnnouncementComposeForm(
            {"title": "T", "body": "<p>x</p>", "audience": ""}, is_admin=True, editable_guilds=[]
        )
        assert not form.is_valid()

    def it_flags_a_guild_audience_whose_guild_vanished():
        guild = GuildFactory()
        data = {"audience": f"guild:{guild.pk}", "title": "T", "body": "<p>x</p>", "discord_channel": "none"}
        form = AnnouncementComposeForm(data, is_admin=True, editable_guilds=[guild])
        guild.delete()  # gone between render and submit
        assert not form.is_valid()
        assert "audience" in form.errors


def describe_compose_helpers():
    def it_returns_no_guilds_for_a_non_admin_without_a_member():
        request = RequestFactory().get("/")
        assert list(_compose_editable_guilds(request, None)) == []

    def it_counts_zero_for_a_guild_audience_with_no_guild():
        assert _compose_count_for("guild", None) == 0

    def it_counts_the_site_audience_via_the_model():
        assert _compose_count_for("site", None) == 0  # no activated members seeded

    def it_falls_back_when_no_field_specific_error_is_present():
        assert (
            _compose_first_error(types.SimpleNamespace(errors={"x": []})) == "Fix the highlighted fields before saving."
        )
