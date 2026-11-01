"""BDD specs for #1042 part 1 through the views: the composer's My past students audience.

Covers the gate (an instructor of any class sees it, a member who teaches nothing does not and is
refused when they post it, an admin only when they teach), the Recipients step (names, count,
untick, no adding), the live count, the in request send with no Discord post, and the preview's
stop line.
"""

from __future__ import annotations

import json
import re

import pytest
from django.contrib.auth.models import User
from django.core import mail
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from classes.factories import ClassOfferingFactory, RegistrationFactory
from classes.models import ClassOffering, Registration
from core.models import EventDelivery
from hub.forms import discord_channel_choices, split_audience
from membership.models import AnnouncementDraft, GuildAnnouncement, Member
from tests.membership.factories import GuildFactory, MembershipPlanFactory

pytestmark = pytest.mark.django_db

_OPTION = '<option value="students"'


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


def _student(username: str) -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, email=f"{username}@example.com", password="x")
    Member.objects.filter(pk=user.member.pk).update(preferred_name=username.title())
    return user


def _teaches(user: User, *, status: str = ClassOffering.Status.PUBLISHED) -> ClassOffering:
    return ClassOfferingFactory(instructor=user.member, image="", gallery=0, status=status)


def _took(offering: ClassOffering, *, user: User | None = None, email: str = "") -> None:
    RegistrationFactory(
        class_offering=offering,
        member=user.member if user is not None else None,
        email=email or (user.email if user is not None else "guest@example.com"),
        first_name="Gail",
        last_name="Guest",
        status=Registration.Status.CONFIRMED,
    )


def _instructor_with_students(client: Client) -> tuple[User, User, User]:
    """A signed in instructor (granted, one published class), with two student accounts and one guest."""
    teacher = _login(client, "teacher1042")
    Member.objects.filter(pk=teacher.member.pk).update(instructor_oriented_at=timezone.now())
    offering = _teaches(teacher)
    ada, ben = _student("ada"), _student("ben")
    _took(offering, user=ada)
    _took(offering, user=ben)
    _took(offering, email="guest@example.com")
    return teacher, ada, ben


def _checked(html: str) -> set[str]:
    """The recipient checkbox values the page renders checked."""
    boxes = re.findall(r'<input[^>]*name="recipients"[^>]*>', html)
    return {re.search(r'value="([^"]+)"', box).group(1) for box in boxes if " checked" in box}


def _post(client: Client, name: str, **data: object) -> object:
    return client.post(reverse(name), {"audience": "students", "body": "<p>Workshop Saturday.</p>", **data})


def describe_the_audience_choice():
    def it_is_offered_to_a_member_who_teaches_any_class():
        client = Client()
        _instructor_with_students(client)
        response = client.get(reverse("hub_compose"))
        assert response.status_code == 200
        assert f"{_OPTION}>My Past Students</option>" in response.content.decode()

    def it_is_offered_to_an_instructor_whose_classes_are_all_archived():
        client = Client()
        teacher = _login(client, "archived1042")
        Member.objects.filter(pk=teacher.member.pk).update(instructor_slug="archived1042")
        _teaches(teacher, status=ClassOffering.Status.ARCHIVED)
        assert _OPTION in client.get(reverse("hub_compose")).content.decode()

    def it_is_not_offered_to_a_guild_lead_who_teaches_nothing():
        client = Client()
        lead = _login(client, "lead1042")
        GuildFactory(guild_lead=lead.member)
        assert _OPTION not in client.get(reverse("hub_compose")).content.decode()

    def it_is_offered_to_an_admin_only_when_they_teach():
        client = Client()
        admin = _login(client, "admin1042", fog_role=Member.FogRole.ADMIN)
        assert _OPTION not in client.get(reverse("hub_compose")).content.decode()
        _teaches(admin)
        assert _OPTION in client.get(reverse("hub_compose")).content.decode()

    def it_splits_and_never_offers_a_discord_channel():
        assert split_audience("students") == ("students", None, None)
        assert discord_channel_choices("students") == []

    def describe_when_posted_by_someone_who_teaches_nothing():
        @pytest.mark.parametrize("name", ["hub_compose_send", "hub_compose_save_draft", "hub_compose_count"])
        def it_is_refused(name: str):
            client = Client()
            lead = _login(client, "lead1042")
            GuildFactory(guild_lead=lead.member)
            assert _post(client, name).status_code == 403
            assert not AnnouncementDraft.objects.exists()

        def it_is_refused_to_an_admin_who_teaches_nothing():
            client = Client()
            _login(client, "admin1042", fog_role=Member.FogRole.ADMIN)
            assert _post(client, "hub_compose_send").status_code == 403


