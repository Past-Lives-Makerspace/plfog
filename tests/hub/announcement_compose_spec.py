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
from django.db.models.signals import post_save
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from factory.django import mute_signals

from django.test import RequestFactory

from classes.factories import ClassOfferingFactory, RegistrationFactory
from classes.models import ClassOffering, Registration
from core.models import EventDelivery, Notification
from hub.forms import AnnouncementComposeForm, discord_channel_choices, split_audience
from hub.views import _compose_count_for, _compose_editable_guilds, _compose_first_error
from membership.models import AnnouncementDraft, GuildAnnouncement
from tests.membership.factories import GuildFactory, MemberFactory, MembershipPlanFactory

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
    def it_renders_the_single_screen_composer_for_an_admin(client: Client):
        _login_admin(client)
        response = client.get(reverse("hub_compose"))
        assert response.status_code == 200
        content = response.content.decode()
        # One flat screen: audience picker + message + the delivery options (email + push + discord).
        assert 'name="audience"' in content
        assert "Delivery" in content
        assert 'name="push_message"' in content
        assert "Push Preview" in content
        assert "Reaching" in content
        assert "Send a Push Notification test to me" in content
        # Each channel is its own bordered section.
        assert "pl-compose-channel" in content
        # No multi-step wizard nav / "Next" buttons any more.
        assert "Next: email" not in content
        assert "pl-wizard-nav" not in content
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
        # Send opens a confirm modal (the button is type=button), so no submit lives in the form.
        assert form_html.count('type="submit"') == 0

    def it_pre_scopes_from_the_audience_query_param(client: Client):
        guild = GuildFactory()
        _login_lead(client, guild)
        content = client.get(reverse("hub_compose") + f"?audience=guild:{guild.pk}").content.decode()
        # The pre-scoped option is selected.
        assert f'value="guild:{guild.pk}"' in content

    def it_resumes_a_draft_you_own(client: Client):
        admin = _login_admin(client)
        draft = AnnouncementDraft.objects.create(author=admin, body="<p>Resume this body</p>")
        content = client.get(reverse("hub_compose_resume", args=[draft.pk])).content.decode()
        assert "Resume this body" in content

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
        response = client.post(reverse("hub_compose_save_draft"), _valid_send_data(body="<p>Draft body</p>"))
        assert response.status_code == 200
        # The title is the auto category (site → "Makerspace Announcement"), not a member subject.
        assert AnnouncementDraft.objects.filter(author=admin, title="Makerspace Announcement").exists()
        assert "Draft saved." in _trigger(response)["showToast"]["message"]

    def it_upserts_the_same_row_on_a_second_save(client: Client):
        admin = _login_admin(client)
        first = client.post(reverse("hub_compose_save_draft"), _valid_send_data(body="<p>v1</p>"))
        draft = AnnouncementDraft.objects.get(author=admin)
        client.post(reverse("hub_compose_save_draft"), _valid_send_data(body="<p>v2 body</p>", draft_pk=str(draft.pk)))
        assert AnnouncementDraft.objects.filter(author=admin).count() == 1
        draft.refresh_from_db()
        assert "v2 body" in draft.body
        assert 'id="compose-draft-pk"' in first.content.decode()

    def it_returns_an_error_toast_and_no_row_on_an_invalid_channel(client: Client):
        admin = _login_admin(client)
        # #general-chat has no webhook configured in tests, so the form rejects the channel.
        response = client.post(reverse("hub_compose_save_draft"), _valid_send_data(discord_channel="general"))
        assert response.status_code == 204
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


