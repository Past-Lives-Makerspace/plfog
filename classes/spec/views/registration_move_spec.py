"""BDD specs for the shared roster move-student endpoint and its row-menu affordance."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from classes.factories import (
    ClassOfferingFactory,
    ClassSessionFactory,
    InstructorFactory,
    RegistrationFactory,
    UserFactory,
)
from classes.models import ClassOffering, CmsActivity, Registration

pytestmark = pytest.mark.django_db

HTMX = {"HX-Request": "true"}

PRICE_NOTE = "Heads up: some of these classes cost a different amount"


def _login_instructor(client, username: str, slug: str):
    user = UserFactory(username=username)
    member = InstructorFactory(user=user, instructor_slug=slug)
    client.force_login(user)
    return member


def _login_plain_member(client, username: str):
    user = UserFactory(username=username)
    member = InstructorFactory(user=user, instructor_slug="")
    client.force_login(user)
    return member


def _bookable_for(instructor, slug: str, **kwargs) -> ClassOffering:
    """A published class of this instructor's with a future first session — a valid instructor move target."""
    offering = ClassOfferingFactory(slug=slug, instructor=instructor, status=ClassOffering.Status.PUBLISHED, **kwargs)
    ClassSessionFactory(class_offering=offering, starts_at=timezone.now() + timedelta(days=7))
    return offering


def _move_url(reg: Registration) -> str:
    return reverse("classes:registration_move", args=[reg.pk])


def describe_gating():
    def it_redirects_anonymous_to_login(client):
        reg = RegistrationFactory()
        response = client.post(_move_url(reg), {"target": ""})
        assert response.status_code == 302
        assert "login" in response["Location"]

    def it_rejects_a_get(admin_user, client):
        reg = RegistrationFactory()
        client.force_login(admin_user)
        response = client.get(_move_url(reg))
        assert response.status_code == 405

    def it_rejects_a_plain_member(client):
        _login_plain_member(client, "mv-plain@example.com")
        reg = RegistrationFactory()
        target = ClassOfferingFactory(slug="mv-plain-dst")
        response = client.post(_move_url(reg), {"target": target.pk})
        assert response.status_code == 403

    def it_rejects_an_instructor_moving_from_someone_elses_class(client):
        mover = _login_instructor(client, "mv-foreign@example.com", "mv-foreign")
        reg = RegistrationFactory()  # someone else's class
        target = _bookable_for(mover, "mv-foreign-dst")
        response = client.post(_move_url(reg), {"target": target.pk})
        assert response.status_code == 403
        reg.refresh_from_db()
        assert reg.class_offering_id != target.pk

    def it_rejects_a_guild_lead_who_can_otherwise_manage_the_roster(client):
        from classes.factories import CategoryFactory
        from tests.membership.factories import GuildFactory

        member = _login_plain_member(client, "mv-lead@example.com")
        guild = GuildFactory(guild_lead=member)
        offering = ClassOfferingFactory(category=CategoryFactory(guild=guild))
        reg = RegistrationFactory(class_offering=offering)
        target = ClassOfferingFactory(slug="mv-lead-dst")
        response = client.post(_move_url(reg), {"target": target.pk})
        assert response.status_code == 403


