"""Specs for #617 and #620: announcement recipients are named by the member and survive a resume.

#617: most login accounts carry no first or last name (the name is on the Member) and the
username is the email, so recipient rows read "email · email". Rows now read
"<display name> · <email>" (the email once when there is no name) and lists sort by that.

#620: a member picked from "Add a member" who is not on the roster is saved on the draft;
reopening the draft renders them as a checked row so the next save keeps them.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from factory.django import mute_signals

from hub.forms import (
    AnnouncementComposeForm,
    BetaFeedbackForm,
    _member_choice,
    _site_account_row,
    announcement_add_member_choices,
    announcement_recipient_choices,
    site_addable_member_choices,
)
from membership.models import AnnouncementDraft, Member
from tests.membership.factories import (
    AnnouncementDraftFactory,
    GuildFactory,
    GuildMembershipFactory,
    MemberFactory,
    MembershipPlanFactory,
)

pytestmark = pytest.mark.django_db

_seq = {"n": 0}


def _account(email: str, name: str = "", *, guild: Any = None, status: str = Member.Status.ACTIVE) -> Member:
    """A member named ``name`` whose login account has no first or last name and the email as username."""
    _seq["n"] += 1
    member = MemberFactory(full_legal_name=name, preferred_name="", status=status)
    with mute_signals(post_save):
        user = User.objects.create_user(username=email, email=email, last_login=timezone.now())
    member.user = user
    member.save(update_fields=["user"])
    if guild is not None:
        GuildMembershipFactory(guild=guild, member=member)
    return member


def _labels(choices: list[tuple[str, str]]) -> list[str]:
    return [label for _value, label in choices]


def _login_lead(client: Client, guild: Any) -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username="names_lead", email="names_lead@x.com", password="p")
    guild.guild_lead = user.member
    guild.save(update_fields=["guild_lead"])
    client.login(username="names_lead", password="p")
    return user


def _guild_draft(guild: Any, author: User | None = None, **kwargs: Any) -> AnnouncementDraft:
    extra = {"author": author} if author is not None else {}
    return AnnouncementDraftFactory(audience=AnnouncementDraft.Audience.GUILD, guild=guild, **extra, **kwargs)


def describe_member_choice():
    def it_names_the_member_once_and_the_email_once():
        member = _account("dana@example.com", "Dana Weaver")
        assert _member_choice(member.user) == (f"user:{member.user_id}", "Dana Weaver · dana@example.com")

    def it_shows_the_email_once_when_there_is_no_name():
        member = _account("nobody@example.com", "")
        assert _member_choice(member.user)[1] == "nobody@example.com"


def describe_announcement_add_member_choices():
    def it_orders_by_display_name_not_by_email():
        _account("a-zed@example.com", "Zed Zimmer")
        _account("z-amy@example.com", "Amy Archer")
        _account("m-mid@example.com", "")
        labels = _labels(announcement_add_member_choices())
        assert labels.index("Amy Archer · z-amy@example.com") < labels.index("Zed Zimmer · a-zed@example.com")
        assert "m-mid@example.com" in labels

    def it_reads_each_row_without_a_doubled_email(django_assert_max_num_queries: Any):
        for n in range(5):
            _account(f"person{n}@example.com", f"Person {n}")
        with django_assert_max_num_queries(1):
            labels = _labels(announcement_add_member_choices())
        assert all(label.count("@") == 1 for label in labels)


def describe_guild_recipient_checklist():
    def it_orders_members_by_display_name_with_mailing_list_addresses_after():
        from tests.membership.factories import GuildMailingListEmailFactory

        guild = GuildFactory()
        _account("a-zed@example.com", "Zed Zimmer", guild=guild)
        _account("z-amy@example.com", "Amy Archer", guild=guild)
        GuildMailingListEmailFactory(guild=guild, email="aaa-list@example.com")
        labels = _labels(announcement_recipient_choices(AnnouncementDraft.Audience.GUILD.value, guild))
        assert labels == ["Amy Archer · z-amy@example.com", "Zed Zimmer · a-zed@example.com", "aaa-list@example.com"]


def describe_site_addable_member_choices():
    def it_orders_by_display_name_and_keeps_the_status():
        _account("a-zed@example.com", "Zed Zimmer", status=Member.Status.FORMER)
        _account("z-amy@example.com", "Amy Archer", status=Member.Status.FORMER)
        labels = _labels(site_addable_member_choices())
        assert labels == ["Amy Archer · z-amy@example.com (Former)", "Zed Zimmer · a-zed@example.com (Former)"]


def describe_the_other_names_shown_next_to_an_email():
    def it_names_a_site_account_without_an_email_by_its_member():
        member = _account("someone@example.com", "Ned Noemail")
        User.objects.filter(pk=member.user_id).update(email="")
        user = User.objects.select_related("member").get(pk=member.user_id)
        assert _site_account_row(user, include_never_logged_in=False) == "Ned Noemail has no email address."

    def it_names_the_author_on_the_announcements_page_and_the_sender_line():
        author = _account("writer@example.com", "Wren Writer").user
        draft = AnnouncementDraftFactory(author=author, show_sender=True)
        assert draft.author_label == "Wren Writer"
        assert draft._sender_line() == "Wren Writer"

    def it_names_added_people_by_member_sorted_by_name():
        zed = _account("a-zed@example.com", "Zed Zimmer").user
        amy = _account("z-amy@example.com", "Amy Archer").user
        draft = AnnouncementDraftFactory(added_recipients={"users": [zed.pk, amy.pk], "custom": []})
        assert draft.added_labels == ["Amy Archer", "Zed Zimmer"]

    def it_names_the_feedback_sender_by_member(mailoutbox: Any):
        user = _account("fixer@example.com", "Fran Fixer").user
        form = BetaFeedbackForm(data={"category": "bug", "subject": "Broken", "message": "It broke."})
        assert form.is_valid(), form.errors
        form.submit(user=user)
        assert "From: Fran Fixer (fixer@example.com)" in mailoutbox[0].body

    def it_names_the_feedback_sender_by_email_once_without_a_name(mailoutbox: Any):
        user = _account("anon@example.com", "").user
        form = BetaFeedbackForm(data={"category": "bug", "subject": "Broken", "message": "It broke."})
        assert form.is_valid(), form.errors
        form.submit(user=user)
        assert "From: anon@example.com\n" in mailoutbox[0].body


def describe_added_member_rows():
    def it_shows_a_saved_off_roster_member_on_resume():
        guild = GuildFactory()
        on_roster = _account("roster@example.com", "Rory Roster", guild=guild)
        added = _account("added@example.com", "Ada Added")
        draft = _guild_draft(guild, recipient_selection={"users": [on_roster.user_id, added.user_id], "custom": []})
        form = AnnouncementComposeForm(
            initial={
                "audience": f"guild:{guild.pk}",
                "recipients": [f"user:{on_roster.user_id}", f"user:{added.user_id}"],
            },
            is_admin=True,
            editable_guilds=[guild],
            draft=draft,
        )
        assert form.added_member_rows == [(f"user:{added.user_id}", "Ada Added · added@example.com")]

    def it_has_no_rows_for_a_fresh_compose():
        guild = GuildFactory()
        _account("roster@example.com", "Rory Roster", guild=guild)
        form = AnnouncementComposeForm(
            initial={"audience": f"guild:{guild.pk}"}, is_admin=True, editable_guilds=[guild]
        )
        assert form.added_member_rows == []

    def it_keeps_posted_additions_on_a_re_render_and_drops_unknown_ones():
        guild = GuildFactory()
        on_roster = _account("roster@example.com", "Rory Roster", guild=guild)
        added = _account("added@example.com", "Ada Added")
        hidden = _account("hidden@example.com", "Hal Hidden")
        Member.objects.filter(pk=hidden.pk).update(hide_from_directory=True)
        data = {
            "audience": f"guild:{guild.pk}",
            "title": "",
            "body": "",
            "recipients": [f"user:{on_roster.user_id}", f"user:{added.user_id}", f"user:{hidden.user_id}", "user:x"],
        }
        form = AnnouncementComposeForm(data, is_admin=True, editable_guilds=[guild])
        assert form.added_member_rows == [(f"user:{added.user_id}", "Ada Added · added@example.com")]

    def it_still_shows_a_saved_member_who_was_hidden_since():
        guild = GuildFactory()
        kept = _account("kept@example.com", "Kip Kept")
        draft = _guild_draft(guild, recipient_selection={"users": [kept.user_id], "custom": []})
        Member.objects.filter(pk=kept.pk).update(hide_from_directory=True)
        form = AnnouncementComposeForm(
            initial={"audience": f"guild:{guild.pk}", "recipients": [f"user:{kept.user_id}"]},
            is_admin=True,
            editable_guilds=[guild],
            draft=draft,
        )
        assert form.added_member_rows == [(f"user:{kept.user_id}", "Kip Kept · kept@example.com")]


def describe_resuming_a_draft():
    def it_renders_a_saved_off_roster_member_as_a_checked_row(client: Client):
        guild = GuildFactory()
        lead = _login_lead(client, guild)
        on_roster = _account("roster@example.com", "Rory Roster", guild=guild)
        added = _account("added@example.com", "Ada Added")
        draft = _guild_draft(
            guild, author=lead, recipient_selection={"users": [on_roster.user_id, added.user_id], "custom": []}
        )
        content = client.get(reverse("hub_compose_resume", args=[draft.pk])).content.decode()
        added_area = content.split('id="compose-added-recipients"')[1].split("</div>")[0]
        assert f'value="user:{added.user_id}"' in added_area
        assert "checked" in added_area
        assert "Ada Added · added@example.com" in added_area
        assert f'value="user:{on_roster.user_id}"' not in added_area
