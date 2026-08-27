"""BDD specs for the consolidated Registrations list: role scope, filters, CSV export."""

from __future__ import annotations

import pytest
from django.urls import reverse

from classes.factories import (
    CategoryFactory,
    ClassOfferingFactory,
    InstructorFactory,
    RegistrationFactory,
    UserFactory,
)
from classes.models import Registration

pytestmark = pytest.mark.django_db


def _instructor_with_class(username="teacher@example.com", slug="t-class"):
    user = UserFactory(username=username)
    member = InstructorFactory(user=user)
    offering = ClassOfferingFactory(slug=slug, instructor=member)
    return user, offering


def describe_admin_registrations_scope():
    def it_lets_an_instructor_see_only_their_classes_registrations(client):
        user, offering = _instructor_with_class()
        RegistrationFactory(class_offering=offering, email="mine@example.com")
        RegistrationFactory(email="theirs@example.com")  # a different class
        client.force_login(user)
        response = client.get(reverse("classes:admin_registrations"))
        assert response.status_code == 200
        assert b"mine@example.com" in response.content
        assert b"theirs@example.com" not in response.content

    def it_forbids_a_plain_member_with_no_classes(member_user, client):
        client.force_login(member_user)
        response = client.get(reverse("classes:admin_registrations"))
        assert response.status_code == 403

    def it_shows_the_registrations_tab_to_an_instructor(client):
        user, _ = _instructor_with_class(username="teach2@example.com", slug="t2")
        client.force_login(user)
        response = client.get(reverse("classes:admin_registrations"))
        assert b"Registrations" in response.content


def describe_admin_registrations_filters():
    def it_filters_by_status(admin_user, client):
        offering = ClassOfferingFactory(slug="f-status")
        RegistrationFactory(
            class_offering=offering, email="confirmed@example.com", status=Registration.Status.CONFIRMED
        )
        RegistrationFactory(class_offering=offering, email="pending@example.com", status=Registration.Status.PENDING)
        client.force_login(admin_user)
        response = client.get(reverse("classes:admin_registrations"), {"status": Registration.Status.CONFIRMED})
        assert b"confirmed@example.com" in response.content
        assert b"pending@example.com" not in response.content

    def it_ignores_an_unknown_status(admin_user, client):
        offering = ClassOfferingFactory(slug="f-badstatus")
        RegistrationFactory(class_offering=offering, email="conf@example.com", status=Registration.Status.CONFIRMED)
        RegistrationFactory(class_offering=offering, email="pend@example.com", status=Registration.Status.PENDING)
        client.force_login(admin_user)
        response = client.get(reverse("classes:admin_registrations"), {"status": "bogus"})
        assert b"conf@example.com" in response.content
        assert b"pend@example.com" in response.content

    def it_filters_by_class(admin_user, client):
        a = ClassOfferingFactory(slug="f-class-a")
        b = ClassOfferingFactory(slug="f-class-b")
        RegistrationFactory(class_offering=a, email="in-a@example.com")
        RegistrationFactory(class_offering=b, email="in-b@example.com")
        client.force_login(admin_user)
        response = client.get(reverse("classes:admin_registrations"), {"class": a.pk})
        assert b"in-a@example.com" in response.content
        assert b"in-b@example.com" not in response.content

    def it_ignores_a_non_numeric_class(admin_user, client):
        offering = ClassOfferingFactory(slug="f-badclass")
        RegistrationFactory(class_offering=offering, email="shown@example.com")
        client.force_login(admin_user)
        response = client.get(reverse("classes:admin_registrations"), {"class": "abc"})
        assert b"shown@example.com" in response.content


