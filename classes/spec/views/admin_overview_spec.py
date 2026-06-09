"""BDD specs for the admin Overview dashboard."""

from __future__ import annotations

from django.urls import reverse


def describe_admin_overview():
    def it_gates_behind_admin_role(member_user, client, db):
        client.force_login(member_user)
        resp = client.get(reverse("classes:admin_overview"))
        assert resp.status_code == 403

    def it_renders_for_admin(admin_user, client, db):
        client.force_login(admin_user)
        resp = client.get(reverse("classes:admin_overview"))
        assert resp.status_code == 200

    def it_is_served_at_the_admin_root(admin_user, client, db):
        assert reverse("classes:admin_overview") == "/classes/admin/"

    def describe_approvals_queue():
        def it_lists_a_pending_class(admin_user, client, db):
            from classes.factories import ClassOfferingFactory
            from classes.models import ClassOffering

            client.force_login(admin_user)
            ClassOfferingFactory(title="Forge Night", status=ClassOffering.Status.PENDING)
            resp = client.get(reverse("classes:admin_overview"))
            assert b"Forge Night" in resp.content

        def it_omits_published_classes_from_the_queue(admin_user, client, db):
            from classes.factories import ClassOfferingFactory
            from classes.models import ClassOffering

            client.force_login(admin_user)
            live = ClassOfferingFactory(title="Already Live", status=ClassOffering.Status.PUBLISHED)
            resp = client.get(reverse("classes:admin_overview"))
            # The approvals queue holds only pending classes. (A published class may
            # still legitimately appear in the Activity panel as a "Class created" event.)
            assert live not in list(resp.context["pending_classes"])

    def describe_waitlist_panel():
        def it_shows_a_class_with_a_waitlisted_registration(admin_user, client, db):
            from classes.factories import ClassOfferingFactory, RegistrationFactory
            from classes.models import ClassOffering, Registration

            client.force_login(admin_user)
            offering = ClassOfferingFactory(title="Blacksmithing", status=ClassOffering.Status.PUBLISHED)
            RegistrationFactory(class_offering=offering, status=Registration.Status.WAITLISTED)
            resp = client.get(reverse("classes:admin_overview"))
            assert b"Blacksmithing" in resp.content

    def describe_recent_registrations():
        def it_shows_a_recent_registrant_linking_to_detail(admin_user, client, db):
            from classes.factories import RegistrationFactory

            client.force_login(admin_user)
            reg = RegistrationFactory(first_name="Jess", last_name="Park")
            resp = client.get(reverse("classes:admin_overview"))
            assert b"Jess" in resp.content
            detail = reverse("classes:admin_registration_detail", kwargs={"pk": reg.pk})
            assert detail.encode() in resp.content

        def it_links_to_the_full_registrations_table(admin_user, client, db):
            client.force_login(admin_user)
            resp = client.get(reverse("classes:admin_overview"))
            assert reverse("classes:admin_registrations").encode() in resp.content

    def describe_activity_panel():
        def it_links_to_the_full_activity_log(admin_user, client, db):
            client.force_login(admin_user)
            resp = client.get(reverse("classes:admin_overview"))
            assert reverse("classes:admin_activity").encode() in resp.content

    def describe_stats():
        def it_counts_a_registration_from_this_week(admin_user, client, db):
            from classes.factories import RegistrationFactory

            client.force_login(admin_user)
            RegistrationFactory()
            resp = client.get(reverse("classes:admin_overview"))
            assert resp.status_code == 200
            assert resp.context["stats"]["new_regs_week"] == 1

        def it_sums_collected_cents_over_30_days(admin_user, client, db):
            from classes.factories import RegistrationFactory
            from classes.models import Registration

            client.force_login(admin_user)
            RegistrationFactory(amount_paid_cents=4500, status=Registration.Status.CONFIRMED)
            resp = client.get(reverse("classes:admin_overview"))
            assert resp.context["stats"]["collected_30d"] == 4500

        def it_builds_a_14_day_registration_series(admin_user, client, db):
            client.force_login(admin_user)
            resp = client.get(reverse("classes:admin_overview"))
            assert len(resp.context["reg_by_day"]) == 14