def describe_hub_compose_push_test():
    def it_403s_a_plain_member(client: Client):
        _login_plain(client)
        assert client.post(reverse("hub_compose_push_test")).status_code == 403

    def it_reports_when_no_devices_are_registered(client: Client):
        _login_admin(client, username="pusher")
        response = client.post(reverse("hub_compose_push_test"))
        assert response.status_code == 204
        assert "No push devices" in _trigger(response)["showToast"]["message"]

    def it_fires_a_test_at_the_authors_own_devices(client: Client, monkeypatch):
        from core import push_admin

        _login_admin(client, username="pusher")
        monkeypatch.setattr(
            push_admin, "send_test_push", lambda *a, **k: push_admin.TestSendResult(delivered=2, attempted=2)
        )
        response = client.post(reverse("hub_compose_push_test"))
        assert response.status_code == 204
        assert "2 device(s)" in _trigger(response)["showToast"]["message"]


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
        response = client.post(reverse("hub_compose_preview"), _valid_send_data(body="<p>hi</p>"))
        assert response.status_code == 200
        assert b"<iframe" in response.content
        # The email leads with the auto category (site → "Makerspace Announcement").
        assert b"Makerspace Announcement" in response.content

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
        assert split_audience("site") == ("site", None, None)
        assert split_audience(f"guild:{guild.pk}") == ("guild", guild, None)
        assert split_audience("guild:abc") == ("guild", None, None)
        assert split_audience("bogus") == ("", None, None)

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

    def it_splits_a_class_audience_value():
        offering = ClassOfferingFactory()
        assert split_audience(f"class:{offering.pk}") == ("class", None, offering)
        assert split_audience("class:abc") == ("class", None, None)

    def it_offers_a_class_audience_for_each_taught_class():
        offering = ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED)
        form = AnnouncementComposeForm(is_admin=False, editable_guilds=[], editable_classes=[offering])
        values = [value for value, _label in form.fields["audience"].choices]
        assert f"class:{offering.pk}" in values

    def it_cleans_a_class_audience_into_the_class_offering():
        offering = ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED)
        form = AnnouncementComposeForm(
            {"audience": f"class:{offering.pk}", "title": "T", "body": "<p>x</p>"}, editable_classes=[offering]
        )
        assert form.is_valid(), form.errors
        assert form.cleaned_data["class_offering"] == offering

    def it_flags_a_class_audience_whose_class_vanished():
        offering = ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED)
        data = {"audience": f"class:{offering.pk}", "title": "T", "body": "<p>x</p>"}
        form = AnnouncementComposeForm(data, editable_classes=[offering])
        offering.delete()  # gone between render and submit
        assert not form.is_valid()
        assert "audience" in form.errors

    def it_offers_no_discord_channel_for_a_class_audience():
        assert discord_channel_choices("class") == []

    def it_defaults_a_guild_with_discord_roles_to_pinging_its_role():
        guild = GuildFactory(name="Ceramics", discord_role_ids=["123"])
        form = AnnouncementComposeForm(is_admin=False, editable_guilds=[guild])
        labels = dict(form.fields["mention"].choices)
        assert labels[AnnouncementDraft.Mention.ROLE.value] == "@Ceramics"
        assert form.fields["mention"].initial == AnnouncementDraft.Mention.ROLE.value

    def it_omits_the_role_ping_and_defaults_to_no_ping_without_configured_roles():
        # Regression for issue #271: the old @everyone default plus a shared channel
        # fallback pinged the whole makerspace from a webhook less guild.
        guild = GuildFactory(name="Roleless", discord_role_ids=[])
        form = AnnouncementComposeForm(is_admin=False, editable_guilds=[guild])
        values = [value for value, _label in form.fields["mention"].choices]
        assert AnnouncementDraft.Mention.ROLE.value not in values
        assert form.fields["mention"].initial == AnnouncementDraft.Mention.NONE.value

    def it_defaults_a_webhook_less_guild_to_not_posting_even_with_shared_channels():
        # Regression for issue #271: the default fell through to site wide #general-chat.
        from core.models import SiteConfiguration

        config = SiteConfiguration.load()
        config.discord_general_webhook_url = "https://discord.com/api/webhooks/9/x"
        config.save()
        guild = GuildFactory(discord_webhook_url="")
        form = AnnouncementComposeForm(is_admin=False, editable_guilds=[guild])
        assert form.fields["discord_channel"].initial == "none"

    def it_defaults_a_guild_with_its_own_webhook_to_its_own_channel():
        guild = GuildFactory(discord_webhook_url="https://discord.com/api/webhooks/9/x")
        form = AnnouncementComposeForm(is_admin=False, editable_guilds=[guild])
        assert form.fields["discord_channel"].initial == "guild"

    def it_labels_the_guild_channel_with_its_real_name_when_synced():
        guild = GuildFactory(discord_channel_name="#glass", discord_webhook_url="https://d/hook")
        form = AnnouncementComposeForm(is_admin=False, editable_guilds=[guild])
        labels = dict(form.fields["discord_channel"].choices)
        assert labels.get("guild") == "#glass"
        assert "Our Guild Channel" not in labels.values()

    def it_falls_back_to_a_generic_channel_label_before_a_sync():
        guild = GuildFactory(discord_channel_name="", discord_webhook_url="https://d/hook")
        form = AnnouncementComposeForm(is_admin=False, editable_guilds=[guild])
        labels = dict(form.fields["discord_channel"].choices)
        assert labels.get("guild") == "your guild's channel"

    def it_defaults_the_three_channel_toggles_on():
        guild = GuildFactory()
        form = AnnouncementComposeForm(is_admin=False, editable_guilds=[guild])
        assert form.fields["push_enabled"].initial is True
        assert form.fields["send_email"].initial is True
        assert form.fields["discord_enabled"].initial is True

    def it_accepts_an_optional_push_message():
        form = AnnouncementComposeForm(
            {"audience": "site", "title": "T", "body": "<p>x</p>", "push_message": "Snow day. Closed."},
            is_admin=True,
            editable_guilds=[],
        )
        assert form.is_valid(), form.errors
        assert form.cleaned_data["push_message"] == "Snow day. Closed."

    def it_rejects_a_push_message_past_the_cap():
        form = AnnouncementComposeForm(
            {"audience": "site", "title": "T", "body": "<p>x</p>", "push_message": "x" * 181},
            is_admin=True,
            editable_guilds=[],
        )
        assert not form.is_valid()
        assert "push_message" in form.errors