def describe_admin_registrations_export():
    def it_streams_a_csv_for_admins(admin_user, client):
        RegistrationFactory(email="export-me@example.com")
        client.force_login(admin_user)
        response = client.get(reverse("classes:admin_registrations_export"))
        assert response["Content-Type"] == "text/csv"
        body = b"".join(response.streaming_content).decode()
        assert "export-me@example.com" in body

    def it_scopes_the_export_to_an_instructors_classes(client):
        user, offering = _instructor_with_class(username="teach3@example.com", slug="t3")
        RegistrationFactory(class_offering=offering, email="mine-exp@example.com")
        RegistrationFactory(email="theirs-exp@example.com")
        client.force_login(user)
        response = client.get(reverse("classes:admin_registrations_export"))
        body = b"".join(response.streaming_content).decode()
        assert "mine-exp@example.com" in body
        assert "theirs-exp@example.com" not in body

    def it_honors_the_status_filter_in_the_export(admin_user, client):
        offering = ClassOfferingFactory(slug="exp-status")
        RegistrationFactory(class_offering=offering, email="c-exp@example.com", status=Registration.Status.CONFIRMED)
        RegistrationFactory(class_offering=offering, email="p-exp@example.com", status=Registration.Status.PENDING)
        client.force_login(admin_user)
        response = client.get(reverse("classes:admin_registrations_export"), {"status": Registration.Status.CONFIRMED})
        body = b"".join(response.streaming_content).decode()
        assert "c-exp@example.com" in body
        assert "p-exp@example.com" not in body


