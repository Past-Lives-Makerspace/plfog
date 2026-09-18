"""BDD specs for a guild lead's approval surviving the instructor's next submission.

A guild lead signs off on dates and space. Before this, every resubmission deleted their
APPROVED row and reopened their gate, so a copy edit two rounds later re-asked them about a
schedule they had already blessed. Now the APPROVED row survives, carrying a fingerprint of
the schedule it approved, and only a schedule change asks them again.

Every spec here builds its own class and drives its own approval history through the real
API. One shared offering fixture would let a single arrangement of approval rows stand in
for all of them, and the arrangements are exactly what these specs are about.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any, cast

from django.contrib.auth import get_user_model
from django.core import mail
from django.utils import timezone

from classes.factories import CategoryFactory, ClassOfferingFactory, ClassSessionFactory, InstructorFactory
from classes.models import ClassApproval, ClassOffering, CmsActivity
from core.models import Notification
from tests.membership.factories import GuildFactory, MembershipPlanFactory

if TYPE_CHECKING:
    from django.contrib.auth.models import User

LEAD_EMAIL = "guild.lead@example.com"
OTHER_LEAD_EMAIL = "other.guild.lead@example.com"


def _make_lead_user(email: str) -> "User":
    """A User whose auto-created Member can lead a guild, reachable at ``email``."""
    from membership.models import Member

    MembershipPlanFactory()
    user = get_user_model().objects.create_user(username=email, email=email)
    member = Member.objects.get(user=user)
    member.full_legal_name = f"Lead {email.split('@')[0]}"
    member.save(update_fields=["full_legal_name"])
    return user


def _second_guild_with_lead() -> tuple[Any, "User"]:
    """A second guild, led by a second person, for the "moved to another guild" specs."""
    from membership.models import Member

    lead_user = _make_lead_user(OTHER_LEAD_EMAIL)
    guild = GuildFactory(guild_lead=Member.objects.get(user=lead_user))
    return guild, lead_user


def _guilded_draft(settings: Any, *, lead_email: str = LEAD_EMAIL) -> tuple[ClassOffering, "User"]:
    """A submittable DRAFT class in a guild whose lead has a real, reachable email.

    The premise every spec starts from, and nothing more: no approval rows exist yet. Each
    spec builds its own history on top.
    """
    from membership.models import Member

    settings.CLASS_ADMIN_NOTIFY_EMAILS = "admin@example.com"
    lead_user = _make_lead_user(lead_email)
    guild = GuildFactory(guild_lead=Member.objects.get(user=lead_user))
    instructor = InstructorFactory(full_legal_name="Iris Smith", instructor_slug="iris")
    offering = ClassOfferingFactory(
        ready=True,
        category=CategoryFactory(guild=guild),
        instructor=instructor,
        status=ClassOffering.Status.DRAFT,
    )
    return cast("ClassOffering", offering), lead_user


def _lead_approves_then_admin_bounces(
    offering: ClassOffering,
    lead_user: "User",
    admin_user: "User",
    *,
    decision: str = ClassApproval.Decision.CHANGES_REQUESTED,
) -> ClassApproval:
    """Drive one full round: submit, the lead approves, the admin sends it back to DRAFT.

    Leaves the class in DRAFT holding exactly two rows — the lead's APPROVED one and the
    admin's bounce — which is the arrangement the resubmission specs then act on. Returns
    the lead's row.
    """
    (lead_row,) = offering.submit_for_review()
    lead_row.decide(ClassApproval.Decision.APPROVED, user=lead_user)
    offering.refresh_from_db()
    admin_row = offering.approvals.get(role=ClassApproval.Role.ADMIN, decision="")
    admin_row.class_offering = offering
    admin_row.decide(decision, user=admin_user, notes="Tighten the description.")
    offering.refresh_from_db()
    assert offering.status == ClassOffering.Status.DRAFT
    lead_row.refresh_from_db()
    return lead_row


def _move_every_session(offering: ClassOffering, delta: timedelta) -> None:
    """Shift the whole schedule, keeping it in the future so readiness still passes."""
    for session in offering.sessions.all():
        session.starts_at += delta
        session.ends_at += delta
        session.save(update_fields=["starts_at", "ends_at"])


def _addressed(email: str) -> bool:
    return any(email in message.to for message in mail.outbox)


def describe_schedule_fingerprint():
    def it_gives_a_class_with_no_sessions_a_real_digest_not_an_empty_string(db):
        """The empty string means "never stamped". The empty schedule must not collide with it."""
        offering = ClassOfferingFactory()
        assert offering.sessions.count() == 0
        assert len(offering.schedule_fingerprint) == 64

    def it_changes_when_a_session_is_added(db):
        offering = ClassOfferingFactory(ready=True)
        before = offering.schedule_fingerprint
        start = timezone.now() + timedelta(days=9)
        ClassSessionFactory(class_offering=offering, starts_at=start, ends_at=start + timedelta(hours=2))
        assert offering.schedule_fingerprint != before

    def it_changes_when_a_session_is_removed(db):
        offering = ClassOfferingFactory(ready=True)
        start = timezone.now() + timedelta(days=9)
        extra = ClassSessionFactory(class_offering=offering, starts_at=start, ends_at=start + timedelta(hours=2))
        before = offering.schedule_fingerprint
        extra.delete()
        assert offering.schedule_fingerprint != before

    def it_changes_when_a_session_is_retimed(db):
        offering = ClassOfferingFactory(ready=True)
        before = offering.schedule_fingerprint
        _move_every_session(offering, timedelta(hours=1))
        assert offering.schedule_fingerprint != before

    def it_changes_when_only_the_end_time_moves(db):
        offering = ClassOfferingFactory(ready=True)
        before = offering.schedule_fingerprint
        session = offering.sessions.get()
        session.ends_at += timedelta(minutes=30)
        session.save(update_fields=["ends_at"])
        assert offering.schedule_fingerprint != before

    def it_ignores_sort_order(db):
        """Dragging the dates into a different display order is not a schedule change.

        This is the member-facing statement, not the guard on ``sorted()``:
        ``ClassSession.Meta.ordering`` already fixes iteration order, so this passes either
        way. What it does kill is a fingerprint that digested ``sort_order``. The spec below
        is the one that makes the sort itself load bearing.
        """
        offering = ClassOfferingFactory(ready=True)
        start = timezone.now() + timedelta(days=9)
        ClassSessionFactory(class_offering=offering, starts_at=start, ends_at=start + timedelta(hours=2), sort_order=1)
        before = offering.schedule_fingerprint
        for index, session in enumerate(offering.sessions.order_by("-starts_at")):
            session.sort_order = index
            session.save(update_fields=["sort_order"])
        assert offering.schedule_fingerprint == before

    def it_ignores_the_order_the_session_rows_arrive_in(db):
        """It reads ``sessions.all()``, so a prefetch chooses the order. Sorting is what
        keeps that from changing the answer.
        """
        from django.db.models import Prefetch

        from classes.models import ClassSession

        offering = ClassOfferingFactory(ready=True)
        start = timezone.now() + timedelta(days=9)
        ClassSessionFactory(class_offering=offering, starts_at=start, ends_at=start + timedelta(hours=2))
        before = offering.schedule_fingerprint

        reversed_prefetch = ClassOffering.objects.prefetch_related(
            Prefetch("sessions", queryset=ClassSession.objects.order_by("-starts_at"))
        ).get(pk=offering.pk)

        assert [s.pk for s in reversed_prefetch.sessions.all()] != [s.pk for s in offering.sessions.all()]
        assert reversed_prefetch.schedule_fingerprint == before

    def it_matches_two_classes_that_share_a_schedule(db):
        """It digests the dates, not the rows: same dates, same fingerprint."""
        start = timezone.now() + timedelta(days=4)
        first = ClassOfferingFactory()
        second = ClassOfferingFactory()
        for offering in (first, second):
            ClassSessionFactory(class_offering=offering, starts_at=start, ends_at=start + timedelta(hours=2))
        assert first.schedule_fingerprint == second.schedule_fingerprint


def describe_decide():
    def describe_when_a_guild_lead_approves():
        def it_stamps_the_schedule_they_approved(db, settings):
            offering, lead_user = _guilded_draft(settings)
            (lead_row,) = offering.submit_for_review()
            lead_row.decide(ClassApproval.Decision.APPROVED, user=lead_user)
            lead_row.refresh_from_db()
            assert lead_row.approved_schedule_fingerprint == offering.schedule_fingerprint

        def it_stamps_the_guild_they_approved_for(db, settings):
            offering, lead_user = _guilded_draft(settings)
            (lead_row,) = offering.submit_for_review()
            lead_row.decide(ClassApproval.Decision.APPROVED, user=lead_user)
            lead_row.refresh_from_db()
            assert lead_row.approved_for_guild_id == offering.category.guild_id

    def describe_when_a_guild_lead_asks_for_changes():
        def it_stamps_nothing(db, settings):
            offering, lead_user = _guilded_draft(settings)
            (lead_row,) = offering.submit_for_review()
            lead_row.decide(ClassApproval.Decision.CHANGES_REQUESTED, user=lead_user, notes="Add prerequisites.")
            lead_row.refresh_from_db()
            assert lead_row.approved_schedule_fingerprint == ""
            assert lead_row.approved_for_guild_id is None

    def describe_when_an_admin_approves():
        def it_stamps_nothing(db, settings, admin_user):
            """Only the lead's approval is a statement about the dates."""
            offering, lead_user = _guilded_draft(settings)
            (lead_row,) = offering.submit_for_review()
            lead_row.decide(ClassApproval.Decision.APPROVED, user=lead_user)
            offering.refresh_from_db()
            admin_row = offering.approvals.get(role=ClassApproval.Role.ADMIN, decision="")
            admin_row.class_offering = offering
            admin_row.decide(ClassApproval.Decision.APPROVED, user=admin_user)
            admin_row.refresh_from_db()
            assert admin_row.approved_schedule_fingerprint == ""
            assert admin_row.approved_for_guild_id is None