def describe_instructor_move():
    def it_moves_a_student_to_another_class_they_instruct(client):
        mine = _login_instructor(client, "mv-own@example.com", "mv-own")
        source = ClassOfferingFactory(slug="mv-own-src", instructor=mine)
        target = _bookable_for(mine, "mv-own-dst")
        reg = RegistrationFactory(class_offering=source, status=Registration.Status.CONFIRMED)
        response = client.post(_move_url(reg), {"target": target.pk})
        assert response.status_code == 302
        assert response["Location"] == reverse("classes:teach_class_registrations", args=[source.pk])
        reg.refresh_from_db()
        assert reg.class_offering_id == target.pk

    def it_logs_the_move_with_the_instructor_as_actor(client):
        mine = _login_instructor(client, "mv-log@example.com", "mv-log")
        source = ClassOfferingFactory(slug="mv-log-src", instructor=mine)
        target = _bookable_for(mine, "mv-log-dst")
        reg = RegistrationFactory(class_offering=source, status=Registration.Status.CONFIRMED)
        client.post(_move_url(reg), {"target": target.pk})
        entry = CmsActivity.objects.get(kind=CmsActivity.Kind.REGISTRATION_MOVED, registration=reg)
        assert entry.actor == mine.user
        assert entry.payload == {"from": source.title, "to": target.title}

    def it_promotes_the_source_waitlist_when_a_seat_holder_moves(client):
        mine = _login_instructor(client, "mv-wl@example.com", "mv-wl")
        source = ClassOfferingFactory(slug="mv-wl-src", instructor=mine, capacity=1)
        target = _bookable_for(mine, "mv-wl-dst")
        reg = RegistrationFactory(class_offering=source, status=Registration.Status.CONFIRMED)
        waiting = RegistrationFactory(class_offering=source, status=Registration.Status.WAITLISTED)
        client.post(_move_url(reg), {"target": target.pk})
        waiting.refresh_from_db()
        assert waiting.waitlist_notified_at is not None

    def it_rejects_a_target_they_do_not_instruct(client):
        mine = _login_instructor(client, "mv-nodst@example.com", "mv-nodst")
        source = ClassOfferingFactory(slug="mv-nodst-src", instructor=mine)
        foreign = _bookable_for(InstructorFactory(instructor_slug="mv-nodst-f"), "mv-nodst-foreign")
        reg = RegistrationFactory(class_offering=source, status=Registration.Status.CONFIRMED)
        response = client.post(_move_url(reg), {"target": foreign.pk})
        assert response.status_code == 302
        reg.refresh_from_db()
        assert reg.class_offering_id == source.pk

    def it_rejects_a_target_that_already_started(client):
        mine = _login_instructor(client, "mv-past@example.com", "mv-past")
        source = ClassOfferingFactory(slug="mv-past-src", instructor=mine)
        past = ClassOfferingFactory(slug="mv-past-dst", instructor=mine, status=ClassOffering.Status.PUBLISHED)
        ClassSessionFactory(class_offering=past, starts_at=timezone.now() - timedelta(days=1))
        reg = RegistrationFactory(class_offering=source, status=Registration.Status.CONFIRMED)
        response = client.post(_move_url(reg), {"target": past.pk})
        assert response.status_code == 302
        reg.refresh_from_db()
        assert reg.class_offering_id == source.pk

    def it_rejects_their_own_draft_target(client):
        mine = _login_instructor(client, "mv-draft@example.com", "mv-draft")
        source = ClassOfferingFactory(slug="mv-draft-src", instructor=mine)
        draft = _bookable_for(mine, "mv-draft-dst")
        draft.status = ClassOffering.Status.DRAFT
        draft.save(update_fields=["status"])
        reg = RegistrationFactory(class_offering=source, status=Registration.Status.CONFIRMED)
        response = client.post(_move_url(reg), {"target": draft.pk})
        assert response.status_code == 302
        reg.refresh_from_db()
        assert reg.class_offering_id == source.pk

    def it_rejects_a_full_target(client):
        mine = _login_instructor(client, "mv-full@example.com", "mv-full")
        source = ClassOfferingFactory(slug="mv-full-src", instructor=mine)
        full = _bookable_for(mine, "mv-full-dst", capacity=1)
        RegistrationFactory(class_offering=full, status=Registration.Status.CONFIRMED)
        reg = RegistrationFactory(class_offering=source, status=Registration.Status.CONFIRMED)
        response = client.post(_move_url(reg), {"target": full.pk})
        assert response.status_code == 302
        reg.refresh_from_db()
        assert reg.class_offering_id == source.pk


def describe_admin_move():
    def it_moves_any_registration_into_any_upcoming_class(admin_user, client):
        client.force_login(admin_user)
        source = ClassOfferingFactory(slug="mv-adm-src")
        target = ClassOfferingFactory(slug="mv-adm-dst")  # a draft, undated class — admin breadth
        reg = RegistrationFactory(class_offering=source, status=Registration.Status.CONFIRMED)
        response = client.post(_move_url(reg), {"target": target.pk})
        assert response.status_code == 302
        assert response["Location"] == reverse("classes:admin_class_registrations", args=[source.pk])
        reg.refresh_from_db()
        assert reg.class_offering_id == target.pk

    def it_can_move_into_a_full_class(admin_user, client):
        client.force_login(admin_user)
        source = ClassOfferingFactory(slug="mv-adm-f-src")
        full = ClassOfferingFactory(slug="mv-adm-f-dst", capacity=1)
        RegistrationFactory(class_offering=full, status=Registration.Status.CONFIRMED)
        reg = RegistrationFactory(class_offering=source, status=Registration.Status.CONFIRMED)
        response = client.post(_move_url(reg), {"target": full.pk})
        assert response.status_code == 302
        reg.refresh_from_db()
        assert reg.class_offering_id == full.pk

    def it_rejects_a_past_target(admin_user, client):
        client.force_login(admin_user)
        source = ClassOfferingFactory(slug="mv-adm-p-src")
        past = ClassOfferingFactory(slug="mv-adm-p-dst")
        ClassSessionFactory(class_offering=past, starts_at=timezone.now() - timedelta(days=1))
        reg = RegistrationFactory(class_offering=source, status=Registration.Status.CONFIRMED)
        response = client.post(_move_url(reg), {"target": past.pk})
        assert response.status_code == 302
        reg.refresh_from_db()
        assert reg.class_offering_id == source.pk


