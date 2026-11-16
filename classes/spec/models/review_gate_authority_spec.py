"""BDD specs for who may still decide a class review gate, now that one approval publishes (#1183).

An admin only looking at a guilded class leaves it with the guild lead; a lead gate decides
only for the guild it asked and, signed out, only while the person it was sent to still leads
that guild; and of two approvals racing, only one publishes.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.core import mail
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone

from classes.factories import CategoryFactory, ClassOfferingFactory, UserFactory
from classes.models import ClassApproval, ClassInstructor, ClassOffering, CmsActivity
from classes.overdue import overdue_reviews
from membership.models import Member
from tests.membership.factories import GuildFactory


def _lead(email: str) -> Member:
    return Member.objects.get(user=UserFactory(username=email, email=email))


def _guilded_pending(lead: Member, guild_name: str = "Woodshop") -> tuple[ClassOffering, ClassApproval]:
    guild = GuildFactory(name=guild_name, guild_lead=lead)
    offering = ClassOfferingFactory(
        ready=True, status=ClassOffering.Status.DRAFT, category=CategoryFactory(guild=guild)
    )
    (gate,) = offering.submit_for_review()
    return offering, gate


def describe_an_admin_opening_the_review_page():
    def it_mints_no_admin_row_and_the_class_stays_with_the_guild_lead(db, client, admin_user):
        offering, gate = _guilded_pending(_lead("look-lead@example.com"))
        ClassApproval.objects.filter(pk=gate.pk).update(created_at=timezone.now() - timedelta(days=4))
        client.force_login(admin_user)

        response = client.get(reverse("classes:admin_class_review", kwargs={"pk": offering.pk}))

        assert response.status_code == 200
        assert not offering.approvals.filter(role=ClassApproval.Role.ADMIN).exists()
        offering.refresh_from_db()
        steps = offering.review_pipeline().steps
        assert (steps[1].key, steps[1].state) == ("guild_lead", "current")
        assert offering.lifecycle == ClassOffering.Lifecycle.AWAITING_GUILD_LEAD
        (overdue,) = [row for row in overdue_reviews() if row.offering.pk == offering.pk]
        assert overdue.gate.pk == gate.pk

    def it_still_lets_the_admin_approve_early_from_the_page(db, client, admin_user):
        offering, gate = _guilded_pending(_lead("early-lead@example.com"))
        client.force_login(admin_user)

        client.post(
            reverse("classes:admin_class_review", kwargs={"pk": offering.pk}),
            {"decision": ClassApproval.Decision.APPROVED, "notes": ""},
        )

        offering.refresh_from_db()
        assert offering.status == ClassOffering.Status.PUBLISHED
        assert offering.approvals.get(role=ClassApproval.Role.ADMIN).decided_by == admin_user
        gate.refresh_from_db()
        assert gate.decision == ClassApproval.Decision.APPROVED

    def it_strands_no_admin_row_when_an_unready_class_is_refused(db, client, admin_user):
        offering, _gate = _guilded_pending(_lead("unready-lead@example.com"))
        ClassOffering.objects.filter(pk=offering.pk).update(capacity=0)
        client.force_login(admin_user)

        response = client.post(
            reverse("classes:admin_class_review", kwargs={"pk": offering.pk}),
            {"decision": ClassApproval.Decision.APPROVED, "notes": ""},
        )

        assert "Not ready to publish" in response.content.decode()
        assert not offering.approvals.filter(role=ClassApproval.Role.ADMIN).exists()

    def it_reads_a_legacy_open_admin_row_beside_the_lead_gate_as_with_the_lead(db):
        offering, _gate = _guilded_pending(_lead("legacy-lead@example.com"))
        ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.ADMIN)
        assert offering.review_pipeline().steps[1].key == "guild_lead"


def describe_a_lead_gate_whose_class_moved_guild():
    def it_refuses_the_old_guilds_approval_when_a_bulk_update_skipped_the_repoint(db):
        lead = _lead("moved-lead@example.com")
        offering, gate = _guilded_pending(lead)
        ClassOffering.objects.filter(pk=offering.pk).update(category=CategoryFactory(guild=GuildFactory(name="Glass")))
        gate.class_offering.refresh_from_db()

        with pytest.raises(ValidationError, match="another guild"):
            gate.decide(ClassApproval.Decision.APPROVED, user=lead.user)

        gate.refresh_from_db()
        assert gate.decision == ""
        offering.refresh_from_db()
        assert offering.status == ClassOffering.Status.PENDING

    def it_reasks_the_new_guild_when_a_class_types_guild_changes(db):
        offering, old_gate = _guilded_pending(_lead("type-old-lead@example.com"))
        new_lead = _lead("type-new-lead@example.com")
        new_guild = GuildFactory(name="Leather", guild_lead=new_lead)
        category = offering.category

        category.guild = new_guild
        category.save()

        assert not ClassApproval.objects.filter(pk=old_gate.pk).exists()
        fresh = offering.approvals.get(role=ClassApproval.Role.GUILD_LEAD, decision="")
        assert fresh.opened_for_guild_id == new_guild.pk
        assert fresh.opened_for_lead_id == new_lead.pk

    def it_moves_the_class_to_an_admin_when_the_class_type_leaves_its_guild(db):
        offering, _gate = _guilded_pending(_lead("type-gone-lead@example.com"))
        category = offering.category

        category.guild = None
        category.save()

        assert not offering.approvals.filter(role=ClassApproval.Role.GUILD_LEAD, decision="").exists()
        assert offering.approvals.filter(role=ClassApproval.Role.ADMIN, decision="").count() == 1

    def it_leaves_classes_alone_when_the_guild_change_is_not_written(db):
        offering, gate = _guilded_pending(_lead("unwritten-lead@example.com"))
        category = offering.category
        original_guild_id = category.guild_id
        category.guild = GuildFactory(name="Elsewhere", guild_lead=_lead("elsewhere-lead@example.com"))
        category.save(update_fields=["name"])

        gate.refresh_from_db()
        assert gate.opened_for_guild_id == original_guild_id
        assert offering.approvals.filter(role=ClassApproval.Role.GUILD_LEAD).count() == 1

    def it_leaves_classes_alone_when_a_class_type_saves_without_a_guild_change(db):
        offering, gate = _guilded_pending(_lead("type-same-lead@example.com"))
        category = offering.category
        category.name = f"{category.name} Renamed"
        category.save()
        assert list(offering.approvals.all()) == [gate]


def describe_a_signed_out_review_link():
    def it_stamps_the_guilds_lead_when_the_gate_opens(db):
        lead = _lead("stamp-lead@example.com")
        _offering, gate = _guilded_pending(lead)
        assert gate.opened_for_lead_id == lead.pk

    def it_decides_while_that_person_still_leads_the_guild(db):
        offering, gate = _guilded_pending(_lead("still-lead@example.com"))
        gate.decide(ClassApproval.Decision.APPROVED)
        offering.refresh_from_db()
        assert offering.status == ClassOffering.Status.PUBLISHED

    def it_is_refused_once_the_lead_was_replaced(db):
        offering, gate = _guilded_pending(_lead("old-lead@example.com"))
        guild = offering.category.guild
        guild.guild_lead = _lead("new-lead@example.com")
        guild.save(update_fields=["guild_lead"])
        gate.refresh_from_db()

        with pytest.raises(ValidationError, match="past guild lead"):
            gate.decide(ClassApproval.Decision.APPROVED)

        offering.refresh_from_db()
        assert offering.status == ClassOffering.Status.PENDING

    def it_is_refused_once_the_leads_account_was_deleted(db):
        from membership.services.account_deletion import delete_own_account

        lead = _lead("gone-lead@example.com")
        offering, gate = _guilded_pending(lead)
        delete_own_account(lead)
        gate.refresh_from_db()

        with pytest.raises(ValidationError, match="past guild lead"):
            gate.decide(ClassApproval.Decision.CHANGES_REQUESTED, notes="Too late.")

        offering.refresh_from_db()
        assert offering.status == ClassOffering.Status.PENDING

    def it_still_lets_the_new_lead_decide_signed_in(db):
        offering, gate = _guilded_pending(_lead("handover-old@example.com"))
        new_lead = _lead("handover-new@example.com")
        guild = offering.category.guild
        guild.guild_lead = new_lead
        guild.save(update_fields=["guild_lead"])
        gate.refresh_from_db()

        gate.decide(ClassApproval.Decision.APPROVED, user=new_lead.user)

        offering.refresh_from_db()
        assert offering.status == ClassOffering.Status.PUBLISHED

    def it_refuses_a_signed_in_person_who_does_not_review_the_guild(db):
        _offering, gate = _guilded_pending(_lead("own-lead@example.com"))
        stranger = UserFactory(username="stranger@example.com", email="stranger@example.com")
        with pytest.raises(ValidationError, match="Only the guild lead"):
            gate.decide(ClassApproval.Decision.APPROVED, user=stranger)


def describe_two_approvals_racing():
    def it_publishes_once_and_the_second_is_a_no_op(db, admin_user):
        lead = _lead("race-lead@example.com")
        offering, gate = _guilded_pending(lead)
        stale_admin = ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.ADMIN)
        # The second request loaded the class while it was still pending.
        stale_admin.class_offering = ClassOffering.objects.get(pk=offering.pk)

        assert gate.decide(ClassApproval.Decision.APPROVED, user=lead.user) is True
        approvals_logged = CmsActivity.objects.filter(
            class_offering=offering, kind=CmsActivity.Kind.CLASS_APPROVED
        ).count()
        mail.outbox.clear()

        assert stale_admin.decide(ClassApproval.Decision.APPROVED, user=admin_user) is False

        offering.refresh_from_db()
        assert offering.status == ClassOffering.Status.PUBLISHED
        assert offering.approved_by == lead.user
        assert (
            CmsActivity.objects.filter(class_offering=offering, kind=CmsActivity.Kind.CLASS_APPROVED).count()
            == approvals_logged
        )
        assert mail.outbox == []
        stale_admin.refresh_from_db()
        assert stale_admin.decided_by is None

    def it_refuses_a_stale_quick_approve_without_leaving_an_admin_row(db, admin_user):
        lead = _lead("race-quick-lead@example.com")
        offering, gate = _guilded_pending(lead)
        stale = ClassOffering.objects.get(pk=offering.pk)
        gate.decide(ClassApproval.Decision.APPROVED, user=lead.user)

        with pytest.raises(ValueError, match="already decided"):
            stale.approve(admin_user)

        assert not offering.approvals.filter(role=ClassApproval.Role.ADMIN).exists()

    def it_records_nothing_when_a_stale_review_page_decision_loses(db, admin_user):
        lead = _lead("race-page-lead@example.com")
        offering, gate = _guilded_pending(lead)
        stale = ClassOffering.objects.get(pk=offering.pk)
        gate.decide(ClassApproval.Decision.APPROVED, user=lead.user)

        decided = stale.record_review_decision(
            ClassApproval.Decision.APPROVED,
            role=ClassApproval.Role.ADMIN,
            approval=None,
            user=admin_user,
            notes="",
        )

        assert decided is None
        assert not offering.approvals.filter(role=ClassApproval.Role.ADMIN).exists()

    def it_tells_the_reviewer_whose_post_lost_and_sends_no_email(db, client, admin_user, monkeypatch):
        lead = _lead("race-post-lead@example.com")
        offering, gate = _guilded_pending(lead)
        real_record = ClassOffering.record_review_decision

        def lead_gets_there_first(self, *args, **kwargs):
            # The lead's approval commits after the admin's page loaded the class as pending.
            gate.decide(ClassApproval.Decision.APPROVED, user=lead.user)
            mail.outbox.clear()
            return real_record(self, *args, **kwargs)

        monkeypatch.setattr(ClassOffering, "record_review_decision", lead_gets_there_first)
        client.force_login(admin_user)

        response = client.post(
            reverse("classes:admin_class_review", kwargs={"pk": offering.pk}),
            {"decision": ClassApproval.Decision.APPROVED, "notes": "Looks good"},
            follow=True,
        )

        assert "Another reviewer already decided this class." in response.content.decode()
        offering.refresh_from_db()
        assert offering.approved_by == lead.user
        assert not offering.approvals.filter(role=ClassApproval.Role.ADMIN).exists()
        assert mail.outbox == []


def describe_a_lead_who_teaches_the_class():
    def it_publishes_on_their_own_approval(db):
        # Felix, 2026-11-15 (#1183): a lead or Class Reviewer may self publish; no second look.
        lead = _lead("teaching-lead@example.com")
        offering, gate = _guilded_pending(lead)
        ClassInstructor.objects.create(class_offering=offering, member=lead)

        gate.decide(ClassApproval.Decision.APPROVED, user=lead.user)

        offering.refresh_from_db()
        assert offering.status == ClassOffering.Status.PUBLISHED
        assert not offering.approvals.filter(role=ClassApproval.Role.ADMIN).exists()