def describe_the_recipients_step():
    def it_lists_everyone_with_the_count_and_no_add_people():
        client = Client()
        _teacher, ada, ben = _instructor_with_students(client)
        html = client.get(reverse("hub_compose"), {"audience": "students"}).content.decode()
        assert f'value="user:{ada.pk}"' in html
        assert f'value="user:{ben.pk}"' in html
        assert 'value="custom:guest@example.com"' in html
        assert "Gail Guest · guest@example.com" in html
        assert _checked(html) == {f"user:{ada.pk}", f"user:{ben.pk}", "custom:guest@example.com"}
        assert "recipientCount: 3," in html
        assert "data-compose-students-note" in html
        assert "data-compose-add-people" not in html

    def it_carries_a_roster_hand_off_in_the_url():
        client = Client()
        _teacher, ada, ben = _instructor_with_students(client)
        html = client.get(
            reverse("hub_compose"), {"audience": "students", "recipients": [f"user:{ada.pk}"]}
        ).content.decode()
        assert _checked(html) == {f"user:{ada.pk}"}
        assert f'value="user:{ben.pk}"' in html

    def it_counts_live_on_an_audience_change():
        client = Client()
        _instructor_with_students(client)
        response = client.get(reverse("hub_compose_count"), {"audience": "students"})
        assert json.loads(response["HX-Trigger"]) == {"compose-count": {"count": 3}}
        assert 'value="custom:guest@example.com"' in response.content.decode()


def describe_sending():
    def it_reaches_only_the_people_left_ticked_in_the_request():
        client = Client()
        teacher, ada, ben = _instructor_with_students(client)
        response = _post(
            client,
            "hub_compose_send",
            recipients=[f"user:{ada.pk}", "custom:guest@example.com"],
            send_email="on",
            push_enabled="on",
            discord_enabled="on",
            discord_channel=GuildAnnouncement.DiscordChannel.NONE,
            mention="none",
        )
        assert response.status_code == 302
        draft = AnnouncementDraft.objects.get()
        assert (draft.audience, draft.author, draft.sent_at is not None) == ("students", teacher, True)
        assert sorted(message.to[0] for message in mail.outbox) == ["ada@example.com", "guest@example.com"]
        assert not EventDelivery.objects.filter(target_ref=f"user:{ben.pk}").exists()
        assert not EventDelivery.objects.filter(channel="discord").exists()

    def it_drops_a_person_added_who_never_took_a_class():
        client = Client()
        _teacher, ada, _ben = _instructor_with_students(client)
        stranger = _student("stranger")
        _post(
            client,
            "hub_compose_send",
            recipients=[f"user:{ada.pk}", f"user:{stranger.pk}", "custom:typed@example.com"],
            send_email="on",
            mention="none",
        )
        assert [message.to[0] for message in mail.outbox] == ["ada@example.com"]

    def it_lists_on_email_members_under_whose_students_they_are():
        client = Client()
        _instructor_with_students(client)
        _post(client, "hub_compose_send", send_email="on", mention="none")
        html = client.get(reverse("hub_announcements"), {"tab": "sent"}).content.decode()
        assert AnnouncementDraft.objects.get().audience_label.startswith("Past students of ")
        assert AnnouncementDraft.objects.get().audience_label in html


def describe_the_preview():
    def it_shows_the_stop_line():
        client = Client()
        teacher, _ada, _ben = _instructor_with_students(client)
        response = _post(client, "hub_compose_preview", show_sender="on")
        assert response.status_code == 200
        assert "Stop emails from" in response.content.decode()
        assert "/classes/stop-emails/__PL_STOP_" in response.content.decode()
        assert teacher.email not in response.content.decode().split("data-past-students-stop", 1)[1].split("</p>", 1)[0]
