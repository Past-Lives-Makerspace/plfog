"""BDD specs for the teaching Overview dashboard."""

from __future__ import annotations

import pytest
from django.urls import reverse

from classes.factories import (
    ClassOfferingFactory,
    InstructorFactory,
    RegistrationFactory,
    UserFactory,
)
from classes.models import ClassOffering, Registration
from membership.models import Member


@pytest.fixture
def instructor_fixture(db):
    user = UserFactory(username="teacher@example.com")
    return InstructorFactory(user=user, full_legal_name="Teacher T", instructor_slug="teacher-t")


@pytest.fixture
def other_instructor(db):
    user = UserFactory(username="other@example.com")
    return InstructorFactory(user=user, full_legal_name="Other", instructor_slug="other")


def _pending_class_in_guild_led_by(lead_member, title, slug):
    """Create a PENDING class in a guild led by ``lead_member``, with a guild-lead gate."""
    from classes.factories import CategoryFactory
    from classes.models import ClassApproval
    from tests.membership.factories import GuildFactory

    guild = GuildFactory(name=f"Guild for {slug}", guild_lead=lead_member)
    cat = CategoryFactory(guild=guild)
    offering = ClassOfferingFactory(title=title, slug=slug, category=cat, status=ClassOffering.Status.PENDING)
    ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.GUILD_LEAD)
    return offering


