"""BDD specs for the two-lane (Guild Lead ∥ Admin) class-approval state machine.

Replaces ``approval_sequencing_spec.py``. Both review gates open at submit and neither waits
for the other, so what used to be "stage one then stage two" is now two independent lanes
with two ways to close: an admin publishing over an open lane, or an admin holding their
approval until the guild lead answers.
"""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError

from classes.factories import CategoryFactory, ClassOfferingFactory, InstructorFactory
from classes.models import ADMIN_OVERRIDE_NOTE, ClassApproval, ClassOffering
from core.models import Notification
from tests.membership.factories import GuildFactory, MembershipPlanFactory

Decision = ClassApproval.Decision
Role = ClassApproval.Role


@pytest.fixture
def guild_lead_user(db):
    """A User whose auto-created Member leads a guild, with a real email."""
    MembershipPlanFactory()
    User = get_user_model()
    user = User.objects.create_user(username="lead@example.com", email="lead@example.com")
    from membership.models import Member

    member = Member.objects.get(user=user)
    member.full_legal_name = "Lead Person"
    member.save(update_fields=["full_legal_name"])
    return user


@pytest.fixture
def guilded_offering(db, guild_lead_user, settings):
    """A DRAFT offering whose category links a guild led by ``guild_lead_user``."""
    from membership.models import Member

    settings.CLASS_ADMIN_NOTIFY_EMAILS = "admin@example.com"
    lead = Member.objects.get(user=guild_lead_user)
    guild = GuildFactory(name="Forge Guild", guild_lead=lead)
    cat = CategoryFactory(guild=guild)
    instructor = InstructorFactory(full_legal_name="Iris Smith", instructor_slug="iris")
    return ClassOfferingFactory(ready=True, category=cat, instructor=instructor, status=ClassOffering.Status.DRAFT)


def _rows_by_role(offering: ClassOffering) -> dict[str, ClassApproval]:
    return {row.role: row for row in offering.approvals.all()}


def _lead(user):
    from membership.models import Member

    return Member.objects.get(user=user)


def describe_submit_for_review():
    def describe_with_a_guild_lead():
        def it_opens_both_gates_in_one_cycle(guilded_offering):
            rows = guilded_offering.submit_for_review()
            guilded_offering.refresh_from_db()
            assert guilded_offering.status == ClassOffering.Status.PENDING
            assert [row.role for row in rows] == [Role.GUILD_LEAD, Role.ADMIN]
            assert guilded_offering.approvals.count() == 2
            assert {row.decision for row in rows} == {""}

        def it_logs_both_opened_gates_on_the_activity_row(guilded_offering):
            from classes.models import CmsActivity

            guilded_offering.submit_for_review()
            entry = guilded_offering.activity.get(kind=CmsActivity.Kind.CLASS_SUBMITTED)
            assert entry.payload["opened_gates"] == [Role.GUILD_LEAD, Role.ADMIN]

    def describe_without_a_guild_lead():
        def it_opens_only_the_admin_gate(db, settings):
            settings.CLASS_ADMIN_NOTIFY_EMAILS = "admin@example.com"
            offering = ClassOfferingFactory(
                ready=True, category=CategoryFactory(guild=None), status=ClassOffering.Status.DRAFT
            )
            rows = offering.submit_for_review()
            assert len(rows) == 1
            assert offering.approvals.count() == 1
            assert rows[0].role == Role.ADMIN


