"""BDD specs for the guild lead gate: which guild it asks, what its approval records, and resets.

A guild lead signs off on dates and space, and since #1183 their approval publishes the class.
The gate is stamped with the guild it asks when it opens, so re-filing a class under another
guild asks that guild instead; an approval records a fingerprint of the schedule it approved.

Every spec here builds its own class and drives its own approval history through the real
API. One shared offering fixture would let a single arrangement of approval rows stand in
for all of them, and the arrangements are exactly what these specs are about.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import TYPE_CHECKING, Any, cast

from django.contrib.auth import get_user_model
from django.core import mail
from django.utils import timezone

from classes.factories import CategoryFactory, ClassOfferingFactory, ClassSessionFactory, InstructorFactory
from classes.models import ClassApproval, ClassOffering
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


def _move_every_session(offering: ClassOffering, delta: timedelta) -> None:
    """Shift the whole schedule, keeping it in the future so readiness still passes."""
    for session in offering.sessions.all():
        session.starts_at += delta
        session.ends_at += delta
        session.save(update_fields=["starts_at", "ends_at"])


def _addressed(email: str) -> bool:
    """Whether ``email`` was mailed, or is asked in the next 8 AM digest (review requests, #894)."""
    from tests.core.events.digest_support import digest_emails

    return any(email in message.to for message in mail.outbox) or bool(digest_emails(email))


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

    def describe_a_flexible_class():
        """Its schedule is its date window (#545): a moved window reopens the gate as a moved session does."""

        def _flexible(**traits) -> ClassOffering:
            return ClassOfferingFactory(scheduling_model=ClassOffering.SchedulingModel.FLEXIBLE, **traits)

        def it_changes_when_the_first_day_moves(db):
            offering = _flexible(flexible_starts_on=date(2026, 11, 2), flexible_ends_on=date(2026, 12, 1))
            before = offering.schedule_fingerprint
            offering.flexible_starts_on = date(2026, 11, 3)
            offering.save(update_fields=["flexible_starts_on"])
            assert offering.schedule_fingerprint != before

        def it_changes_when_the_last_day_moves(db):
            offering = _flexible(flexible_starts_on=date(2026, 11, 2), flexible_ends_on=date(2026, 12, 1))
            before = offering.schedule_fingerprint
            offering.flexible_ends_on = date(2026, 12, 2)
            offering.save(update_fields=["flexible_ends_on"])
            assert offering.schedule_fingerprint != before

        def it_changes_when_a_window_is_set_on_a_class_that_had_none_and_when_cleared(db):
            offering = _flexible()
            open_ended = offering.schedule_fingerprint
            offering.flexible_ends_on = date(2026, 12, 1)
            offering.save(update_fields=["flexible_ends_on"])
            assert offering.schedule_fingerprint != open_ended
            offering.flexible_ends_on = None
            offering.save(update_fields=["flexible_ends_on"])
            assert offering.schedule_fingerprint == open_ended

        def it_tells_a_first_day_from_a_last_day(db):
            # The two ends are digested in their own slots, so "From Nov 2" never hashes as "Through Nov 2".
            first_only = _flexible(flexible_starts_on=date(2026, 11, 2))
            last_only = _flexible(flexible_ends_on=date(2026, 11, 2))
            assert first_only.schedule_fingerprint != last_only.schedule_fingerprint

        def it_matches_two_flexible_classes_that_share_a_window(db):
            first = _flexible(flexible_starts_on=date(2026, 11, 2), flexible_ends_on=date(2026, 12, 1))
            second = _flexible(flexible_starts_on=date(2026, 11, 2), flexible_ends_on=date(2026, 12, 1))
            assert first.schedule_fingerprint == second.schedule_fingerprint
            assert len(first.schedule_fingerprint) == 64

        def it_leaves_a_fixed_class_digest_alone_whatever_the_window_columns_hold(db):
            # The form clears the window on a Fixed save; the digest reads sessions only, so even a
            # row with stale window columns hashes exactly as it always has.
            start = timezone.now() + timedelta(days=4)
            plain = ClassOfferingFactory()
            stale = ClassOfferingFactory(flexible_starts_on=date(2026, 11, 2), flexible_ends_on=date(2026, 12, 1))
            for offering in (plain, stale):
                ClassSessionFactory(class_offering=offering, starts_at=start, ends_at=start + timedelta(hours=2))
            assert plain.schedule_fingerprint == stale.schedule_fingerprint


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

        def it_never_lets_the_new_guilds_lead_spend_the_old_guilds_gate(db, settings):
            """The decision the reopen exists to prevent, driven end to end.

            Guild 2's staff approve (which publishes), the class is taken back to draft, the
            instructor moves it back to guild 1 and resubmits. Guild 1's own lead must still
            be asked: they never saw this class, and a decision made in another guild cannot
            answer for them.
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
            assert offering.status == ClassOffering.Status.PUBLISHED
            offering.unpublish()
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

        def it_does_not_carry_the_group_off_without_the_class(db, settings):
            """The sibling sync one line below reads the same dirty field.

            A save that leaves the class where it is would otherwise re-file every other
            run of the same class into a category the class itself never moved to, which
            splits the group the block exists to keep together.
            """
            offering, lead_user = _guilded_draft(settings)
            other_guild, _other_lead_user = _second_guild_with_lead()
            sibling = cast(
                ClassOffering,
                ClassOfferingFactory(
                    ready=True,
                    title=offering.title,
                    slug=f"{offering.slug}-second-run",
                    category=offering.category,
                    instructor=offering.instructor,
                ),
            )
            assert sibling.grouping_key == offering.grouping_key

            offering.category = CategoryFactory(guild=other_guild)
            offering.save(update_fields=["title"])

            offering.refresh_from_db()
            sibling.refresh_from_db()
            assert sibling.category_id == offering.category_id

        def it_leaves_a_move_inside_one_guild_alone(db, settings):
            """Forge Basics to Forge Advanced asks nobody anything new."""
            offering, lead_user = _guilded_draft(settings)
            (gate,) = offering.submit_for_review()

            offering.category = CategoryFactory(guild=offering.category.guild)
            offering.save(update_fields=["category"])
            offering.refresh_from_db()

            assert list(offering.approvals.all()) == [gate]

        def it_falls_back_to_the_admin_gate_when_the_new_category_has_no_guild(db, settings):
            """Reopening reruns the gate choice rather than assuming a lead exists."""
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
            behind claiming the guild it is filed under now approved this schedule.
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
            """Only the lead's approval is a statement about the dates, even on a guilded class."""
            offering, _lead_user = _guilded_draft(settings)
            (lead_row,) = offering.submit_for_review()
            admin_row = offering.approve(admin_user)
            admin_row.refresh_from_db()
            lead_row.refresh_from_db()
            assert admin_row.approved_schedule_fingerprint == ""
            assert admin_row.opened_for_guild_id is None
            assert lead_row.approved_schedule_fingerprint == ""


def describe_a_resubmitted_class():
    def it_never_reads_as_bounced_on_any_surface(db, settings):
        """The guild lead's spent bounce row is gone, and the class is back with the lead."""
        offering, lead_user = _guilded_draft(settings)
        (lead_row,) = offering.submit_for_review()
        lead_row.decide(ClassApproval.Decision.CHANGES_REQUESTED, user=lead_user, notes="Tighten the description.")
        offering.refresh_from_db()
        assert offering.status == ClassOffering.Status.DRAFT

        offering.submit_for_review()

        annotated = ClassOffering.objects.with_lifecycle_inputs().get(pk=offering.pk)
        assert offering._is_bounced is False
        assert annotated.bounced is False
        assert offering.lifecycle == ClassOffering.Lifecycle.AWAITING_GUILD_LEAD
        assert annotated.lifecycle == ClassOffering.Lifecycle.AWAITING_GUILD_LEAD
        assert offering.latest_bounce_row is None
        assert list(ClassOffering.objects.changes_requested()) == []
        pipeline = offering.review_pipeline()
        assert pipeline.is_bounced is False
        assert [(step.key, step.state) for step in pipeline.steps] == [
            ("submitted", "done"),
            ("guild_lead", "current"),
            ("live", "ahead"),
        ]

    def it_goes_back_to_the_lead_after_an_admin_declined_it_early(db, settings, admin_user):
        """An admin may decide a guilded class early; a resubmission still goes to the guild."""
        offering, _lead_user = _guilded_draft(settings)
        offering.submit_for_review()
        admin_row = ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.ADMIN)
        admin_row.decide(ClassApproval.Decision.DENIED, user=admin_user, notes="Not this season.")
        offering.refresh_from_db()
        assert offering.status == ClassOffering.Status.DRAFT

        (row,) = offering.submit_for_review()

        assert row.role == ClassApproval.Role.GUILD_LEAD
        assert offering._is_bounced is False
        assert offering.latest_bounce_row is None


def describe_the_full_reset_paths():
    """Withdraw, restore and unpublish still clear every row."""

    def it_clears_the_open_lead_gate_on_withdraw(db, settings):
        offering, _lead_user = _guilded_draft(settings)
        offering.submit_for_review()
        assert offering.approvals.count() == 1

        offering.withdraw_submission()

        assert offering.approvals.count() == 0

    def it_clears_every_row_on_restore(db, settings):
        offering, lead_user = _guilded_draft(settings)
        (lead_row,) = offering.submit_for_review()
        lead_row.decide(ClassApproval.Decision.CHANGES_REQUESTED, user=lead_user, notes="Tighten the description.")
        offering.refresh_from_db()
        assert offering.approvals.count() == 1
        offering.archive()

        offering.restore()

        assert offering.approvals.count() == 0

    def it_clears_every_row_on_unpublish(db, settings):
        offering, lead_user = _guilded_draft(settings)
        (lead_row,) = offering.submit_for_review()
        lead_row.decide(ClassApproval.Decision.APPROVED, user=lead_user)
        offering.refresh_from_db()
        assert offering.status == ClassOffering.Status.PUBLISHED

        offering.unpublish()

        assert offering.approvals.count() == 0
