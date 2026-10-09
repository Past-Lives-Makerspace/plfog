"""The composer's "Add people" picker and the guild's remembered list (#729).

Covers the hub half: saved members on a guild roster (by name, pre-checked), the add list
(``add_people_choices``) left of everyone already listed, typed addresses
(``classify_announcement_additions``, ``hub_compose_add_addresses``, the clean and the resumed
rows), the picker markup on guild, class and site audiences, Guild Settings showing and removing
a saved member, and the lead's whole flow: nine members added in one send are listed for the
guild's other lead next time.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.template.loader import render_to_string
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from factory.django import mute_signals

from core.models import Notification
from hub.forms import AnnouncementComposeForm, announcement_recipient_choices, classify_announcement_additions
from membership.models import AnnouncementDraft, GuildMailingListEmail, Member
from tests.membership.factories import (
    AnnouncementDraftFactory,
    GuildFactory,
    GuildMailingListEmailFactory,
    GuildMembershipFactory,
    MemberFactory,
    MembershipPlanFactory,
)

pytestmark = pytest.mark.django_db

_seq = {"n": 0}


def _account(email: str, *, name: str = "", hidden: bool = False, active: bool = True) -> Member:
    _seq["n"] += 1
    member = MemberFactory(full_legal_name=name or f"Person {_seq['n']}", hide_from_directory=hidden)
    with mute_signals(post_save):
        user = User.objects.create_user(
            username=f"madd_{_seq['n']}", email=email, last_login=timezone.now(), is_active=active
        )
    member.user = user
    member.save(update_fields=["user"])
    return member


def _roster_member(guild, email: str, name: str = "") -> Member:
    member = _account(email, name=name)
    GuildMembershipFactory(guild=guild, member=member)
    return member


def _login_lead(client: Client, guild, username: str) -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, email=f"{username}@x.com", password="p")
    if guild.guild_lead_id is None:
        guild.guild_lead = user.member
        guild.save(update_fields=["guild_lead"])
    client.login(username=username, password="p")
    return user


def _login_admin(client: Client, username: str = "madd_admin") -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, email=f"{username}@x.com", password="p")
    member = user.member
    member.fog_role = Member.FogRole.ADMIN
    member.save(update_fields=["fog_role"])
    member.sync_user_permissions()
    client.login(username=username, password="p")
    return user


def _guild_post(guild, **overrides) -> dict:
    data = {
        "audience": f"guild:{guild.pk}",
        "body": "<p>Open shop night moves to Thursday.</p>",
        "discord_channel": "none",
        "mention": "none",
        "draft_pk": "",
        "send_email": "on",
    }
    data.update(overrides)
    return data


def _form(audience: str, **kwargs) -> AnnouncementComposeForm:
    return AnnouncementComposeForm(is_admin=True, initial={"audience": audience}, **kwargs)


def describe_saved_members_on_the_roster():
    def it_lists_a_saved_member_by_name_once_and_never_as_an_address():
        guild = GuildFactory()
        roster = _roster_member(guild, "roster@example.com", "Rory Roster")
        saved = _account("saved@example.com", name="Sam Saved")
        GuildMailingListEmailFactory(guild=guild, email="saved@example.com", user=saved.user)
        GuildMailingListEmailFactory(guild=guild, email="roster@example.com", user=roster.user)
        GuildMailingListEmailFactory(guild=guild, email="booster@example.com")
        choices = announcement_recipient_choices(AnnouncementDraft.Audience.GUILD.value, guild)
        assert choices == [
            (f"user:{roster.user_id}", "Rory Roster · roster@example.com"),
            (f"user:{saved.user_id}", "Sam Saved · saved@example.com"),
            ("custom:booster@example.com", "booster@example.com"),
        ]

    def it_checks_saved_members_by_default():
        guild = GuildFactory()
        saved = _account("saved@example.com")
        GuildMailingListEmailFactory(guild=guild, email="saved@example.com", user=saved.user)
        form = _form(f"guild:{guild.pk}", editable_guilds=[guild])
        assert f"user:{saved.user_id}" in form.fields["recipients"].initial


def describe_add_people_choices():
    def it_offers_every_member_not_already_listed():
        guild = GuildFactory()
        roster = _roster_member(guild, "roster@example.com")
        saved = _account("saved@example.com")
        GuildMailingListEmailFactory(guild=guild, email="saved@example.com", user=saved.user)
        outsider = _account("outsider@example.com")
        added = _account("added@example.com")
        form = AnnouncementComposeForm(
            is_admin=True,
            editable_guilds=[guild],
            initial={"audience": f"guild:{guild.pk}", "recipients": [f"user:{added.user_id}"]},
        )
        values = {value for value, _label in form.add_people_choices}
        assert f"user:{outsider.user_id}" in values
        assert not values & {f"user:{roster.user_id}", f"user:{saved.user_id}", f"user:{added.user_id}"}

    def it_is_offered_on_guild_and_class_announcements_and_saves_only_for_a_guild():
        from classes.factories import ClassOfferingFactory

        guild = GuildFactory()
        offering = ClassOfferingFactory()
        guild_form = _form(f"guild:{guild.pk}", editable_guilds=[guild])
        class_form = _form(f"class:{offering.pk}", editable_classes=[offering])
        site_form = _form("site")
        assert (guild_form.allows_add_people, guild_form.saves_added_people) == (True, True)
        assert (class_form.allows_add_people, class_form.saves_added_people) == (True, False)
        assert (site_form.allows_add_people, site_form.saves_added_people) == (False, False)


def describe_classify_announcement_additions():
    def it_adds_a_members_address_as_that_member_and_others_as_email_only():
        member = _account("Member@Example.com", name="Mo Member")
        rows, problems = classify_announcement_additions(
            ["member@example.com", "guest@example.com", "Guest@example.com", "nope"]
        )
        assert rows == [
            (f"user:{member.user_id}", "Mo Member · Member@example.com", False),
            ("custom:guest@example.com", "guest@example.com", True),
        ]
        assert problems == ["nope isn't an email address."]

    def it_adds_a_member_once_when_two_of_their_addresses_are_typed():
        member = _account("main@example.com", name="Mo Member")
        Member.objects.filter(pk=member.pk).update(notification_email="other@example.com")
        rows, _problems = classify_announcement_additions(["main@example.com", "other@example.com"])
        assert rows == [(f"user:{member.user_id}", "Mo Member · main@example.com", False)]

    def it_adds_a_hidden_or_turned_off_member_by_address_only():
        _account("hidden@example.com", hidden=True)
        _account("off@example.com", active=False)
        rows, _problems = classify_announcement_additions(["hidden@example.com", "off@example.com"])
        assert rows == [
            ("custom:hidden@example.com", "hidden@example.com", True),
            ("custom:off@example.com", "off@example.com", True),
        ]


def describe_typed_addresses_in_the_form():
    def it_keeps_a_valid_typed_address_on_a_guild_and_drops_a_bad_one():
        guild = GuildFactory()
        roster = _roster_member(guild, "roster@example.com")
        data = _guild_post(
            guild, recipients=[f"user:{roster.user_id}", "custom:typed@example.com", "custom:bad", "custom:UP@x.com"]
        )
        form = AnnouncementComposeForm(data, is_admin=True, editable_guilds=[guild])
        assert form.is_valid(), form.errors
        assert form.cleaned_data["recipient_selection"] == {"users": [roster.user_id], "custom": ["typed@example.com"]}
        assert form.added_member_rows == [("custom:typed@example.com", "typed@example.com (email only)")]

    def it_keeps_a_typed_address_on_a_class():
        from classes.factories import ClassOfferingFactory

        offering = ClassOfferingFactory()
        data = {
            "audience": f"class:{offering.pk}",
            "body": "<p>x</p>",
            "discord_channel": "none",
            "recipients": ["custom:typed@example.com"],
        }
        form = AnnouncementComposeForm(data, is_admin=True, editable_classes=[offering])
        assert form.is_valid(), form.errors
        assert form.cleaned_data["recipient_selection"] == {"users": [], "custom": ["typed@example.com"]}

    def it_never_takes_a_typed_address_on_a_site_announcement():
        data = {"audience": "site", "body": "<p>x</p>", "discord_channel": "none", "recipients": ["custom:t@x.com"]}
        form = AnnouncementComposeForm(data, is_admin=True)
        assert form.is_valid(), form.errors
        assert form.cleaned_data["recipient_selection"] == {}
        assert form.added_member_rows == []


def describe_hub_compose_add_addresses():
    def it_returns_a_checked_row_per_address(client: Client):
        guild = GuildFactory()
        _login_lead(client, guild, "addr_lead")
        member = _account("member@example.com", name="Mo Member")
        response = client.post(
            reverse("hub_compose_add_addresses"),
            {"audience": f"guild:{guild.pk}", "add_addresses": "member@example.com,\nguest@example.com"},
        )
        body = response.content.decode()
        assert response.status_code == 200
        assert f'name="recipients" value="user:{member.user_id}"' in body
        assert 'value="custom:guest@example.com"' in body
        assert "guest@example.com (email only)" in body
        assert 'x-init="adoptRow($el)"' in body
        assert "HX-Trigger" not in response.headers

    def it_says_why_an_address_was_refused(client: Client):
        guild = GuildFactory()
        _login_lead(client, guild, "addr_bad")
        response = client.post(
            reverse("hub_compose_add_addresses"), {"audience": f"guild:{guild.pk}", "add_addresses": "nope"}
        )
        assert "isn't an email address" in response.headers["HX-Trigger"]
        empty = client.post(
            reverse("hub_compose_add_addresses"), {"audience": f"guild:{guild.pk}", "add_addresses": ""}
        )
        assert "Type an email address" in empty.headers["HX-Trigger"]

    def it_refuses_a_guild_the_sender_does_not_lead(client: Client):
        _login_lead(client, GuildFactory(), "addr_other")
        other = GuildFactory()
        response = client.post(
            reverse("hub_compose_add_addresses"), {"audience": f"guild:{other.pk}", "add_addresses": "a@x.com"}
        )
        assert response.status_code == 403

    def it_refuses_a_site_announcement_even_for_an_admin(client: Client):
        _login_admin(client)
        response = client.post(reverse("hub_compose_add_addresses"), {"audience": "site", "add_addresses": "a@x.com"})
        assert response.status_code == 403


def describe_the_picker_markup():
    def _render(form) -> str:
        return render_to_string("hub/partials/_compose_recipient_picker.html", {"form": form})

    def it_renders_the_add_people_panel_with_the_saved_note_on_a_guild():
        guild = GuildFactory()
        roster = _roster_member(guild, "roster@example.com")
        outsider = _account("outsider@example.com")
        html = _render(_form(f"guild:{guild.pk}", editable_guilds=[guild]))
        assert 'x-data="composeAddPeople"' in html
        assert "data-compose-add-toggle" in html
        assert f'<input type="checkbox" value="user:{outsider.user_id}" data-compose-add-pick>' in html
        assert f'value="user:{roster.user_id}" data-compose-add-pick' not in html
        assert "data-compose-add-selected" in html
        assert 'id="compose-add-addresses"' in html
        assert "data-compose-add-saved-note" in html

    def it_renders_the_panel_without_the_saved_note_on_a_class():
        from classes.factories import ClassOfferingFactory

        offering = ClassOfferingFactory()
        html = _render(_form(f"class:{offering.pk}", editable_classes=[offering]))
        assert "data-compose-add-toggle" in html
        assert "data-compose-add-saved-note" not in html

    def it_renders_no_panel_on_a_site_announcement():
        html = _render(_form("site"))
        assert "data-compose-add-toggle" not in html
        assert "No members to list yet." in html


def describe_guild_settings_saved_members():
    def _payload(rows: list[dict[str, str]], *, initial: int) -> dict[str, str]:
        data = {
            "mailing_list-TOTAL_FORMS": str(len(rows)),
            "mailing_list-INITIAL_FORMS": str(initial),
            "mailing_list-MIN_NUM_FORMS": "0",
            "mailing_list-MAX_NUM_FORMS": "1000",
        }
        for i, row in enumerate(rows):
            for key, value in row.items():
                data[f"mailing_list-{i}-{key}"] = value
        return data

    def it_lists_a_saved_member_by_name_with_a_remove_button(client: Client):
        guild = GuildFactory()
        _login_lead(client, guild, "gs_lead")
        saved = _account("saved@example.com", name="Sam Saved")
        GuildMailingListEmailFactory(guild=guild, email="saved@example.com", user=saved.user)
        body = client.get(f"{reverse('hub_guild_edit', args=[guild.pk])}?tab=announcements").content.decode()
        assert f'data-mailing-list-member="{saved.user_id}"' in body
        assert "Sam Saved · saved@example.com" in body
        assert "Remove this member" in body

    def it_keeps_a_saved_members_address_and_removes_them_on_delete(client: Client):
        guild = GuildFactory()
        _login_lead(client, guild, "gs_remove")
        saved = _account("saved@example.com")
        row = GuildMailingListEmailFactory(guild=guild, email="saved@example.com", user=saved.user)
        url = reverse("hub_guild_mailing_list_save", args=[guild.pk])
        client.post(
            url,
            _payload(
                [{"id": str(row.pk), "email": "changed@example.com", "label": "Friend", "sort_order": "0"}], initial=1
            ),
        )
        row.refresh_from_db()
        assert (row.email, row.label) == ("saved@example.com", "Friend")
        client.post(url, _payload([{"id": str(row.pk), "label": "", "sort_order": "0", "DELETE": "on"}], initial=1))
        assert not GuildMailingListEmail.objects.filter(pk=row.pk).exists()
        form = _form(f"guild:{guild.pk}", editable_guilds=[guild])
        assert f"user:{saved.user_id}" not in {value for value, _label in form.recipient_choices}


def describe_a_lead_adding_nine_people():
    def it_saves_them_on_send_and_lists_them_for_the_other_lead_next_time(client: Client, mailoutbox):
        guild = GuildFactory()
        roster = _roster_member(guild, "roster@example.com")
        nine = [_account(f"writer{i}@example.com", name=f"Writer {i}") for i in range(9)]
        _login_lead(client, guild, "penina")
        picks = [f"user:{roster.user_id}"] + [f"user:{member.user_id}" for member in nine]
        response = client.post(
            reverse("hub_compose_send"), _guild_post(guild, recipients=[*picks, "custom:friend@example.com"])
        )
        assert response.status_code == 302
        assert guild.mailing_list_emails.filter(user__isnull=False).count() == 9
        assert guild.mailing_list_emails.filter(email="friend@example.com", user__isnull=True).exists()
        assert all(
            Notification.objects.filter(user=member.user, trigger="guild_announcement").exists() for member in nine
        )
        addresses = [address for message in mailoutbox for address in message.to]
        assert len(addresses) == len(set(addresses)) == 11

        # The guild's second lead opens a new announcement: all nine and the address are checked.
        other = Client()
        second = _login_lead(other, guild, "second_lead")
        guild.guild_lead = second.member
        guild.save(update_fields=["guild_lead"])
        form = other.get(f"{reverse('hub_compose')}?audience=guild:{guild.pk}").context["form"]
        checked = set(form.fields["recipients"].initial)
        assert {f"user:{member.user_id}" for member in nine} <= checked
        assert "custom:friend@example.com" in checked

    def it_does_not_save_anyone_from_a_draft_save(client: Client):
        guild = GuildFactory()
        added = _account("added@example.com")
        _login_lead(client, guild, "draft_lead")
        client.post(reverse("hub_compose_save_draft"), _guild_post(guild, recipients=[f"user:{added.user_id}"]))
        assert AnnouncementDraft.objects.filter(guild=guild).exists()
        assert not guild.mailing_list_emails.exists()

    def it_saves_nothing_when_a_saved_draft_resumes_its_typed_address(client: Client):
        guild = GuildFactory()
        user = _login_lead(client, guild, "resume_lead")
        draft = AnnouncementDraftFactory(
            author=user,
            audience=AnnouncementDraft.Audience.GUILD,
            guild=guild,
            recipient_selection={"users": [], "custom": ["typed@example.com"]},
        )
        body = client.get(reverse("hub_compose_resume", args=[draft.pk])).content.decode()
        assert 'value="custom:typed@example.com" class="pl-recipient-checklist__box" checked' in body
        assert not guild.mailing_list_emails.exists()