def describe_mine_filter():
    def it_filters_to_registrations_for_classes_i_teach_or_authored(admin_user, client):
        me = admin_user.member
        taught = ClassOfferingFactory(slug="reg-mine-taught", instructor=me)
        authored = ClassOfferingFactory(slug="reg-mine-authored", created_by=me)  # taught by someone else
        other = ClassOfferingFactory(slug="reg-not-mine")
        RegistrationFactory(class_offering=taught, email="taught@example.com")
        RegistrationFactory(class_offering=authored, email="authored@example.com")
        RegistrationFactory(class_offering=other, email="other@example.com")
        client.force_login(admin_user)
        response = client.get(reverse("classes:admin_registrations") + "?mine=1")
        assert b"taught@example.com" in response.content
        assert b"authored@example.com" in response.content
        assert b"other@example.com" not in response.content

    def it_composes_with_status_and_class_filters(admin_user, client):
        me = admin_user.member
        a = ClassOfferingFactory(slug="reg-mine-a", instructor=me)
        b = ClassOfferingFactory(slug="reg-mine-b", instructor=me)
        RegistrationFactory(class_offering=a, email="a-confirmed@example.com", status=Registration.Status.CONFIRMED)
        RegistrationFactory(class_offering=a, email="a-pending@example.com", status=Registration.Status.PENDING)
        RegistrationFactory(class_offering=b, email="b-confirmed@example.com", status=Registration.Status.CONFIRMED)
        client.force_login(admin_user)
        response = client.get(
            reverse("classes:admin_registrations"),
            {"mine": "1", "status": Registration.Status.CONFIRMED, "class": a.pk},
        )
        assert b"a-confirmed@example.com" in response.content
        assert b"a-pending@example.com" not in response.content  # wrong status
        assert b"b-confirmed@example.com" not in response.content  # wrong class

    def it_keeps_plain_instructor_param_working(admin_user, client):
        # instructor=<pk> stays the plain instructor filter — pre-change bookmarks work.
        teacher = InstructorFactory(full_legal_name="Solo Teacher", instructor_slug="solo-teacher")
        offering = ClassOfferingFactory(slug="reg-solo", instructor=teacher)
        other = ClassOfferingFactory(slug="reg-solo-other")
        RegistrationFactory(class_offering=offering, email="solo-reg@example.com")
        RegistrationFactory(class_offering=other, email="other-reg@example.com")
        client.force_login(admin_user)
        response = client.get(reverse("classes:admin_registrations"), {"instructor": teacher.pk})
        assert b"solo-reg@example.com" in response.content
        assert b"other-reg@example.com" not in response.content

    def it_shows_the_toggle_without_an_instructor_slug(admin_user, client):
        assert not admin_user.member.instructor_slug
        client.force_login(admin_user)
        response = client.get(reverse("classes:admin_registrations"))
        assert b"My Classes" in response.content

    def it_shows_the_toggle_to_a_non_admin_instructor(client):
        user, _ = _instructor_with_class(username="teach-toggle@example.com", slug="reg-tt")
        client.force_login(user)
        response = client.get(reverse("classes:admin_registrations"))
        assert b"My Classes" in response.content

    def it_narrows_a_guild_leads_scoped_view(client):
        from tests.membership.factories import GuildFactory

        user = UserFactory(username="guild-lead@example.com")
        lead = InstructorFactory(user=user, full_legal_name="The Lead", instructor_slug="the-lead")
        guild = GuildFactory(guild_lead=lead)
        cat = CategoryFactory(guild=guild)
        # In the lead's guild but taught by someone else — visible via scope, not mine.
        others = ClassOfferingFactory(slug="gl-others", category=cat)
        # The lead's own class — both scoped and mine.
        own = ClassOfferingFactory(slug="gl-own", instructor=lead)
        RegistrationFactory(class_offering=others, email="scoped-not-mine@example.com")
        RegistrationFactory(class_offering=own, email="lead-own@example.com")
        client.force_login(user)
        # Without mine, the lead's scoped view shows both.
        wide = client.get(reverse("classes:admin_registrations"))
        assert b"scoped-not-mine@example.com" in wide.content
        assert b"lead-own@example.com" in wide.content
        # mine=1 narrows to strictly teach-or-authored.
        narrow = client.get(reverse("classes:admin_registrations") + "?mine=1")
        assert b"lead-own@example.com" in narrow.content
        assert b"scoped-not-mine@example.com" not in narrow.content

    def it_returns_empty_for_a_user_with_no_member(client):
        # Superuser passes the admin gate with no linked Member; a NULL-instructor/
        # NULL-author class's registrations must not leak to a memberless "mine".
        user = UserFactory(username="reg-super-nomember@example.com", is_superuser=True, is_staff=True)
        user.member.delete()
        orphan = ClassOfferingFactory(slug="reg-orphan", instructor=None, created_by=None)
        RegistrationFactory(class_offering=orphan, email="orphan-reg@example.com")
        client.force_login(user)
        response = client.get(reverse("classes:admin_registrations") + "?mine=1")
        assert response.status_code == 200
        assert b"orphan-reg@example.com" not in response.content

    def it_ignores_bogus_mine_values(admin_user, client):
        me = admin_user.member
        mine = ClassOfferingFactory(slug="reg-bogus-mine", instructor=me)
        other = ClassOfferingFactory(slug="reg-bogus-other")
        RegistrationFactory(class_offering=mine, email="bogus-mine@example.com")
        RegistrationFactory(class_offering=other, email="bogus-other@example.com")
        client.force_login(admin_user)
        response = client.get(reverse("classes:admin_registrations") + "?mine=yes")
        assert response.context["mine_active"] is False
        assert b"bogus-mine@example.com" in response.content
        assert b"bogus-other@example.com" in response.content

    def it_renders_a_hidden_mine_input_when_active(admin_user, client):
        client.force_login(admin_user)
        response = client.get(reverse("classes:admin_registrations") + "?mine=1")
        assert b'<input type="hidden" name="mine" value="1">' in response.content

    def it_renders_the_mine_empty_state_with_a_clear_link(admin_user, client):
        # Admin owns no classes; one foreign registration exists so the list is not
        # simply empty — mine=1 must show the mine-specific empty state.
        other = ClassOfferingFactory(slug="reg-empty-other")
        RegistrationFactory(class_offering=other, email="present@example.com")
        client.force_login(admin_user)
        response = client.get(reverse("classes:admin_registrations") + "?mine=1")
        html = response.content.decode()
        assert "None of these belong to a class where you are the instructor or author" in html
        assert "Show all registrations" in html
        assert "mine" not in response.context["mine_clear_url"]
