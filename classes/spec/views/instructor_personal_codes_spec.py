"""The instructor's discount code surfaces (#1065): always visible, read only, whatever the site flag says.

Every instructor sees their personal code on My Classes and on the Discount Codes tab, every live
code that works on their classes on that tab, and the codes that work on one class on its
Overview. Making or requesting a code stays behind ``instructor_discount_codes_enabled``.
``classes/spec/conftest.py`` turns that flag ON for every spec here, so each surface is checked
with it switched off explicitly as well.
"""

from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse

from classes.factories import (
    CategoryFactory,
    ClassOfferingFactory,
    DiscountCodeFactory,
    InstructorFactory,
    UserFactory,
)
from classes.models import ClassOffering, DiscountCode
from core.models import SiteConfiguration
from membership.models import AdminCapability, Member
from tests.membership.factories import GuildFactory, MembershipPlanFactory

pytestmark = pytest.mark.django_db

CARD = b"data-personal-code "
CLASS_CODES = b"data-class-codes"
CREATE = reverse("classes:teach_discount_code_create")
REQUEST = reverse("classes:teach_discount_code_request")
TAB = reverse("classes:teach_discount_codes")


def _flag(on: bool, *, approval: bool = False) -> None:
    config = SiteConfiguration.load()
    config.instructor_discount_codes_enabled = on
    config.instructor_discount_codes_need_approval = approval
    config.save(update_fields=["instructor_discount_codes_enabled", "instructor_discount_codes_need_approval"])


def _member(username: str, name: str) -> tuple[object, Member]:
    MembershipPlanFactory()
    user = UserFactory(username=username)
    member = Member.objects.get(user=user)
    Member.objects.filter(pk=member.pk).update(full_legal_name=name, preferred_name="")
    return user, Member.objects.get(pk=member.pk)


@pytest.fixture
def teacher() -> tuple[object, Member]:
    """An instructor granted teaching the way an admin grants it, so they hold a personal code."""
    user, member = _member("pc-teacher@example.com", "Robin Example")
    member.grant_teaching(granted_by=None)
    return user, member


@pytest.fixture
def teacher_client(client: Client, teacher) -> Client:
    client.force_login(teacher[0])
    return client


@pytest.fixture
def their_class(teacher) -> ClassOffering:
    return ClassOfferingFactory(
        title="Pewter Casting", instructor=teacher[1], category=CategoryFactory(), status=ClassOffering.Status.PUBLISHED
    )


def _overview(offering: ClassOffering) -> str:
    return reverse("classes:teach_class_detail", kwargs={"pk": offering.pk})


@pytest.fixture(params=[False, True], ids=["setting_off", "setting_on"])
def either_setting(request) -> bool:
    _flag(request.param)
    return request.param


def describe_every_surface_whatever_the_setting(either_setting):
    def it_shows_the_personal_code_card_on_my_classes(teacher_client, teacher):
        html = teacher_client.get(reverse("classes:teach_dashboard")).content
        assert CARD in html
        assert b'data-guidance-target="classes.personal-code"' in html
        assert b'value="ROBIN10"' in html
        assert b"Works on every class you teach. Share it with anyone." in html
        assert b"data-personal-code-copy" in html

    def it_offers_the_discount_codes_tab(teacher_client):
        html = teacher_client.get(reverse("classes:teach_dashboard")).content.decode()
        assert f'<a href="{TAB}" class="vote-tab' in html

    def it_shows_the_card_and_the_codes_that_work_on_their_classes_on_the_tab(teacher_client, their_class):
        DiscountCodeFactory(code="EVERYONE15", class_offering=None)
        DiscountCodeFactory(code="PEWTER20", class_offering=their_class)
        resp = teacher_client.get(TAB)
        assert resp.status_code == 200
        html = resp.content.decode()
        assert 'value="ROBIN10"' in html
        assert "<code>EVERYONE15</code>" in html
        assert "<code>PEWTER20</code>" in html
        assert "<td data-code-scope>Pewter Casting</td>" in html
        # The personal code is in the card, not listed a second time.
        assert "<code>ROBIN10</code>" not in html
        assert 'class="vote-tab vote-tab--active" aria-current="page">Discount Codes' in html

    def it_shows_the_codes_that_work_on_one_class_on_its_overview(teacher_client, their_class):
        DiscountCodeFactory(code="EVERYONE15", class_offering=None)
        DiscountCodeFactory(code="PEWTER20", class_offering=their_class)
        DiscountCodeFactory(code="ELSEWHERE", class_offering=ClassOfferingFactory())
        html = teacher_client.get(_overview(their_class)).content.decode()
        assert 'data-guidance-target="classes.class-codes"' in html
        for code in ("ROBIN10", "EVERYONE15", "PEWTER20"):
            assert f"<code>{code}</code>" in html
        assert "<code>ELSEWHERE</code>" not in html
        assert f'href="{TAB}">See Your Discount Codes' in html

    def it_never_lists_an_inactive_or_unapproved_code(teacher_client, teacher, their_class):
        DiscountCodeFactory(code="SWITCHEDOFF", class_offering=None, is_active=False)
        DiscountCodeFactory(code="WAITING", class_offering=their_class, is_approved=False)
        DiscountCodeFactory(code="ADMINDRAFT", class_offering=None, instructor=teacher[1], is_approved=False)
        for url in (TAB, _overview(their_class), reverse("classes:teach_dashboard")):
            html = teacher_client.get(url).content
            for code in (b"SWITCHEDOFF", b"WAITING", b"ADMINDRAFT"):
                assert code not in html, (url, code)

    def it_hides_the_card_once_an_admin_switches_the_code_off(teacher_client, teacher, their_class):
        DiscountCode.objects.filter(instructor=teacher[1], is_personal=True).update(is_active=False)
        for url in (TAB, reverse("classes:teach_dashboard"), _overview(their_class)):
            html = teacher_client.get(url).content
            assert CARD not in html, url
            assert b"ROBIN10" not in html, url