def describe_teach_overview():
    def it_is_served_at_the_instructor_root(db):
        assert reverse("classes:teach_overview") == "/classes/teach/"

    def it_blocks_anonymous(db, client):
        resp = client.get(reverse("classes:teach_overview"))
        assert resp.status_code == 302

    def it_blocks_inactive_members(db, client):
        user = UserFactory(username="inactive@example.com")
        InstructorFactory(user=user, status=Member.Status.FORMER)
        client.force_login(user)
        resp = client.get(reverse("classes:teach_overview"))
        assert resp.status_code == 403

    def it_renders_for_an_active_member(member_user, client):
        client.force_login(member_user)
        resp = client.get(reverse("classes:teach_overview"))
        assert resp.status_code == 200

    def describe_empty_state():
        def it_offers_create_first_class_when_they_have_none(instructor_fixture, client):
            client.force_login(instructor_fixture.user)
            resp = client.get(reverse("classes:teach_overview"))
            assert resp.context["has_classes"] is False
            assert reverse("classes:teach_class_create").encode() in resp.content

    def describe_needs_attention():
        def it_lists_my_pending_class(instructor_fixture, client):
            ClassOfferingFactory(
                instructor=instructor_fixture,
                title="Forge Night",
                slug="forge",
                status=ClassOffering.Status.PENDING,
            )
            client.force_login(instructor_fixture.user)
            resp = client.get(reverse("classes:teach_overview"))
            assert b"Forge Night" in resp.content

        def it_does_not_show_another_instructors_class(instructor_fixture, other_instructor, client):
            ClassOfferingFactory(
                instructor=other_instructor,
                title="Not Mine",
                slug="notmine",
                status=ClassOffering.Status.PENDING,
            )
            client.force_login(instructor_fixture.user)
            resp = client.get(reverse("classes:teach_overview"))
            assert b"Not Mine" not in resp.content

    def describe_recent_signups():
        def it_shows_a_recent_registrant_on_my_class(instructor_fixture, client):
            mine = ClassOfferingFactory(instructor=instructor_fixture, slug="mine")
            RegistrationFactory(class_offering=mine, first_name="Jess", last_name="Park")
            client.force_login(instructor_fixture.user)
            resp = client.get(reverse("classes:teach_overview"))
            assert b"Jess" in resp.content

        def it_does_not_show_another_instructors_registrant(instructor_fixture, other_instructor, client):
            theirs = ClassOfferingFactory(instructor=other_instructor, slug="theirs-regs")
            RegistrationFactory(class_offering=theirs, first_name="NotMine", last_name="Guest")
            client.force_login(instructor_fixture.user)
            resp = client.get(reverse("classes:teach_overview"))
            assert b"NotMine" not in resp.content

    def describe_waitlist():
        def it_shows_a_class_with_an_active_waitlist(instructor_fixture, client):
            mine = ClassOfferingFactory(instructor=instructor_fixture, title="Wheel 101", slug="wheel")
            RegistrationFactory(class_offering=mine, status=Registration.Status.WAITLISTED)
            client.force_login(instructor_fixture.user)
            resp = client.get(reverse("classes:teach_overview"))
            assert b"Waitlists" in resp.content
            assert b"Wheel 101" in resp.content
            assert b"waiting" in resp.content

        def it_shows_an_empty_message_when_no_waitlists(instructor_fixture, client):
            # The compact Waitlists strip always renders (admin parity); empty shows a note.
            ClassOfferingFactory(instructor=instructor_fixture, slug="no-wait")
            client.force_login(instructor_fixture.user)
            resp = client.get(reverse("classes:teach_overview"))
            assert b"No active waitlists." in resp.content

    def describe_quick_links():
        def it_links_to_registrations_and_codes(instructor_fixture, client):
            # Registrations and Discount Codes are reachable from the Overview —
            # now as top-level nav tabs rather than the old footer quick-links.
            ClassOfferingFactory(instructor=instructor_fixture, slug="quicklinks")
            client.force_login(instructor_fixture.user)
            resp = client.get(reverse("classes:teach_overview"))
            for name in [
                "classes:teach_registrations",
                "classes:teach_discount_codes",
            ]:
                assert reverse(name).encode() in resp.content

    def describe_avatar_menu():
        def it_links_to_hub_settings_for_active_members(member_user, client):
            client.force_login(member_user)
            resp = client.get(reverse("classes:teach_overview"))
            assert b"/settings/" in resp.content

    def describe_stats():
        def it_counts_my_published_classes(instructor_fixture, client):
            ClassOfferingFactory(instructor=instructor_fixture, slug="pub", status=ClassOffering.Status.PUBLISHED)
            client.force_login(instructor_fixture.user)
            resp = client.get(reverse("classes:teach_overview"))
            assert resp.context["stats"]["published"] == 1

    def describe_guild_lead_queue():
        def describe_when_member_is_a_guild_lead():
            def it_shows_the_pending_class_with_a_review_link(instructor_fixture, client):
                from classes.models import ClassApproval

                offering = _pending_class_in_guild_led_by(instructor_fixture, "Guild Pending Class", "guild-pending")
                client.force_login(instructor_fixture.user)
                resp = client.get(reverse("classes:teach_overview"))
                assert resp.context["is_guild_lead"] is True
                assert b"Guild Pending Class" in resp.content
                token = offering.approvals.get(role=ClassApproval.Role.GUILD_LEAD).token
                assert reverse("classes:class_review", kwargs={"token": token}).encode() in resp.content

        def describe_when_member_is_not_a_guild_lead():
            def it_hides_the_panel(instructor_fixture, client):
                ClassOfferingFactory(instructor=instructor_fixture, slug="ord")
                client.force_login(instructor_fixture.user)
                resp = client.get(reverse("classes:teach_overview"))
                assert resp.context["is_guild_lead"] is False
                assert b"Classes in your guild waiting on your review" not in resp.content

    def describe_awaiting_admin_validation_strip():
        def _lead_approved_class_led_by(lead_member, title, slug):
            """A PENDING class whose guild-lead gate ``lead_member`` approved; admin gate open."""
            from classes.models import ClassApproval

            offering = _pending_class_in_guild_led_by(lead_member, title, slug)
            gl_row = offering.approvals.get(role=ClassApproval.Role.GUILD_LEAD)
            gl_row.decision = ClassApproval.Decision.APPROVED
            gl_row.save(update_fields=["decision"])
            ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.ADMIN)
            return offering

        def it_shows_a_class_the_lead_approved_that_waits_on_admin(instructor_fixture, client):
            offering = _lead_approved_class_led_by(instructor_fixture, "Waiting On Admin", "waiting-admin")
            client.force_login(instructor_fixture.user)
            resp = client.get(reverse("classes:teach_overview"))
            assert b"Awaiting Admin Validation" in resp.content
            assert b"Waiting On Admin" in resp.content
            assert reverse("classes:teach_class_edit", kwargs={"pk": offering.pk}).encode() in resp.content

        def it_hides_the_strip_when_nothing_waits_on_admin(instructor_fixture, client):
            _pending_class_in_guild_led_by(instructor_fixture, "Still Undecided", "still-undecided")
            client.force_login(instructor_fixture.user)
            resp = client.get(reverse("classes:teach_overview"))
            assert b"Awaiting Admin Validation" not in resp.content

        def it_does_not_show_another_leads_class(instructor_fixture, other_instructor, client):
            _lead_approved_class_led_by(other_instructor, "Foreign Waiting", "foreign-waiting")
            # Make instructor_fixture a lead of their own (empty) guild so the panel area renders.
            _pending_class_in_guild_led_by(instructor_fixture, "Own Queue Class", "own-queue")
            client.force_login(instructor_fixture.user)
            resp = client.get(reverse("classes:teach_overview"))
            assert b"Foreign Waiting" not in resp.content
            assert b"Awaiting Admin Validation" not in resp.content

    def describe_guild_lead_queue_exclusions():
        def it_excludes_pending_classes_in_guilds_the_member_does_not_lead(
            instructor_fixture, other_instructor, client
        ):
            # The member leads their own guild...
            _pending_class_in_guild_led_by(instructor_fixture, "Mine Pending", "mine-pending")
            # ...but a class in a different (unled) guild must not appear.
            _pending_class_in_guild_led_by(other_instructor, "Foreign Pending", "foreign-pending")

            client.force_login(instructor_fixture.user)
            resp = client.get(reverse("classes:teach_overview"))
            assert b"Mine Pending" in resp.content
            assert b"Foreign Pending" not in resp.content


