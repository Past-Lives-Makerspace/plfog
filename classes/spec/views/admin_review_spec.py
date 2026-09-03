"""BDD specs for the admin class-review view and approve shortcut."""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.urls import reverse

from classes.factories import ClassOfferingFactory, CategoryFactory
from classes.models import ClassApproval, ClassOffering


def describe_admin_class_review():
    def it_renders_200_for_admin(admin_user, client, db):
        offering = ClassOfferingFactory(status=ClassOffering.Status.DRAFT)
        offering.submit_for_review()
        client.force_login(admin_user)
        response = client.get(reverse("classes:admin_class_review", kwargs={"pk": offering.pk}))
        assert response.status_code == 200

    def it_creates_an_approval_row_for_a_pending_offering_if_none_exists(admin_user, client, db):
        offering = ClassOfferingFactory(status=ClassOffering.Status.PENDING)
        client.force_login(admin_user)
        response = client.get(reverse("classes:admin_class_review", kwargs={"pk": offering.pk}))
        assert response.status_code == 200
        assert offering.approvals.exists()

    def it_redirects_to_admin_class_review_after_post(admin_user, client, db):
        offering = ClassOfferingFactory(status=ClassOffering.Status.DRAFT)
        offering.submit_for_review()
        client.force_login(admin_user)
        response = client.post(
            reverse("classes:admin_class_review", kwargs={"pk": offering.pk}),
            {"decision": ClassApproval.Decision.APPROVED, "notes": ""},
        )
        assert response.status_code == 302
        assert response.url == reverse("classes:admin_class_review", kwargs={"pk": offering.pk})

    def it_gates_behind_admin_role(member_user, client, db):
        offering = ClassOfferingFactory(status=ClassOffering.Status.DRAFT)
        client.force_login(member_user)
        response = client.get(reverse("classes:admin_class_review", kwargs={"pk": offering.pk}))
        assert response.status_code == 403

    def describe_non_pending_offerings():
        """Only a PENDING class is reviewable — the page never mints rows or accepts decisions otherwise."""

        def it_does_not_mint_an_approval_row_for_a_draft(admin_user, client, db):
            offering = ClassOfferingFactory(status=ClassOffering.Status.DRAFT)
            client.force_login(admin_user)
            response = client.get(reverse("classes:admin_class_review", kwargs={"pk": offering.pk}))
            assert response.status_code == 200
            assert b"not awaiting review" in response.content
            assert not offering.approvals.exists()

        def it_rejects_an_approve_decision_on_a_draft(admin_user, client, db):
            offering = ClassOfferingFactory(status=ClassOffering.Status.DRAFT)
            client.force_login(admin_user)
            response = client.post(
                reverse("classes:admin_class_review", kwargs={"pk": offering.pk}),
                {"decision": ClassApproval.Decision.APPROVED, "notes": ""},
            )
            assert response.status_code == 200
            offering.refresh_from_db()
            assert offering.status == ClassOffering.Status.DRAFT
            assert not offering.approvals.exists()

        def it_rejects_an_approve_decision_on_an_archived_class(admin_user, client, db):
            offering = ClassOfferingFactory(status=ClassOffering.Status.ARCHIVED)
            client.force_login(admin_user)
            response = client.post(
                reverse("classes:admin_class_review", kwargs={"pk": offering.pk}),
                {"decision": ClassApproval.Decision.APPROVED, "notes": ""},
            )
            assert response.status_code == 200
            offering.refresh_from_db()
            assert offering.status == ClassOffering.Status.ARCHIVED

        def it_does_not_touch_publish_stamps_when_reapproving_a_published_class(admin_user, client, db):
            from django.utils import timezone

            stamp = timezone.now()
            offering = ClassOfferingFactory(
                status=ClassOffering.Status.PUBLISHED, approved_by=admin_user, published_at=stamp
            )
            client.force_login(admin_user)
            response = client.post(
                reverse("classes:admin_class_review", kwargs={"pk": offering.pk}),
                {"decision": ClassApproval.Decision.APPROVED, "notes": ""},
            )
            assert response.status_code == 200
            offering.refresh_from_db()
            assert offering.status == ClassOffering.Status.PUBLISHED
            assert offering.approved_by == admin_user
            assert offering.published_at == stamp

        def it_refuses_a_stale_tokenized_link_on_a_draft(client, db):
            # A leftover undecided row (e.g. after a bounce to DRAFT) must not let
            # the emailed token page record a decision on a non-pending class.
            offering = ClassOfferingFactory(status=ClassOffering.Status.DRAFT)
            row = ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.GUILD_LEAD)
            response = client.post(
                reverse("classes:class_review", kwargs={"token": row.token}),
                {"decision": ClassApproval.Decision.APPROVED, "notes": ""},
            )
            assert response.status_code == 200
            assert b"not awaiting review" in response.content
            offering.refresh_from_db()
            row.refresh_from_db()
            assert offering.status == ClassOffering.Status.DRAFT
            assert row.decision == ""

        def it_keeps_a_published_class_published_on_a_rejection_post(admin_user, client, db):
            offering = ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED)
            client.force_login(admin_user)
            response = client.post(
                reverse("classes:admin_class_review", kwargs={"pk": offering.pk}),
                {"decision": ClassApproval.Decision.CHANGES_REQUESTED, "notes": "Too late."},
            )
            assert response.status_code == 200
            offering.refresh_from_db()
            assert offering.status == ClassOffering.Status.PUBLISHED


def _make_guilded_category(db):
    """Create a Category linked to a Guild that has a guild lead with an email."""
    from membership.models import Guild, Member, MembershipPlan

    User = get_user_model()
    plan, _ = MembershipPlan.objects.get_or_create(name="Standard", defaults={"monthly_price": "50.00"})
    lead_user, _ = User.objects.get_or_create(
        username="guildlead@example.com", defaults={"email": "guildlead@example.com"}
    )
    lead_member, _ = Member.objects.get_or_create(
        user=lead_user, defaults={"full_legal_name": "Guild Lead", "membership_plan": plan}
    )
    guild = Guild.objects.create(name="Test Guild for Review", guild_lead=lead_member)
    cat = CategoryFactory(guild=guild)
    return cat


def describe_admin_class_approve():
    def it_publishes_even_while_the_guild_gate_is_open(admin_user, client, db):
        """Admin approval is final: approving publishes immediately and closes the guild-lead gate."""
        cat = _make_guilded_category(db)
        offering = ClassOfferingFactory(status=ClassOffering.Status.DRAFT, category=cat)
        offering.submit_for_review()

        client.force_login(admin_user)
        response = client.post(reverse("classes:admin_class_approve", kwargs={"pk": offering.pk}))
        assert response.status_code == 302
        offering.refresh_from_db()
        assert offering.status == ClassOffering.Status.PUBLISHED
        assert not offering.approvals.filter(decision="").exists()
