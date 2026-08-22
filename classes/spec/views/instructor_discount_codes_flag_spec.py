"""Instructor discount-code surface behind the site flag (default off in production).

The autouse ``_instructor_discount_codes_on`` fixture in ``classes/spec/conftest.py``
keeps every pre-flag spec on today's behavior; the flag-off specs here flip the flag
back off explicitly to exercise the gated path.
"""

from __future__ import annotations

from django.urls import reverse

from classes.factories import CategoryFactory, ClassOfferingFactory
from core.models import SiteConfiguration
from membership.models import Member


def _set_flag(value: bool) -> None:
    config = SiteConfiguration.load()
    config.instructor_discount_codes_enabled = value
    config.save(update_fields=["instructor_discount_codes_enabled"])


def _own_offering(member_user) -> object:
    return ClassOfferingFactory(category=CategoryFactory(), instructor=Member.objects.get(user=member_user))


def describe_instructor_discount_codes_flag_off():
    def it_hides_the_teach_nav_tile(client, member_user):
        _set_flag(False)
        client.force_login(member_user)
        resp = client.get(reverse("classes:teach_dashboard"))
        assert resp.status_code == 200
        assert reverse("classes:teach_discount_codes").encode() not in resp.content

    def it_hides_the_per_class_subtab(client, member_user):
        _set_flag(False)
        offering = _own_offering(member_user)
        client.force_login(member_user)
        resp = client.get(reverse("classes:teach_class_detail", kwargs={"pk": offering.pk}))
        assert resp.status_code == 200
        assert reverse("classes:teach_class_discount_codes", kwargs={"pk": offering.pk}).encode() not in resp.content

    def it_hides_the_inline_section_on_the_class_edit_form(client, member_user):
        _set_flag(False)
        offering = _own_offering(member_user)
        client.force_login(member_user)
        resp = client.get(reverse("classes:teach_class_edit", kwargs={"pk": offering.pk}))
        assert resp.status_code == 200
        assert b"Discount Codes for This Class" not in resp.content

    def it_redirects_the_list_view_to_the_teach_dashboard(client, member_user):
        _set_flag(False)
        client.force_login(member_user)
        resp = client.get(reverse("classes:teach_discount_codes"))
        assert resp.status_code == 302
        assert resp["Location"] == reverse("classes:teach_dashboard")

    def it_redirects_the_create_view(client, member_user):
        _set_flag(False)
        client.force_login(member_user)
        resp = client.get(reverse("classes:teach_discount_code_create"))
        assert resp.status_code == 302
        assert resp["Location"] == reverse("classes:teach_dashboard")

    def it_redirects_the_edit_view(client, member_user):
        _set_flag(False)
        client.force_login(member_user)
        # The gate short-circuits before the view body, so no code row is needed.
        resp = client.get(reverse("classes:teach_discount_code_edit", kwargs={"pk": 9999}))
        assert resp.status_code == 302
        assert resp["Location"] == reverse("classes:teach_dashboard")

    def it_redirects_the_delete_view(client, member_user):
        _set_flag(False)
        client.force_login(member_user)
        resp = client.post(reverse("classes:teach_discount_code_delete", kwargs={"pk": 9999}))
        assert resp.status_code == 302
        assert resp["Location"] == reverse("classes:teach_dashboard")

    def it_redirects_the_approve_view(client, member_user):
        _set_flag(False)
        client.force_login(member_user)
        resp = client.post(reverse("classes:teach_discount_code_approve", kwargs={"pk": 9999}))
        assert resp.status_code == 302
        assert resp["Location"] == reverse("classes:teach_dashboard")

    def it_redirects_the_per_class_view(client, member_user):
        _set_flag(False)
        offering = _own_offering(member_user)
        client.force_login(member_user)
        resp = client.get(reverse("classes:teach_class_discount_codes", kwargs={"pk": offering.pk}))
        assert resp.status_code == 302
        assert resp["Location"] == reverse("classes:teach_dashboard")

    def describe_admins_are_unaffected():
        def it_still_lets_admins_manage_discount_codes_from_classes_admin(client, admin_user):
            client.force_login(admin_user)
            _set_flag(False)
            assert client.get(reverse("classes:admin_discount_codes")).status_code == 200
            _set_flag(True)
            assert client.get(reverse("classes:admin_discount_codes")).status_code == 200


def describe_instructor_discount_codes_flag_on():
    # The autouse conftest fixture already flips the flag on for every spec here.
    def it_restores_the_discount_codes_list(client, member_user):
        client.force_login(member_user)
        resp = client.get(reverse("classes:teach_discount_codes"))
        assert resp.status_code == 200

    def it_shows_the_teach_nav_tile(client, member_user):
        client.force_login(member_user)
        resp = client.get(reverse("classes:teach_dashboard"))
        assert reverse("classes:teach_discount_codes").encode() in resp.content

    def it_shows_the_per_class_subtab(client, member_user):
        offering = _own_offering(member_user)
        client.force_login(member_user)
        resp = client.get(reverse("classes:teach_class_detail", kwargs={"pk": offering.pk}))
        assert reverse("classes:teach_class_discount_codes", kwargs={"pk": offering.pk}).encode() in resp.content

    def it_shows_the_inline_section_on_the_class_edit_form(client, member_user):
        offering = _own_offering(member_user)
        client.force_login(member_user)
        resp = client.get(reverse("classes:teach_class_edit", kwargs={"pk": offering.pk}))
        assert b"Discount Codes for This Class" in resp.content
