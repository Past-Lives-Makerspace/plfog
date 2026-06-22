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
            assert b"Active waitlists" in resp.content
            assert b"Wheel 101" in resp.content

        def it_hides_the_waitlist_section_when_none(instructor_fixture, client):
            ClassOfferingFactory(instructor=instructor_fixture, slug="no-wait")
            client.force_login(instructor_fixture.user)
            resp = client.get(reverse("classes:teach_overview"))
            assert b"Active waitlists" not in resp.content

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