def describe_with_the_setting_off():
    @pytest.fixture(autouse=True)
    def _off() -> None:
        _flag(False)

    def it_offers_no_way_to_make_or_ask_for_a_code(teacher_client, their_class):
        for url in (TAB, reverse("classes:teach_dashboard"), _overview(their_class)):
            html = teacher_client.get(url).content.decode()
            assert CREATE not in html, url
            assert REQUEST not in html, url
        html = teacher_client.get(TAB).content
        assert b"data-instructor-code-tools" not in html

    def it_says_to_ask_an_admin_only_where_making_a_code_was_offered(teacher_client, their_class):
        assert b"data-ask-an-admin" in teacher_client.get(TAB).content
        for url in (reverse("classes:teach_dashboard"), _overview(their_class)):
            assert b"data-ask-an-admin" not in teacher_client.get(url).content, url

    def it_still_keeps_the_per_class_tab_and_the_create_routes_behind_the_flag(teacher_client, their_class):
        resp = teacher_client.get(reverse("classes:teach_class_discount_codes", kwargs={"pk": their_class.pk}))
        assert resp.status_code == 302
        assert teacher_client.get(CREATE).status_code == 302
        assert teacher_client.get(REQUEST).status_code == 302


def describe_with_the_setting_on():
    def it_keeps_the_codes_they_made_with_their_controls_in_the_direct_flow(teacher_client, teacher):
        _flag(True)
        mine = DiscountCodeFactory(code="MINEPENDING", created_by=teacher[0], is_approved=False)
        html = teacher_client.get(TAB).content.decode()
        assert "data-instructor-code-tools" in html
        assert CREATE in html
        assert reverse("classes:teach_discount_code_edit", kwargs={"pk": mine.pk}) in html
        assert "data-ask-an-admin" not in html

    def it_offers_request_a_code_in_approval_mode(teacher_client):
        _flag(True, approval=True)
        html = teacher_client.get(TAB).content.decode()
        assert REQUEST in html
        assert CREATE not in html
        assert "Your Requests" in html

    def it_hides_an_admins_unapproved_code_on_the_class_tab_but_keeps_the_instructors_own_draft(
        teacher_client, teacher, their_class
    ):
        _flag(True)
        DiscountCodeFactory(code="ADMINDRAFT", class_offering=their_class, is_approved=False)
        DiscountCodeFactory(code="MYDRAFT", class_offering=their_class, is_approved=False, created_by=teacher[0])
        html = teacher_client.get(reverse("classes:teach_class_discount_codes", kwargs={"pk": their_class.pk})).content
        assert b"ADMINDRAFT" not in html
        assert b"MYDRAFT" in html

    def it_shows_an_admin_every_code_on_the_class_tab(client, admin_user, their_class):
        DiscountCodeFactory(code="ADMINDRAFT", class_offering=their_class, is_approved=False)
        client.force_login(admin_user)
        html = client.get(reverse("classes:teach_class_discount_codes", kwargs={"pk": their_class.pk})).content
        assert b"ADMINDRAFT" in html


def describe_co_teaching():
    def it_lists_a_partners_code_on_the_tab_and_on_the_shared_class(teacher_client, teacher):
        _, partner = _member("pc-partner@example.com", "Morgan Partner")
        partner.grant_teaching(granted_by=None)
        shared = ClassOfferingFactory(instructor=partner, category=CategoryFactory())
        shared.set_instructors([partner, teacher[1]])
        for url in (TAB, _overview(shared)):
            assert b"<code>MORGAN10</code>" in teacher_client.get(url).content, url


