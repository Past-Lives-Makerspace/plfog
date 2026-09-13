"""BDD specs for the printable class flyer (standalone print page, editor-gated, and locked
with the QR downloads until the class is published)."""

from __future__ import annotations

import pytest
from django.template.defaultfilters import date as date_filter
from django.urls import reverse
from django.utils.timezone import localtime

from classes.factories import CategoryFactory, ClassOfferingFactory, SeriesClassOfferingFactory, UserFactory
from classes.models import ClassOffering
from membership.models import Member
from tests.membership.factories import GuildFactory

Status = ClassOffering.Status

# The three marketing artifacts that lock and unlock together.
ARTIFACTS = [
    pytest.param(lambda pk: reverse("classes:class_flyer", args=[pk]), id="flyer"),
    pytest.param(lambda pk: reverse("classes:class_qr", args=[pk, "svg"]), id="qr-svg"),
    pytest.param(lambda pk: reverse("classes:class_qr", args=[pk, "png"]), id="qr-png"),
]
# Every editor can_edit_class admits who is not an admin: each is a distinct leg of that
# helper (instructor, guild lead FK, guild officer fog_role), so each is exercised.
NON_ADMIN_EDITORS = ["instructor", "guild_lead", "guild_officer"]
# The reasons are pinned as literals here on purpose: a spec that compared against the
# property would pass whatever it said.
PENDING_REASON = "The printable flyer and QR downloads unlock once this class is approved and published."
RETIRED_REASONS = {
    Status.CANCELLED: "This class is cancelled, so the flyer and QR downloads are no longer available.",
    Status.ARCHIVED: "This class is archived, so the flyer and QR downloads are no longer available.",
}