def describe_compose_helpers():
    def it_returns_no_guilds_for_a_non_admin_without_a_member():
        request = RequestFactory().get("/")
        assert list(_compose_editable_guilds(request, None)) == []

    def it_counts_zero_for_a_guild_audience_with_no_guild():
        assert _compose_count_for("guild", None) == 0

    def it_counts_the_site_audience_via_the_model():
        assert _compose_count_for("site", None) == 0  # no activated members seeded

    def it_counts_zero_for_a_class_audience_with_no_class():
        assert _compose_count_for("class", None, None) == 0

    def it_falls_back_when_no_field_specific_error_is_present():
        assert (
            _compose_first_error(types.SimpleNamespace(errors={"x": []})) == "Fix the highlighted fields before saving."
        )


def _instructor(
    client: Client,
    username: str = "instr",
    *,
    slug: bool = True,
    status: str = ClassOffering.Status.PUBLISHED,
):
    """Log in a member who teaches one class (PUBLISHED by default); returns (user, member, offering).

    ``slug=False`` makes a *teaching-only* instructor — no public instructor profile
    (``instructor_slug`` unset), which is the shape that used to bounce off the composer gate.
    """
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, email=f"{username}@x.com", password="p")
    member = user.member
    if slug:
        member.instructor_slug = username
        member.save(update_fields=["instructor_slug"])
    offering = ClassOfferingFactory(instructor=member, status=status)
    client.login(username=username, password="p")
    return user, member, offering


