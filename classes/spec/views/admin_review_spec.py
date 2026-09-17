"""BDD specs for the admin class-review view and approve shortcut."""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.urls import reverse

from classes.factories import ClassOfferingFactory, CategoryFactory
from classes.models import ClassApproval, ClassOffering


def describe_admin_class_review():
    def it_renders_200_for_admin(admin_user, client, db):
        offering = ClassOfferingFactory(ready=True, status=ClassOffering.Status.DRAFT)
        offering.submit_for_review()
        client.force_login(admin_user)
        response = client.get(reverse("classes:admin_class_review", kwargs={"pk": offering.pk}))
        assert response.status_code == 200

    def it_creates_an_approval_row_for_a_pending_offering_if_none_exists(admin_user, client, db):
        offering = ClassOfferingFactory(ready=True, status=ClassOffering.Status.PENDING)
        client.force_login(admin_user)
        response = client.get(reverse("classes:admin_class_review", kwargs={"pk": offering.pk}))
        assert response.status_code == 200
        assert offering.approvals.exists()

    def it_redirects_to_admin_class_review_after_post(admin_user, client, db):
        offering = ClassOfferingFactory(ready=True, status=ClassOffering.Status.DRAFT)
        offering.submit_for_review()
        client.force_login(admin_user)
        response = client.post(
            reverse("classes:admin_class_review", kwargs={"pk": offering.pk}),
            {"decision": ClassApproval.Decision.APPROVED, "notes": ""},
        )
        assert response.status_code == 302
        assert response.url == reverse("classes:admin_class_review", kwargs={"pk": offering.pk})

    def it_gates_behind_admin_role(member_user, client, db):
        offering = ClassOfferingFactory(ready=True, status=ClassOffering.Status.DRAFT)
        client.force_login(member_user)
        response = client.get(reverse("classes:admin_class_review", kwargs={"pk": offering.pk}))
        assert response.status_code == 404

    def describe_non_pending_offerings():
        """Only a PENDING class is reviewable — the page never mints rows or accepts decisions otherwise."""

        def it_does_not_mint_an_approval_row_for_a_draft(admin_user, client, db):
            offering = ClassOfferingFactory(ready=True, status=ClassOffering.Status.DRAFT)
            client.force_login(admin_user)
            response = client.get(reverse("classes:admin_class_review", kwargs={"pk": offering.pk}))
            assert response.status_code == 200
            assert b"not awaiting review" in response.content
            assert not offering.approvals.exists()

        def it_rejects_an_approve_decision_on_a_draft(admin_user, client, db):
            offering = ClassOfferingFactory(ready=True, status=ClassOffering.Status.DRAFT)
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
            offering = ClassOfferingFactory(ready=True, status=ClassOffering.Status.ARCHIVED)
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
                ready=True, status=ClassOffering.Status.PUBLISHED, approved_by=admin_user, published_at=stamp
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
            offering = ClassOfferingFactory(ready=True, status=ClassOffering.Status.DRAFT)
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
            offering = ClassOfferingFactory(ready=True, status=ClassOffering.Status.PUBLISHED)
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
        offering = ClassOfferingFactory(ready=True, status=ClassOffering.Status.DRAFT, category=cat)
        offering.submit_for_review()

        client.force_login(admin_user)
        response = client.post(reverse("classes:admin_class_approve", kwargs={"pk": offering.pk}))
        assert response.status_code == 302
        offering.refresh_from_db()
        assert offering.status == ClassOffering.Status.PUBLISHED
        assert not offering.approvals.filter(decision="").exists()


def _both_lanes_open(**kwargs) -> tuple[ClassOffering, ClassApproval, ClassApproval]:
    """A submitted class under a guild with a lead: both lanes open, lead row first."""
    offering = ClassOfferingFactory(
        ready=True, status=ClassOffering.Status.DRAFT, category=_make_guilded_category(None), **kwargs
    )
    lead_row, admin_row = offering.submit_for_review()
    return offering, lead_row, admin_row