def describe_class_flyer():
    def describe_access():
        def it_renders_for_the_owning_instructor(member_user, client, free_offering, db):
            free_offering.instructor = member_user.member
            free_offering.save(update_fields=["instructor"])
            client.force_login(member_user)
            resp = client.get(reverse("classes:class_flyer", args=[free_offering.pk]))
            assert resp.status_code == 200

        def it_renders_for_an_admin(admin_user, client, free_offering, db):
            client.force_login(admin_user)
            resp = client.get(reverse("classes:class_flyer", args=[free_offering.pk]))
            assert resp.status_code == 200

        def it_forbids_a_non_owner_member(member_user, client, free_offering, db):
            client.force_login(member_user)
            resp = client.get(reverse("classes:class_flyer", args=[free_offering.pk]))
            assert resp.status_code == 403

        def it_forbids_an_anonymous_visitor(client, free_offering, db):
            resp = client.get(reverse("classes:class_flyer", args=[free_offering.pk]))
            assert resp.status_code == 403

    def describe_content():
        def it_shows_the_title_qr_and_registration_url(admin_user, client, free_offering, db):
            client.force_login(admin_user)
            body = client.get(reverse("classes:class_flyer", args=[free_offering.pk])).content.decode()
            assert "Free Demo" in body  # the class title
            assert "<svg" in body  # inline QR
            assert free_offering.qr_url in body  # the scan-to-register permalink

        def it_is_a_standalone_page_without_member_chrome(admin_user, client, free_offering, db):
            client.force_login(admin_user)
            body = client.get(reverse("classes:class_flyer", args=[free_offering.pk])).content
            assert b"hub-sidebar" not in body
            assert b"pl-topbar" not in body

        def it_shows_the_studio_venue(admin_user, client, free_offering, db):
            client.force_login(admin_user)
            body = client.get(reverse("classes:class_flyer", args=[free_offering.pk])).content.decode()
            assert "2808 SE 9th Ave, Portland, OR 97202" in body

    def describe_hero_fallbacks():
        def it_renders_the_offerings_own_hero_image(admin_user, client, free_offering, db):
            # free_offering carries a factory-built hero image by default.
            client.force_login(admin_user)
            body = client.get(reverse("classes:class_flyer", args=[free_offering.pk])).content.decode()
            assert "pl-flyer__hero-img" in body
            assert "pl-flyer__hero-placeholder" not in body

        def it_falls_back_to_the_legacy_image_url(admin_user, client, db):
            offering = ClassOfferingFactory(image="", legacy_image_url="https://legacy.example/hero.jpg")
            client.force_login(admin_user)
            body = client.get(reverse("classes:class_flyer", args=[offering.pk])).content.decode()
            assert "pl-flyer__hero-img" in body
            assert "_legacy-image" in body  # served through the legacy-image proxy
            assert "pl-flyer__hero-placeholder" not in body

        def it_renders_a_placeholder_when_no_image(admin_user, client, db):
            offering = ClassOfferingFactory(title="Zebra Craft", image="", legacy_image_url="")
            client.force_login(admin_user)
            resp = client.get(reverse("classes:class_flyer", args=[offering.pk]))
            assert resp.status_code == 200
            body = resp.content.decode()
            assert "pl-flyer__hero-placeholder" in body
            assert ">Z</div>" in body  # graceful initial, no crash on missing image

    def describe_schedule():
        def it_renders_a_single_session_date(admin_user, client, free_offering, db):
            client.force_login(admin_user)
            body = client.get(reverse("classes:class_flyer", args=[free_offering.pk])).content.decode()
            session = free_offering.sessions.first()
            assert date_filter(localtime(session.starts_at), "l, F j, Y") in body
            assert "Series ·" not in body

        def it_lists_every_date_for_a_series(admin_user, client, db):
            offering = SeriesClassOfferingFactory(session_count=3)
            client.force_login(admin_user)
            body = client.get(reverse("classes:class_flyer", args=[offering.pk])).content.decode()
            assert "Series · 3 sessions" in body

        def it_notes_flexible_scheduling_when_no_sessions(admin_user, client, db):
            offering = ClassOfferingFactory(scheduling_model=ClassOffering.SchedulingModel.FLEXIBLE)
            client.force_login(admin_user)
            body = client.get(reverse("classes:class_flyer", args=[offering.pk])).content.decode()
            assert "Flexible — arrange dates directly with the instructor." in body

        def it_shows_tba_when_no_sessions_and_not_flexible(admin_user, client, db):
            offering = ClassOfferingFactory(scheduling_model=ClassOffering.SchedulingModel.FIXED)
            client.force_login(admin_user)
            body = client.get(reverse("classes:class_flyer", args=[offering.pk])).content.decode()
            assert "Dates to be announced." in body

    def describe_price():
        def it_shows_free_for_a_free_class(admin_user, client, free_offering, db):
            client.force_login(admin_user)
            body = client.get(reverse("classes:class_flyer", args=[free_offering.pk])).content.decode()
            assert "<strong>Free</strong>" in body

        def it_shows_the_price_and_member_price_for_a_paid_class(admin_user, client, db):
            offering = ClassOfferingFactory(price_cents=5000, member_discount_pct=10)
            client.force_login(admin_user)
            body = client.get(reverse("classes:class_flyer", args=[offering.pk])).content.decode()
            assert "<strong>$50</strong>" in body
            assert "$45 for Past Lives members" in body

    def describe_edit_page_link():
        def it_links_the_flyer_from_the_admin_edit_page(admin_user, client, free_offering, db):
            client.force_login(admin_user)
            body = client.get(reverse("classes:admin_class_edit", args=[free_offering.pk])).content.decode()
            assert reverse("classes:class_flyer", args=[free_offering.pk]) in body
            assert "Open printable flyer" in body