def describe_needs_attention_card():
    """The three review queues render as one card, and an empty queue renders nothing at all."""

    def it_collapses_to_one_quiet_line_when_nothing_waits(instructor_fixture, client):
        client.force_login(instructor_fixture.user)
        html = client.get(reverse("classes:teach_overview")).content.decode()
        assert "Needs Attention · all clear" in html
        assert "Needs Your Attention" not in html
        assert "Waiting on Your Review" not in html
        assert "Awaiting Admin Validation" not in html

    def it_shows_only_the_instructors_own_pipeline_when_that_is_all_there_is(instructor_fixture, client):
        ClassOfferingFactory(instructor=instructor_fixture, title="My Draft", slug="my-draft")
        client.force_login(instructor_fixture.user)
        html = client.get(reverse("classes:teach_overview")).content.decode()
        assert "Needs Your Attention" in html
        assert "My Draft" in html
        assert "Waiting on Your Review" not in html
        assert "Needs Attention · all clear" not in html

    def it_shows_a_guild_leads_queue_even_with_no_classes_of_their_own(instructor_fixture, client):
        """A lead who teaches nothing still has a review queue, so the card must not hide with it."""
        _pending_class_in_guild_led_by(instructor_fixture, "Someone Elses Class", "someone-elses")
        client.force_login(instructor_fixture.user)
        resp = client.get(reverse("classes:teach_overview"))
        html = resp.content.decode()
        assert resp.context["has_classes"] is False
        assert "Waiting on Your Review" in html
        assert "Someone Elses Class" in html
        assert "Needs Your Attention" not in html

    def it_counts_every_queue_in_the_cards_own_total(instructor_fixture, client):
        _pending_class_in_guild_led_by(instructor_fixture, "Guild Queue Class", "guild-queue")
        ClassOfferingFactory(instructor=instructor_fixture, title="My Draft", slug="my-draft")
        client.force_login(instructor_fixture.user)
        resp = client.get(reverse("classes:teach_overview"))
        assert resp.context["stats"]["needs_attention"] == 2

    def it_drops_the_all_caught_up_reassurance_lines(instructor_fixture, client):
        """Those three empty-state paragraphs were the bulk this round set out to remove."""
        client.force_login(instructor_fixture.user)
        html = client.get(reverse("classes:teach_overview")).content.decode()
        assert "you're all caught up" not in html


