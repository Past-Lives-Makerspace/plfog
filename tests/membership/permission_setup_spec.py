"""BDD specs for Get Set Up (#1167): a granted permission's checklist, its badge and its Guide page.

Tour Guide is the worked example: four steps that tick from what the guide has done (a healthy
Google Calendar, any tour hours, a tour marked Attended) or from Got It, an optional calendar step
that never holds up finishing and is unavailable while Google sign in is not set up, a badge given
once on finishing and taken back on revoke, and the wiki page an admin links as the Guide.

Names are factory strings no changelog could contain (STANDARDS.md section 8).
"""

from __future__ import annotations

import importlib
from datetime import timedelta

import pytest
from django.apps import apps as django_apps
from django.utils import timezone

from membership.models import AdminCapability, LeadershipBadge, PermissionSetup, WikiPage
from membership.permission_setup import SetupState, spec_for
from membership.services.provisioning import provision_user_for_member
from tests.membership.factories import MemberFactory
from tests.tours.conftest import make_guide
from tests.tours.factories import TourBookingFactory, TourCalendarConnectionFactory, TourHoursFactory
from tours import setup as tour_setup
from tours.models import TourBooking, TourCalendarConnection

pytestmark = pytest.mark.django_db

TOUR_GUIDE = AdminCapability.Capability.TOUR_GUIDE


@pytest.fixture
def google_on(settings) -> None:
    """Google sign in for Tour Guides is set up, so the calendar step can be done."""
    settings.GOOGLE_OAUTH_CLIENT_ID = "client-id"
    settings.GOOGLE_OAUTH_CLIENT_SECRET = "client-secret"


def _guide(granted_by=None):
    member = MemberFactory(full_legal_name="Quillon Brask")
    member.admin_capabilities.create(capability=TOUR_GUIDE, granted_by=granted_by)
    return member


def _load(member) -> SetupState:
    state = SetupState.load(member, TOUR_GUIDE)
    assert state is not None
    return state


def _keys(state: SetupState, attr: str) -> list[str]:
    return [view.step.key for view in state.steps if getattr(view, attr)]


def _attend(guide, party_size: int = 3) -> TourBooking:
    return TourBookingFactory(guide=guide, status=TourBooking.Status.ATTENDED, party_size=party_size)


def describe_spec_for():
    def it_has_tour_guide_and_nothing_else_yet():
        assert spec_for(TOUR_GUIDE) is tour_setup.TOUR_GUIDE_SETUP
        assert spec_for("not_a_permission") is None

    def it_raises_on_an_unknown_step():
        with pytest.raises(KeyError):
            tour_setup.TOUR_GUIDE_SETUP.step("nope")

    def it_finds_a_step_by_key():
        assert tour_setup.TOUR_GUIDE_SETUP.step(tour_setup.LEARN).got_it is True


