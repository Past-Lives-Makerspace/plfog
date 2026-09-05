"""The class review surfaces admit CMS Administrators (CLASS_APPROVER holders) alongside fog-admins.

Scope is deliberately tight: the classes list, class detail, approve action, and
review page — the surfaces the capability's notifications point at. Every other
classes admin surface stays admin-only.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from classes.factories import CategoryFactory, ClassOfferingFactory, UserFactory
from classes.models import ClassApproval, ClassOffering


@pytest.fixture
def cms_admin_user(db):
    """A plain member holding the CLASS_APPROVER capability (a CMS Administrator)."""
    from membership.models import AdminCapability, Member
    from tests.membership.factories import MembershipPlanFactory

    MembershipPlanFactory()
    user = UserFactory(username="cms@example.com")
    member = Member.objects.get(user=user)
    member.admin_capabilities.create(capability=AdminCapability.Capability.CLASS_APPROVER)
    return user


@pytest.fixture
def guilded_pending_offering(db):
    """A PENDING offering whose guild-lead gate is still open (admin gate not yet created)."""
    from membership.models import Member
    from tests.membership.factories import GuildFactory

    lead_user = UserFactory(username="strip-lead@example.com")
    lead = Member.objects.get(user=lead_user)
    guild = GuildFactory(name="Capability Guild", guild_lead=lead)
    cat = CategoryFactory(guild=guild)
    offering = ClassOfferingFactory(ready=True, slug="cap-pending", category=cat, status=ClassOffering.Status.PENDING)
    ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.GUILD_LEAD)
    return offering


def describe_review_capability_access():
    def it_admits_cms_administrators_to_the_review_surfaces(cms_admin_user, guilded_pending_offering, client):
        client.force_login(cms_admin_user)
        for name, kwargs in [
            ("classes:admin_classes", {}),
            ("classes:admin_class_detail", {"pk": guilded_pending_offering.pk}),
            ("classes:admin_class_review", {"pk": guilded_pending_offering.pk}),
        ]:
            assert client.get(reverse(name, kwargs=kwargs)).status_code == 200, f"CMS Administrator blocked from {name}"

    def it_lets_a_cms_administrator_open_the_student_preview(cms_admin_user, guilded_pending_offering, client):
        # The review page embeds this preview in an iframe — it must not 403 on them.
        client.force_login(cms_admin_user)
        response = client.get(reverse("classes:class_preview", kwargs={"pk": guilded_pending_offering.pk}))
        assert response.status_code == 200

    def it_lets_a_cms_administrator_approve_and_publish(cms_admin_user, guilded_pending_offering, client):
        client.force_login(cms_admin_user)
        response = client.post(reverse("classes:admin_class_approve", kwargs={"pk": guilded_pending_offering.pk}))
        assert response.status_code == 302
        guilded_pending_offering.refresh_from_db()
        assert guilded_pending_offering.status == ClassOffering.Status.PUBLISHED

    def it_keeps_other_classes_admin_surfaces_admin_only(cms_admin_user, client):
        client.force_login(cms_admin_user)
        for name in (
            "classes:admin_overview",
            "classes:admin_categories",
            "classes:admin_settings",
        ):
            assert client.get(reverse(name)).status_code == 403, f"CMS Administrator wrongly admitted to {name}"

    def it_forbids_a_plain_member(member_user, guilded_pending_offering, client):
        client.force_login(member_user)
        assert client.get(reverse("classes:admin_classes")).status_code == 403
        response = client.post(reverse("classes:admin_class_approve", kwargs={"pk": guilded_pending_offering.pk}))
        assert response.status_code == 403
        guilded_pending_offering.refresh_from_db()
        assert guilded_pending_offering.status == ClassOffering.Status.PENDING

    def it_forbids_holders_of_an_unrelated_capability(db, guilded_pending_offering, client):
        from membership.models import AdminCapability, Member

        user = UserFactory(username="billing-cap@example.com")
        member = Member.objects.get(user=user)
        member.admin_capabilities.create(capability=AdminCapability.Capability.BILLING_APPROVER)
        client.force_login(user)
        assert client.get(reverse("classes:admin_classes")).status_code == 403
        assert (
            client.get(reverse("classes:admin_class_review", kwargs={"pk": guilded_pending_offering.pk})).status_code
            == 403
        )

    def describe_detail_page_controls():
        """Nothing on the detail page dead-ends in a 403 for a CMS Administrator."""

        def _admin_only_urls(offering):
            return [
                reverse("classes:admin_class_edit", kwargs={"pk": offering.pk}),
                reverse("classes:admin_class_duplicate", kwargs={"pk": offering.pk}),
                reverse("classes:admin_class_archive", kwargs={"pk": offering.pk}),
                reverse("classes:admin_class_delete", kwargs={"pk": offering.pk}),
                reverse("classes:admin_class_registrations", kwargs={"pk": offering.pk}),
                reverse("classes:admin_class_waitlist", kwargs={"pk": offering.pk}),
                reverse("classes:admin_class_discount_codes", kwargs={"pk": offering.pk}),
                reverse("classes:admin_class_emails", kwargs={"pk": offering.pk}),
            ]

        def it_shows_only_approve_and_review_to_a_cms_administrator(cms_admin_user, guilded_pending_offering, client):
            client.force_login(cms_admin_user)
            response = client.get(reverse("classes:admin_class_detail", kwargs={"pk": guilded_pending_offering.pk}))
            html = response.content.decode()
            assert reverse("classes:admin_class_approve", kwargs={"pk": guilded_pending_offering.pk}) in html
            assert "Review with notes" in html
            for url in _admin_only_urls(guilded_pending_offering):
                assert url not in html, f"CMS Administrator sees dead-end control {url}"

        def it_shows_every_control_to_a_full_admin(admin_user, guilded_pending_offering, client):
            client.force_login(admin_user)
            response = client.get(reverse("classes:admin_class_detail", kwargs={"pk": guilded_pending_offering.pk}))
            html = response.content.decode()
            assert reverse("classes:admin_class_approve", kwargs={"pk": guilded_pending_offering.pk}) in html
            for url in _admin_only_urls(guilded_pending_offering):
                assert url in html, f"full admin missing control {url}"

    def describe_view_as_preview():
        def it_still_admits_an_admin_previewing_another_role(admin_user, client, db):
            # The admin leg checks the *actual* role — a view-as preview can't revoke it.
            client.force_login(admin_user)
            session = client.session
            session["view_as_role"] = "member"
            session.save()
            assert client.get(reverse("classes:admin_classes")).status_code == 200

        def it_still_admits_a_capability_holder_under_a_preview(cms_admin_user, client):
            # The capability leg reads the linked member directly — preview-independent.
            client.force_login(cms_admin_user)
            session = client.session
            session["view_as_role"] = "member"
            session.save()
            assert client.get(reverse("classes:admin_classes")).status_code == 200