def describe_class_audience_views():
    def it_lets_an_instructor_open_the_composer(client: Client):
        _instructor(client)
        assert client.get(reverse("hub_compose")).status_code == 200

    def it_offers_the_instructors_class_as_an_audience(client: Client):
        _user, _member, offering = _instructor(client)
        content = client.get(reverse("hub_compose")).content.decode()
        assert f'value="class:{offering.pk}"' in content

    def it_sends_a_class_announcement_to_the_confirmed_roster(client: Client):
        _user, _member, offering = _instructor(client)
        student = MemberFactory()
        with mute_signals(post_save):
            student_user = User.objects.create_user(username="stu", email="stu@x.com", last_login=timezone.now())
        student.user = student_user
        student.save(update_fields=["user"])
        RegistrationFactory(class_offering=offering, member=student, status=Registration.Status.CONFIRMED)

        resp = client.post(
            reverse("hub_compose_send"),
            data=_valid_send_data(
                audience=f"class:{offering.pk}", title="Moved", body="<p>Thursday now.</p>", discord_channel=""
            ),
        )
        assert resp.status_code == 302
        assert Notification.objects.filter(user=student_user, trigger="class_announcement").exists()

    def it_forbids_sending_to_a_class_you_do_not_teach(client: Client):
        _instructor(client, username="teacher")
        other = ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED)  # a different instructor's class
        resp = client.post(
            reverse("hub_compose_send"),
            data=_valid_send_data(audience=f"class:{other.pk}", title="Nope", body="<p>x</p>", discord_channel=""),
        )
        assert resp.status_code == 403

    def it_stores_the_push_message_on_send(client: Client):
        _user, _member, offering = _instructor(client)
        client.post(
            reverse("hub_compose_send"),
            data=_valid_send_data(
                audience=f"class:{offering.pk}",
                title="T",
                body="<p>x</p>",
                discord_channel="",
                push_message="Thu 6pm, same room.",
            ),
        )
        draft = AnnouncementDraft.objects.latest("created_at")
        assert draft.push_message == "Thu 6pm, same room."

    def it_shows_the_waitlist_toggle_and_email_only_note_for_a_class(client: Client):
        _user, _member, offering = _instructor(client)
        RegistrationFactory(
            class_offering=offering, member=None, email="guest@x.com", status=Registration.Status.CONFIRMED
        )
        content = client.get(reverse("hub_compose"), {"audience": f"class:{offering.pk}"}).content.decode()
        assert "Also include the waitlist" in content
        assert "no app account" in content  # the email-only note
        assert "guest@x.com" in content

    def it_emails_a_guest_registrant_who_has_no_account(client: Client, mailoutbox):
        _user, _member, offering = _instructor(client)
        RegistrationFactory(
            class_offering=offering, member=None, email="guest@x.com", status=Registration.Status.CONFIRMED
        )
        resp = client.post(
            reverse("hub_compose_send"),
            data=_valid_send_data(
                audience=f"class:{offering.pk}", title="T", body="<p>hi</p>", discord_channel="", send_email="on"
            ),
        )
        assert resp.status_code == 302
        assert "guest@x.com" in {addr for message in mailoutbox for addr in message.to}

    def it_includes_the_waitlist_on_send_when_the_toggle_is_on(client: Client, mailoutbox):
        _user, _member, offering = _instructor(client)
        RegistrationFactory(
            class_offering=offering, member=None, email="wait@x.com", status=Registration.Status.WAITLISTED
        )
        client.post(
            reverse("hub_compose_send"),
            data=_valid_send_data(
                audience=f"class:{offering.pk}",
                title="T",
                body="<p>hi</p>",
                discord_channel="",
                send_email="on",
                include_waitlist="on",
            ),
        )
        assert "wait@x.com" in {addr for message in mailoutbox for addr in message.to}

    def it_rescopes_the_live_count_when_the_waitlist_is_included(client: Client):
        _user, _member, offering = _instructor(client)
        RegistrationFactory(class_offering=offering, member=None, email="c@x.com", status=Registration.Status.CONFIRMED)
        RegistrationFactory(
            class_offering=offering, member=None, email="w@x.com", status=Registration.Status.WAITLISTED
        )
        base = client.get(reverse("hub_compose_count"), {"audience": f"class:{offering.pk}"})
        assert json.loads(base["HX-Trigger"])["compose-count"]["count"] == 1
        widened = client.get(
            reverse("hub_compose_count"), {"audience": f"class:{offering.pk}", "include_waitlist": "on"}
        )
        assert json.loads(widened["HX-Trigger"])["compose-count"]["count"] == 2