def describe_guild_lead_approves():
    def it_asks_the_existing_open_admin_row_for_the_last_word(guilded_offering, guild_lead_user, admin_user):
        from membership.models import AdminCapability, Member

        Member.objects.get(user=admin_user).admin_capabilities.create(
            capability=AdminCapability.Capability.CLASS_APPROVER
        )
        guilded_offering.submit_for_review()
        rows = _rows_by_role(guilded_offering)
        admin_pk = rows[Role.ADMIN].pk
        rows[Role.GUILD_LEAD].decide(Decision.APPROVED, user=guild_lead_user)
        guilded_offering.refresh_from_db()

        assert guilded_offering.status == ClassOffering.Status.PENDING
        # No second admin row: submit already opened the one the escalation fires against.
        admin_rows = guilded_offering.approvals.filter(role=Role.ADMIN)
        assert [row.pk for row in admin_rows] == [admin_pk]
        assert admin_rows.get().decision == ""
        # Two rows on this trigger, not one: the admin lane opened (and said so) at submit,
        # and this second row is the news that the guild lead has now signed off.
        notes = list(
            Notification.objects.filter(user=admin_user, trigger="class_validation_requested").order_by("created_at")
        )
        assert len(notes) == 2
        assert "Lead Person" in notes[-1].body

    def it_links_the_in_app_row_to_the_logged_in_admin_review_screen(guilded_offering, guild_lead_user, admin_user):
        from django.urls import reverse

        from membership.models import AdminCapability, Member

        Member.objects.get(user=admin_user).admin_capabilities.create(
            capability=AdminCapability.Capability.CLASS_APPROVER
        )
        guilded_offering.submit_for_review()
        _rows_by_role(guilded_offering)[Role.GUILD_LEAD].decide(Decision.APPROVED, user=guild_lead_user)
        # No bearer token for the admin lane: a CLASS_APPROVER holder is a logged-in reviewer
        # whose grant already opens this screen on any class.
        expected = reverse("classes:admin_class_review", kwargs={"pk": guilded_offering.pk})
        for note in Notification.objects.filter(user=admin_user, trigger="class_validation_requested"):
            assert note.url == expected


def describe_admin_approves_and_publishes():
    def it_publishes_over_an_open_guild_lead_lane(guilded_offering, admin_user):
        guilded_offering.submit_for_review()
        guilded_offering.approve(admin_user)
        guilded_offering.refresh_from_db()
        assert guilded_offering.status == ClassOffering.Status.PUBLISHED
        assert guilded_offering.approved_by == admin_user
        assert guilded_offering.published_at is not None

    def it_closes_that_lane_as_overridden_not_approved(guilded_offering, admin_user):
        guilded_offering.submit_for_review()
        guilded_offering.approve(admin_user)
        gl_row = guilded_offering.approvals.get(role=Role.GUILD_LEAD)
        assert gl_row.decision == Decision.OVERRIDDEN_BY_ADMIN
        assert gl_row.decided_by is None
        assert gl_row.decided_at is not None
        # The note literal is what backfill_override_decisions matches on; keep it verbatim.
        assert gl_row.notes == ADMIN_OVERRIDE_NOTE

    def it_does_not_credit_the_guild_with_an_approval(guilded_offering, admin_user):
        guilded_offering.submit_for_review()
        guilded_offering.approve(admin_user)
        guilded_offering.refresh_from_db()
        # The guild page's "{{ guild.name }} approved these" reads this, and must stay empty.
        assert guilded_offering.guild_lead_approved_at is None

    def it_drops_the_class_out_of_the_guild_lead_queue(guilded_offering, guild_lead_user, admin_user):
        guilded_offering.submit_for_review()
        lead = _lead(guild_lead_user)
        assert ClassOffering.objects.awaiting_guild_lead(lead).count() == 1
        guilded_offering.approve(admin_user)
        assert ClassOffering.objects.awaiting_guild_lead(lead).count() == 0

    def it_refuses_an_unready_class_before_touching_a_row(guilded_offering, admin_user):
        guilded_offering.submit_for_review()
        guilded_offering.sessions.all().delete()
        with pytest.raises(ValidationError):
            guilded_offering.approve(admin_user)
        guilded_offering.refresh_from_db()
        assert guilded_offering.status == ClassOffering.Status.PENDING
        assert {row.decision for row in guilded_offering.approvals.all()} == {""}