def describe_move_affordance():
    def it_offers_move_student_on_the_teach_registrations_tab(client, menu_region):
        mine = _login_instructor(client, "mv-ui@example.com", "mv-ui")
        source = ClassOfferingFactory(slug="mv-ui-src", instructor=mine)
        _bookable_for(mine, "mv-ui-dst", title="Destination Class")
        reg = RegistrationFactory(class_offering=source, status=Registration.Status.CONFIRMED)
        content = client.get(reverse("classes:teach_class_registrations", args=[source.pk])).content.decode()
        assert ">Move Student</button>" in menu_region(content, f"reg-row-{reg.pk}")
        assert f"move-reg-{reg.pk}" in content
        assert "Destination Class" in content

    def it_offers_move_student_on_the_admin_class_tab(admin_user, client, menu_region):
        client.force_login(admin_user)
        source = ClassOfferingFactory(slug="mv-ui-adm")
        reg = RegistrationFactory(class_offering=source, status=Registration.Status.CONFIRMED)
        content = client.get(reverse("classes:admin_class_registrations", args=[source.pk])).content.decode()
        assert ">Move Student</button>" in menu_region(content, f"reg-row-{reg.pk}")
        assert f"move-reg-{reg.pk}" in content

    def it_shows_the_empty_state_when_the_instructor_has_no_other_bookable_class(client):
        mine = _login_instructor(client, "mv-empty@example.com", "mv-empty")
        source = ClassOfferingFactory(slug="mv-empty-src", instructor=mine)
        RegistrationFactory(class_offering=source, status=Registration.Status.CONFIRMED)
        content = client.get(reverse("classes:teach_class_registrations", args=[source.pk])).content.decode()
        assert "There are no other upcoming classes to move this student into." in content

    def it_omits_move_student_from_a_guild_leads_row_swap(client, menu_region):
        from classes.factories import CategoryFactory
        from tests.membership.factories import GuildFactory

        member = _login_plain_member(client, "mv-lead-ui@example.com")
        guild = GuildFactory(guild_lead=member)
        offering = ClassOfferingFactory(category=CategoryFactory(guild=guild))
        reg = RegistrationFactory(class_offering=offering, status=Registration.Status.CONFIRMED)
        response = client.post(reverse("classes:registration_remove", args=[reg.pk]), headers=HTMX)
        assert response.status_code == 200
        assert "Move Student" not in menu_region(response.content.decode(), f"reg-row-{reg.pk}")

    def it_keeps_move_student_in_an_instructors_row_swap(client, menu_region):
        mine = _login_instructor(client, "mv-swap@example.com", "mv-swap")
        offering = ClassOfferingFactory(slug="mv-swap-src", instructor=mine)
        reg = RegistrationFactory(class_offering=offering, status=Registration.Status.CONFIRMED)
        response = client.post(reverse("classes:registration_remove", args=[reg.pk]), headers=HTMX)
        assert response.status_code == 200
        assert ">Move Student</button>" in menu_region(response.content.decode(), f"reg-row-{reg.pk}")


def describe_price_note():
    def it_warns_the_instructor_when_a_listed_class_costs_a_different_amount(client):
        mine = _login_instructor(client, "mv-pn@example.com", "mv-pn")
        source = ClassOfferingFactory(slug="mv-pn-src", instructor=mine)
        _bookable_for(mine, "mv-pn-dst", price_cents=6000)
        RegistrationFactory(class_offering=source, status=Registration.Status.CONFIRMED, amount_paid_cents=5000)
        content = client.get(reverse("classes:teach_class_registrations", args=[source.pk])).content.decode()
        assert PRICE_NOTE in content

    def it_stays_quiet_when_every_listed_class_matches_what_the_student_paid(client):
        mine = _login_instructor(client, "mv-pq@example.com", "mv-pq")
        source = ClassOfferingFactory(slug="mv-pq-src", instructor=mine)
        _bookable_for(mine, "mv-pq-dst", price_cents=5000)
        RegistrationFactory(class_offering=source, status=Registration.Status.CONFIRMED, amount_paid_cents=5000)
        content = client.get(reverse("classes:teach_class_registrations", args=[source.pk])).content.decode()
        assert PRICE_NOTE not in content

    def it_never_shows_in_the_admin_modal(admin_user, client):
        client.force_login(admin_user)
        source = ClassOfferingFactory(slug="mv-pa-src")
        ClassOfferingFactory(slug="mv-pa-dst", price_cents=6000)
        RegistrationFactory(class_offering=source, status=Registration.Status.CONFIRMED, amount_paid_cents=5000)
        content = client.get(reverse("classes:admin_class_registrations", args=[source.pk])).content.decode()
        assert PRICE_NOTE not in content