def describe_locked_composer():
    def it_locks_the_audience_to_a_class_and_hides_the_picker(client: Client):
        _user, _member, offering = _instructor(client)
        content = client.get(f"{reverse('hub_compose')}?audience=class:{offering.pk}&lock=1").content.decode()
        assert "Sending to:" in content
        assert offering.title in content
        assert "Who is this for?" not in content  # the picker is replaced by the locked banner
        assert 'name="audience"' in content  # still submitted, as a hidden input

    def it_locks_the_audience_to_a_guild_for_a_lead(client: Client):
        guild = GuildFactory()
        _login_lead(client, guild)
        content = client.get(f"{reverse('hub_compose')}?audience=guild:{guild.pk}&lock=1").content.decode()
        assert "Sending to:" in content
        assert guild.name in content
        assert "Who is this for?" not in content

    def it_leaves_the_picker_when_not_locked(client: Client):
        _user, _member, offering = _instructor(client)
        content = client.get(f"{reverse('hub_compose')}?audience=class:{offering.pk}").content.decode()
        assert "Who is this for?" in content
        assert "Sending to:" not in content


def describe_teaching_instructor_gate():
    def it_admits_a_slugless_teaching_instructor_to_the_locked_composer(client: Client):
        # The bug: is_instructor is the public-profile flag, so a real teacher without a slug
        # bounced to the propose flow. The class page's button URL must land on the composer.
        _user, _member, offering = _instructor(client, username="noslug", slug=False)
        response = client.get(f"{reverse('hub_compose')}?audience=class:{offering.pk}&lock=1")
        assert response.status_code == 200
        content = response.content.decode()
        assert "Sending to:" in content
        assert f"Registrants of {offering.title}" in content

    def it_admits_a_slugless_teaching_instructor_to_the_open_composer(client: Client):
        # The broadened general gate: teaching a published class qualifies on its own, and the
        # class shows up as an audience option.
        _user, _member, offering = _instructor(client, username="noslug2", slug=False)
        response = client.get(reverse("hub_compose"))
        assert response.status_code == 200
        assert f'value="class:{offering.pk}"' in response.content.decode()

    def it_frames_the_locked_class_composer_as_emailing_the_registrants(client: Client):
        _user, _member, offering = _instructor(client)
        content = client.get(f"{reverse('hub_compose')}?audience=class:{offering.pk}&lock=1").content.decode()
        assert f"Email the registrants of {offering.title}" in content
        assert "This goes to everyone registered for this class." in content

    def it_keeps_the_announce_framing_for_a_locked_guild(client: Client):
        guild = GuildFactory()
        _login_lead(client, guild)
        content = client.get(f"{reverse('hub_compose')}?audience=guild:{guild.pk}&lock=1").content.decode()
        assert f"Announce to {guild.name}" in content
        assert "This goes to everyone registered for this class." not in content

    def it_admits_the_instructor_of_an_unpublished_class_via_the_lock(client: Client):
        # A pending class's instructor legitimately emails early registrants before publish.
        _user, _member, offering = _instructor(
            client, username="draftteach", slug=False, status=ClassOffering.Status.DRAFT
        )
        response = client.get(f"{reverse('hub_compose')}?audience=class:{offering.pk}&lock=1")
        assert response.status_code == 200
        assert f"Email the registrants of {offering.title}" in response.content.decode()

    def it_sends_to_the_instructors_own_unpublished_class(client: Client):
        _user, _member, offering = _instructor(
            client, username="draftsend", slug=False, status=ClassOffering.Status.DRAFT
        )
        student = MemberFactory()
        with mute_signals(post_save):
            student_user = User.objects.create_user(username="stu3", email="stu3@x.com", last_login=timezone.now())
        student.user = student_user
        student.save(update_fields=["user"])
        RegistrationFactory(class_offering=offering, member=student, status=Registration.Status.CONFIRMED)
        response = client.post(
            reverse("hub_compose_send"),
            data=_valid_send_data(
                audience=f"class:{offering.pk}", title="Early note", body="<p>See you soon.</p>", discord_channel=""
            ),
        )
        assert response.status_code == 302
        assert Notification.objects.filter(user=student_user, trigger="class_announcement").exists()

    def it_still_bounces_a_plain_member_from_a_locked_class_they_do_not_teach(client: Client):
        _login_plain(client)
        offering = ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED)
        response = client.get(f"{reverse('hub_compose')}?audience=class:{offering.pk}&lock=1")
        assert response.status_code == 302
        assert response.url == reverse("hub_guild_announcement_propose")

    def it_resumes_a_lock_only_teachers_own_draft(client: Client):
        # Saved from the locked composer; the resume URL carries no ?audience, so the gate
        # must judge the draft's own class audience instead of bouncing to propose.
        user, _member, offering = _instructor(
            client, username="draftresume", slug=False, status=ClassOffering.Status.DRAFT
        )
        save = client.post(
            reverse("hub_compose_save_draft"),
            _valid_send_data(audience=f"class:{offering.pk}", body="<p>Early note draft</p>", discord_channel=""),
        )
        assert save.status_code == 200
        draft = AnnouncementDraft.objects.get(author=user)
        response = client.get(reverse("hub_compose_resume", args=[draft.pk]))
        assert response.status_code == 200
        content = response.content.decode()
        assert "Early note draft" in content
        assert f'value="class:{offering.pk}"' in content  # the draft's class is a valid audience choice

    def it_admits_a_lock_only_teacher_to_the_push_test(client: Client):
        # The push-test button posts the whole form, so the audience travels with it and the
        # audience-aware gate admits the lock-only teacher (204 with a toast, never a 403).
        _user, _member, offering = _instructor(
            client, username="pushlock", slug=False, status=ClassOffering.Status.DRAFT
        )
        response = client.post(reverse("hub_compose_push_test"), {"audience": f"class:{offering.pk}"})
        assert response.status_code == 204

    def it_still_bounces_a_plain_member_from_a_locked_guild(client: Client):
        # A non-class pre-scope never admits on its own — only the general gate applies.
        _login_plain(client, username="plain2")
        guild = GuildFactory()
        response = client.get(f"{reverse('hub_compose')}?audience=guild:{guild.pk}&lock=1")
        assert response.status_code == 302
        assert response.url == reverse("hub_guild_announcement_propose")