def describe_admin_approves_and_holds():
    def it_records_the_approval_and_publishes_nothing(guilded_offering, admin_user):
        guilded_offering.submit_for_review()
        row = guilded_offering.approve(admin_user, publish_now=False)
        guilded_offering.refresh_from_db()
        assert row.decision == Decision.APPROVED
        assert guilded_offering.status == ClassOffering.Status.PENDING
        assert guilded_offering.published_at is None
        assert guilded_offering.approvals.get(role=Role.GUILD_LEAD).decision == ""

    def it_publishes_when_the_lead_then_approves_attributed_to_the_admin(guilded_offering, admin_user, guild_lead_user):
        guilded_offering.submit_for_review()
        guilded_offering.approve(admin_user, publish_now=False)
        gl_row = guilded_offering.approvals.get(role=Role.GUILD_LEAD)
        gl_row.decide(Decision.APPROVED, user=guild_lead_user)
        guilded_offering.refresh_from_db()
        assert guilded_offering.status == ClassOffering.Status.PUBLISHED
        assert guilded_offering.approved_by == admin_user
        assert guilded_offering.guild_lead_approved_at is not None

    def it_lands_in_the_held_queue_and_leaves_it_on_publication(guilded_offering, admin_user, guild_lead_user):
        guilded_offering.submit_for_review()
        guilded_offering.approve(admin_user, publish_now=False)
        assert list(ClassOffering.objects.awaiting_admin_held()) == [guilded_offering]
        guilded_offering.approvals.get(role=Role.GUILD_LEAD).decide(Decision.APPROVED, user=guild_lead_user)
        assert ClassOffering.objects.awaiting_admin_held().count() == 0

    def it_never_mints_a_second_admin_row_on_a_second_approve(guilded_offering, admin_user):
        guilded_offering.submit_for_review()
        first = guilded_offering.approve(admin_user, publish_now=False)
        second = guilded_offering.approve(admin_user)
        guilded_offering.refresh_from_db()
        assert second.pk == first.pk
        assert guilded_offering.approvals.filter(role=Role.ADMIN).count() == 1
        assert guilded_offering.status == ClassOffering.Status.PUBLISHED
        # A held class reads its admin lane closed, so the queue never claims it open again.
        assert ClassOffering.objects.awaiting_admin().count() == 0

    def it_refuses_a_lead_approval_that_would_publish_an_unready_class(guilded_offering, admin_user, guild_lead_user):
        """The readiness pre-guard covers any decision that will publish, not only the admin's."""
        guilded_offering.submit_for_review()
        guilded_offering.approve(admin_user, publish_now=False)
        guilded_offering.sessions.all().delete()
        gl_row = guilded_offering.approvals.get(role=Role.GUILD_LEAD)
        with pytest.raises(ValidationError):
            gl_row.decide(Decision.APPROVED, user=guild_lead_user)
        gl_row.refresh_from_db()
        guilded_offering.refresh_from_db()
        assert gl_row.decision == ""
        assert guilded_offering.status == ClassOffering.Status.PENDING

    def it_lets_a_lead_request_changes_on_a_held_class(guilded_offering, admin_user, guild_lead_user):
        guilded_offering.submit_for_review()
        guilded_offering.approve(admin_user, publish_now=False)
        gl_row = guilded_offering.approvals.get(role=Role.GUILD_LEAD)
        gl_row.decide(Decision.CHANGES_REQUESTED, user=guild_lead_user, notes="The kiln is booked.")
        guilded_offering.refresh_from_db()
        assert guilded_offering.status == ClassOffering.Status.DRAFT
        assert guilded_offering.published_at is None


def describe_guild_lead_approves_with_no_admin_row_at_all():
    def it_creates_nothing_and_publishes_nothing(guilded_offering, guild_lead_user):
        """A cycle from before both lanes opened together. Nothing is minted behind anyone's back."""
        guilded_offering.submit_for_review()
        guilded_offering.approvals.filter(role=Role.ADMIN).delete()
        gl_row = guilded_offering.approvals.get(role=Role.GUILD_LEAD)
        gl_row.decide(Decision.APPROVED, user=guild_lead_user)
        guilded_offering.refresh_from_db()
        assert guilded_offering.status == ClassOffering.Status.PENDING
        assert not guilded_offering.approvals.filter(role=Role.ADMIN).exists()

    def it_publishes_nothing_when_the_lead_approval_is_itself_held(guilded_offering, admin_user, guild_lead_user):
        """``publish_now=False`` publishes nothing whichever lane passes it."""
        guilded_offering.submit_for_review()
        guilded_offering.approve(admin_user, publish_now=False)
        gl_row = guilded_offering.approvals.get(role=Role.GUILD_LEAD)
        gl_row.decide(Decision.APPROVED, user=guild_lead_user, publish_now=False)
        guilded_offering.refresh_from_db()
        assert guilded_offering.status == ClassOffering.Status.PENDING
        assert guilded_offering.published_at is None