def describe_lock_until_published():
    @pytest.fixture
    def actors(member_user, admin_user, db):
        """Every editor of one guild-led class: its instructor, the guild lead, a guild officer, an admin."""
        lead_user = UserFactory(username="flyer-lead@example.com")
        guild = GuildFactory(name="Flyer Guild", guild_lead=Member.objects.get(user=lead_user))
        officer_user = UserFactory(username="flyer-officer@example.com")
        officer = officer_user.member
        officer.fog_role = Member.FogRole.GUILD_OFFICER
        officer.save(update_fields=["fog_role"])
        officer.sync_user_permissions()
        return {
            "instructor": member_user,
            "guild_lead": lead_user,
            "guild_officer": officer_user,
            "admin": admin_user,
            "guild": guild,
        }

    def _offering(actors, status):
        return ClassOfferingFactory(
            category=CategoryFactory(guild=actors["guild"]), instructor=actors["instructor"].member, status=status
        )

    @pytest.mark.parametrize("artifact", ARTIFACTS)
    @pytest.mark.parametrize("status", [Status.DRAFT, Status.PENDING])
    @pytest.mark.parametrize("role", NON_ADMIN_EDITORS)
    def it_refuses_an_unpublished_class_to_editors_who_are_not_admins(client, actors, role, status, artifact):
        offering = _offering(actors, status)
        client.force_login(actors[role])
        resp = client.get(artifact(offering.pk))
        assert resp.status_code == 403
        # The refusal explains why, and it is the same sentence the share card shows.
        assert resp.content.decode() == PENDING_REASON

    @pytest.mark.parametrize("artifact", ARTIFACTS)
    @pytest.mark.parametrize("status", [Status.CANCELLED, Status.ARCHIVED])
    @pytest.mark.parametrize("role", NON_ADMIN_EDITORS)
    def it_refuses_a_retired_class_without_promising_publication(client, actors, role, status, artifact):
        # Cancelled and archived lock again (not bookable), but "once it is published"
        # would be false for a class that was live, so the sentence names the state.
        offering = _offering(actors, status)
        client.force_login(actors[role])
        resp = client.get(artifact(offering.pk))
        assert resp.status_code == 403
        assert resp.content.decode() == RETIRED_REASONS[status]
        assert "published" not in resp.content.decode()

    @pytest.mark.parametrize("artifact", ARTIFACTS)
    @pytest.mark.parametrize("status", list(Status))
    def it_lets_an_admin_open_every_status(client, actors, status, artifact):
        offering = _offering(actors, status)
        client.force_login(actors["admin"])
        assert client.get(artifact(offering.pk)).status_code == 200

    @pytest.mark.parametrize("artifact", ARTIFACTS)
    @pytest.mark.parametrize("role", [*NON_ADMIN_EDITORS, "admin"])
    def it_opens_a_published_class_for_every_editor(client, actors, role, artifact):
        offering = _offering(actors, Status.PUBLISHED)
        client.force_login(actors[role])
        assert client.get(artifact(offering.pk)).status_code == 200

    @pytest.mark.parametrize("artifact", ARTIFACTS)
    def it_keeps_the_plain_refusal_for_a_stranger(client, actors, artifact):
        # can_edit_class is still the first gate: a member who cannot edit the class gets
        # the old "no access" line and learns nothing about its review state.
        offering = _offering(actors, Status.DRAFT)
        client.force_login(UserFactory(username="flyer-stranger@example.com"))
        resp = client.get(artifact(offering.pk))
        assert resp.status_code == 403
        assert resp.content.decode() == "You don't have access to this class."

    @pytest.mark.parametrize("artifact", ARTIFACTS)
    def it_locks_an_admin_previewing_as_a_lower_role(client, actors, artifact):
        # The override honors view-as like every permissions helper: an admin who instructs
        # the class and previews as a member sees the lock the instructor would.
        offering = ClassOfferingFactory(
            category=CategoryFactory(guild=actors["guild"]), instructor=actors["admin"].member, status=Status.DRAFT
        )
        client.force_login(actors["admin"])
        session = client.session
        session["view_as_role"] = "member"
        session.save()
        resp = client.get(artifact(offering.pk))
        assert resp.status_code == 403
        assert resp.content.decode() == PENDING_REASON