def describe_send_announcement_entry_points():
    def it_shows_a_send_email_button_on_the_teach_class_page(client: Client):
        _user, member, offering = _instructor(client, username="teachbtn")
        member.instructor_oriented_at = timezone.now()  # the teach portal's own gate
        member.save(update_fields=["instructor_oriented_at"])
        content = client.get(reverse("classes:teach_class_detail", args=[offering.pk])).content.decode()
        assert "</svg>Send Email</a>" in content
        assert "Send Announcement" not in content
        assert f"audience=class:{offering.pk}" in content

    def it_shows_a_send_announcement_button_to_guild_editors(client: Client):
        guild = GuildFactory()
        _login_lead(client, guild)
        content = client.get(reverse("hub_guild_detail", args=[guild.slug])).content.decode()
        assert "Send Announcement" in content
        assert f"audience=guild:{guild.pk}" in content

    def it_shows_a_send_email_button_on_the_admin_class_page(client: Client):
        # The admin twin lands on the same registrant-addressed composer, so it carries the
        # same "Send Email" label as the teach-side button.
        _login_admin(client)
        offering = ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED)
        content = client.get(reverse("classes:admin_class_detail", args=[offering.pk])).content.decode()
        assert "</svg>Send Email</a>" in content
        assert "Send Announcement" not in content
        assert f"audience=class:{offering.pk}" in content

    def it_lets_an_admin_send_to_a_class_they_do_not_teach_when_locked(client: Client):
        # The class page's button opens the locked composer; an admin who isn't the instructor must
        # still be able to send (the pre-scoped class becomes a valid audience choice).
        _login_admin(client)
        offering = ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED)  # taught by someone else
        student = MemberFactory()
        with mute_signals(post_save):
            student_user = User.objects.create_user(username="stu2", email="stu2@x.com", last_login=timezone.now())
        student.user = student_user
        student.save(update_fields=["user"])
        RegistrationFactory(class_offering=offering, member=student, status=Registration.Status.CONFIRMED)

        resp = client.post(
            reverse("hub_compose_send"),
            data=_valid_send_data(audience=f"class:{offering.pk}", title="Hi", body="<p>x</p>", discord_channel=""),
        )
        assert resp.status_code == 302
        assert Notification.objects.filter(user=student_user, trigger="class_announcement").exists()