def describe_resubmitting_after_a_lead_approved():
    def describe_when_the_dates_are_unchanged():
        def it_opens_the_admin_gate_and_never_re_asks_the_lead(db, settings, admin_user):
            """Acceptance criterion 1."""
            offering, lead_user = _guilded_draft(settings)
            lead_row = _lead_approves_then_admin_bounces(offering, lead_user, admin_user)
            mail.outbox.clear()
            notices_before = Notification.objects.filter(user=lead_user, trigger="class_review_requested").count()

            (row,) = offering.submit_for_review()

            assert row.role == ClassApproval.Role.ADMIN
            assert row.decision == ""
            assert list(offering.approvals.filter(role=ClassApproval.Role.GUILD_LEAD)) == [lead_row]
            assert not _addressed(LEAD_EMAIL)
            assert (
                Notification.objects.filter(user=lead_user, trigger="class_review_requested").count() == notices_before
            )

        def it_drops_out_of_the_lead_queue_and_into_the_admin_queue(db, settings, admin_user):
            """Trap 4: a skipped lead must not linger in a review queue."""
            from membership.models import Member

            offering, lead_user = _guilded_draft(settings)
            _lead_approves_then_admin_bounces(offering, lead_user, admin_user)
            lead = Member.objects.get(user=lead_user)

            offering.submit_for_review()

            assert ClassOffering.objects.awaiting_guild_lead(lead).count() == 0
            assert list(ClassOffering.objects.awaiting_admin()) == [offering]
            assert list(ClassOffering.objects.awaiting_admin_validation(lead)) == [offering]

        def it_lets_the_admin_publish_on_the_second_round(db, settings, admin_user):
            """Trap 1: the surviving APPROVED row must not strand the admin gate."""
            offering, lead_user = _guilded_draft(settings)
            _lead_approves_then_admin_bounces(offering, lead_user, admin_user)
            offering.submit_for_review()

            admin_row = offering.approvals.get(role=ClassApproval.Role.ADMIN, decision="")
            admin_row.class_offering = offering
            admin_row.decide(ClassApproval.Decision.APPROVED, user=admin_user)

            offering.refresh_from_db()
            assert offering.status == ClassOffering.Status.PUBLISHED

        def it_leaves_the_skip_auditable_in_the_activity_feed(db, settings, admin_user):
            """A skip is a gate that nobody walked through, so it has to be findable after
            the fact. ``CLASS_SUBMITTED`` records both halves: guild_lead was a required
            role, and the first stage opened at admin anyway. That pair is only reachable
            through the skip, which is what makes an audit query over the feed exact.
            """
            offering, lead_user = _guilded_draft(settings)
            _lead_approves_then_admin_bounces(offering, lead_user, admin_user)

            offering.submit_for_review()

            event = offering.activity.filter(kind=CmsActivity.Kind.CLASS_SUBMITTED).order_by("-created_at").first()
            assert event is not None
            assert event.payload["first_stage"] == ClassApproval.Role.ADMIN
            assert ClassApproval.Role.GUILD_LEAD in event.payload["required_roles"]

    def describe_when_a_session_time_moved():
        def it_reopens_the_lead_gate_notifies_them_and_leaves_no_bounce_behind(db, settings, admin_user):
            """Acceptance criterion 2, on the arrangement that separates a fix from a guess:
            an APPROVED lead row, the admin's bounce row, and a changed schedule all at once.
            """
            offering, lead_user = _guilded_draft(settings)
            stale_row = _lead_approves_then_admin_bounces(offering, lead_user, admin_user)
            assert offering.approvals.filter(role=ClassApproval.Role.ADMIN).count() == 1
            _move_every_session(offering, timedelta(days=1))
            mail.outbox.clear()

            (row,) = offering.submit_for_review()

            assert row.role == ClassApproval.Role.GUILD_LEAD
            assert row.pk != stale_row.pk
            assert _addressed(LEAD_EMAIL)
            assert not offering.approvals.filter(role=ClassApproval.Role.ADMIN).exists()
            assert offering.lifecycle == ClassOffering.Lifecycle.AWAITING_GUILD_LEAD
            assert offering._is_bounced is False

        def it_shows_up_again_in_the_lead_queue(db, settings, admin_user):
            from membership.models import Member

            offering, lead_user = _guilded_draft(settings)
            _lead_approves_then_admin_bounces(offering, lead_user, admin_user)
            _move_every_session(offering, timedelta(days=1))

            offering.submit_for_review()

            lead = Member.objects.get(user=lead_user)
            assert list(ClassOffering.objects.awaiting_guild_lead(lead)) == [offering]

        def it_opens_exactly_one_fresh_admin_gate_when_the_lead_approves_again(db, settings, admin_user):
            """Trap 1 on the harder path: the old APPROVED lead row must not block escalation."""
            offering, lead_user = _guilded_draft(settings)
            _lead_approves_then_admin_bounces(offering, lead_user, admin_user)
            _move_every_session(offering, timedelta(days=1))
            (row,) = offering.submit_for_review()

            row.decide(ClassApproval.Decision.APPROVED, user=lead_user)

            offering.refresh_from_db()
            assert offering.status == ClassOffering.Status.PENDING
            admin_rows = offering.approvals.filter(role=ClassApproval.Role.ADMIN)
            assert admin_rows.count() == 1
            assert admin_rows.get().decision == ""

        def it_resolves_the_open_and_approved_lead_rows_to_the_rows_they_mean(db, settings, admin_user):
            """Trap 2: two APPROVED lead rows plus a fresh one must not confuse either property."""
            offering, lead_user = _guilded_draft(settings)
            stale_row = _lead_approves_then_admin_bounces(offering, lead_user, admin_user)
            _move_every_session(offering, timedelta(days=1))
            (fresh_row,) = offering.submit_for_review()

            assert offering.open_guild_lead_approval is not None
            assert offering.open_guild_lead_approval.pk == fresh_row.pk
            assert offering.guild_lead_approved_at == stale_row.decided_at

            fresh_row.decide(ClassApproval.Decision.APPROVED, user=lead_user)

            offering = ClassOffering.objects.get(pk=offering.pk)
            fresh_row.refresh_from_db()
            assert offering.open_guild_lead_approval is None
            assert offering.guild_lead_approved_at == fresh_row.decided_at

    def describe_when_an_admin_bounced_it_while_the_lead_gate_was_still_open():
        def it_leaves_exactly_one_open_lead_gate(db, settings, admin_user):
            """An undecided gate from the last round must not survive. The admin review page
            mints its own ADMIN row whenever the class is PENDING (``_class_review_view``),
            so an admin really can send a class back before the lead has touched it.
            """
            offering, lead_user = _guilded_draft(settings)
            (stale_gate,) = offering.submit_for_review()
            admin_row = ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.ADMIN)
            admin_row.class_offering = offering
            admin_row.decide(ClassApproval.Decision.CHANGES_REQUESTED, user=admin_user, notes="Not yet.")
            offering.refresh_from_db()

            (row,) = offering.submit_for_review()

            assert row.role == ClassApproval.Role.GUILD_LEAD
            assert list(offering.approvals.all()) == [row]
            assert not ClassApproval.objects.filter(pk=stale_gate.pk).exists()

    def describe_when_the_dates_go_back_to_a_schedule_the_lead_already_approved():
        def it_skips_the_lead_again(db, settings, admin_user):
            """Any schedule the lead has approved counts, not only the most recent one."""
            offering, lead_user = _guilded_draft(settings)
            _lead_approves_then_admin_bounces(offering, lead_user, admin_user)
            _move_every_session(offering, timedelta(days=1))
            (reopened,) = offering.submit_for_review()
            reopened.decide(ClassApproval.Decision.CHANGES_REQUESTED, user=lead_user, notes="Wrong week.")
            offering.refresh_from_db()
            _move_every_session(offering, timedelta(days=-1))

            (row,) = offering.submit_for_review()

            assert row.role == ClassApproval.Role.ADMIN

    def describe_when_the_lead_asked_for_changes_instead_of_approving():
        def it_reopens_the_lead_gate_and_keeps_no_bounce_row(db, settings):
            """Acceptance criterion 3, changes-requested half."""
            offering, lead_user = _guilded_draft(settings)
            (lead_row,) = offering.submit_for_review()
            lead_row.decide(ClassApproval.Decision.CHANGES_REQUESTED, user=lead_user, notes="Add prerequisites.")
            offering.refresh_from_db()

            (row,) = offering.submit_for_review()

            assert row.role == ClassApproval.Role.GUILD_LEAD
            assert offering.approvals.count() == 1
            assert offering._is_bounced is False
            assert offering.lifecycle == ClassOffering.Lifecycle.AWAITING_GUILD_LEAD

        def it_reopens_the_lead_gate_after_a_denial_too(db, settings):
            """Acceptance criterion 3, denied half: DENIED bounces the same way."""
            offering, lead_user = _guilded_draft(settings)
            (lead_row,) = offering.submit_for_review()
            lead_row.decide(ClassApproval.Decision.DENIED, user=lead_user, notes="Not this quarter.")
            offering.refresh_from_db()

            (row,) = offering.submit_for_review()

            assert row.role == ClassApproval.Role.GUILD_LEAD
            assert offering.approvals.count() == 1
            assert offering._is_bounced is False

    def describe_when_the_approval_predates_the_fingerprint():
        def it_asks_the_lead_again(db, settings, admin_user):
            """Acceptance criterion 5: an unstamped row is unknown, and unknown re-asks."""
            offering, lead_user = _guilded_draft(settings)
            lead_row = _lead_approves_then_admin_bounces(offering, lead_user, admin_user)
            ClassApproval.objects.filter(pk=lead_row.pk).update(approved_schedule_fingerprint="")

            (row,) = offering.submit_for_review()

            assert row.role == ClassApproval.Role.GUILD_LEAD

        def it_asks_the_lead_again_when_only_the_guild_stamp_is_missing(db, settings, admin_user):
            """A row from before ``approved_for_guild`` existed carries a null guild. Null is
            not "any guild", it is "unknown", and unknown re-asks.
            """
            offering, lead_user = _guilded_draft(settings)
            lead_row = _lead_approves_then_admin_bounces(offering, lead_user, admin_user)
            ClassApproval.objects.filter(pk=lead_row.pk).update(approved_for_guild=None)

            (row,) = offering.submit_for_review()

            assert row.role == ClassApproval.Role.GUILD_LEAD

    def describe_when_the_class_moved_to_another_guild():
        def it_asks_the_new_guild_and_never_credits_the_old_one(db, settings, admin_user):
            """The instructor can change "Guild Type" on their own composer while a bounced
            class is a draft, so guild 1's approval must not stand in for guild 2's.
            """
            from membership.models import Member

            offering, lead_user = _guilded_draft(settings)
            _lead_approves_then_admin_bounces(offering, lead_user, admin_user)
            other_guild, other_lead_user = _second_guild_with_lead()
            offering.category = CategoryFactory(guild=other_guild)
            offering.save(update_fields=["category"])
            mail.outbox.clear()

            (row,) = offering.submit_for_review()

            assert row.role == ClassApproval.Role.GUILD_LEAD
            assert _addressed(OTHER_LEAD_EMAIL)
            assert not _addressed(LEAD_EMAIL)
            new_lead = Member.objects.get(user=other_lead_user)
            assert list(ClassOffering.objects.awaiting_guild_lead(new_lead)) == [offering]

        def it_shows_the_new_guild_an_unapproved_pipeline_and_no_validation_queue(db, settings, admin_user):
            """The two surfaces that would otherwise tell guild 2 they had signed off."""
            from membership.models import Member

            offering, lead_user = _guilded_draft(settings)
            _lead_approves_then_admin_bounces(offering, lead_user, admin_user)
            other_guild, other_lead_user = _second_guild_with_lead()
            offering.category = CategoryFactory(guild=other_guild)
            offering.save(update_fields=["category"])

            offering.submit_for_review()

            new_lead = Member.objects.get(user=other_lead_user)
            assert list(ClassOffering.objects.awaiting_admin_validation(new_lead)) == []
            guild_step = next(step for step in offering.review_pipeline().steps if step.key == "guild_lead")
            assert guild_step.state == "current"
            assert "Approved" not in guild_step.detail

    def describe_when_the_category_changes_inside_the_same_guild():
        def it_still_skips_the_lead(db, settings, admin_user):
            """The guild is what approved, not the category. Re-filing a class from Forge
            Basics to Forge Advanced is not a new guild's decision.
            """
            offering, lead_user = _guilded_draft(settings)
            _lead_approves_then_admin_bounces(offering, lead_user, admin_user)
            same_guild = offering.category.guild
            offering.category = CategoryFactory(guild=same_guild)
            offering.save(update_fields=["category"])

            (row,) = offering.submit_for_review()

            assert row.role == ClassApproval.Role.ADMIN

    def describe_when_the_guild_has_a_different_lead_now():
        def it_still_skips_because_the_guild_approved_not_the_person(db, settings, admin_user):
            """A deliberate choice, pinned here: the row records that the GUILD signed off on
            these dates, and replacing the officer does not retract it.
            """
            from membership.models import Member

            offering, lead_user = _guilded_draft(settings)
            _lead_approves_then_admin_bounces(offering, lead_user, admin_user)
            successor_user = _make_lead_user("successor.lead@example.com")
            guild = offering.category.guild
            guild.guild_lead = Member.objects.get(user=successor_user)
            guild.save(update_fields=["guild_lead"])
            mail.outbox.clear()

            (row,) = offering.submit_for_review()

            assert row.role == ClassApproval.Role.ADMIN
            assert not _addressed("successor.lead@example.com")


