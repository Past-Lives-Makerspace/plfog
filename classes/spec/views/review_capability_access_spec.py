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
            ("classes:teach_class_detail", {"pk": guilded_pending_offering.pk}),
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

    def it_refuses_a_plain_member(member_user, guilded_pending_offering, client):
        client.force_login(member_user)
        assert client.get(reverse("classes:admin_classes")).status_code == 403
        response = client.post(reverse("classes:admin_class_approve", kwargs={"pk": guilded_pending_offering.pk}))
        assert response.status_code == 404
        guilded_pending_offering.refresh_from_db()
        assert guilded_pending_offering.status == ClassOffering.Status.PENDING

    def it_refuses_holders_of_an_unrelated_capability(db, guilded_pending_offering, client):
        from membership.models import AdminCapability, Member

        user = UserFactory(username="billing-cap@example.com")
        member = Member.objects.get(user=user)
        member.admin_capabilities.create(capability=AdminCapability.Capability.BILLING_APPROVER)
        client.force_login(user)
        assert client.get(reverse("classes:admin_classes")).status_code == 403
        assert (
            client.get(reverse("classes:admin_class_review", kwargs={"pk": guilded_pending_offering.pk})).status_code
            == 404
        )

    def describe_detail_page_controls():
        """Nothing on the detail page dead-ends in a 403 for a CMS Administrator."""

        def _admin_only_urls(offering):
            """The controls a full admin gets and a CMS Administrator must not be shown.

            Six of the eight moved onto the merged per-class routes; the two lifecycle
            actions that never had a teaching twin kept their own.
            """
            return [
                reverse("classes:teach_class_edit", kwargs={"pk": offering.pk}),
                reverse("classes:admin_class_duplicate", kwargs={"pk": offering.pk}),
                reverse("classes:admin_class_archive", kwargs={"pk": offering.pk}),
                reverse("classes:admin_class_delete", kwargs={"pk": offering.pk}),
                reverse("classes:teach_class_registrations", kwargs={"pk": offering.pk}),
                reverse("classes:teach_class_waitlist", kwargs={"pk": offering.pk}),
                reverse("classes:teach_class_discount_codes", kwargs={"pk": offering.pk}),
                reverse("classes:teach_class_emails", kwargs={"pk": offering.pk}),
            ]

        def it_shows_only_approve_and_review_to_a_cms_administrator(cms_admin_user, guilded_pending_offering, client):
            client.force_login(cms_admin_user)
            response = client.get(reverse("classes:teach_class_detail", kwargs={"pk": guilded_pending_offering.pk}))
            html = response.content.decode()
            assert reverse("classes:admin_class_approve", kwargs={"pk": guilded_pending_offering.pk}) in html
            assert "Review with notes" in html
            for url in _admin_only_urls(guilded_pending_offering):
                assert url not in html, f"CMS Administrator sees dead-end control {url}"

        def it_shows_every_control_to_a_full_admin(admin_user, guilded_pending_offering, client):
            client.force_login(admin_user)
            response = client.get(reverse("classes:teach_class_detail", kwargs={"pk": guilded_pending_offering.pk}))
            html = response.content.decode()
            assert reverse("classes:admin_class_approve", kwargs={"pk": guilded_pending_offering.pk}) in html
            for url in _admin_only_urls(guilded_pending_offering):
                assert url in html, f"full admin missing control {url}"

    def describe_a_cms_administrator_who_leads_the_classes_guild():
        """Ruling 25, end to end: the overlap population approves from a page it can open.

        The capability alone is not the ruling. Approve and "Review with notes…" render on
        the Overview and nowhere else, the CMS Administrator's own queue
        (``classes/admin/classes_list.html``) links every row to the Overview, and
        ``admin_class_approve`` redirects back to it on every exit. So the composed set
        holding ``can_approve`` while the Overview 404s would deliver none of it.
        """

        @pytest.fixture
        def lead_with_the_grant(db, guilded_pending_offering):
            from membership.models import AdminCapability

            lead = guilded_pending_offering.category.guild.guild_lead
            lead.admin_capabilities.create(capability=AdminCapability.Capability.CLASS_APPROVER)
            lead.refresh_from_db()
            return lead

        def it_opens_the_overview_on_its_own_guilds_class(lead_with_the_grant, guilded_pending_offering, client):
            client.force_login(lead_with_the_grant.user)
            url = reverse("classes:teach_class_detail", kwargs={"pk": guilded_pending_offering.pk})
            assert client.get(url).status_code == 200

        def it_is_shown_the_approve_control(lead_with_the_grant, guilded_pending_offering, client):
            client.force_login(lead_with_the_grant.user)
            html = client.get(
                reverse("classes:teach_class_detail", kwargs={"pk": guilded_pending_offering.pk})
            ).content.decode()
            # Both controls by URL rather than by label: every hub page carries the whole
            # CHANGELOG in its context, so a copy assertion here could be satisfied one day
            # by a release note rather than by the button.
            assert reverse("classes:admin_class_approve", kwargs={"pk": guilded_pending_offering.pk}) in html
            assert reverse("classes:admin_class_review", kwargs={"pk": guilded_pending_offering.pk}) in html

        def it_can_approve_and_publish(lead_with_the_grant, guilded_pending_offering, client):
            client.force_login(lead_with_the_grant.user)
            response = client.post(
                reverse("classes:admin_class_approve", kwargs={"pk": guilded_pending_offering.pk}), follow=True
            )
            # follow=True: the redirect target is the Overview, and landing on a 404 would
            # mean they published without ever seeing that they had.
            assert response.status_code == 200
            guilded_pending_offering.refresh_from_db()
            assert guilded_pending_offering.status == ClassOffering.Status.PUBLISHED

        def it_keeps_the_composer_the_changelog_promises_it(lead_with_the_grant, guilded_pending_offering, client):
            # "Guild leads and guild staff can open a class requested under their guild,
            # edit it and write its welcome email, without needing teaching access first."
            assert lead_with_the_grant.can_create_classes is False
            client.force_login(lead_with_the_grant.user)
            for name in ("classes:teach_class_edit", "classes:teach_class_emails"):
                url = reverse(name, kwargs={"pk": guilded_pending_offering.pk})
                assert client.get(url).status_code == 200, f"composed guild lead blocked from {name}"

        def it_is_still_refused_the_roster(lead_with_the_grant, guilded_pending_offering, client):
            client.force_login(lead_with_the_grant.user)
            url = reverse("classes:teach_class_registrations", kwargs={"pk": guilded_pending_offering.pk})
            assert client.get(url).status_code == 404

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
