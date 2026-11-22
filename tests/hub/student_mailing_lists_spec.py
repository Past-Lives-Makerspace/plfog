"""BDD specs for an instructor's lists on Email Members' Mailing Lists tab (#1294).

Felix: instructors "DO have mailing lists: their classes and the mailing list of all the students
who ever took their classes". Your Students lists My Past Students and each class's roster, each
opening the composer on that audience; guild lists are unchanged.
"""

from __future__ import annotations

import re

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from classes.factories import ClassOfferingFactory, RegistrationFactory
from classes.models import ClassOffering, PastStudentOptOut, Registration
from membership.models import Member
from tests.membership.factories import GuildFactory, MembershipPlanFactory

pytestmark = pytest.mark.django_db

_TAB = {"tab": "mailing-lists"}


def _teacher(client: Client, username: str, *, granted: bool = True) -> Member:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, email=f"{username}@x.com", password="p")
    member = user.member  # type: ignore[attr-defined]
    member.instructor_oriented_at = timezone.now() if granted else None
    member.instructor_slug = username
    member.save(update_fields=["instructor_oriented_at", "instructor_slug"])
    client.login(username=username, password="p")
    return member


def _confirmed(offering: ClassOffering, email: str) -> Registration:
    return RegistrationFactory(class_offering=offering, email=email, status=Registration.Status.CONFIRMED)


def _page(client: Client, **params: str) -> str:
    response = client.get(reverse("hub_announcements"), params)
    assert response.status_code == 200
    return response.content.decode()


def _student_rows(html: str) -> dict[str, str]:
    """Each Your Students row's key and its count line."""
    rows = re.findall(r'data-student-list="([^"]+)".*?data-student-list-count>([^<]+)<', html, re.S)
    return {key: count.strip() for key, count in rows}


def describe_your_students():
    def it_lists_past_students_and_each_class_with_people_on_it(client: Client):
        member = _teacher(client, "ms_teach")
        older = ClassOfferingFactory(instructor=member, title="Older Class", status=ClassOffering.Status.PUBLISHED)
        newer = ClassOfferingFactory(instructor=member, title="Newer Class", status=ClassOffering.Status.PUBLISHED)
        ClassOfferingFactory(instructor=member, title="Empty Class", status=ClassOffering.Status.PUBLISHED)
        _confirmed(older, "a@x.com")
        _confirmed(older, "b@x.com")
        _confirmed(newer, "a@x.com")
        RegistrationFactory(class_offering=newer, email="w@x.com", status=Registration.Status.WAITLISTED)
        html = _page(client, **_TAB)
        assert "data-student-lists" in html
        assert _student_rows(html) == {
            "past-students": "2 people, everyone who took any of your classes. Anyone who stopped your emails is left off.",
            f"class-{newer.pk}": "1 registered",
            f"class-{older.pk}": "2 registered",
        }
        assert list(_student_rows(html)) == ["past-students", f"class-{newer.pk}", f"class-{older.pk}"]
        assert f'href="{reverse("hub_compose")}?audience=students"' in html
        assert f'href="{reverse("hub_compose")}?audience=class:{newer.pk}&amp;lock=1"' in html

    def it_leaves_off_anyone_who_stopped_its_emails(client: Client):
        member = _teacher(client, "ms_optout")
        offering = ClassOfferingFactory(instructor=member, status=ClassOffering.Status.PUBLISHED)
        _confirmed(offering, "stays@x.com")
        _confirmed(offering, "left@x.com")
        PastStudentOptOut.objects.record(instructor=member, email="left@x.com")
        assert _student_rows(_page(client, **_TAB))["past-students"].startswith("1 person,")

    def it_counts_the_lists_on_the_tab_from_every_tab(client: Client):
        member = _teacher(client, "ms_count")
        offering = ClassOfferingFactory(instructor=member, status=ClassOffering.Status.PUBLISHED)
        _confirmed(offering, "c@x.com")
        for tab in ("drafts", "sent", "polls"):
            html = _page(client, tab=tab)
            assert re.search(r'data-announcements-count="mailing-lists">2<', html), tab
            assert "data-student-lists" not in html

    def it_sits_under_the_guild_lists_for_a_lead_who_teaches(client: Client):
        member = _teacher(client, "ms_lead")
        guild = GuildFactory(name="Lead Guild", guild_lead=member)
        offering = ClassOfferingFactory(instructor=member, status=ClassOffering.Status.PUBLISHED)
        _confirmed(offering, "d@x.com")
        html = _page(client, **_TAB)
        assert f'data-mailing-list-guild="{guild.pk}"' in html
        assert html.index("data-mailing-lists") < html.index("data-student-lists")
        assert re.search(r'data-announcements-count="mailing-lists">3<', html)

    def it_shows_a_guild_lead_who_teaches_nothing_its_guild_lists_alone(client: Client):
        MembershipPlanFactory()
        user = User.objects.create_user(username="ms_plainlead", email="pl@x.com", password="p")
        GuildFactory(guild_lead=user.member)  # type: ignore[attr-defined]
        client.login(username="ms_plainlead", password="p")
        html = _page(client, **_TAB)
        assert "data-mailing-lists" in html
        assert "data-student-lists" not in html

    def it_shows_an_empty_list_with_email_disabled_before_anyone_registered(client: Client):
        # Felix: Your Students always shows for an instructor; an empty list offers no send.
        member = _teacher(client, "ms_nobody")
        ClassOfferingFactory(instructor=member, status=ClassOffering.Status.PUBLISHED)
        html = _page(client, **_TAB)
        assert "data-student-lists" in html
        assert _student_rows(html) == {"past-students": "0 people. No students yet."}
        assert "data-student-list-disabled" in html
        assert "?audience=students" not in html
        assert re.search(r'data-announcements-count="mailing-lists">1<', html)

    def it_shows_an_instructor_who_teaches_no_class_yet_the_empty_list(client: Client):
        _teacher(client, "ms_noclass")
        html = _page(client, **_TAB)
        assert _student_rows(html) == {"past-students": "0 people. No students yet."}

    def it_disables_past_students_when_everyone_stopped_its_emails(client: Client):
        member = _teacher(client, "ms_allstopped")
        offering = ClassOfferingFactory(instructor=member, status=ClassOffering.Status.PUBLISHED)
        _confirmed(offering, "gone@x.com")
        PastStudentOptOut.objects.record(instructor=member, email="gone@x.com")
        rows = _student_rows(_page(client, **_TAB))
        assert rows["past-students"] == "0 people. No students yet."
        assert rows[f"class-{offering.pk}"] == "1 registered"

    def it_keeps_past_students_but_not_classes_it_may_no_longer_email(client: Client):
        # Revoked teaching: the composer still offers My Past Students, but no class roster.
        member = _teacher(client, "ms_revoked", granted=False)
        offering = ClassOfferingFactory(instructor=member, status=ClassOffering.Status.PUBLISHED)
        _confirmed(offering, "e@x.com")
        rows = _student_rows(_page(client, **_TAB))
        assert list(rows) == ["past-students"]

    def it_lists_a_class_a_granted_instructor_teaches_whatever_its_status(client: Client):
        member = _teacher(client, "ms_archived")
        offering = ClassOfferingFactory(instructor=member, status=ClassOffering.Status.ARCHIVED)
        _confirmed(offering, "f@x.com")
        assert f"class-{offering.pk}" in _student_rows(_page(client, **_TAB))

    def it_gives_a_lock_only_instructor_the_mailing_lists_tab_alone(client: Client):
        # No profile slug and only a draft with a registrant: no compose rights, but a list to email.
        MembershipPlanFactory()
        user = User.objects.create_user(username="ms_lockonly", email="lo@x.com", password="p")
        member = user.member  # type: ignore[attr-defined]
        member.instructor_oriented_at = timezone.now()
        member.save(update_fields=["instructor_oriented_at"])
        offering = ClassOfferingFactory(instructor=member, status=ClassOffering.Status.DRAFT)
        _confirmed(offering, "g@x.com")
        client.login(username="ms_lockonly", password="p")
        html = _page(client)
        assert "data-student-lists" in html
        assert 'data-announcements-tab="drafts"' not in html