def describe_a_resubmitted_class():
    def it_never_reads_as_bounced_on_any_surface(db, settings, admin_user):
        """Acceptance criterion 4, on the skip path: the admin's spent bounce row is gone."""
        offering, lead_user = _guilded_draft(settings)
        _lead_approves_then_admin_bounces(offering, lead_user, admin_user)

        offering.submit_for_review()

        annotated = ClassOffering.objects.with_lifecycle_inputs().get(pk=offering.pk)
        assert offering._is_bounced is False
        assert annotated.bounced is False
        assert offering.lifecycle == ClassOffering.Lifecycle.AWAITING_ADMIN
        assert annotated.lifecycle == ClassOffering.Lifecycle.AWAITING_ADMIN
        assert offering.latest_bounce_row is None
        assert list(ClassOffering.objects.changes_requested()) == []
        pipeline = offering.review_pipeline()
        assert pipeline.is_bounced is False
        # The strip remembers the lead's sign-off instead of resetting to stage one.
        assert [(step.key, step.state) for step in pipeline.steps] == [
            ("submitted", "done"),
            ("guild_lead", "done"),
            ("admin", "current"),
            ("live", "ahead"),
        ]

    def it_never_reads_as_bounced_after_a_denial_either(db, settings, admin_user):
        """The other bounce decision, on the reopen path."""
        offering, lead_user = _guilded_draft(settings)
        _lead_approves_then_admin_bounces(offering, lead_user, admin_user, decision=ClassApproval.Decision.DENIED)
        _move_every_session(offering, timedelta(days=1))

        offering.submit_for_review()

        annotated = ClassOffering.objects.with_lifecycle_inputs().get(pk=offering.pk)
        assert offering._is_bounced is False
        assert annotated.bounced is False
        assert offering.lifecycle == ClassOffering.Lifecycle.AWAITING_GUILD_LEAD
        assert offering.latest_bounce_row is None


