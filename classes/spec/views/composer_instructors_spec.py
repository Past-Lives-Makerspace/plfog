"""BDD specs for the Instructors control on the composer's Basics step, and what a second
instructor can do with the class (#686, part 1).

Admins and every instructor of the class get the control; the guild's staff editing a draft
they do not teach get the rows as hidden inputs, so the set round trips unchanged. Every
instructor opens the composer, saves, submits for review and reaches the class's tabs.
"""

from __future__ import annotations

import json
import re
from html import unescape

import pytest
from django.urls import reverse
from django.utils.text import slugify

from classes.factories import CategoryFactory, ClassOfferingFactory, InstructorFactory, UserFactory
from classes.forms import AT_LEAST_ONE_INSTRUCTOR, INSTRUCTORS_HINT, no_teaching_access_message
from classes.models import ClassOffering
from membership.models import Member
from tests.membership.factories import GuildFactory, GuildStaffMembershipFactory, MemberFactory

pytestmark = pytest.mark.django_db


def _instructor(name: str) -> Member:
    user = UserFactory(username=f"{slugify(name)}@example.com")
    return InstructorFactory(user=user, full_legal_name=name, instructor_slug=slugify(name))


def _rows(html: str) -> list[dict]:
    match = re.search(r'data-instructor-rows="([^"]*)"', html)
    assert match is not None, "no Instructors control on the page"
    return json.loads(unescape(match.group(1)))


def _members(offering: ClassOffering) -> list[int]:
    return [member.pk for member in offering.instructor_list]


def _payload(offering: ClassOffering | None = None, **extra) -> dict:
    payload = {
        "title": offering.title if offering is not None else "Picture Framing",
        "category": (offering.category if offering is not None else CategoryFactory()).pk,
        "description": "Build a frame for the piece you bring, and learn to cut a mat for it.",
        "price_cents": "60.00",
        "capacity": "6",
        "scheduling_model": ClassOffering.SchedulingModel.FIXED,
        "scheduling_type": ClassOffering.SchedulingType.SINGLE_SESSION,
        "action": "save",
        "step": "1",
        "sessions-TOTAL_FORMS": "0",
        "sessions-INITIAL_FORMS": "0",
        "sessions-MIN_NUM_FORMS": "0",
        "sessions-MAX_NUM_FORMS": "1000",
        "faq-TOTAL_FORMS": "0",
        "faq-INITIAL_FORMS": "0",
        "faq-MIN_NUM_FORMS": "0",
        "faq-MAX_NUM_FORMS": "1000",
    }
    payload.update(extra)
    return payload


def _edit(offering: ClassOffering) -> str:
    return reverse("classes:teach_class_edit", kwargs={"pk": offering.pk})


def describe_the_basics_step():
    def it_lists_the_author_on_a_new_class(client):
        me = _instructor("Alex Eason")
        client.force_login(me.user)
        html = client.get(reverse("classes:teach_class_create")).content.decode()
        assert _rows(html) == [{"pk": me.pk, "name": "Alex Eason"}]
        assert 'name="add_instructor"' in html
        assert INSTRUCTORS_HINT in html

    def it_shows_the_control_to_every_instructor_of_the_class(client):
        me, partner = _instructor("Alex Eason"), _instructor("Jenelle Giordano")
        offering = ClassOfferingFactory(instructor=me, instructors=[me, partner], status=ClassOffering.Status.DRAFT)
        client.force_login(partner.user)
        html = client.get(_edit(offering)).content.decode()
        assert [row["name"] for row in _rows(html)] == ["Alex Eason", "Jenelle Giordano"]
        assert 'name="add_instructor"' in html

    def it_shows_the_control_to_an_admin(admin_user, client):
        offering = ClassOfferingFactory(status=ClassOffering.Status.DRAFT)
        client.force_login(admin_user)
        html = client.get(_edit(offering)).content.decode()
        assert [row["pk"] for row in _rows(html)] == [offering.instructor_id]
        assert 'name="instructor"' not in html

    def it_gives_the_guilds_staff_no_instructors_control(client):
        staffer = _instructor("Sam Staff")
        guild = GuildFactory()
        GuildStaffMembershipFactory(guild=guild, member=staffer)
        offering = ClassOfferingFactory(category=CategoryFactory(guild=guild), status=ClassOffering.Status.DRAFT)
        client.force_login(staffer.user)
        html = client.get(_edit(offering)).content.decode()
        assert "data-instructor-rows" not in html
        assert 'name="add_instructor"' not in html
        assert 'name="instructors"' not in html

    def it_ignores_a_changed_list_the_guilds_staff_post(client):
        # The form built for a staffer carries no instructors field, so the rows and the payee stay.
        staffer = _instructor("Sam Staff")
        guild = GuildFactory()
        GuildStaffMembershipFactory(guild=guild, member=staffer)
        offering = ClassOfferingFactory(category=CategoryFactory(guild=guild), status=ClassOffering.Status.DRAFT)
        teacher_pk = offering.instructor_id
        client.force_login(staffer.user)
        resp = client.post(
            _edit(offering),
            _payload(offering, title="Renamed By Staff", instructors=[staffer.pk], add_instructor=staffer.pk),
        )
        assert resp.status_code == 302
        offering.refresh_from_db()
        assert offering.title == "Renamed By Staff"
        assert _members(offering) == [teacher_pk]
        assert offering.instructor_id == teacher_pk

    def it_lets_the_guilds_staff_save_a_class_nobody_is_listed_on(client):
        # Six legacy runs carry no instructor; the staffer has no control to satisfy, so no rule fires.
        staffer = _instructor("Sam Staff")
        guild = GuildFactory()
        GuildStaffMembershipFactory(guild=guild, member=staffer)
        offering = ClassOfferingFactory(
            category=CategoryFactory(guild=guild), instructor=None, status=ClassOffering.Status.DRAFT
        )
        client.force_login(staffer.user)
        resp = client.post(_edit(offering), _payload(offering, title="Legacy Run"))
        assert resp.status_code == 302
        offering.refresh_from_db()
        assert offering.title == "Legacy Run"
        assert _members(offering) == []