def describe_admin_tools_sidebar_link():
    def it_shows_the_admin_tools_tab_to_an_admin(client: Client):
        _login_admin(client)
        content = client.get(reverse("hub_member_directory")).content.decode()
        assert reverse("hub_admin_tools") in content

    def it_shows_the_admin_tools_tab_to_a_guild_lead(client: Client):
        guild = GuildFactory()
        _login_lead(client, guild)
        content = client.get(reverse("hub_member_directory")).content.decode()
        assert reverse("hub_admin_tools") in content

    def it_shows_the_admin_tools_tab_to_an_instructor(client: Client):
        _instructor(client)
        content = client.get(reverse("hub_member_directory")).content.decode()
        assert reverse("hub_admin_tools") in content

    def it_shows_the_admin_tools_tab_to_a_slugless_teaching_instructor(client: Client):
        # Teaching a published class counts, with or without the public profile flag.
        _instructor(client, username="noslugtools", slug=False)
        content = client.get(reverse("hub_member_directory")).content.decode()
        assert reverse("hub_admin_tools") in content

    def it_hides_the_admin_tools_tab_from_a_plain_member(client: Client):
        _login_plain(client)
        content = client.get(reverse("hub_member_directory")).content.decode()
        assert reverse("hub_admin_tools") not in content


def describe_admin_tools_page():
    def it_shows_every_tool_to_an_admin(client: Client):
        _login_admin(client)
        content = client.get(reverse("hub_admin_tools")).content.decode()
        assert reverse("hub_compose") in content
        assert reverse("hub_orientations_dashboard") in content
        assert reverse("hub_admin_members") in content
        assert reverse("hub_push_test") in content

    def it_shows_announcements_and_orientations_to_a_guild_lead(client: Client):
        guild = GuildFactory()
        _login_lead(client, guild)
        content = client.get(reverse("hub_admin_tools")).content.decode()
        assert reverse("hub_compose") in content
        assert reverse("hub_orientations_dashboard") in content
        assert reverse("hub_admin_members") not in content  # admin-only card
        assert reverse("hub_push_test") not in content

    def it_shows_only_announcements_to_a_pure_instructor(client: Client):
        _instructor(client)
        content = client.get(reverse("hub_admin_tools")).content.decode()
        assert reverse("hub_compose") in content
        assert reverse("hub_orientations_dashboard") not in content  # lead/staff only
        assert reverse("hub_push_test") not in content  # admin-only

    def it_redirects_a_plain_member_home(client: Client):
        _login_plain(client)
        resp = client.get(reverse("hub_admin_tools"))
        assert resp.status_code == 302
        assert resp.url == reverse("hub_home")

    def describe_quickstart_guide_cards():
        """The two Quickstart tiles were removed from Admin Tools.

        This block used to pin their per-role gating: an admin saw both, a guild lead
        saw only the guild-lead guide, an instructor only the instructor guide. The
        tiles are gone, so the coverage is inverted rather than deleted — no role gets
        a Quickstart link on this page any more. The guides themselves stay published
        in the Help Center, which tests/hub/admin_tools_spec.py pins.
        """

        _QUICKSTART_HREFS = (
            "/help/running-a-guild/guild-lead-quickstart/",
            "/help/teaching/instructor-quickstart/",
        )

        def it_shows_neither_quickstart_to_an_admin(client: Client):
            _login_admin(client)
            content = client.get(reverse("hub_admin_tools")).content.decode()
            assert not [href for href in _QUICKSTART_HREFS if href in content]

        def it_shows_neither_quickstart_to_a_guild_lead(client: Client):
            guild = GuildFactory()
            _login_lead(client, guild)
            content = client.get(reverse("hub_admin_tools")).content.decode()
            assert not [href for href in _QUICKSTART_HREFS if href in content]

        def it_shows_neither_quickstart_to_a_pure_instructor(client: Client):
            _instructor(client)
            content = client.get(reverse("hub_admin_tools")).content.decode()
            assert not [href for href in _QUICKSTART_HREFS if href in content]