def describe_the_full_reset_paths():
    """Acceptance criterion 6: withdraw, restore and unpublish still clear every row."""

    def it_clears_an_approved_lead_row_on_withdraw(db, settings, admin_user):
        offering, lead_user = _guilded_draft(settings)
        (lead_row,) = offering.submit_for_review()
        lead_row.decide(ClassApproval.Decision.APPROVED, user=lead_user)
        offering.refresh_from_db()
        assert offering.approvals.count() == 2

        offering.withdraw_submission()

        assert offering.approvals.count() == 0

    def it_clears_every_row_on_restore(db, settings, admin_user):
        offering, lead_user = _guilded_draft(settings)
        _lead_approves_then_admin_bounces(offering, lead_user, admin_user)
        assert offering.approvals.count() == 2
        offering.archive()

        offering.restore()

        assert offering.approvals.count() == 0

    def it_clears_every_row_on_unpublish(db, settings, admin_user):
        offering, lead_user = _guilded_draft(settings)
        (lead_row,) = offering.submit_for_review()
        lead_row.decide(ClassApproval.Decision.APPROVED, user=lead_user)
        offering.refresh_from_db()
        admin_row = offering.approvals.get(role=ClassApproval.Role.ADMIN, decision="")
        admin_row.class_offering = offering
        admin_row.decide(ClassApproval.Decision.APPROVED, user=admin_user)
        offering.refresh_from_db()
        assert offering.status == ClassOffering.Status.PUBLISHED

        offering.unpublish()

        assert offering.approvals.count() == 0
