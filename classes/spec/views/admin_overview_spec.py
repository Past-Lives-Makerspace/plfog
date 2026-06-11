"""BDD specs for the admin Overview dashboard."""

from __future__ import annotations

from datetime import timedelta

from django.urls import reverse
from django.utils import timezone


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

    def describe_upcoming_classes():
        def it_lists_a_published_class_with_a_session_this_week(admin_user, client, db):
            from classes.factories import ClassOfferingFactory, ClassSessionFactory
            from classes.models import ClassOffering

            client.force_login(admin_user)
            offering = ClassOfferingFactory(title="Wheel Throwing", status=ClassOffering.Status.PUBLISHED)
            ClassSessionFactory(class_offering=offering, starts_at=timezone.now() + timedelta(days=2))
            resp = client.get(reverse("classes:admin_overview"))
            assert offering in list(resp.context["upcoming_classes"])

        def it_excludes_a_class_whose_next_session_is_after_this_week(admin_user, client, db):
            from classes.factories import ClassOfferingFactory, ClassSessionFactory
            from classes.models import ClassOffering

            client.force_login(admin_user)
            offering = ClassOfferingFactory(title="Far Off", status=ClassOffering.Status.PUBLISHED)
            ClassSessionFactory(class_offering=offering, starts_at=timezone.now() + timedelta(days=20))
            resp = client.get(reverse("classes:admin_overview"))
            assert offering not in list(resp.context["upcoming_classes"])

        def it_excludes_an_unpublished_class(admin_user, client, db):
            from classes.factories import ClassOfferingFactory, ClassSessionFactory
            from classes.models import ClassOffering

            client.force_login(admin_user)
            offering = ClassOfferingFactory(title="Draft Class", status=ClassOffering.Status.DRAFT)
            ClassSessionFactory(class_offering=offering, starts_at=timezone.now() + timedelta(days=2))
            resp = client.get(reverse("classes:admin_overview"))
            assert offering not in list(resp.context["upcoming_classes"])

        def it_lists_a_class_once_when_it_has_several_sessions_this_week(admin_user, client, db):
            from classes.factories import ClassOfferingFactory, ClassSessionFactory
            from classes.models import ClassOffering

            client.force_login(admin_user)
            offering = ClassOfferingFactory(title="Twice Weekly", status=ClassOffering.Status.PUBLISHED)
            ClassSessionFactory(class_offering=offering, starts_at=timezone.now() + timedelta(days=1))
            ClassSessionFactory(class_offering=offering, starts_at=timezone.now() + timedelta(days=4))
            resp = client.get(reverse("classes:admin_overview"))
            assert list(resp.context["upcoming_classes"]).count(offering) == 1

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

        def it_names_the_actor_who_performed_an_event(admin_user, client, db):
            from django.contrib.auth import get_user_model

            from classes.models import CmsActivity

            client.force_login(admin_user)
            user = get_user_model().objects.create_user(
                username="zelda@example.com", email="zelda@example.com", first_name="Zelda", last_name="Forge"
            )
            CmsActivity.objects.create(kind=CmsActivity.Kind.CLASS_CREATED, actor=user)
            resp = client.get(reverse("classes:admin_overview"))
            assert b"Zelda Forge" in resp.content

        def it_falls_back_to_system_for_an_actorless_event(admin_user, client, db):
            from classes.models import CmsActivity

            client.force_login(admin_user)
            CmsActivity.objects.create(kind=CmsActivity.Kind.CLASS_CREATED)
            resp = client.get(reverse("classes:admin_overview"))
            assert b"System" in resp.content

    def describe_stats():
        def it_counts_a_registration_in_the_window(admin_user, client, db):
            from classes.factories import RegistrationFactory

            client.force_login(admin_user)
            RegistrationFactory()
            resp = client.get(reverse("classes:admin_overview"))
            assert resp.status_code == 200
            assert resp.context["stats"]["new_regs"] == 1

        def it_sums_collected_confirmed_cents(admin_user, client, db):
            from classes.factories import RegistrationFactory
            from classes.models import Registration

            client.force_login(admin_user)
            RegistrationFactory(amount_paid_cents=4500, status=Registration.Status.CONFIRMED)
            resp = client.get(reverse("classes:admin_overview"))
            assert resp.context["stats"]["collected"] == 4500

        def it_excludes_non_confirmed_amounts_from_collected(admin_user, client, db):
            from classes.factories import RegistrationFactory
            from classes.models import Registration

            client.force_login(admin_user)
            # A cancelled registration that nonetheless carries a paid amount must not
            # count toward "collected" — only confirmed money is collected money.
            RegistrationFactory(amount_paid_cents=9900, status=Registration.Status.CANCELLED)
            resp = client.get(reverse("classes:admin_overview"))
            assert resp.context["stats"]["collected"] == 0

        def it_builds_a_daily_registration_series_for_the_default_window(admin_user, client, db):
            client.force_login(admin_user)
            resp = client.get(reverse("classes:admin_overview"))
            # Default range is the last 7 days, so the chart spans 7 daily buckets.
            assert len(resp.context["reg_by_day"]) == 7

        def it_counts_todays_registration_in_the_series(admin_user, client, db):
            import datetime

            from classes.factories import RegistrationFactory

            client.force_login(admin_user)
            RegistrationFactory()
            resp = client.get(reverse("classes:admin_overview"))
            today = datetime.date.today()
            today_entry = next(d for d in resp.context["reg_by_day"] if d["date"] == today)
            assert today_entry["count"] == 1

    def describe_range_filter():
        def it_defaults_to_the_last_7_days(admin_user, client, db):
            client.force_login(admin_user)
            resp = client.get(reverse("classes:admin_overview"))
            assert resp.context["selected_range"]["key"] == "7"

        def it_excludes_registrations_older_than_the_default_window(admin_user, client, db):
            from classes.factories import RegistrationFactory
            from classes.models import Registration

            client.force_login(admin_user)
            reg = RegistrationFactory()
            Registration.objects.filter(pk=reg.pk).update(registered_at=timezone.now() - timedelta(days=10))
            resp = client.get(reverse("classes:admin_overview"))
            assert resp.context["stats"]["new_regs"] == 0

        def it_honors_an_explicit_range(admin_user, client, db):
            from classes.factories import RegistrationFactory
            from classes.models import Registration

            client.force_login(admin_user)
            reg = RegistrationFactory()
            Registration.objects.filter(pk=reg.pk).update(registered_at=timezone.now() - timedelta(days=10))
            resp = client.get(reverse("classes:admin_overview"), {"range": "30"})
            assert resp.context["selected_range"]["key"] == "30"
            assert resp.context["stats"]["new_regs"] == 1

        def it_falls_back_to_the_default_for_an_unknown_range(admin_user, client, db):
            client.force_login(admin_user)
            resp = client.get(reverse("classes:admin_overview"), {"range": "bogus"})
            assert resp.context["selected_range"]["key"] == "7"

        def it_counts_all_time_when_requested(admin_user, client, db):
            from classes.factories import RegistrationFactory
            from classes.models import Registration

            client.force_login(admin_user)
            reg = RegistrationFactory()
            Registration.objects.filter(pk=reg.pk).update(registered_at=timezone.now() - timedelta(days=200))
            resp = client.get(reverse("classes:admin_overview"), {"range": "all"})
            assert resp.context["selected_range"]["key"] == "all"
            assert resp.context["stats"]["new_regs"] == 1

        def it_adapts_the_chart_length_to_a_short_range(admin_user, client, db):
            client.force_login(admin_user)
            resp = client.get(reverse("classes:admin_overview"), {"range": "3"})
            assert len(resp.context["reg_by_day"]) == 3

        def it_bounds_the_chart_for_all_time(admin_user, client, db):
            client.force_login(admin_user)
            resp = client.get(reverse("classes:admin_overview"), {"range": "all"})
            # "All time" has no day span, so the chart falls back to a bounded 30-day view.
            assert len(resp.context["reg_by_day"]) == 30