def describe_SetupState_load():
    def it_is_none_without_the_grant():
        assert SetupState.load(MemberFactory(), TOUR_GUIDE) is None

    def it_is_none_for_a_permission_with_no_card():
        member = MemberFactory()
        assert SetupState.load(member, "not_a_permission") is None

    def describe_on_a_first_visit():
        def it_opens_the_calendar_step_when_google_is_set_up(google_on):
            state = _load(_guide())
            assert state.open_step is not None
            assert state.open_step.step.key == tour_setup.CALENDAR
            assert state.done_count == 0
            assert len(state.counted) == 4
            assert state.title == "Get Set Up to Give Tours"

        def it_marks_the_calendar_unavailable_and_opens_hours_without_google():
            state = _load(_guide())
            assert _keys(state, "unavailable") == [tour_setup.CALENDAR]
            assert state.open_step is not None
            assert state.open_step.step.key == tour_setup.HOURS
            assert len(state.counted) == 3
            calendar = state.steps[0]
            assert calendar.note.startswith("Not available yet")

        def it_names_whoever_granted_it():
            granter = MemberFactory(full_legal_name="Ottilie Varnsworth")
            provision_user_for_member(granter)
            granter.refresh_from_db()
            state = _load(_guide(granted_by=granter.user))
            assert state.granter_name == "Ottilie Varnsworth"

        def it_says_an_admin_when_nobody_or_the_member_granted_it():
            assert _load(_guide()).granter_name == "an admin"
            member = MemberFactory()
            provision_user_for_member(member)
            member.refresh_from_db()
            member.admin_capabilities.create(capability=TOUR_GUIDE, granted_by=member.user)
            assert _load(member).granter_name == "an admin"

        def it_makes_one_progress_row():
            guide = _guide()
            _load(guide)
            _load(guide)
            assert PermissionSetup.objects.filter(member=guide, permission=TOUR_GUIDE).count() == 1

    def describe_steps_that_tick_themselves():
        def it_ticks_the_calendar_with_a_healthy_connection_and_names_the_account(google_on):
            guide = _guide()
            TourCalendarConnectionFactory(guide=guide, google_email="quill@example.org")
            state = _load(guide)
            assert tour_setup.CALENDAR in _keys(state, "done")
            assert state.steps[0].note == "Done · quill@example.org"

        def it_does_not_tick_a_broken_calendar(google_on):
            guide = _guide()
            TourCalendarConnectionFactory(guide=guide, status=TourCalendarConnection.Status.BROKEN)
            assert tour_setup.CALENDAR not in _keys(_load(guide), "done")

        def it_says_done_alone_for_a_connection_with_no_email(google_on):
            guide = _guide()
            TourCalendarConnectionFactory(guide=guide, google_email="")
            assert _load(guide).steps[0].note == "Done"

        def it_ticks_hours_and_switches_to_the_milestone_title():
            guide = _guide()
            TourHoursFactory(guide=guide)
            state = _load(guide)
            assert tour_setup.HOURS in _keys(state, "done")
            assert state.title == "Visitors Can Book You Now"
            assert "lobby kiosk" in state.lead
            assert state.open_step is not None
            assert state.open_step.step.key == tour_setup.LEARN

        def it_ticks_the_first_tour_on_an_attended_booking_only():
            guide = _guide()
            TourBookingFactory(guide=guide, status=TourBooking.Status.NO_SHOW)
            assert tour_setup.FIRST_TOUR not in _keys(_load(guide), "done")
            _attend(guide)
            assert tour_setup.FIRST_TOUR in _keys(_load(guide), "done")

    def describe_steps_the_member_marks():
        def it_ticks_the_reading_step_on_got_it():
            guide = _guide()
            _load(guide).row.mark_read(tour_setup.LEARN)
            assert tour_setup.LEARN in _keys(_load(guide), "done")

        def it_skips_an_optional_step_and_opens_the_next(google_on):
            guide = _guide()
            _load(guide).row.skip(tour_setup.CALENDAR)
            state = _load(guide)
            assert _keys(state, "skipped") == [tour_setup.CALENDAR]
            assert state.steps[0].note == "Skipped"
            assert state.done_count == 1
            assert state.open_step is not None
            assert state.open_step.step.key == tour_setup.HOURS

        def it_never_counts_a_skip_on_a_required_step():
            guide = _guide()
            row = _load(guide).row
            row.skipped_steps = [tour_setup.HOURS]
            row.save()
            assert _keys(_load(guide), "skipped") == []

        def it_marks_each_step_once():
            row = _load(_guide()).row
            row.mark_read(tour_setup.LEARN)
            row.mark_read(tour_setup.LEARN)
            row.skip(tour_setup.CALENDAR)
            row.skip(tour_setup.CALENDAR)
            row.refresh_from_db()
            assert row.read_steps == [tour_setup.LEARN]
            assert row.skipped_steps == [tour_setup.CALENDAR]

    def describe_finishing():
        def _do_required(guide):
            TourHoursFactory(guide=guide)
            _attend(guide, party_size=4)
            _load(guide).row.mark_read(tour_setup.LEARN)

        def it_finishes_without_the_optional_calendar(google_on):
            guide = _guide()
            _do_required(guide)
            state = _load(guide)
            assert state.finished
            assert state.open_step is not None
            assert state.open_step.step.key == tour_setup.CALENDAR

        def it_gives_the_gold_tour_guide_badge_once():
            guide = _guide()
            _do_required(guide)
            first = _load(guide)
            assert first.finished
            assert first.just_finished
            badge = LeadershipBadge.objects.get(permission=TOUR_GUIDE)
            assert badge.label == "Tour Guide"
            assert badge.color == "#EEB44B"
            assert list(badge.members.all()) == [guide]
            assert first.badges == [badge]
            second = _load(guide)
            assert not second.just_finished
            assert second.row.finish() is False
            assert badge.members.count() == 1

        def it_keeps_a_badge_admins_renamed():
            LeadershipBadge.objects.create(label="Tour Wrangler", color="#123456", permission=TOUR_GUIDE)
            guide = _guide()
            _do_required(guide)
            _load(guide)
            assert list(guide.leadership_badges.values_list("label", flat=True)) == ["Tour Wrangler"]

        def it_counts_tours_given_and_visitors():
            guide = _guide()
            _do_required(guide)
            _attend(guide, party_size=2)
            stats = _load(guide).facts.stats
            assert [(stat.label, stat.value) for stat in stats] == [("Tours Given", 2), ("Visitors", 6)]

        def it_drops_the_pill_and_the_badge_line_once_an_admin_takes_the_badge():
            guide = _guide()
            _do_required(guide)
            assert _load(guide).done_line == tour_setup.TOUR_GUIDE_SETUP.done_line
            LeadershipBadge.objects.get(permission=TOUR_GUIDE).take(guide)
            state = _load(guide)
            assert state.finished
            assert state.badges == []
            assert state.done_line == "You're set up to give tours."

        def it_shows_only_the_members_own_badge():
            guide = _guide()
            _do_required(guide)
            _load(guide)
            other = _guide()
            _do_required(other)
            _load(other)
            LeadershipBadge.objects.get(permission=TOUR_GUIDE).take(guide)
            assert _load(guide).badges == []
            assert len(_load(other).badges) == 1

        def it_has_no_badges_before_finishing():
            assert _load(_guide()).badges == []

        def it_states_the_pay_rule_and_never_a_figure():
            assert (
                tour_setup.TOUR_GUIDE_SETUP.rule_line == "You're paid $50 when a visitor you toured becomes a member."
            )
            labels = [stat.label for stat in tour_setup.tour_guide_facts(_guide()).stats]
            assert labels == ["Tours Given", "Visitors"]

        def it_never_celebrates_an_unfinished_setup():
            assert _load(_guide()).row.celebrate() is False

    def describe_hiding():
        def it_hides_and_shows_the_card():
            guide = _guide()
            _load(guide).row.hide()
            assert _load(guide).hidden
            _load(guide).row.show()
            assert not _load(guide).hidden


