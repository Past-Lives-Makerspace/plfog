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


def describe_opening_a_guild_lead_gate():
    """The guild is stamped when the row is minted, and that is what makes it trustworthy.

    Stamping it at decision time instead reads a field the instructor can still edit, which
    is the hole these specs hold shut.
    """

    def it_stamps_the_guild_being_asked(db, settings):
        offering, lead_user = _guilded_draft(settings)
        (lead_row,) = offering.submit_for_review()
        assert lead_row.role == ClassApproval.Role.GUILD_LEAD
        assert lead_row.opened_for_guild_id == offering.category.guild_id

    def it_stamps_no_guild_on_an_admin_gate(db, settings):
        """An admin answers for the makerspace, not for a guild."""
        settings.CLASS_ADMIN_NOTIFY_EMAILS = "admin@example.com"
        instructor = InstructorFactory(full_legal_name="Iris Smith", instructor_slug="iris")
        offering = cast(
            ClassOffering,
            ClassOfferingFactory(
                ready=True,
                category=CategoryFactory(guild=None),
                instructor=instructor,
                status=ClassOffering.Status.DRAFT,
            ),
        )
        (row,) = offering.submit_for_review()
        assert row.role == ClassApproval.Role.ADMIN
        assert row.opened_for_guild_id is None

    def describe_when_the_instructor_refiles_the_class_while_the_gate_is_open():
        def it_closes_the_old_gate_and_opens_one_for_the_new_guild(db, settings, admin_user):
            """The invitation must not outlive the question.

            ``awaiting_guild_lead`` scopes by the class's current guild, so leaving the old
            gate standing hands guild 2's staff a token minted for guild 1. Whichever guild
            the row names, one of the two is then misrepresented: name guild 1 and guild 1
            is later skipped on a decision its own lead never made; name guild 2 and guild 2
            is skipped on a decision made by a stranger.
            """
            from membership.models import Member

            offering, lead_user = _guilded_draft(settings)
            other_guild, other_lead_user = _second_guild_with_lead()
            (old_gate,) = offering.submit_for_review()
            mail.outbox.clear()

            offering.category = CategoryFactory(guild=other_guild)
            offering.save(update_fields=["category"])
            offering.refresh_from_db()

            assert not ClassApproval.objects.filter(pk=old_gate.pk).exists()
            gate = offering.approvals.get(role=ClassApproval.Role.GUILD_LEAD, decision="")
            assert gate.opened_for_guild_id == other_guild.pk
            assert _addressed(OTHER_LEAD_EMAIL)
            new_lead = Member.objects.get(user=other_lead_user)
            assert list(ClassOffering.objects.awaiting_guild_lead(new_lead)) == [offering]
            old_lead = Member.objects.get(user=lead_user)
            assert list(ClassOffering.objects.awaiting_guild_lead(old_lead)) == []

        def it_never_lets_the_new_guilds_lead_spend_the_old_guilds_gate(db, settings, admin_user):
            """The decision the reopen exists to prevent, driven end to end.

            Guild 2's staff approve, the admin bounces, the instructor moves the class back
            to guild 1 and resubmits. Guild 1's own lead must still be asked: they never saw
            this class, and a decision made in another guild cannot answer for them.
            """
            offering, lead_user = _guilded_draft(settings)
            other_guild, other_lead_user = _second_guild_with_lead()
            home_category = offering.category
            offering.submit_for_review()

            offering.category = CategoryFactory(guild=other_guild)
            offering.save(update_fields=["category"])
            offering.refresh_from_db()
            gate = offering.approvals.get(role=ClassApproval.Role.GUILD_LEAD, decision="")
            gate.class_offering = offering
            gate.decide(ClassApproval.Decision.APPROVED, user=other_lead_user)
            offering.refresh_from_db()
            admin_row = offering.approvals.get(role=ClassApproval.Role.ADMIN, decision="")
            admin_row.class_offering = offering
            admin_row.decide(ClassApproval.Decision.CHANGES_REQUESTED, user=admin_user, notes="Trim it.")
            offering.refresh_from_db()

            offering.category = home_category
            offering.save(update_fields=["category"])
            offering.refresh_from_db()
            mail.outbox.clear()

            (row,) = offering.submit_for_review()

            assert row.role == ClassApproval.Role.GUILD_LEAD
            assert row.opened_for_guild_id == home_category.guild_id
            assert _addressed(LEAD_EMAIL)

        def it_opens_a_gate_for_a_class_carried_into_a_guild(db, settings):
            """The other half of the same contract, and the one an admin tool reaches.

            ``refile_into_guild_categories`` moves guildless classes into guild categories
            one save at a time, behind the admin guild tagging screen. A PENDING class
            moved that way starts requiring guild-lead review and has no gate to satisfy
            it, so without this its guild step is never reached, the guild's dashboard
            shows nothing, and an admin can publish it without the guild ever being asked.
            """
            from membership.models import Member

            settings.CLASS_ADMIN_NOTIFY_EMAILS = "admin@example.com"
            lead_user = _make_lead_user(LEAD_EMAIL)
            guild = GuildFactory(guild_lead=Member.objects.get(user=lead_user))
            instructor = InstructorFactory(full_legal_name="Iris Smith", instructor_slug="iris")
            offering = cast(
                ClassOffering,
                ClassOfferingFactory(
                    ready=True,
                    category=CategoryFactory(guild=None),
                    instructor=instructor,
                    status=ClassOffering.Status.DRAFT,
                ),
            )
            (admin_gate,) = offering.submit_for_review()
            assert admin_gate.role == ClassApproval.Role.ADMIN
            mail.outbox.clear()

            offering.category = CategoryFactory(guild=guild)
            offering.save(update_fields=["category"])
            offering.refresh_from_db()

            gate = offering.approvals.get(role=ClassApproval.Role.GUILD_LEAD, decision="")
            assert gate.opened_for_guild_id == guild.pk
            assert _addressed(LEAD_EMAIL)
            lead = Member.objects.get(user=lead_user)
            assert list(ClassOffering.objects.awaiting_guild_lead(lead)) == [offering]

        def it_does_not_mint_a_second_admin_gate(db, settings):
            """An admin who opened the review page already minted one."""
            offering, lead_user = _guilded_draft(settings)
            offering.submit_for_review()
            ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.ADMIN)

            offering.category = CategoryFactory(guild=None)
            offering.save(update_fields=["category"])
            offering.refresh_from_db()

            assert offering.approvals.filter(role=ClassApproval.Role.ADMIN, decision="").count() == 1

        def it_ignores_a_category_the_save_never_wrote(db, settings):
            """``update_fields`` without the category writes no category.

            The instance is dirty and the database is not, so acting on the difference
            would withdraw a live gate and mail a lead about a class still filed elsewhere.
            """
            offering, lead_user = _guilded_draft(settings)
            other_guild, _other_lead_user = _second_guild_with_lead()
            (gate,) = offering.submit_for_review()
            mail.outbox.clear()

            offering.category = CategoryFactory(guild=other_guild)
            offering.title = "Renamed"
            offering.save(update_fields=["title"])
            offering.refresh_from_db()

            assert offering.category.guild_id != other_guild.pk
            assert list(offering.approvals.all()) == [gate]
            assert not _addressed(OTHER_LEAD_EMAIL)

        def it_leaves_a_move_inside_one_guild_alone(db, settings):
            """Forge Basics to Forge Advanced asks nobody anything new."""
            offering, lead_user = _guilded_draft(settings)
            (gate,) = offering.submit_for_review()

            offering.category = CategoryFactory(guild=offering.category.guild)
            offering.save(update_fields=["category"])
            offering.refresh_from_db()

            assert list(offering.approvals.all()) == [gate]

        def it_leaves_a_decided_gate_alone(db, settings, admin_user):
            """An approval already given is history, not an invitation to withdraw."""
            offering, lead_user = _guilded_draft(settings)
            other_guild, _other_lead_user = _second_guild_with_lead()
            lead_row = _lead_approves_then_admin_bounces(offering, lead_user, admin_user)

            offering.category = CategoryFactory(guild=other_guild)
            offering.save(update_fields=["category"])
            offering.refresh_from_db()

            lead_row.refresh_from_db()
            assert lead_row.decision == ClassApproval.Decision.APPROVED

        def it_falls_back_to_the_admin_gate_when_the_new_category_has_no_guild(db, settings):
            """Reopening reruns the first-stage choice rather than assuming a lead exists."""
            offering, lead_user = _guilded_draft(settings)
            offering.submit_for_review()

            offering.category = CategoryFactory(guild=None)
            offering.save(update_fields=["category"])
            offering.refresh_from_db()

            assert not offering.approvals.filter(role=ClassApproval.Role.GUILD_LEAD).exists()
            assert offering.approvals.filter(role=ClassApproval.Role.ADMIN, decision="").count() == 1

        def it_kills_the_emailed_link_the_old_lead_was_sent(db, settings):
            """The old lead's token dies with the gate, which is the point.

            Deleting the row rather than reassigning it is what makes the link safe: guild
            1's lead cannot spend an invitation to a question that is no longer theirs, and
            guild 2's staff are never handed a token minted for someone else.
            """
            offering, lead_user = _guilded_draft(settings)
            other_guild, _other_lead_user = _second_guild_with_lead()
            (old_gate,) = offering.submit_for_review()
            old_token = old_gate.token

            offering.category = CategoryFactory(guild=other_guild)
            offering.save(update_fields=["category"])
            offering.refresh_from_db()

            assert not ClassApproval.objects.filter(token=old_token).exists()
            fresh = offering.approvals.get(role=ClassApproval.Role.GUILD_LEAD, decision="")
            assert fresh.token != old_token

        def it_refuses_to_credit_a_gate_whose_guild_no_longer_matches(db, settings):
            """Defence in depth, for a stale gate the reopen never saw.

            ``_repoint_open_guild_lead_gate`` runs on ``save``, so a bulk ``update`` that
            moves a class between categories bypasses it. A gate left stranded that way can
            still be decided, and the class still moves on, but it must not leave a stamp
            behind that lets a later submission skip the guild that never answered.
            """
            offering, lead_user = _guilded_draft(settings)
            other_guild, _other_lead_user = _second_guild_with_lead()
            (gate,) = offering.submit_for_review()
            ClassOffering.objects.filter(pk=offering.pk).update(category=CategoryFactory(guild=other_guild))
            offering.refresh_from_db()

            gate.refresh_from_db()
            gate.class_offering = offering
            gate.decide(ClassApproval.Decision.APPROVED, user=lead_user)
            gate.refresh_from_db()

            assert gate.decision == ClassApproval.Decision.APPROVED
            assert gate.approved_schedule_fingerprint == ""
            assert not offering._guild_already_approved_this_schedule


