"""BDD specs for the derived class lifecycle: the property, its note, the annotations, and the querysets."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from classes.factories import CategoryFactory, ClassOfferingFactory, ClassSessionFactory, UserFactory
from classes.lifecycle import ADMIN_FACETS, INSTRUCTOR_FACETS, facet_rows, resolve_facet
from classes.models import ClassApproval, ClassOffering
from tests.membership.factories import GuildFactory, MemberFactory

Lifecycle = ClassOffering.Lifecycle
Status = ClassOffering.Status


def _guilded_category(guild_name: str = "Woodshop"):
    lead = MemberFactory(_pre_signup_email=f"{guild_name.lower()}-lead@example.com")
    guild = GuildFactory(name=guild_name, guild_lead=lead)
    return CategoryFactory(guild=guild)


def _session(offering: ClassOffering, days: int) -> None:
    start = timezone.now() + timedelta(days=days)
    ClassSessionFactory(class_offering=offering, starts_at=start, ends_at=start + timedelta(hours=2))


def _bounce(offering: ClassOffering, role: str, decision: str, notes: str = "", when=None) -> ClassApproval:
    row = ClassApproval.objects.create(class_offering=offering, role=role, decision=decision, notes=notes)
    ClassApproval.objects.filter(pk=row.pk).update(decided_at=when or timezone.now())
    row.refresh_from_db()
    return row


def describe_lifecycle():
    def it_reads_archived_first(db):
        offering = ClassOfferingFactory(status=Status.ARCHIVED)
        ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.GUILD_LEAD)
        assert offering.lifecycle == Lifecycle.ARCHIVED

    def it_reads_cancelled(db):
        assert ClassOfferingFactory(status=Status.CANCELLED).lifecycle == Lifecycle.CANCELLED

    def it_reads_with_guild_lead_while_the_guild_gate_is_open(db):
        offering = ClassOfferingFactory(status=Status.PENDING, category=_guilded_category())
        ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.GUILD_LEAD)
        assert offering.lifecycle == Lifecycle.AWAITING_GUILD_LEAD
        assert offering.lifecycle_label == "With guild lead (Woodshop)"
        assert offering.lifecycle_note == "Woodshop"

    def it_reads_awaiting_admin_with_an_open_admin_row(db):
        offering = ClassOfferingFactory(status=Status.PENDING)
        ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.ADMIN)
        assert offering.lifecycle == Lifecycle.AWAITING_ADMIN

    def it_reads_awaiting_admin_with_zero_rows(db):
        offering = ClassOfferingFactory(status=Status.PENDING)
        assert offering.lifecycle == Lifecycle.AWAITING_ADMIN
        assert offering.lifecycle_label == "Awaiting admin"

    def it_reads_changes_requested_for_a_draft_with_a_changes_requested_row(db):
        offering = ClassOfferingFactory(status=Status.DRAFT)
        _bounce(offering, ClassApproval.Role.GUILD_LEAD, ClassApproval.Decision.CHANGES_REQUESTED, "Add safety notes")
        assert offering.lifecycle == Lifecycle.CHANGES_REQUESTED
        assert offering.lifecycle_note == "The guild lead asked for changes: Add safety notes"

    def it_reads_changes_requested_for_a_draft_with_a_denied_row(db):
        offering = ClassOfferingFactory(status=Status.DRAFT)
        _bounce(offering, ClassApproval.Role.ADMIN, ClassApproval.Decision.DENIED)
        assert offering.lifecycle == Lifecycle.CHANGES_REQUESTED
        assert offering.lifecycle_note == "An admin declined it."

    def it_reads_plain_draft_when_only_an_approved_guild_lead_row_remains(db):
        # An APPROVED guild-lead row left behind by an admin bounce never counts as a bounce.
        offering = ClassOfferingFactory(status=Status.DRAFT)
        ClassApproval.objects.create(
            class_offering=offering, role=ClassApproval.Role.GUILD_LEAD, decision=ClassApproval.Decision.APPROVED
        )
        assert offering.lifecycle == Lifecycle.DRAFT

    def it_reads_plain_draft(db):
        assert ClassOfferingFactory(status=Status.DRAFT).lifecycle == Lifecycle.DRAFT

    def it_reads_upcoming_for_a_published_dated_class_with_a_future_session(db):
        offering = ClassOfferingFactory(status=Status.PUBLISHED)
        _session(offering, 2)
        assert offering.lifecycle == Lifecycle.UPCOMING
        assert offering.lifecycle_note == ""

    def it_reads_completed_once_the_last_session_has_ended(db):
        offering = ClassOfferingFactory(status=Status.PUBLISHED)
        _session(offering, -2)
        assert offering.lifecycle == Lifecycle.COMPLETED
        assert offering.lifecycle_note.startswith("Ended ")

    def it_keeps_a_flexible_published_class_upcoming_forever(db):
        offering = ClassOfferingFactory(
            status=Status.PUBLISHED, scheduling_model=ClassOffering.SchedulingModel.FLEXIBLE, flexible_note="Email me"
        )
        _session(offering, -30)
        assert offering.lifecycle == Lifecycle.UPCOMING

    def it_reads_upcoming_with_the_no_dates_note_for_a_dated_class_with_zero_sessions(db):
        offering = ClassOfferingFactory(status=Status.PUBLISHED)
        assert offering.lifecycle == Lifecycle.UPCOMING
        assert offering.lifecycle_note == "No dates yet"
        assert offering not in ClassOffering.objects.bookable()

    def describe_lifecycle_note():
        def it_quotes_the_latest_bouncing_row_when_two_decided_rows_exist(db):
            offering = ClassOfferingFactory(status=Status.DRAFT)
            _bounce(
                offering,
                ClassApproval.Role.GUILD_LEAD,
                ClassApproval.Decision.CHANGES_REQUESTED,
                "Older note",
                when=timezone.now() - timedelta(days=3),
            )
            _bounce(offering, ClassApproval.Role.ADMIN, ClassApproval.Decision.DENIED, "Newer note")
            assert offering.lifecycle_note == "An admin declined it: Newer note"

        def it_names_nobody_when_the_bounce_has_no_note(db):
            offering = ClassOfferingFactory(status=Status.DRAFT)
            _bounce(offering, ClassApproval.Role.GUILD_LEAD, ClassApproval.Decision.CHANGES_REQUESTED)
            assert offering.lifecycle_note == "The guild lead asked for changes."

        def it_falls_back_to_created_at_for_a_bounced_row_that_was_never_stamped(db):
            # The badge (``bounced`` annotation) and the pipeline must agree on an unstamped row.
            offering = ClassOfferingFactory(status=Status.DRAFT)
            ClassApproval.objects.create(
                class_offering=offering, role=ClassApproval.Role.ADMIN, decision=ClassApproval.Decision.DENIED
            )
            assert offering.lifecycle == Lifecycle.CHANGES_REQUESTED
            assert offering.lifecycle_note == "An admin declined it."
            pipeline = offering.review_pipeline()
            assert pipeline.is_bounced is True
            assert pipeline.headline == "Declined by an admin"

        def it_ranks_an_unstamped_newer_row_above_an_older_stamped_one(db):
            offering = ClassOfferingFactory(status=Status.DRAFT)
            _bounce(
                offering,
                ClassApproval.Role.GUILD_LEAD,
                ClassApproval.Decision.CHANGES_REQUESTED,
                "Older",
                when=timezone.now() - timedelta(days=5),
            )
            ClassApproval.objects.create(
                class_offering=offering,
                role=ClassApproval.Role.ADMIN,
                decision=ClassApproval.Decision.DENIED,
                notes="Newer",
            )
            assert offering.lifecycle_note == "An admin declined it: Newer"

        def it_is_empty_for_a_plain_draft(db):
            assert ClassOfferingFactory(status=Status.DRAFT).lifecycle_note == ""

    def describe_with_lifecycle_inputs():
        @pytest.fixture
        def mixed_set(db):
            rows = {
                "archived": ClassOfferingFactory(status=Status.ARCHIVED),
                "cancelled": ClassOfferingFactory(status=Status.CANCELLED),
                "guild": ClassOfferingFactory(status=Status.PENDING, category=_guilded_category("Glass")),
                "admin": ClassOfferingFactory(status=Status.PENDING),
                "bounced": ClassOfferingFactory(status=Status.DRAFT),
                "draft": ClassOfferingFactory(status=Status.DRAFT),
                "upcoming": ClassOfferingFactory(status=Status.PUBLISHED),
                "completed": ClassOfferingFactory(status=Status.PUBLISHED),
                "undated": ClassOfferingFactory(status=Status.PUBLISHED),
            }
            ClassApproval.objects.create(class_offering=rows["guild"], role=ClassApproval.Role.GUILD_LEAD)
            _bounce(rows["bounced"], ClassApproval.Role.ADMIN, ClassApproval.Decision.CHANGES_REQUESTED)
            _session(rows["upcoming"], 2)
            _session(rows["completed"], -2)
            return rows

        def it_matches_the_property_for_every_row_with_no_per_row_queries(mixed_set, django_assert_num_queries):
            expected = {pk: offering.lifecycle for pk, offering in ((o.pk, o) for o in mixed_set.values())}
            qs = ClassOffering.objects.with_lifecycle_inputs().select_related("category__guild")
            with django_assert_num_queries(1):
                resolved = {o.pk: o.lifecycle for o in qs}
            assert resolved == expected

        def it_is_idempotent(db):
            qs = ClassOffering.objects.with_lifecycle_inputs()
            assert qs.with_lifecycle_inputs() is qs

        def it_orders_by_lifecycle(mixed_set):
            ordered = list(ClassOffering.objects.with_lifecycle_inputs().order_by("lifecycle_order", "pk"))
            keys = [o.lifecycle for o in ordered]
            assert keys == [
                Lifecycle.DRAFT,
                Lifecycle.CHANGES_REQUESTED,
                Lifecycle.AWAITING_GUILD_LEAD,
                Lifecycle.AWAITING_ADMIN,
                Lifecycle.UPCOMING,
                Lifecycle.UPCOMING,
                Lifecycle.COMPLETED,
                Lifecycle.CANCELLED,
                Lifecycle.ARCHIVED,
            ]

    def describe_queryset_methods():
        @pytest.fixture
        def rows(db):
            guild_pending = ClassOfferingFactory(status=Status.PENDING, category=_guilded_category("Metal"))
            ClassApproval.objects.create(class_offering=guild_pending, role=ClassApproval.Role.GUILD_LEAD)
            admin_pending = ClassOfferingFactory(status=Status.PENDING)
            ClassApproval.objects.create(class_offering=admin_pending, role=ClassApproval.Role.ADMIN)
            rowless_pending = ClassOfferingFactory(status=Status.PENDING)
            bounced = ClassOfferingFactory(status=Status.DRAFT)
            _bounce(bounced, ClassApproval.Role.GUILD_LEAD, ClassApproval.Decision.DENIED)
            draft = ClassOfferingFactory(status=Status.DRAFT)
            upcoming = ClassOfferingFactory(status=Status.PUBLISHED)
            _session(upcoming, 3)
            flexible = ClassOfferingFactory(
                status=Status.PUBLISHED, scheduling_model=ClassOffering.SchedulingModel.FLEXIBLE
            )
            undated = ClassOfferingFactory(status=Status.PUBLISHED)
            completed = ClassOfferingFactory(status=Status.PUBLISHED)
            _session(completed, -3)
            cancelled = ClassOfferingFactory(status=Status.CANCELLED)
            archived = ClassOfferingFactory(status=Status.ARCHIVED)
            return {
                "guild_pending": guild_pending,
                "admin_pending": admin_pending,
                "rowless_pending": rowless_pending,
                "bounced": bounced,
                "draft": draft,
                "upcoming": upcoming,
                "flexible": flexible,
                "undated": undated,
                "completed": completed,
                "cancelled": cancelled,
                "archived": archived,
            }

        def it_awaiting_admin_includes_the_open_admin_row_and_the_rowless_pending_class(rows):
            assert set(ClassOffering.objects.awaiting_admin()) == {rows["admin_pending"], rows["rowless_pending"]}

        def it_awaiting_guild_lead_any_lists_only_open_guild_gates(rows):
            assert list(ClassOffering.objects.awaiting_guild_lead_any()) == [rows["guild_pending"]]

        def it_changes_requested_lists_only_bounced_drafts(rows):
            assert list(ClassOffering.objects.changes_requested()) == [rows["bounced"]]

        def it_upcoming_published_keeps_dated_future_flexible_and_undated_classes(rows):
            assert set(ClassOffering.objects.upcoming_published()) == {
                rows["upcoming"],
                rows["flexible"],
                rows["undated"],
            }

        def it_completed_lists_only_finished_dated_classes(rows):
            assert list(ClassOffering.objects.completed()) == [rows["completed"]]

        def it_cancelled_lists_cancelled_classes(rows):
            assert list(ClassOffering.objects.cancelled()) == [rows["cancelled"]]

        def it_facets_map_to_the_queryset_methods_with_counts(rows):
            base = ClassOffering.objects.all()
            chips = {row.key: row for row in facet_rows(ADMIN_FACETS, base, ADMIN_FACETS[0], lambda key: f"?s={key}")}
            assert chips[""].count == 11
            assert chips["needs_review"].count == 3
            assert chips["awaiting_guild_lead"].count == 1
            assert chips["awaiting_admin"].count == 2
            assert chips["draft"].count == 1
            assert chips["changes_requested"].count == 1
            assert chips["upcoming"].count == 3
            assert chips["completed"].count == 1
            assert chips["cancelled"].count == 1
            assert chips["archived"].count == 1
            assert chips[""].is_selected is True
            assert chips["archived"].url == "?s=archived"

        def it_instructor_facets_split_needs_attention_and_in_review(rows):
            base = ClassOffering.objects.all()
            chips = {row.key: row for row in facet_rows(INSTRUCTOR_FACETS, base, INSTRUCTOR_FACETS[1], lambda k: k)}
            assert chips["needs_attention"].count == 2
            assert chips["in_review"].count == 3
            assert chips["needs_attention"].is_selected is True

        def it_resolves_an_unknown_facet_key_to_all(db):
            assert resolve_facet(ADMIN_FACETS, "bogus").key == ""
            assert resolve_facet(ADMIN_FACETS, "completed").label == "Completed"


def describe_first_gate_label():
    def it_names_the_guild_lead_when_the_category_has_one(db):
        offering = ClassOfferingFactory(category=_guilded_category("Textiles"))
        assert offering.first_gate_label == "the guild lead (Textiles)"

    def it_names_an_admin_otherwise(db):
        assert ClassOfferingFactory().first_gate_label == "an admin"


def describe_cancellation_reason_sentence():
    def it_adds_a_period(db):
        assert ClassOfferingFactory(cancellation_reason="Instructor unwell").cancellation_reason_sentence == (
            "Instructor unwell."
        )

    def it_keeps_existing_end_punctuation(db):
        assert ClassOfferingFactory(cancellation_reason="Flooded!").cancellation_reason_sentence == "Flooded!"

    def it_is_empty_without_a_reason(db):
        assert ClassOfferingFactory().cancellation_reason_sentence == ""


def describe_registration_counts():
    def it_counts_active_and_paid_registrations(db):
        from classes.factories import RegistrationFactory
        from classes.models import Registration

        offering = ClassOfferingFactory()
        RegistrationFactory(class_offering=offering, status=Registration.Status.CONFIRMED, amount_paid_cents=500)
        RegistrationFactory(class_offering=offering, status=Registration.Status.WAITLISTED)
        RegistrationFactory(class_offering=offering, status=Registration.Status.CANCELLED, amount_paid_cents=500)
        assert offering.active_registration_count == 2
        assert offering.paid_registration_count == 1


def describe_decider_name():
    def it_falls_back_to_the_username_when_the_user_has_no_name_or_email(db):
        user = UserFactory(username="bare-user", email="")
        offering = ClassOfferingFactory(status=Status.PENDING)
        row = ClassApproval.objects.create(
            class_offering=offering,
            role=ClassApproval.Role.ADMIN,
            decision=ClassApproval.Decision.APPROVED,
            decided_by=user,
        )
        assert ClassOffering._decider_name(row) == "bare-user"
        assert ClassOffering._decider_name(ClassApproval(class_offering=offering, role=ClassApproval.Role.ADMIN)) == ""