HOLD_HELP_TEXT = "The class stays unpublished until the guild lead confirms the room is free."


def describe_the_admins_two_approvals():
    """Approve and publish, or approve and hold for the room check.

    The hold is a FORM value, not a fourth reviewer verdict: it records an ordinary APPROVED
    row with ``publish_now=False``, so nothing in the data model means "yes, but".
    """

    def it_offers_the_hold_only_while_the_guild_lane_is_open(admin_user, client, db):
        offering, _lead_row, _admin_row = _both_lanes_open()
        client.force_login(admin_user)
        html = client.get(reverse("classes:admin_class_review", kwargs={"pk": offering.pk})).content.decode()
        assert "Approve and publish" in html
        assert "Approve, hold for the room check" in html
        assert 'value="approved_hold"' in html
        # Human-approved copy, verbatim: it says exactly what holding costs.
        assert HOLD_HELP_TEXT in html

    def it_offers_no_hold_when_the_category_has_no_guild_lead(admin_user, client, db):
        offering = ClassOfferingFactory(ready=True, status=ClassOffering.Status.DRAFT)
        offering.submit_for_review()
        client.force_login(admin_user)
        html = client.get(reverse("classes:admin_class_review", kwargs={"pk": offering.pk})).content.decode()
        assert "Approve, hold for the room check" not in html
        assert HOLD_HELP_TEXT not in html
        assert 'value="approved"' in html

    def it_offers_no_hold_once_the_guild_lead_has_answered(admin_user, client, db):
        offering, lead_row, _admin_row = _both_lanes_open()
        lead_row.decide(ClassApproval.Decision.APPROVED)
        client.force_login(admin_user)
        html = client.get(reverse("classes:admin_class_review", kwargs={"pk": offering.pk})).content.decode()
        assert "Approve, hold for the room check" not in html

    def it_offers_no_hold_on_the_guild_leads_own_lane(client, db):
        _offering, lead_row, _admin_row = _both_lanes_open()
        html = client.get(reverse("classes:class_review", kwargs={"token": lead_row.token})).content.decode()
        assert "Approve, hold for the room check" not in html
        assert 'value="approved"' in html

    def it_records_the_hold_as_an_approval_that_publishes_nothing(admin_user, client, db):
        offering, lead_row, admin_row = _both_lanes_open()
        client.force_login(admin_user)
        response = client.post(
            reverse("classes:admin_class_review", kwargs={"pk": offering.pk}),
            {"decision": "approved_hold", "notes": ""},
        )
        assert response.status_code == 302
        admin_row.refresh_from_db()
        lead_row.refresh_from_db()
        assert admin_row.decision == ClassApproval.Decision.APPROVED
        # The lane being held for stays open — it is not overridden, and it is not decided.
        assert lead_row.decision == ""
        offering.refresh_from_db()
        assert offering.status == ClassOffering.Status.PENDING

    def it_publishes_and_closes_the_open_lane_on_approve_and_publish(admin_user, client, db):
        offering, lead_row, admin_row = _both_lanes_open()
        client.force_login(admin_user)
        response = client.post(
            reverse("classes:admin_class_review", kwargs={"pk": offering.pk}),
            {"decision": ClassApproval.Decision.APPROVED, "notes": ""},
        )
        assert response.status_code == 302
        offering.refresh_from_db()
        lead_row.refresh_from_db()
        admin_row.refresh_from_db()
        assert offering.status == ClassOffering.Status.PUBLISHED
        assert admin_row.decision == ClassApproval.Decision.APPROVED
        assert lead_row.decision == ClassApproval.Decision.OVERRIDDEN_BY_ADMIN