def describe_decide():
    def describe_when_a_guild_lead_approves():
        def it_stamps_the_schedule_they_approved(db, settings):
            offering, lead_user = _guilded_draft(settings)
            (lead_row,) = offering.submit_for_review()
            lead_row.decide(ClassApproval.Decision.APPROVED, user=lead_user)
            lead_row.refresh_from_db()
            assert lead_row.approved_schedule_fingerprint == offering.schedule_fingerprint

        def it_leaves_the_guild_stamp_where_the_gate_put_it(db, settings):
            """The decision records the schedule. It must not re-read the guild."""
            offering, lead_user = _guilded_draft(settings)
            (lead_row,) = offering.submit_for_review()
            opened_for = lead_row.opened_for_guild_id
            lead_row.decide(ClassApproval.Decision.APPROVED, user=lead_user)
            lead_row.refresh_from_db()
            assert lead_row.opened_for_guild_id == opened_for

    def describe_when_a_guild_lead_asks_for_changes():
        def it_stamps_no_schedule(db, settings):
            """A bounce approved nothing, so it carries no schedule. The guild the gate was
            opened for is a fact about the asking and stands either way.
            """
            offering, lead_user = _guilded_draft(settings)
            (lead_row,) = offering.submit_for_review()
            lead_row.decide(ClassApproval.Decision.CHANGES_REQUESTED, user=lead_user, notes="Add prerequisites.")
            lead_row.refresh_from_db()
            assert lead_row.approved_schedule_fingerprint == ""
            assert lead_row.opened_for_guild_id == offering.category.guild_id

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
            assert admin_row.opened_for_guild_id is None


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
            """A row from before ``opened_for_guild`` existed carries a null guild. Null is
            not "any guild", it is "unknown", and unknown re-asks.
            """
            offering, lead_user = _guilded_draft(settings)
            lead_row = _lead_approves_then_admin_bounces(offering, lead_user, admin_user)
            ClassApproval.objects.filter(pk=lead_row.pk).update(opened_for_guild=None)

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

        def it_does_not_credit_the_other_guilds_lead_after_the_class_moves_back(db, settings, admin_user):
            """Two approved rows now stand, one per guild, and the newest is the wrong one.

            Reading the newest row per role put guild 2's lead on guild 1's pipeline by name.
            Guild 1's staff would be told a person they have never met signed their class off.
            """
            offering, lead_user = _guilded_draft(settings)
            other_guild, other_lead_user = _second_guild_with_lead()
            home_category = offering.category

            _lead_approves_then_admin_bounces(offering, lead_user, admin_user)
            offering.category = CategoryFactory(guild=other_guild)
            offering.save(update_fields=["category"])
            offering.refresh_from_db()
            _lead_approves_then_admin_bounces(offering, other_lead_user, admin_user)
            offering.category = home_category
            offering.save(update_fields=["category"])
            offering.refresh_from_db()

            guild_step = next(step for step in offering.review_pipeline().steps if step.key == "guild_lead")
            assert OTHER_LEAD_EMAIL not in guild_step.detail
            home_row = offering.approvals.get(role=ClassApproval.Role.GUILD_LEAD, opened_for_guild=home_category.guild)
            assert offering.guild_lead_approved_at == home_row.decided_at

        def it_shows_a_guild_less_category_no_guild_lead_credit(db, settings, admin_user):
            """Moved somewhere with no guild at all, there is no one the sign-off speaks for."""
            offering, lead_user = _guilded_draft(settings)
            _lead_approves_then_admin_bounces(offering, lead_user, admin_user)
            offering.category = CategoryFactory(guild=None)
            offering.save(update_fields=["category"])
            offering.refresh_from_db()

            assert offering.guild_lead_approved_at is None
            assert [step.key for step in offering.review_pipeline().steps] == ["submitted", "admin", "live"]

        def it_keeps_the_dashboard_and_the_pipeline_telling_one_story(db, settings):
            """Three surfaces, one class, and they must not disagree.

            A class whose guild-lead gate was approved elsewhere used to sit on the new
            guild's dashboard under "waiting on an admin", implying their guild had signed
            it off, directly above a pipeline correctly showing that they had not.
            """
            from membership.models import Member

            offering, lead_user = _guilded_draft(settings)
            other_guild, other_lead_user = _second_guild_with_lead()
            (gate,) = offering.submit_for_review()
            gate.decide(ClassApproval.Decision.APPROVED, user=lead_user)
            offering.refresh_from_db()
            assert offering.approvals.filter(role=ClassApproval.Role.ADMIN, decision="").exists()

            offering.category = CategoryFactory(guild=other_guild)
            offering.save(update_fields=["category"])
            offering.refresh_from_db()

            new_lead = Member.objects.get(user=other_lead_user)
            assert list(ClassOffering.objects.awaiting_admin_validation(new_lead)) == []
            assert offering.guild_lead_approved_at is None

        def it_credits_nobody_when_the_guild_is_deleted_under_the_class(db, settings, admin_user):
            """Both sides go null at once, and null must not match null.

            ``Category.guild`` and ``ClassApproval.opened_for_guild`` are both SET_NULL, so
            deleting a guild blanks the class's guild and the stamp together. Comparing the
            two would then say the row speaks for this class, which is the "null means two
            things" reading this predicate exists to refuse.
            """
            offering, lead_user = _guilded_draft(settings)
            lead_row = _lead_approves_then_admin_bounces(offering, lead_user, admin_user)
            offering.category.guild.delete()
            offering.refresh_from_db()
            lead_row.refresh_from_db()
            assert offering.category.guild_id is None
            assert lead_row.opened_for_guild_id is None

            assert offering.guild_lead_approved_at is None
            guild_steps = [step for step in offering.review_pipeline().steps if step.key == "guild_lead"]
            assert guild_steps == []

        def it_credits_nobody_once_the_approving_guild_is_deleted(db, settings, admin_user):
            """``opened_for_guild`` is SET_NULL, so a deleted guild blanks a decided stamp.

            A blank stamp therefore cannot be read as "predates the field, credit it": the
            same value now also means "the guild that approved this no longer exists". Of
            the two readings only one is safe, so a decided row with no guild speaks for
            nobody.
            """
            offering, lead_user = _guilded_draft(settings)
            other_guild, _other_lead_user = _second_guild_with_lead()
            lead_row = _lead_approves_then_admin_bounces(offering, lead_user, admin_user)
            offering.category.guild.delete()
            offering.category = CategoryFactory(guild=other_guild)
            offering.save(update_fields=["category"])
            offering.refresh_from_db()
            lead_row.refresh_from_db()
            assert lead_row.opened_for_guild_id is None

            assert offering.guild_lead_approved_at is None
            (row,) = offering.submit_for_review()
            assert row.role == ClassApproval.Role.GUILD_LEAD

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