def describe_marking_attended_from_the_save_path():
    def it_gives_the_badge_without_the_guide_opening_tours_and_keeps_their_toast():
        from tours.booking import mark_tour_outcome

        guide = _guide()
        TourHoursFactory(guide=guide)
        PermissionSetup.for_member(guide, TOUR_GUIDE).mark_read(tour_setup.LEARN)
        starts = timezone.now() - timedelta(hours=2)
        booking = TourBookingFactory(guide=guide, starts_at=starts, ends_at=starts + timedelta(hours=1))
        mark_tour_outcome(booking, TourBooking.Status.ATTENDED)
        assert guide.leadership_badges.filter(permission=TOUR_GUIDE).exists()
        row = PermissionSetup.objects.get(member=guide)
        assert row.completed_at is not None
        assert row.celebrated_at is None
        assert _load(guide).just_finished

    def it_changes_nothing_for_a_guide_without_the_grant():
        from tours.booking import mark_tour_outcome

        admin_guide = MemberFactory()
        starts = timezone.now() - timedelta(hours=2)
        booking = TourBookingFactory(guide=admin_guide, starts_at=starts, ends_at=starts + timedelta(hours=1))
        mark_tour_outcome(booking, TourBooking.Status.ATTENDED)
        assert not PermissionSetup.objects.filter(member=admin_guide).exists()
        assert not LeadershipBadge.objects.filter(permission=TOUR_GUIDE).exists()

    def it_leaves_a_guide_with_steps_left_unfinished():
        from tours.booking import mark_tour_outcome

        guide = _guide()
        starts = timezone.now() - timedelta(hours=2)
        booking = TourBookingFactory(guide=guide, starts_at=starts, ends_at=starts + timedelta(hours=1))
        mark_tour_outcome(booking, TourBooking.Status.NO_SHOW)
        mark_tour_outcome(booking, TourBooking.Status.ATTENDED)
        assert PermissionSetup.objects.get(member=guide).completed_at is None


def describe_revoking_tour_guide():
    def _finished_guide():
        guide = _guide()
        TourHoursFactory(guide=guide)
        _attend(guide)
        _load(guide).row.mark_read(tour_setup.LEARN)
        assert _load(guide).finished
        return guide

    def it_takes_the_badge_back_through_the_permissions_tab():
        guide = _finished_guide()
        guide.sync_admin_capabilities([])
        assert not guide.leadership_badges.exists()
        row = PermissionSetup.objects.get(member=guide)
        assert row.completed_at is None
        assert row.celebrated_at is None

    def it_takes_the_badge_back_through_the_view_as_switch():
        guide = _finished_guide()
        guide.set_admin_capability(TOUR_GUIDE, False)
        assert not guide.leadership_badges.exists()

    def it_earns_it_again_after_a_new_grant():
        guide = _finished_guide()
        guide.set_admin_capability(TOUR_GUIDE, False)
        guide.set_admin_capability(TOUR_GUIDE, True)
        state = _load(guide)
        assert state.finished
        assert state.just_finished
        assert guide.leadership_badges.filter(permission=TOUR_GUIDE).exists()

    def it_leaves_other_members_alone():
        guide = _finished_guide()
        other = _finished_guide()
        guide.sync_admin_capabilities([])
        assert other.leadership_badges.exists()

    def it_withdraws_nothing_for_an_empty_list():
        guide = _finished_guide()
        PermissionSetup.withdraw(guide, [])
        assert guide.leadership_badges.exists()

    def it_withdraws_nothing_when_switching_off_a_grant_they_never_had():
        member = MemberFactory()
        LeadershipBadge.for_permission(TOUR_GUIDE).give(member)
        member.set_admin_capability(TOUR_GUIDE, False)
        assert member.leadership_badges.exists()


