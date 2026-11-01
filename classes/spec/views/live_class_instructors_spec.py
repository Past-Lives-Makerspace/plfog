"""BDD specs for the Instructors control on a live class's edit page (#686).

A published class's dates stay locked (#1067), but who teaches it is not one of them: a
partner added changes nothing a registrant booked on. The class's own instructors get the
Basics step's control on the light edit page; the guild's staff save the list untouched.
"""

from __future__ import annotations

import json
import re
from datetime import timedelta
from html import unescape

import pytest
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify

from classes.factories import (
    CategoryFactory,
    ClassOfferingFactory,
    ClassSessionFactory,
    InstructorFactory,
    UserFactory,
)
from classes.forms import AT_LEAST_ONE_INSTRUCTOR, no_teaching_access_message
from classes.models import ClassOffering
from classes.spec.live_edit import live_facts
from membership.models import Member
from tests.membership.factories import GuildFactory, GuildStaffMembershipFactory, MemberFactory

pytestmark = pytest.mark.django_db


def _instructor(name: str) -> Member:
    user = UserFactory(username=f"{slugify(name)}@example.com")
    return InstructorFactory(user=user, full_legal_name=name, instructor_slug=slugify(name))


def _live(instructor: Member, **kwargs) -> ClassOffering:
    offering = ClassOfferingFactory(
        instructor=instructor, status=ClassOffering.Status.PUBLISHED, published_at=timezone.now(), **kwargs
    )
    start = timezone.now() + timedelta(days=3)
    ClassSessionFactory(class_offering=offering, starts_at=start, ends_at=start + timedelta(hours=2))
    return offering


def _rows(html: str) -> list[dict]:
    match = re.search(r'data-instructor-rows="([^"]*)"', html)
    assert match is not None, "no Instructors control on the page"
    return json.loads(unescape(match.group(1)))


def _members(offering: ClassOffering) -> list[int]:
    return [member.pk for member in offering.instructor_list]


def _payload(offering: ClassOffering, **extra) -> dict:
    payload = {
        **live_facts(offering),
        "description": "Build a frame for the piece you bring, and learn to cut a mat for it.",
        "prerequisites": "",
        "materials_included": "",
        "materials_to_bring": "A picture to frame",
        "safety_requirements": "",
        "age_guardian_note": "",
        "flexible_note": "",
        "video_url": "",
        "faq-TOTAL_FORMS": "0",
        "faq-INITIAL_FORMS": "0",
        "faq-MIN_NUM_FORMS": "0",
        "faq-MAX_NUM_FORMS": "1000",
    }
    payload.update(extra)
    return payload


def _edit(offering: ClassOffering) -> str:
    return reverse("classes:teach_class_edit", kwargs={"pk": offering.pk})