def describe_a_held_class():
    def it_reuses_the_decided_admin_row_instead_of_minting_a_second(admin_user, client, db):
        """The GET row-creator used to mint a second ADMIN row on a held class.

        With two rows, one decided and one open, ``awaiting_admin()`` reported the admin lane
        open again on a class an admin had already answered, and the overview listed it back
        in "Waiting on You".
        """
        offering, _lead_row, admin_row = _both_lanes_open()
        admin_row.decide(ClassApproval.Decision.APPROVED, user=admin_user, publish_now=False)
        client.force_login(admin_user)
        html = client.get(reverse("classes:admin_class_review", kwargs={"pk": offering.pk})).content.decode()

        assert offering.approvals.filter(role=ClassApproval.Role.ADMIN).count() == 1
        assert not ClassOffering.objects.awaiting_admin().filter(pk=offering.pk).exists()
        assert ClassOffering.objects.awaiting_admin_held().filter(pk=offering.pk).exists()
        # The page shows the decision it already carries rather than a second blank form.
        assert "You already decided this approved." in html
        assert 'name="decision"' not in html


def describe_an_overridden_guild_lead_lane():
    """A lane an admin shut by publishing over it is never drawn as a yes the guild gave."""

    def it_tells_the_lead_the_admin_closed_it_rather_than_that_they_decided_it(admin_user, client, db):
        offering, lead_row, _admin_row = _both_lanes_open()
        offering.approve(admin_user)
        lead_row.refresh_from_db()
        assert lead_row.decision == ClassApproval.Decision.OVERRIDDEN_BY_ADMIN

        html = client.get(reverse("classes:class_review", kwargs={"token": lead_row.token})).content.decode()
        assert "This review was closed when an admin published the class." in html
        assert "You already decided this" not in html
        assert "overridden by admin" not in html
        # Neutral panel, not the green one every real verdict gets.
        assert "pl-review-decided--closed" in html

    def it_draws_the_lane_on_the_strip_without_a_tick(admin_user, client, db):
        offering, lead_row, _admin_row = _both_lanes_open()
        offering.approve(admin_user)
        html = client.get(reverse("classes:class_review", kwargs={"token": lead_row.token})).content.decode()
        assert "pl-pipeline__step--overridden" in html
        assert "Closed when an admin published" in html


def describe_the_two_lane_strip():
    def it_forks_the_review_column_and_names_both_reviewers(admin_user, client, db):
        offering, _lead_row, _admin_row = _both_lanes_open()
        client.force_login(admin_user)
        html = client.get(reverse("classes:admin_class_review", kwargs={"pk": offering.pk})).content.decode()
        headline = "Waiting on the guild lead (Test Guild for Review) and an admin"
        assert "pl-pipeline__col--parallel" in html
        assert 'data-column="review"' in html
        assert f'aria-label="{headline}"' in html
        # Criterion 26: the sentence read out loud is the sentence printed under the strip.
        assert f'<p class="pl-pipeline__headline">{headline}</p>' in html

    def it_draws_one_lane_for_a_category_with_no_guild_lead(admin_user, client, db):
        offering = ClassOfferingFactory(ready=True, status=ClassOffering.Status.DRAFT)
        offering.submit_for_review()
        client.force_login(admin_user)
        html = client.get(reverse("classes:admin_class_review", kwargs={"pk": offering.pk})).content.decode()
        assert "pl-pipeline__col--parallel" not in html
        assert 'data-step="admin"' in html
        assert 'data-step="guild_lead"' not in html

    def it_is_included_on_every_surface_that_draws_it():
        """Four include statements across three templates.

        ``templates/classes/admin/class_detail.html`` was the fourth surface and was deleted
        with #403; the count is of includes, not of files, because the class overview draws
        the strip twice (carded for the instructor, bare for everyone else).
        """
        from pathlib import Path

        root = Path(__file__).resolve().parents[3] / "templates"
        surfaces = [
            root / "classes" / "_components" / "class_composer.html",
            root / "classes" / "admin" / "class_review.html",
            root / "classes" / "teach" / "class_overview.html",
        ]
        includes = sum(p.read_text().count('include "classes/_components/review_pipeline.html"') for p in surfaces)
        assert includes == 4
