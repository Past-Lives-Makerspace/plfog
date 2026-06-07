"""BDD specs for the admin activity feed view."""

from __future__ import annotations

from django.urls import reverse

from classes.factories import ClassOfferingFactory, RegistrationFactory
from classes.models import CmsActivity


def describe_admin_activity():
    def it_returns_200_for_admin(admin_user, client, db):
        response = client.get(reverse("classes:admin_activity"))
        # unauthenticated → redirect
        assert response.status_code == 302
        client.force_login(admin_user)
        response = client.get(reverse("classes:admin_activity"))
        assert response.status_code == 200

    def it_returns_403_for_non_admin(member_user, client, db):
        client.force_login(member_user)
        response = client.get(reverse("classes:admin_activity"))
        assert response.status_code == 403

    def it_shows_all_events_by_default(admin_user, client, db):
        offering = ClassOfferingFactory()
        client.force_login(admin_user)
        response = client.get(reverse("classes:admin_activity"))
        assert response.status_code == 200
        assert CmsActivity.objects.filter(class_offering=offering).exists()

    def it_filters_by_group(admin_user, client, db):
        ClassOfferingFactory()
        RegistrationFactory()
        client.force_login(admin_user)
        response = client.get(reverse("classes:admin_activity") + "?group=classes")
        assert response.status_code == 200
        # All returned events should be class-lifecycle kinds
        class_kinds = {
            CmsActivity.Kind.CLASS_CREATED,
            CmsActivity.Kind.CLASS_SUBMITTED,
            CmsActivity.Kind.CLASS_APPROVED,
            CmsActivity.Kind.CLASS_CHANGES_REQUESTED,
            CmsActivity.Kind.CLASS_DENIED,
            CmsActivity.Kind.CLASS_PUBLISHED,
            CmsActivity.Kind.CLASS_ARCHIVED,
        }
        for event in response.context["events"]:
            assert event.kind in class_kinds

    def it_filters_by_search_on_class_title(admin_user, client, db):
        ClassOfferingFactory(title="Pottery Basics")
        ClassOfferingFactory(title="Laser Cutting")
        client.force_login(admin_user)
        response = client.get(reverse("classes:admin_activity") + "?q=pottery")
        assert response.status_code == 200
        titles = [e.class_offering.title for e in response.context["events"] if e.class_offering]
        assert all("Pottery" in t for t in titles)

    def it_returns_empty_on_no_match(admin_user, client, db):
        client.force_login(admin_user)
        response = client.get(reverse("classes:admin_activity") + "?q=zzznomatchzzz")
        assert response.status_code == 200
        assert list(response.context["events"]) == []
