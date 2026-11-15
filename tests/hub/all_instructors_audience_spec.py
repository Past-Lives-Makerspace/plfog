"""BDD specs for #806 through the views: the composer offers admins an All Instructors audience.

Covers the admin only picker and its gate (a guild lead or an instructor who crafts the audience
gets 403), the composer's count, the inline send, and a draft that saves, resumes and lists on
Announcements as All Instructors.
"""

from __future__ import annotations

import json

import pytest
from django.contrib.auth.models import User
from django.contrib.messages import get_messages
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from classes.factories import ClassOfferingFactory
from hub.forms import discord_channel_choices, split_audience
from membership.models import AdminCapability, AnnouncementDraft, GuildAnnouncement, Member
from tests.membership.factories import GuildFactory, MemberFactory, MembershipPlanFactory

pytestmark = pytest.mark.django_db

_OPTION = '<option value="instructor"'


def _login(client: Client, username: str, *, fog_role: str = Member.FogRole.MEMBER) -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(
        username=username, email=f"{username}@example.com", password="pass", last_login=timezone.now()
    )
    member = user.member
    member.fog_role = fog_role
    member.save(update_fields=["fog_role"])
    member.sync_user_permissions()
    client.login(username=username, password="pass")
    return user


def _admin(client: Client) -> User:
    return _login(client, "admin806", fog_role=Member.FogRole.ADMIN)


def _lead(client: Client) -> User:
    user = _login(client, "lead806")
    GuildFactory(guild_lead=user.member)
    return user


def _instructor(client: Client) -> User:
    user = _login(client, "teacher806")
    Member.objects.filter(pk=user.member.pk).update(instructor_oriented_at=timezone.now())
    ClassOfferingFactory(instructor=user.member)
    return user


def _admin_viewing_as_member(client: Client) -> User:
    user = _admin(client)
    session = client.session
    session["view_as_role"] = "member"
    session.save()
    return user


def _class_approver(client: Client) -> User:
    user = _login(client, "approver806")
    user.member.admin_capabilities.create(capability=AdminCapability.Capability.CLASS_APPROVER)
    return user


def _instructor_draft() -> AnnouncementDraft:
    sender = User.objects.create_superuser(username="sender806", email="s@example.com", password="p")
    return AnnouncementDraft.objects.create(
        author=sender, audience=AnnouncementDraft.Audience.INSTRUCTORS, body="<p>x</p>"
    )


def _instructors() -> None:
    """Two instructor accounts (one never signed in) and one instructor with no account."""
    MembershipPlanFactory()
    for username, last_login in (("taught", timezone.now()), ("unsigned", None)):
        user = User.objects.create_user(username=username, email=f"{username}@example.com", last_login=last_login)
        Member.objects.filter(pk=user.member.pk).update(instructor_oriented_at=timezone.now())
    MemberFactory(_pre_signup_email="noaccount@example.com", instructor_slug="no-account")


def _post(**overrides: str) -> dict[str, str]:
    data = {
        "audience": "instructor",
        "body": "<p>Pay day moved.</p>",
        "send_email": "on",
        "discord_channel": "none",
        "mention": "none",
        "draft_pk": "",
    }
    data.update(overrides)
    return data