def describe_share_card():
    """The Share & Print card on the class pages: what is there before and after publication."""

    FLYER_BUTTON = "Open printable flyer"
    DOWNLOADS = ("Download QR (SVG)", "Download QR (PNG)")
    TOUR_KEY = 'data-help-key="teach.class-qr"'
    # Substrings after the apostrophe, so the assertions do not care how it is escaped.
    LIVE_HINT = "public page. Print it on signage, add it to a flyer, or hand it out."
    PENDING_HINT = "public page once it is published. The share link stays the same."

    def _teach_edit(client, user, offering):
        client.force_login(user)
        resp = client.get(reverse("classes:teach_class_edit", args=[offering.pk]))
        assert resp.status_code == 200
        return resp.content.decode()

    def _admin_edit(client, user, offering):
        client.force_login(user)
        resp = client.get(reverse("classes:admin_class_edit", args=[offering.pk]))
        assert resp.status_code == 200
        return resp.content.decode()

    def _assert_locked(body, offering):
        assert FLYER_BUTTON not in body
        assert reverse("classes:class_flyer", args=[offering.pk]) not in body
        for label in DOWNLOADS:
            assert label not in body
        assert reverse("classes:class_qr", args=[offering.pk, "svg"]) not in body
        assert reverse("classes:class_qr", args=[offering.pk, "png"]) not in body
        assert PENDING_REASON in body
        assert PENDING_HINT in body
        assert LIVE_HINT not in body

    def _assert_unlocked(body, offering):
        assert FLYER_BUTTON in body
        assert reverse("classes:class_flyer", args=[offering.pk]) in body
        for label in DOWNLOADS:
            assert label in body
        assert reverse("classes:class_qr", args=[offering.pk, "svg"]) in body
        assert reverse("classes:class_qr", args=[offering.pk, "png"]) in body
        assert PENDING_REASON not in body

    @pytest.mark.parametrize("status", [Status.DRAFT, Status.PENDING])
    def it_hides_the_flyer_button_and_downloads_from_the_instructor_before_publication(member_user, client, status, db):
        offering = ClassOfferingFactory(instructor=member_user.member, status=status)
        body = _teach_edit(client, member_user, offering)
        _assert_locked(body, offering)
        # The share card itself stays, link and all.
        assert "pl-qr-share" in body
        assert offering.qr_url in body

    def it_shows_the_flyer_button_and_downloads_to_the_instructor_once_published(member_user, client, db):
        # A live class lands on the light edit page, so the unlocked card has to be there.
        offering = ClassOfferingFactory(instructor=member_user.member, status=Status.PUBLISHED)
        body = _teach_edit(client, member_user, offering)
        _assert_unlocked(body, offering)
        assert LIVE_HINT in body
        assert PENDING_HINT not in body
        # Between Locked Details and the form, so Save stays the last thing on the page.
        assert body.index("Locked Details") < body.index("pl-qr-share-block") < body.index('<form method="post">')

    def it_shows_an_admin_the_draft_flyer_button_and_downloads_as_before(admin_user, client, db):
        offering = ClassOfferingFactory(status=Status.DRAFT)
        body = _admin_edit(client, admin_user, offering)
        _assert_unlocked(body, offering)
        # The hint is still honest about the QR: it opens the page once published.
        assert PENDING_HINT in body

    def it_shows_an_admin_the_live_hint_once_published(admin_user, client, db):
        offering = ClassOfferingFactory(status=Status.PUBLISHED)
        body = _admin_edit(client, admin_user, offering)
        _assert_unlocked(body, offering)
        assert LIVE_HINT in body

    @pytest.mark.parametrize("status", [Status.DRAFT, Status.PUBLISHED])
    def it_keeps_the_tour_target_on_the_card_in_every_state(member_user, client, status, db):
        # The "Print a Flyer or QR" tour stop targets teach.class-qr; the key moved from the
        # button (absent on a draft) to the card wrapper, so the stop always finds it.
        offering = ClassOfferingFactory(instructor=member_user.member, status=status)
        body = _teach_edit(client, member_user, offering)
        assert body.count(TOUR_KEY) == 1