def describe_who_sees_none_of_it():
    def it_refuses_a_member_who_cannot_teach(client):
        user, _member_row = _member("pc-learner@example.com", "Lee Learner")
        client.force_login(user)
        for url in (TAB, reverse("classes:teach_dashboard")):
            resp = client.get(url)
            assert resp.status_code == 302, url
            assert resp["Location"] == reverse("classes:teach_overview")
        assert not DiscountCode.objects.filter(instructor__user=user).exists()

    def it_refuses_another_instructors_class_overview(client, their_class):
        user, other = _member("pc-other@example.com", "Ola Other")
        other.grant_teaching(granted_by=None)
        client.force_login(user)
        assert client.get(_overview(their_class)).status_code == 404

    def it_sends_an_anonymous_visitor_to_log_in(client, their_class):
        for url in (TAB, _overview(their_class)):
            resp = client.get(url)
            assert resp.status_code == 302
            assert "login" in resp["Location"].lower()

    def it_shows_no_codes_to_a_reviewer_or_an_admin_on_the_overview(client, admin_user, their_class):
        user, reviewer = _member("pc-reviewer@example.com", "Rae Reviewer")
        reviewer.admin_capabilities.create(capability=AdminCapability.Capability.CLASS_APPROVER)
        for viewer in (user, admin_user):
            client.force_login(viewer)
            resp = client.get(_overview(their_class))
            assert resp.status_code == 200
            assert CLASS_CODES not in resp.content

    def it_shows_no_codes_to_a_guild_lead_who_does_not_teach_the_class(client, teacher):
        user, lead = _member("pc-lead@example.com", "Lane Lead")
        guild = GuildFactory(name="Pewter Guild", guild_lead=lead)
        offering = ClassOfferingFactory(instructor=teacher[1], category=CategoryFactory(name="Pewter", guild=guild))
        client.force_login(user)
        # A guild lead has no Overview on a class they do not teach, and no Discount Codes tab.
        assert client.get(_overview(offering)).status_code == 404
        assert client.get(TAB).status_code == 302

    def it_shows_codes_to_a_guild_lead_who_also_teaches_the_class(client):
        user, lead = _member("pc-lead2@example.com", "Lane Lead")
        lead.grant_teaching(granted_by=None)
        guild = GuildFactory(name="Pewter Guild", guild_lead=lead)
        offering = ClassOfferingFactory(instructor=lead, category=CategoryFactory(name="Pewter", guild=guild))
        client.force_login(user)
        assert b"<code>LANE10</code>" in client.get(_overview(offering)).content


def describe_admin_pages():
    def it_lets_an_admin_edit_and_switch_off_a_personal_code(client, admin_user, teacher_client, teacher):
        code = DiscountCode.objects.get(instructor=teacher[1], is_personal=True)
        admin = Client()
        admin.force_login(admin_user)
        assert b"ROBIN10" in admin.get(reverse("classes:admin_discount_codes")).content
        resp = admin.post(
            reverse("classes:admin_discount_code_edit", kwargs={"pk": code.pk}),
            {"code": "ROBIN25", "description": code.description, "discount_pct": "25", "discount_fixed_cents": ""},
        )
        assert resp.status_code == 302
        code.refresh_from_db()
        assert (code.code, code.discount_pct, code.is_active, code.is_personal) == ("ROBIN25", 25, False, True)
        assert CARD not in teacher_client.get(reverse("classes:teach_dashboard")).content

    def it_lets_an_admin_change_the_percent_in_site_settings_and_new_grants_use_it(client):
        admin = UserFactory(username="pc-superuser@example.com", is_superuser=True, is_staff=True)
        Member.objects.filter(user=admin).update(fog_role=Member.FogRole.ADMIN)
        client.force_login(admin)
        resp = client.post(
            reverse("hub_admin_cms"),
            {"instructor_discount_codes_enabled": "", "instructor_code_discount_pct": "30"},
        )
        assert resp.status_code == 302
        assert SiteConfiguration.load().instructor_code_discount_pct == 30
        assert b'name="instructor_code_discount_pct"' in client.get(reverse("hub_admin_cms")).content
        _, member = _member("pc-new@example.com", "Jacqueline New")
        member.grant_teaching(granted_by=None)
        assert DiscountCode.objects.get(instructor=member, is_personal=True).code == "JACQUELINE30"

    def it_refuses_a_percent_outside_one_to_a_hundred(client):
        admin = UserFactory(username="pc-superuser2@example.com", is_superuser=True, is_staff=True)
        client.force_login(admin)
        resp = client.post(reverse("hub_admin_cms"), {"instructor_code_discount_pct": "0"})
        assert resp.status_code == 200
        assert SiteConfiguration.load().instructor_code_discount_pct == 10


def describe_the_grant_views():
    def it_makes_the_code_when_an_admin_approves_a_teaching_application(client, admin_user):
        _, applicant = _member("pc-applicant@example.com", "Avery Applicant")
        client.force_login(admin_user)
        resp = client.post(reverse("classes:admin_teaching_approve", kwargs={"pk": applicant.pk}))
        assert resp.status_code == 302
        assert DiscountCode.objects.get(instructor=applicant, is_personal=True).code == "AVERY10"

    def it_makes_the_code_from_the_member_edit_permissions_tab_once(client, admin_user):
        _, member = _member("pc-perm@example.com", "Parker Perm")
        client.force_login(admin_user)
        url = reverse("hub_admin_member_teaching", kwargs={"pk": member.pk})
        client.post(url, {"action": "grant"})
        client.post(url, {"action": "grant"})
        assert list(DiscountCode.objects.filter(instructor=member).values_list("code", flat=True)) == ["PARKER10"]

    def it_makes_nothing_for_an_instructor_factory_without_a_grant():
        assert not DiscountCode.objects.filter(instructor=InstructorFactory()).exists()