def describe_the_all_instructors_audience():
    def it_splits_the_instructor_value():
        assert split_audience("instructor") == ("instructor", None, None)

    def it_offers_only_the_shared_discord_channels():
        values = [value for value, _label in discord_channel_choices("instructor")]
        assert GuildAnnouncement.DiscordChannel.GUILD not in values
        assert GuildAnnouncement.DiscordChannel.NONE in values

    def it_is_offered_to_an_admin_right_after_guild_leads(client: Client):
        _admin(client)
        content = client.get(reverse("hub_compose")).content.decode()
        leads = content.index('<option value="leads"')
        instructors = content.index(_OPTION)
        assert leads < instructors
        assert "<option" not in content[content.index(">", leads) : instructors]
        assert ">All Instructors</option>" in content

    @pytest.mark.parametrize(
        "login",
        [_lead, _instructor, _admin_viewing_as_member, _class_approver],
        ids=["guild lead", "instructor", "admin viewing as a member", "class approver"],
    )
    def describe_for_a_member_who_is_not_an_admin():
        def it_is_not_offered(client: Client, login):
            login(client)
            assert _OPTION not in client.get(reverse("hub_compose")).content.decode()

        @pytest.mark.parametrize("route", ["hub_compose_send", "hub_compose_save_draft"])
        def it_refuses_a_crafted_post(client: Client, login, route: str):
            login(client)
            assert client.post(reverse(route), _post()).status_code == 403
            assert AnnouncementDraft.objects.count() == 0

        def it_refuses_the_count(client: Client, login):
            login(client)
            assert client.get(reverse("hub_compose_count"), {"audience": "instructor"}).status_code == 403

        def it_cannot_resume_an_instructor_draft_by_pk(client: Client, login):
            draft = _instructor_draft()
            login(client)
            assert client.get(reverse("hub_compose_resume", args=[draft.pk])).status_code == 404

        def it_cannot_delete_an_instructor_draft_by_pk(client: Client, login):
            draft = _instructor_draft()
            login(client)
            assert client.post(reverse("hub_compose_delete_draft", args=[draft.pk])).status_code == 404
            assert AnnouncementDraft.objects.filter(pk=draft.pk).exists()

        def it_does_not_list_an_instructor_draft(client: Client, login):
            draft = _instructor_draft()
            login(client)
            listed = client.get(reverse("hub_announcements")).content.decode()
            assert reverse("hub_compose_resume", args=[draft.pk]) not in listed

    def describe_the_count():
        def it_counts_accounts_and_the_userless_instructor_on_a_fresh_compose(client: Client):
            _instructors()
            _admin(client)
            content = client.get(reverse("hub_compose"), {"audience": "instructor"}).content.decode()
            assert "recipientCount: 3," in content
            assert "instructorReach: { users: 2, emailOnly: 1 }" in content

        def it_drops_the_userless_instructor_from_a_draft_with_email_off(client: Client):
            _instructors()
            admin = _admin(client)
            draft = AnnouncementDraft.objects.create(
                author=admin, audience=AnnouncementDraft.Audience.INSTRUCTORS, body="<p>x</p>", send_email=False
            )
            content = client.get(reverse("hub_compose_resume", args=[draft.pk])).content.decode()
            assert "recipientCount: 2," in content

        def it_pushes_the_live_count_on_an_audience_change(client: Client):
            _instructors()
            _admin(client)
            response = client.get(reverse("hub_compose_count"), {"audience": "instructor"})
            assert json.loads(response["HX-Trigger"]) == {"compose-count": {"count": 3}}

    def it_sends_inline_for_an_admin(client: Client):
        _instructors()
        _admin(client)
        response = client.post(reverse("hub_compose_send"), _post())
        draft = AnnouncementDraft.objects.get()
        assert draft.audience == AnnouncementDraft.Audience.INSTRUCTORS
        assert draft.title == "Instructors Announcement"
        assert draft.sent_at is not None
        assert draft.send_requested_at is None
        assert [str(m) for m in get_messages(response.wsgi_request)] == ["Email sent to 3 recipient(s)."]

    def it_titles_an_urgent_send(client: Client):
        _admin(client)
        client.post(reverse("hub_compose_send"), _post(mark_as_urgent="on"))
        assert AnnouncementDraft.objects.get().title == "Urgent: Instructors Announcement"

    def describe_a_draft():
        def it_saves_resumes_selected_and_lists_as_all_instructors(client: Client):
            _admin(client)
            assert client.post(reverse("hub_compose_save_draft"), _post()).status_code == 200
            draft = AnnouncementDraft.objects.get()
            assert draft.audience == AnnouncementDraft.Audience.INSTRUCTORS
            assert draft.sent_at is None
            resumed = client.get(reverse("hub_compose_resume", args=[draft.pk])).content.decode()
            assert '<option value="instructor" selected>' in resumed
            listed = client.get(reverse("hub_announcements")).content.decode()
            assert reverse("hub_compose_resume", args=[draft.pk]) in listed
            assert '<td data-label="Audience">All Instructors</td>' in listed