def describe_guild_approve_classes_help_key():
    """The Info View entry and its ShotSpec both hang off this key, so it cannot come and go
    with the queue's contents the way a group heading does."""

    def it_survives_an_empty_review_queue(instructor_fixture, client):
        from tests.membership.factories import GuildFactory

        GuildFactory(name="Quiet Guild", guild_lead=instructor_fixture)
        client.force_login(instructor_fixture.user)
        resp = client.get(reverse("classes:teach_overview"))
        html = resp.content.decode()
        assert resp.context["is_guild_lead"] is True
        assert "Waiting on Your Review" not in html
        assert html.count('data-help-key="guild.approve-classes"') == 1

    def it_is_there_exactly_once_with_a_full_queue(instructor_fixture, client):
        _pending_class_in_guild_led_by(instructor_fixture, "Queued Class", "queued-class")
        client.force_login(instructor_fixture.user)
        html = client.get(reverse("classes:teach_overview")).content.decode()
        assert "Waiting on Your Review" in html
        assert html.count('data-help-key="guild.approve-classes"') == 1

    def it_is_not_offered_to_someone_who_leads_no_guild(instructor_fixture, client):
        client.force_login(instructor_fixture.user)
        resp = client.get(reverse("classes:teach_overview"))
        assert resp.context["is_guild_lead"] is False
        assert 'data-help-key="guild.approve-classes"' not in resp.content.decode()


def describe_a_leads_own_pending_class():
    """A lead teaching a PENDING class in their own guild sits in two of the card's queues."""

    def it_appears_once_in_the_queue_that_is_actually_blocking(instructor_fixture, client):
        offering = _pending_class_in_guild_led_by(instructor_fixture, "Double Booked", "double-booked")
        offering.instructor = instructor_fixture
        offering.save(update_fields=["instructor"])
        client.force_login(instructor_fixture.user)
        resp = client.get(reverse("classes:teach_overview"))
        html = resp.content.decode()
        assert html.count("Double Booked") == 1
        # It is their review that is blocking it, so that is the group it belongs to.
        assert "Waiting on Your Review" in html
        assert list(resp.context["pending_classes"]) == []

    def it_counts_the_class_once(instructor_fixture, client):
        offering = _pending_class_in_guild_led_by(instructor_fixture, "Counted Once", "counted-once")
        offering.instructor = instructor_fixture
        offering.save(update_fields=["instructor"])
        client.force_login(instructor_fixture.user)
        resp = client.get(reverse("classes:teach_overview"))
        assert resp.context["stats"]["needs_attention"] == 1
        assert resp.context["stats"]["attention"] == 0

    def it_leaves_at_a_glance_counting_every_pending_class(instructor_fixture, client):
        """That tile is a plain fact about their catalog, not a mirror of the card."""
        offering = _pending_class_in_guild_led_by(instructor_fixture, "Still Pending", "still-pending")
        offering.instructor = instructor_fixture
        offering.save(update_fields=["instructor"])
        client.force_login(instructor_fixture.user)
        resp = client.get(reverse("classes:teach_overview"))
        assert resp.context["stats"]["pending"] == 1

    def it_keeps_a_pending_class_outside_the_lead_queue_in_the_pipeline(instructor_fixture, client):
        """The dedupe must only drop rows that genuinely appear twice."""
        offering = ClassOfferingFactory(
            instructor=instructor_fixture,
            title="Ordinary Pending",
            slug="ordinary-pending",
            status=ClassOffering.Status.PENDING,
        )
        client.force_login(instructor_fixture.user)
        resp = client.get(reverse("classes:teach_overview"))
        assert list(resp.context["pending_classes"]) == [offering]
        assert "Needs Your Attention" in resp.content.decode()