def describe_saving_the_list():
    def it_adds_a_partner_and_keeps_the_order(client):
        me, partner = _instructor("Alex Eason"), _instructor("Jenelle Giordano")
        offering = ClassOfferingFactory(instructor=me, status=ClassOffering.Status.DRAFT)
        client.force_login(me.user)
        resp = client.post(_edit(offering), _payload(offering, instructors=[me.pk, partner.pk]))
        assert resp.status_code == 302
        assert _members(offering) == [me.pk, partner.pk]
        assert ClassOffering.objects.get(pk=offering.pk).instructor_id == me.pk

    def it_starts_a_new_class_with_the_author_and_whoever_they_add(client):
        me, partner = _instructor("Alex Eason"), _instructor("Jenelle Giordano")
        client.force_login(me.user)
        resp = client.post(reverse("classes:teach_class_create"), _payload(instructors=[me.pk, partner.pk]))
        assert resp.status_code == 302
        created = ClassOffering.objects.get(title="Picture Framing")
        assert _members(created) == [me.pk, partner.pk]
        assert created.instructor_id == me.pk
        assert created.created_by_id == me.pk

    def it_refuses_an_empty_list_on_the_basics_step(client):
        me = _instructor("Alex Eason")
        offering = ClassOfferingFactory(instructor=me, status=ClassOffering.Status.DRAFT)
        client.force_login(me.user)
        resp = client.post(_edit(offering), _payload(offering, instructors=[""]))
        html = resp.content.decode()
        assert resp.status_code == 200
        assert AT_LEAST_ONE_INSTRUCTOR in html
        assert "phase: 1," in html
        assert _members(offering) == [me.pk]

    def it_refuses_a_forged_member_by_name(client):
        me = _instructor("Alex Eason")
        plain = MemberFactory(full_legal_name="Pat Plain")
        offering = ClassOfferingFactory(instructor=me, status=ClassOffering.Status.DRAFT)
        client.force_login(me.user)
        resp = client.post(_edit(offering), _payload(offering, instructors=[me.pk, plain.pk]))
        assert resp.status_code == 200
        assert no_teaching_access_message(plain) in unescape(resp.content.decode())
        assert _members(offering) == [me.pk]
        # The refused page lists what was posted, so the row is there to untick.
        assert [row["pk"] for row in _rows(resp.content.decode())] == [me.pk, plain.pk]


def describe_a_second_instructor():
    @pytest.fixture
    def shared(db):
        me, partner = _instructor("Alex Eason"), _instructor("Jenelle Giordano")
        offering = ClassOfferingFactory(
            instructor=me, instructors=[me, partner], status=ClassOffering.Status.DRAFT, ready=True
        )
        return partner, offering

    def it_opens_the_composer_and_saves(shared, client):
        partner, offering = shared
        client.force_login(partner.user)
        assert client.get(_edit(offering)).status_code == 200
        resp = client.post(_edit(offering), _payload(offering, title="Framing With Jenelle"))
        assert resp.status_code == 302
        offering.refresh_from_db()
        assert offering.title == "Framing With Jenelle"

    def it_submits_the_class_for_review(shared, client):
        partner, offering = shared
        client.force_login(partner.user)
        resp = client.post(reverse("classes:teach_class_submit", kwargs={"pk": offering.pk}))
        assert resp.status_code == 302
        offering.refresh_from_db()
        assert offering.status == ClassOffering.Status.PENDING

    def it_reaches_the_registrations_and_discount_codes_tabs(shared, client):
        partner, offering = shared
        client.force_login(partner.user)
        assert client.get(reverse("classes:teach_class_registrations", kwargs={"pk": offering.pk})).status_code == 200
        assert client.get(reverse("classes:teach_class_discount_codes", kwargs={"pk": offering.pk})).status_code == 200

    def it_sees_both_names_on_the_class_screen_header(shared, client):
        partner, offering = shared
        client.force_login(partner.user)
        html = client.get(reverse("classes:teach_class_detail", kwargs={"pk": offering.pk})).content.decode()
        assert '<span class="pl-class-meta__item">Alex Eason and Jenelle Giordano</span>' in html

    def it_loses_the_instructor_screens_with_teaching_access(shared, client):
        partner, offering = shared
        partner.instructor_oriented_at = None
        partner.save(update_fields=["instructor_oriented_at"])
        client.force_login(partner.user)
        assert client.get(_edit(offering)).status_code == 404
        assert offering.teaches(partner) is True