def describe_counting_off_the_tab():
    """#1297 review: Drafts, Sent and Polls count the lists without asking each class's gate."""

    def it_asks_no_class_gate_off_the_tab_for_a_member_without_the_grant(client: Client, monkeypatch):
        import hub.views

        member = _teacher(client, "ms_count_ungranted", granted=False)
        guild = GuildFactory(guild_lead=member)
        from classes.factories import CategoryFactory

        _confirmed(
            ClassOfferingFactory(instructor=member, category=CategoryFactory(guild=guild)),
            "in-guild@x.com",
        )
        _confirmed(ClassOfferingFactory(instructor=member), "outside@x.com")
        asked: list[int] = []
        real = hub.views._can_announce_to_class

        def counting(request, offering):  # noqa: ANN001, ANN202
            asked.append(offering.pk)
            return real(request, offering)

        monkeypatch.setattr(hub.views, "_can_announce_to_class", counting)
        drafts = _page(client)
        assert asked == []
        badge = re.search(r'data-announcements-count="mailing-lists">(\d+)<', drafts)
        assert badge is not None
        tab = _page(client, **_TAB)
        listed = re.search(r'data-announcements-count="mailing-lists">(\d+)<', tab)
        assert listed is not None
        # One guild list, Past Students, and the one class in the guild they lead.
        assert badge.group(1) == listed.group(1) == "3"

    def it_counts_every_class_for_an_admin_without_the_grant(client: Client):
        from django.test import RequestFactory

        from hub.view_as import ViewAs
        from hub.views import _guild_reachable_count

        member = _teacher(client, "ms_count_admin", granted=False)
        request = RequestFactory().get("/")
        request.view_as = ViewAs(actual=frozenset({"admin", "member"}), picked=None)  # type: ignore[attr-defined]
        taught = [ClassOfferingFactory(instructor=member), ClassOfferingFactory(instructor=member)]
        assert _guild_reachable_count(request, member, taught) == 2
        request.view_as = None  # type: ignore[attr-defined]
        assert _guild_reachable_count(request, member, taught) == 0


def describe_the_opt_out_line():
    def it_sits_on_past_students_only_never_on_the_intro_or_a_class(client: Client):
        # Felix (#1297): opt outs apply to Past Students; a class roster send is unchanged.
        member = _teacher(client, "ms_optout_copy")
        offering = ClassOfferingFactory(instructor=member, status=ClassOffering.Status.PUBLISHED)
        _confirmed(offering, "copy@x.com")
        html = _page(client, **_TAB)
        intro = re.search(r'pl-mailing-lists__intro">([^<]+)<', html[html.index("data-student-lists") :])
        assert intro is not None
        assert "stopped" not in intro.group(1)
        rows = _student_rows(html)
        assert rows["past-students"].endswith("Anyone who stopped your emails is left off.")
        assert "stopped" not in rows[f"class-{offering.pk}"]
