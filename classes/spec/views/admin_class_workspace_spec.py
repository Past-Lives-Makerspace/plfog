"""BDD specs for the per-class admin Workspace tabs."""

from __future__ import annotations

from django.urls import reverse

from classes.factories import ClassOfferingFactory, DiscountCodeFactory, RegistrationFactory
from classes.models import Registration


def describe_class_registrations_tab():
    def it_gates_behind_admin(member_user, client, db):
        offering = ClassOfferingFactory()
        client.force_login(member_user)
        resp = client.get(reverse("classes:admin_class_registrations", kwargs={"pk": offering.pk}))
        assert resp.status_code == 403

    def it_shows_a_registrant(admin_user, client, db):
        offering = ClassOfferingFactory()
        RegistrationFactory(class_offering=offering, first_name="Jess", last_name="Park")
        client.force_login(admin_user)
        resp = client.get(reverse("classes:admin_class_registrations", kwargs={"pk": offering.pk}))
        assert resp.status_code == 200
        assert b"Jess" in resp.content

    def it_shows_the_subtab_nav(admin_user, client, db):
        offering = ClassOfferingFactory()
        client.force_login(admin_user)
        resp = client.get(reverse("classes:admin_class_registrations", kwargs={"pk": offering.pk}))
        assert reverse("classes:admin_class_waitlist", kwargs={"pk": offering.pk}).encode() in resp.content
        assert reverse("classes:admin_class_discount_codes", kwargs={"pk": offering.pk}).encode() in resp.content


def describe_class_waitlist_tab():
    def it_lists_waitlisted_registrants(admin_user, client, db):
        offering = ClassOfferingFactory()
        RegistrationFactory(
            class_offering=offering, first_name="Wait", last_name="Lister", status=Registration.Status.WAITLISTED
        )
        client.force_login(admin_user)
        resp = client.get(reverse("classes:admin_class_waitlist", kwargs={"pk": offering.pk}))
        assert resp.status_code == 200
        assert b"Wait" in resp.content

    def it_shows_the_waitlist_count_in_the_nav(admin_user, client, db):
        offering = ClassOfferingFactory()
        RegistrationFactory(class_offering=offering, status=Registration.Status.WAITLISTED)
        client.force_login(admin_user)
        resp = client.get(reverse("classes:admin_class_waitlist", kwargs={"pk": offering.pk}))
        assert b"Waitlist (1)" in resp.content

    def it_empty_states_when_no_waitlist(admin_user, client, db):
        offering = ClassOfferingFactory()
        client.force_login(admin_user)
        resp = client.get(reverse("classes:admin_class_waitlist", kwargs={"pk": offering.pk}))
        assert resp.status_code == 200
        assert b"Waitlist (0)" in resp.content


def describe_class_discount_codes_tab():
    def it_shows_a_class_scoped_code(admin_user, client, db):
        offering = ClassOfferingFactory()
        DiscountCodeFactory(code="CLASS10", class_offering=offering)
        client.force_login(admin_user)
        resp = client.get(reverse("classes:admin_class_discount_codes", kwargs={"pk": offering.pk}))
        assert resp.status_code == 200
        assert b"CLASS10" in resp.content

    def it_also_shows_global_codes(admin_user, client, db):
        offering = ClassOfferingFactory()
        DiscountCodeFactory(code="GLOBAL5", class_offering=None)
        client.force_login(admin_user)
        resp = client.get(reverse("classes:admin_class_discount_codes", kwargs={"pk": offering.pk}))
        assert b"GLOBAL5" in resp.content


def describe_class_overview_tab():
    def it_renders_summary_and_actions(admin_user, client, db):
        from classes.models import ClassOffering

        offering = ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED)
        client.force_login(admin_user)
        resp = client.get(reverse("classes:admin_class_detail", kwargs={"pk": offering.pk}))
        assert resp.status_code == 200
        # Summary + Edit action present
        assert reverse("classes:admin_class_edit", kwargs={"pk": offering.pk}).encode() in resp.content
        # Sub-tab nav present (Overview is now part of the workspace)
        assert reverse("classes:admin_class_registrations", kwargs={"pk": offering.pk}).encode() in resp.content

    def it_no_longer_shows_the_inline_student_email_form(admin_user, client, db):
        offering = ClassOfferingFactory()
        RegistrationFactory(class_offering=offering)
        client.force_login(admin_user)
        resp = client.get(reverse("classes:admin_class_detail", kwargs={"pk": offering.pk}))
        # The bulk-email POST form moved to the Registrations tab; Overview no longer posts to admin_class_email.
        assert reverse("classes:admin_class_email", kwargs={"pk": offering.pk}).encode() not in resp.content

    def it_counts_only_confirmed_in_the_capacity_row(admin_user, client, db):
        offering = ClassOfferingFactory(capacity=1)
        RegistrationFactory(class_offering=offering, status=Registration.Status.CONFIRMED)
        # A waitlisted person must NOT inflate the "X/capacity registered" summary.
        RegistrationFactory(class_offering=offering, status=Registration.Status.WAITLISTED)
        client.force_login(admin_user)
        resp = client.get(reverse("classes:admin_class_detail", kwargs={"pk": offering.pk}))
        assert b"1/1 registered" in resp.content


def describe_workspace_counts():
    def it_excludes_cancelled_from_the_registration_badge(admin_user, client, db):
        offering = ClassOfferingFactory()
        RegistrationFactory(class_offering=offering, status=Registration.Status.CONFIRMED)
        RegistrationFactory(class_offering=offering, status=Registration.Status.CANCELLED)
        client.force_login(admin_user)
        resp = client.get(reverse("classes:admin_class_registrations", kwargs={"pk": offering.pk}))
        assert b"Registrations (1)" in resp.content


def describe_class_scoped_discount_code_create():
    def it_returns_to_the_class_discount_codes_tab(admin_user, client, db):
        offering = ClassOfferingFactory()
        client.force_login(admin_user)
        resp = client.post(
            reverse("classes:admin_discount_code_create") + f"?class={offering.pk}",
            {"code": "BACK10", "discount_pct": 10, "is_active": "on"},
        )
        assert resp.status_code == 302
        assert resp["Location"] == reverse("classes:admin_class_discount_codes", kwargs={"pk": offering.pk})