def describe_without_a_guild_lead_admin_approves():
    def it_publishes_directly(db, admin_user, settings):
        settings.CLASS_ADMIN_NOTIFY_EMAILS = "admin@example.com"
        offering = ClassOfferingFactory(
            ready=True, category=CategoryFactory(guild=None), status=ClassOffering.Status.DRAFT
        )
        (admin_row,) = offering.submit_for_review()
        admin_row.class_offering = offering
        admin_row.decide(Decision.APPROVED, user=admin_user)
        offering.refresh_from_db()
        assert offering.status == ClassOffering.Status.PUBLISHED


def describe_guild_lead_requests_changes():
    def it_returns_to_draft_leaving_the_admin_lane_undecided(guilded_offering, guild_lead_user):
        guilded_offering.submit_for_review()
        rows = _rows_by_role(guilded_offering)
        rows[Role.GUILD_LEAD].decide(Decision.CHANGES_REQUESTED, user=guild_lead_user, notes="Add prerequisites.")
        guilded_offering.refresh_from_db()
        assert guilded_offering.status == ClassOffering.Status.DRAFT
        assert guilded_offering.approvals.get(role=Role.ADMIN).decision == ""


def describe_submit_notifications():
    def it_notifies_the_guild_lead(guilded_offering, guild_lead_user):
        guilded_offering.submit_for_review()
        note = Notification.objects.get(user=guild_lead_user, trigger="class_review_requested")
        assert "Iris Smith" in note.body
        assert "Forge Guild" in note.body


def describe_decide_guards():
    def it_refuses_a_decision_when_the_offering_is_not_pending(db):
        offering = ClassOfferingFactory(ready=True, status=ClassOffering.Status.DRAFT)
        row = ClassApproval.objects.create(class_offering=offering, role=Role.ADMIN)
        with pytest.raises(ValueError, match="Only pending"):
            row.decide(Decision.APPROVED)
        row.refresh_from_db()
        assert row.decision == ""
        offering.refresh_from_db()
        assert offering.status == ClassOffering.Status.DRAFT

    def it_refuses_the_override_marker_as_a_reviewer_verdict(db, admin_user):
        """OVERRIDDEN_BY_ADMIN is written by the override path, never recorded by a reviewer."""
        offering = ClassOfferingFactory(ready=True, status=ClassOffering.Status.DRAFT)
        (row,) = offering.submit_for_review()
        with pytest.raises(ValueError, match="Unknown decision"):
            row.decide(Decision.OVERRIDDEN_BY_ADMIN, user=admin_user)
        row.refresh_from_db()
        assert row.decision == ""

    def it_reads_the_status_off_the_locked_row_inside_one_transaction(db, admin_user, monkeypatch):
        """SQLite drops select_for_update() silently, so assert the structure, not the blocking.

        The real serialization is specced against Postgres in
        ``tests/e2e/class_review_serialization_spec.py``. Here: the offering row is locked, it
        is the first lock taken, and ``row.class_offering`` is never rebound to it — the pin
        ``approve()`` sets has to survive, or the publish mutates a different instance.
        """
        from django.db import transaction as db_transaction

        offering = ClassOfferingFactory(ready=True, status=ClassOffering.Status.DRAFT)
        (row,) = offering.submit_for_review()
        row.class_offering = offering
        seen: list[str] = []

        original_atomic = db_transaction.atomic
        original_sfu = ClassOffering.objects.get_queryset().__class__.select_for_update

        def spy_atomic(*args, **kwargs):
            seen.append("atomic")
            return original_atomic(*args, **kwargs)

        def spy_select_for_update(self, *args, **kwargs):
            seen.append("select_for_update")
            return original_sfu(self, *args, **kwargs)

        monkeypatch.setattr("classes.models.transaction.atomic", spy_atomic)
        monkeypatch.setattr(ClassOffering.objects.get_queryset().__class__, "select_for_update", spy_select_for_update)

        row.decide(Decision.APPROVED, user=admin_user)

        assert seen[:2] == ["atomic", "select_for_update"]
        assert row.class_offering is offering
        assert offering.status == ClassOffering.Status.PUBLISHED