def describe_the_live_edit_page():
    def it_shows_the_control_to_the_classs_instructor(client):
        me = _instructor("Alex Eason")
        offering = _live(me)
        client.force_login(me.user)
        html = client.get(_edit(offering)).content.decode()
        assert _rows(html) == [{"pk": me.pk, "name": "Alex Eason"}]
        # Once, from the control above the field loop; the loop skips both of its fields.
        assert html.count('name="add_instructor"') == 1
        assert html.count('name="instructors"') == 1

    def it_adds_a_partner_without_touching_the_locked_facts(client):
        me, partner = _instructor("Alex Eason"), _instructor("Jenelle Giordano")
        offering = _live(me, title="Picture Framing", price_cents=6000, capacity=6)
        client.force_login(me.user)
        resp = client.post(_edit(offering), _payload(offering, instructors=[me.pk, partner.pk]))
        assert resp.status_code == 302
        offering.refresh_from_db()
        assert _members(offering) == [me.pk, partner.pk]
        assert offering.instructor_id == me.pk
        assert offering.status == ClassOffering.Status.PUBLISHED
        assert (offering.title, offering.price_cents, offering.capacity) == ("Picture Framing", 6000, 6)
        assert offering.materials_to_bring == "A picture to frame"

    def it_lets_the_partner_open_the_page_and_save(client):
        me, partner = _instructor("Alex Eason"), _instructor("Jenelle Giordano")
        offering = _live(me, instructors=[me, partner])
        client.force_login(partner.user)
        html = client.get(_edit(offering)).content.decode()
        assert [row["name"] for row in _rows(html)] == ["Alex Eason", "Jenelle Giordano"]
        resp = client.post(
            _edit(offering), _payload(offering, instructors=[me.pk, partner.pk], materials_to_bring="Gloves")
        )
        assert resp.status_code == 302
        offering.refresh_from_db()
        assert offering.materials_to_bring == "Gloves"
        assert _members(offering) == [me.pk, partner.pk]

    def it_sends_an_instructor_who_removed_themselves_to_the_dashboard(client):
        # Allowed, as on the composer: the partner keeps the class and becomes its payee. The
        # class screen is closed to the remover now, so a save that landed must not end on it.
        me, partner = _instructor("Alex Eason"), _instructor("Jenelle Giordano")
        offering = _live(me, instructors=[me, partner])
        client.force_login(me.user)
        resp = client.post(_edit(offering), _payload(offering, instructors=[partner.pk]))
        assert resp.status_code == 302
        assert resp.url == reverse("classes:teach_dashboard")
        assert client.get(resp.url).status_code == 200
        offering.refresh_from_db()
        assert _members(offering) == [partner.pk]
        assert offering.instructor_id == partner.pk
        assert client.get(reverse("classes:teach_class_detail", kwargs={"pk": offering.pk})).status_code == 404

    def it_keeps_the_list_when_a_post_carries_none(client):
        # A save from a page opened before the control shipped changes nothing about the list.
        me, partner = _instructor("Alex Eason"), _instructor("Jenelle Giordano")
        offering = _live(me, instructors=[me, partner])
        client.force_login(me.user)
        resp = client.post(_edit(offering), _payload(offering))
        assert resp.status_code == 302
        assert _members(offering) == [me.pk, partner.pk]

    def it_refuses_an_empty_list(client):
        me = _instructor("Alex Eason")
        offering = _live(me)
        client.force_login(me.user)
        resp = client.post(_edit(offering), _payload(offering, instructors=[""]))
        assert resp.status_code == 200
        assert AT_LEAST_ONE_INSTRUCTOR in resp.content.decode()
        assert _members(offering) == [me.pk]

    def it_refuses_a_member_without_teaching_access(client):
        me = _instructor("Alex Eason")
        plain = MemberFactory(full_legal_name="Pat Plain")
        offering = _live(me)
        client.force_login(me.user)
        resp = client.post(_edit(offering), _payload(offering, instructors=[me.pk, plain.pk]))
        assert resp.status_code == 200
        assert no_teaching_access_message(plain) in unescape(resp.content.decode())
        assert _members(offering) == [me.pk]
        # The refused page lists what was posted, so the row is there to untick.
        assert [row["pk"] for row in _rows(resp.content.decode())] == [me.pk, plain.pk]

    def it_gives_the_guilds_staff_no_control_and_ignores_what_they_post(client):
        staffer = _instructor("Sam Staff")
        guild = GuildFactory()
        GuildStaffMembershipFactory(guild=guild, member=staffer)
        teacher = _instructor("Alex Eason")
        offering = _live(teacher, category=CategoryFactory(guild=guild))
        client.force_login(staffer.user)
        html = client.get(_edit(offering)).content.decode()
        assert "data-instructor-rows" not in html
        assert 'name="add_instructor"' not in html
        resp = client.post(_edit(offering), _payload(offering, instructors=[staffer.pk], materials_to_bring="Clamps"))
        assert resp.status_code == 302
        offering.refresh_from_db()
        assert offering.materials_to_bring == "Clamps"
        assert _members(offering) == [teacher.pk]
        assert offering.instructor_id == teacher.pk