def describe_PermissionSetup():
    def it_names_the_member_and_permission():
        guide = make_guide(full_legal_name="Fenwick Tallow")
        row = PermissionSetup.for_member(guide, TOUR_GUIDE)
        assert str(row) == "Fenwick Tallow: Tour Guide setup"


@pytest.mark.usefixtures("tour_guide_guide")
def describe_guide_page():
    def _page(title: str, **kwargs) -> WikiPage:
        return WikiPage.objects.create(title=title, kind=WikiPage.Kind.HOWTO, body="<p>x</p>", **kwargs)

    def it_finds_the_seeded_tour_guide_guide():
        page = WikiPage.guide_page_for(TOUR_GUIDE)
        assert page is not None
        assert page.title == "Tour Guide Guide"
        assert page.status == WikiPage.Status.OFFICIAL
        assert page.guild is None
        assert page.has_written_body
        assert page.revisions.count() == 1
        assert "Confirm First" in page.body
        assert "Lobby Kiosk" in page.body

    def it_moves_the_link_to_a_newly_picked_page():
        old = WikiPage.guide_page_for(TOUR_GUIDE)
        new = _page("Showing Folks Around Zephyrine")
        new.make_guide_for(TOUR_GUIDE)
        assert WikiPage.guide_page_for(TOUR_GUIDE) == new
        assert old is not None
        old.refresh_from_db()
        assert old.guide_for == ""

    def it_clears_the_link():
        page = WikiPage.guide_page_for(TOUR_GUIDE)
        assert page is not None
        page.make_guide_for("")
        assert WikiPage.guide_page_for(TOUR_GUIDE) is None

    def it_changes_nothing_when_picked_again():
        page = WikiPage.guide_page_for(TOUR_GUIDE)
        assert page is not None
        updated = page.updated_at
        page.make_guide_for(TOUR_GUIDE)
        page.refresh_from_db()
        assert page.updated_at == updated

    def it_ignores_an_archived_or_held_page():
        page = WikiPage.guide_page_for(TOUR_GUIDE)
        assert page is not None
        WikiPage.objects.filter(pk=page.pk).update(archived_at=timezone.now())
        assert WikiPage.guide_page_for(TOUR_GUIDE) is None
        WikiPage.objects.filter(pk=page.pk).update(archived_at=None, is_published=False)
        assert WikiPage.guide_page_for(TOUR_GUIDE) is None


class _Editor:
    """The one thing ``unseed`` reads from a schema editor: its connection's alias."""

    class connection:  # noqa: N801
        alias = "default"


def describe_seed_migration():
    seed_module = importlib.import_module("membership.migrations.0278_seed_tour_guide_guide")

    @pytest.mark.usefixtures("tour_guide_guide")
    def it_does_nothing_when_a_page_is_already_linked():
        before = WikiPage.objects.count()
        seed_module.seed(django_apps, None)
        assert WikiPage.objects.count() == before

    def it_links_an_existing_page_of_that_title():
        existing = WikiPage.objects.create(title="Tour Guide Guide", kind=WikiPage.Kind.HOWTO, body="<p>ours</p>")
        seed_module.seed(django_apps, None)
        existing.refresh_from_db()
        assert existing.guide_for == TOUR_GUIDE
        assert existing.body == "<p>ours</p>"

    def it_starts_every_other_spec_without_the_page():
        assert not WikiPage.objects.exists()

    def it_makes_the_page_with_a_free_slug_and_code():
        WikiPage.objects.create(title="Tour Guide Guide", kind=WikiPage.Kind.HOWTO, archived_at=timezone.now())
        seed_module.seed(django_apps, None)
        page = WikiPage.objects.get(guide_for=TOUR_GUIDE)
        assert page.slug == "tour-guide-guide-2"
        assert len(page.qr_code) == 6
        assert "Getting Paid" in page.search_text
        assert "<h2>" not in page.search_text

    @pytest.mark.usefixtures("tour_guide_guide")
    def it_deletes_an_untouched_page_on_reverse():
        seed_module.unseed(django_apps, _Editor())
        assert not WikiPage.objects.filter(title="Tour Guide Guide").exists()

    @pytest.mark.usefixtures("tour_guide_guide")
    def it_only_unlinks_an_edited_page_on_reverse():
        page = WikiPage.objects.get(guide_for=TOUR_GUIDE)
        WikiPage.objects.filter(pk=page.pk).update(body="<p>Edited by an admin</p>")
        seed_module.unseed(django_apps, _Editor())
        page.refresh_from_db()
        assert page.guide_for == ""