def describe_awaiting_admin_validation():
    """The lead-side counterpart queue: classes they approved that wait on the admin lane."""

    def it_lists_a_class_after_the_lead_approves(guilded_offering, guild_lead_user):
        guilded_offering.submit_for_review()
        lead = _lead(guild_lead_user)
        assert ClassOffering.objects.awaiting_admin_validation(lead).count() == 0
        _rows_by_role(guilded_offering)[Role.GUILD_LEAD].decide(Decision.APPROVED, user=guild_lead_user)
        assert list(ClassOffering.objects.awaiting_admin_validation(lead)) == [guilded_offering]

    def it_excludes_guilds_the_member_does_not_lead(guilded_offering, guild_lead_user, db):
        from membership.models import Member

        guilded_offering.submit_for_review()
        _rows_by_role(guilded_offering)[Role.GUILD_LEAD].decide(Decision.APPROVED, user=guild_lead_user)
        other_lead_member = Member.objects.get(user=get_user_model().objects.create_user(username="other@example.com"))
        GuildFactory(name="Other Guild", guild_lead=other_lead_member)
        assert ClassOffering.objects.awaiting_admin_validation(other_lead_member).count() == 0

    def it_empties_once_the_admin_publishes(guilded_offering, guild_lead_user, admin_user):
        guilded_offering.submit_for_review()
        _rows_by_role(guilded_offering)[Role.GUILD_LEAD].decide(Decision.APPROVED, user=guild_lead_user)
        guilded_offering.refresh_from_db()
        guilded_offering.approve(admin_user)
        assert ClassOffering.objects.awaiting_admin_validation(_lead(guild_lead_user)).count() == 0

    def it_never_fires_on_a_lane_an_admin_overrode(guilded_offering, guild_lead_user, admin_user):
        """An overridden lane is not an approval, so it cannot put the class in this list."""
        guilded_offering.submit_for_review()
        guilded_offering.approve(admin_user)
        assert ClassOffering.objects.awaiting_admin_validation(_lead(guild_lead_user)).count() == 0


def describe_instructor_approved_notification():
    @pytest.fixture
    def offering_with_instructor_user(db, guild_lead_user, settings):
        """A guilded DRAFT offering whose instructor has a real, notifiable User."""
        from membership.models import Member

        settings.CLASS_ADMIN_NOTIFY_EMAILS = "admin@example.com"
        user_model = get_user_model()
        instr_user = user_model.objects.create_user(username="iris@example.com", email="iris@example.com")
        instructor = InstructorFactory(user=instr_user, full_legal_name="Iris Smith", instructor_slug="iris")
        lead = Member.objects.get(user=guild_lead_user)
        guild = GuildFactory(name="Forge Guild", guild_lead=lead)
        cat = CategoryFactory(guild=guild)
        offering = ClassOfferingFactory(
            ready=True, category=cat, instructor=instructor, status=ClassOffering.Status.DRAFT
        )
        return offering, instr_user

    def it_does_not_notify_the_instructor_on_a_lead_approval_alone(offering_with_instructor_user, guild_lead_user):
        offering, instr_user = offering_with_instructor_user
        offering.submit_for_review()
        _rows_by_role(offering)[Role.GUILD_LEAD].decide(Decision.APPROVED, user=guild_lead_user)
        offering.refresh_from_db()
        # The lead approved, but the admin lane is still open — the instructor must not yet
        # be told the class was "approved".
        assert offering.status == ClassOffering.Status.PENDING
        assert not Notification.objects.filter(user=instr_user, trigger="instructor_class_approved").exists()

    def it_notifies_the_instructor_exactly_once_on_publication(
        offering_with_instructor_user, guild_lead_user, admin_user
    ):
        from classes.emails import send_class_review_decision

        offering, instr_user = offering_with_instructor_user
        offering.submit_for_review()
        _rows_by_role(offering)[Role.GUILD_LEAD].decide(Decision.APPROVED, user=guild_lead_user)
        offering.refresh_from_db()
        admin_row = offering.approvals.get(role=Role.ADMIN, decision="")
        admin_row.class_offering = offering
        admin_row.decide(Decision.APPROVED, user=admin_user)
        offering.refresh_from_db()
        admin_row.refresh_from_db()
        assert offering.status == ClassOffering.Status.PUBLISHED
        # The "approved" bell row + the "live!" email both fan out from the single
        # ``instructor_class_approved`` event the view emits via send_class_review_decision
        # after the publishing decision — exactly one bell row, even across the two lanes.
        send_class_review_decision(offering, admin_row)
        assert Notification.objects.filter(user=instr_user, trigger="instructor_class_approved").count() == 1
