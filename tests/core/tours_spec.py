"""BDD-style tests for the guided-tour registry + offer context (Spec C §5)."""

import re
from datetime import timedelta
from pathlib import Path

import pytest
from django.contrib.auth.models import AnonymousUser, User
from django.test import RequestFactory
from django.urls import reverse
from django.utils import timezone

from core.help_registry import HELP_KEYS
from core.models import SiteConfiguration, TourState
from core.tours import (
    TOURS,
    Tour,
    TourStep,
    _admin_audience,
    _demo_class_slug,
    _demo_guild_slug,
    _instructor_class_pk,
    _tour_payload,
    entry_url_for,
    help_card_rows,
    tour_offer_context,
    tours_for,
)
from membership.models import Member
from tests.membership.factories import GuildFactory

pytestmark = pytest.mark.django_db

TOUR_KEY_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
HELP_KEY_TARGET_RE = re.compile(r'^\[data-help-key="([^"]+)"\]$')

TEMPLATES_DIR = Path(__file__).resolve().parent.parent.parent / "templates"
_STAMPED_KEY_RE = re.compile(r'data-help-key="([^"{}]*)"')
_STAMPED_PARAM_RE = re.compile(r'action_help_key="([^"{}]*)"')


def _keys_stamped_in_templates() -> set[str]:
    """Every help key actually stamped as an element attribute under templates/."""
    keys: set[str] = set()
    for path in TEMPLATES_DIR.rglob("*.html"):
        text = path.read_text(encoding="utf-8")
        keys.update(_STAMPED_KEY_RE.findall(text))
        keys.update(_STAMPED_PARAM_RE.findall(text))
    return keys


def _member(name: str = "tour-member", **kwargs) -> Member:
    # Creating the User auto-provisions a linked ACTIVE Member (the
    # ensure_user_has_member signal) — adjust that row rather than minting a second.
    user = User.objects.create_user(username=name, email=f"{name}@example.com")
    member = Member.objects.get(user=user)
    kwargs.setdefault("welcome_dismissed_at", timezone.now())
    for field, value in kwargs.items():
        setattr(member, field, value)
    member.save()
    return member


def _lead(name: str = "tour-lead") -> Member:
    member = _member(name)
    GuildFactory(name=f"Guild of {name}", guild_lead=member)
    return member


def _get(url: str, member: Member):
    request = RequestFactory().get(url)
    request.user = member.user
    return request


def describe_TOURS():
    def it_registers_the_four_role_tours():
        assert set(TOURS) == {"member-welcome", "guild-lead", "instructor", "admin"}

    def it_keys_match_their_tour_and_the_slug_format():
        for key, tour in TOURS.items():
            assert tour.key == key
            assert TOUR_KEY_RE.match(key)

    def it_gives_every_step_a_title_and_body():
        for tour in TOURS.values():
            assert len(tour.steps) >= 2
            for step in tour.steps:
                assert step.title
                assert step.body

    def it_only_targets_registered_help_keys():
        # A tour step naming an unregistered help key would silently vanish from
        # every run (the selector matches nothing) — fail here instead.
        for tour in TOURS.values():
            for step in tour.steps:
                if step.target is None:
                    continue
                match = HELP_KEY_TARGET_RE.match(step.target)
                assert match, f"{tour.key}: non-help-key target {step.target!r}"
                assert match.group(1) in HELP_KEYS, f"{tour.key}: unregistered key {match.group(1)!r}"

    def it_targets_only_keys_stamped_in_a_template():
        # The template->registry drift guard (tests/hub/help_keys_spec) does NOT
        # enforce the reverse: a key can be registered yet stamped in zero
        # templates, so a tour step aimed at it would spotlight nothing on the
        # page. Guard the tour direction explicitly.
        stamped = _keys_stamped_in_templates()
        unstamped = sorted(
            {
                match.group(1)
                for tour in TOURS.values()
                for step in tour.steps
                if step.target is not None and (match := HELP_KEY_TARGET_RE.match(step.target))
                if match.group(1) not in stamped
            }
        )
        assert not unstamped, "tour targets not stamped in any template:\n  " + "\n  ".join(unstamped)

    def it_reverses_every_entry_url(db):
        member = _lead("urls")
        for tour in TOURS.values():
            url = entry_url_for(tour, member)
            assert url.endswith(f"?tour={tour.key}")

    def it_raises_for_a_guild_lead_url_without_a_staffed_guild():
        # Unreachable behind the audience gate, but the helper fails loudly
        # rather than 500ing deeper in reverse().
        member = _member("no-guild")
        with pytest.raises(ValueError, match="staffs no guild"):
            entry_url_for(TOURS["guild-lead"], member)

    def it_uses_title_case_for_tour_and_step_titles():
        # Locks the Title Case copy pass — a revert to sentence case must fail here.
        assert TOURS["member-welcome"].title == "The Member Hub"
        assert TOURS["guild-lead"].title == "Guild Lead Tools"
        assert TOURS["instructor"].title == "The Teaching Portal"
        assert TOURS["admin"].title == "Admin Controls"
        member_step_titles = {step.title for step in TOURS["member-welcome"].steps}
        assert "Everything in One Place" in member_step_titles
        assert "Community Calendar" in member_step_titles
        lead_step_titles = {step.title for step in TOURS["guild-lead"].steps}
        assert "Your Guild's Control Room" in lead_step_titles
        assert "Your Wishlist" in lead_step_titles

    def describe_guild_lead_url_fallback_for_admins_and_officers():
        def it_falls_back_to_the_first_active_guild_ordered_by_name_for_an_admin():
            admin = _member("admin-fallback", fog_role=Member.FogRole.ADMIN)
            GuildFactory(name="Aaa Inactive Guild", is_active=False)  # alphabetically first, but skipped
            alpha = GuildFactory(name="Bbb Active Guild", is_active=True)
            GuildFactory(name="Zzz Active Guild", is_active=True)

            url = entry_url_for(TOURS["guild-lead"], admin)

            assert url == f"/guilds/{alpha.pk}/edit/?tour=guild-lead"

        def it_falls_back_to_the_first_active_guild_for_a_guild_officer():
            officer = _member("officer-fallback", fog_role=Member.FogRole.GUILD_OFFICER)
            only_active = GuildFactory(name="Only Active Guild", is_active=True)

            url = entry_url_for(TOURS["guild-lead"], officer)

            assert url == f"/guilds/{only_active.pk}/edit/?tour=guild-lead"

        def it_prefers_an_admins_own_staffed_guild_over_the_fallback():
            admin = _member("admin-owns", fog_role=Member.FogRole.ADMIN)
            GuildFactory(name="Aaa Would Be Fallback", is_active=True)
            own = GuildFactory(name="Owns This Guild", guild_lead=admin)

            url = entry_url_for(TOURS["guild-lead"], admin)

            assert url == f"/guilds/{own.pk}/edit/?tour=guild-lead"

        def it_raises_for_an_admin_with_no_staffed_guild_and_no_active_guilds():
            admin = _member("admin-stuck", fog_role=Member.FogRole.ADMIN)
            GuildFactory(name="Only Inactive Guild", is_active=False)

            with pytest.raises(ValueError, match="staffs no guild"):
                entry_url_for(TOURS["guild-lead"], admin)

    def describe_audiences():
        def it_welcomes_every_member_and_gates_the_lead_tour():
            plain = _member("plain")
            assert TOURS["member-welcome"].audience(plain) is True
            assert TOURS["guild-lead"].audience(plain) is False
            assert TOURS["instructor"].audience(plain) is False  # locked until the orientation unlock (Spec D)

        def it_admits_unlocked_members_to_the_instructor_tour():
            unlocked = _member("unlocked", instructor_oriented_at=timezone.now())
            assert TOURS["instructor"].audience(unlocked) is True

        def it_admits_guild_leads_and_staff_to_the_lead_tour():
            lead = _lead("lead")
            assert TOURS["guild-lead"].audience(lead) is True

        def it_excludes_inactive_members_from_the_instructor_tour():
            former = _member("former", status=Member.Status.FORMER)
            assert TOURS["instructor"].audience(former) is False

        def it_admits_admins_to_the_lead_tour_even_without_staffing_a_guild():
            GuildFactory(name="Audience Active Guild", is_active=True)
            admin = _member("admin-audience", fog_role=Member.FogRole.ADMIN)
            assert admin.is_guild_lead is False
            assert admin.is_guild_staff is False
            assert TOURS["guild-lead"].audience(admin) is True

        def it_excludes_admins_when_no_active_guild_exists_to_run_the_tour_on():
            # The entry URL is a guild edit page — with zero active guilds the tour
            # simply isn't offered (rather than 500ing the Help page on entry_url_for).
            admin = _member("admin-no-guilds", fog_role=Member.FogRole.ADMIN)
            assert TOURS["guild-lead"].audience(admin) is False

        def it_excludes_admins_when_the_only_guild_is_inactive():
            GuildFactory(name="Audience Inactive Guild", is_active=False)
            admin = _member("admin-inactive-only", fog_role=Member.FogRole.ADMIN)
            assert TOURS["guild-lead"].audience(admin) is False

        def it_admits_guild_officers_to_the_lead_tour_even_without_staffing_a_guild():
            GuildFactory(name="Audience Officer Guild", is_active=True)
            officer = _member("officer-audience", fog_role=Member.FogRole.GUILD_OFFICER)
            assert officer.is_guild_lead is False
            assert officer.is_guild_staff is False
            assert TOURS["guild-lead"].audience(officer) is True


def describe_tours_for():
    def it_lists_only_tours_whose_audience_passes():
        plain = _member("tf-plain")
        assert [t.key for t in tours_for(plain)] == ["member-welcome"]
        unlocked = _member("tf-unlocked", instructor_oriented_at=timezone.now())
        assert [t.key for t in tours_for(unlocked)] == ["member-welcome", "instructor"]

    def it_includes_the_lead_tour_for_leads():
        lead = _lead("tf-lead")
        assert [t.key for t in tours_for(lead)] == ["member-welcome", "guild-lead"]
        lead.instructor_oriented_at = timezone.now()
        lead.save(update_fields=["instructor_oriented_at"])
        assert [t.key for t in tours_for(lead)] == ["member-welcome", "guild-lead", "instructor"]

    def it_includes_the_lead_and_admin_tours_for_admins_who_staff_no_guild():
        GuildFactory(name="Rows Active Guild", is_active=True)
        admin = _member("tf-admin", fog_role=Member.FogRole.ADMIN)
        assert [t.key for t in tours_for(admin)] == ["member-welcome", "guild-lead", "admin"]

    def it_includes_the_lead_tour_for_guild_officers_who_staff_no_guild():
        GuildFactory(name="Rows Officer Guild", is_active=True)
        officer = _member("tf-officer", fog_role=Member.FogRole.GUILD_OFFICER)
        assert [t.key for t in tours_for(officer)] == ["member-welcome", "guild-lead"]


def describe_help_card_rows():
    def it_builds_a_row_per_eligible_tour_with_taken_state():
        member = _member("rows", instructor_oriented_at=timezone.now())
        TourState.objects.mark_completed(member.user, "member-welcome")
        rows = help_card_rows(member)
        assert [row["tour"].key for row in rows] == ["member-welcome", "instructor"]
        assert rows[0]["completed"] is True
        assert rows[1]["completed"] is False
        assert rows[0]["url"] == f"{reverse('hub_home')}?tour=member-welcome"

    def it_treats_dismissed_as_not_taken():
        member = _member("rows-dis")
        TourState.objects.mark_dismissed(member.user, "member-welcome")
        rows = help_card_rows(member)
        assert rows[0]["completed"] is False

    def it_includes_the_lead_tour_row_for_an_admin_via_the_active_guild_fallback():
        admin = _member("rows-admin", fog_role=Member.FogRole.ADMIN)
        fallback_guild = GuildFactory(name="Rows Fallback Guild", is_active=True)
        rows = help_card_rows(admin)
        lead_row = next(row for row in rows if row["tour"].key == "guild-lead")
        assert lead_row["url"] == f"/guilds/{fallback_guild.pk}/edit/?tour=guild-lead"


def describe_tour_offer_context():
    def it_fails_loudly_on_an_unregistered_page_key():
        member = _member("ctx-bad")
        with pytest.raises(KeyError):
            tour_offer_context(_get("/home/", member), "not-a-tour")

    def it_offers_nothing_to_anonymous_visitors():
        request = RequestFactory().get("/home/")
        request.user = AnonymousUser()
        ctx = tour_offer_context(request, "member-welcome")
        assert ctx["show_tour_offer"] is False
        assert ctx["tour_json"] is None
        assert TourState.objects.count() == 0

    def it_offers_nothing_to_a_user_without_a_member():
        user = User.objects.create_user(username="no-member", email="nm@example.com")
        request = RequestFactory().get("/home/")
        request.user = user
        assert tour_offer_context(request, "member-welcome")["show_tour_offer"] is False

    def it_offers_nothing_to_an_ineligible_member():
        member = _member("ctx-inel")
        ctx = tour_offer_context(_get("/x/", member), "guild-lead")
        assert ctx["show_tour_offer"] is False
        assert TourState.objects.count() == 0

    def it_offers_and_writes_an_offered_row_on_first_eligible_get():
        member = _member("ctx-first")
        ctx = tour_offer_context(_get("/home/", member), "member-welcome")
        assert ctx["show_tour_offer"] is True
        assert ctx["tour_autostart"] is False
        assert ctx["tour_json"]["key"] == "member-welcome"
        assert ctx["tour_json"]["autostart"] is False
        assert ctx["tour_json"]["opens_sidebar"] is True
        assert ctx["tour_json"]["state_url"] == reverse("hub_tour_state", kwargs={"tour_key": "member-welcome"})
        assert TourState.objects.status_for(member.user, "member-welcome") == TourState.Status.OFFERED

    def it_is_idempotent_across_refreshes_and_keeps_offering_while_offered():
        member = _member("ctx-again")
        tour_offer_context(_get("/home/", member), "member-welcome")
        ctx = tour_offer_context(_get("/home/", member), "member-welcome")
        assert ctx["show_tour_offer"] is True
        assert TourState.objects.filter(user=member.user, tour_key="member-welcome").count() == 1

    def it_is_suppressed_while_the_welcome_modal_would_show():
        # Brand-new member: nothing dismissed, nothing customized — the modal is
        # deliberately blocking, and the two never render on the same pageview.
        member = _member("ctx-new", welcome_dismissed_at=None)
        ctx = tour_offer_context(_get("/home/", member), "member-welcome")
        assert ctx["show_tour_offer"] is False
        assert TourState.objects.count() == 0  # not even recorded

    def it_is_suppressed_when_guided_tours_are_toggled_off():
        member = _member("ctx-off", guided_tours_enabled=False)
        ctx = tour_offer_context(_get("/home/", member), "member-welcome")
        assert ctx["show_tour_offer"] is False
        assert TourState.objects.count() == 0

    def it_is_suppressed_by_a_dismissed_row():
        member = _member("ctx-dis")
        TourState.objects.mark_dismissed(member.user, "member-welcome")
        assert tour_offer_context(_get("/home/", member), "member-welcome")["show_tour_offer"] is False

    def it_is_suppressed_by_a_completed_row():
        member = _member("ctx-comp")
        TourState.objects.mark_completed(member.user, "member-welcome")
        assert tour_offer_context(_get("/home/", member), "member-welcome")["show_tour_offer"] is False

    def describe_manual_start():
        def it_autostarts_for_an_eligible_member_without_writing_a_row():
            member = _member("ctx-manual")
            ctx = tour_offer_context(_get("/home/?tour=member-welcome", member), "member-welcome")
            assert ctx["tour_autostart"] is True
            assert ctx["show_tour_offer"] is False
            assert ctx["tour_json"]["autostart"] is True
            assert TourState.objects.count() == 0

        def it_works_even_after_dismissal_or_completion():
            member = _member("ctx-retake")
            TourState.objects.mark_completed(member.user, "member-welcome")
            ctx = tour_offer_context(_get("/home/?tour=member-welcome", member), "member-welcome")
            assert ctx["tour_autostart"] is True

        def it_works_with_the_toggle_off():
            member = _member("ctx-manual-off", guided_tours_enabled=False)
            ctx = tour_offer_context(_get("/home/?tour=member-welcome", member), "member-welcome")
            assert ctx["tour_autostart"] is True

        def it_ignores_a_foreign_or_unknown_tour_param():
            member = _member("ctx-foreign")
            ctx = tour_offer_context(_get("/home/?tour=guild-lead", member), "member-welcome")
            assert ctx["tour_autostart"] is False
            ctx = tour_offer_context(_get("/home/?tour=%3Cscript%3E", member), "member-welcome")
            assert ctx["tour_autostart"] is False

        def it_is_silently_ignored_for_an_ineligible_member():
            member = _member("ctx-manual-inel")
            ctx = tour_offer_context(_get("/x/?tour=guild-lead", member), "guild-lead")
            assert ctx["tour_autostart"] is False
            assert ctx["tour_json"] is None

    def describe_when_the_site_flag_is_off():
        # Contrast with it_works_with_the_toggle_off above: the per-member toggle
        # only stops auto-offers, but the site-wide switch kills the feature —
        # even a manual ?tour= start does nothing.
        @pytest.fixture(autouse=True)
        def _site_flag_off():
            config = SiteConfiguration.load()
            config.guided_tours_enabled = False
            config.save()

        def it_suppresses_the_offer():
            member = _member("ctx-site-off")
            ctx = tour_offer_context(_get("/home/", member), "member-welcome")
            assert ctx["show_tour_offer"] is False
            assert ctx["tour_json"] is None
            assert TourState.objects.count() == 0

        def it_blocks_even_a_manual_start():
            member = _member("ctx-site-off-manual")
            ctx = tour_offer_context(_get("/home/?tour=member-welcome", member), "member-welcome")
            assert ctx["tour_autostart"] is False
            assert ctx["tour_json"] is None


def describe_admin_tour():
    def it_admits_a_full_fog_admin():
        admin = _member("adm-full", fog_role=Member.FogRole.ADMIN)
        assert _admin_audience(admin) is True
        assert TOURS["admin"].audience(admin) is True

    def it_admits_a_member_holding_any_admin_capability():
        from membership.models import AdminCapability

        scoped = _member("adm-scoped")
        AdminCapability.objects.create(member=scoped, capability=AdminCapability.Capability.REFUNDS)
        assert _admin_audience(scoped) is True
        assert TOURS["admin"].audience(scoped) is True

    def it_excludes_a_plain_member():
        plain = _member("adm-none")
        assert _admin_audience(plain) is False
        assert TOURS["admin"].audience(plain) is False

    def it_lists_the_admin_tour_on_the_help_card_for_an_admin():
        admin = _member("adm-rows", fog_role=Member.FogRole.ADMIN)
        assert "admin" in [row["tour"].key for row in help_card_rows(admin)]

    def it_reverses_the_admin_entry_url():
        admin = _member("adm-url", fog_role=Member.FogRole.ADMIN)
        assert entry_url_for(TOURS["admin"], admin) == f"{reverse('hub_admin_tools')}?tour=admin"


def describe_member_tour_resolvers():
    def describe__demo_guild_slug():
        def it_prefers_an_active_guild_with_a_future_orientation_slot():
            from tests.membership.factories import OrientationSlotFactory

            member = _member("dg-slot")
            GuildFactory(name="Aaa No Slot Guild", is_active=True)  # alphabetically first, but no slot
            with_slot = GuildFactory(name="Zzz Slotted Guild", is_active=True)
            OrientationSlotFactory(guild=with_slot)
            assert _demo_guild_slug(member) == {"slug": with_slot.slug}

        def it_falls_back_to_the_first_active_guild_by_name_when_none_have_slots():
            member = _member("dg-fallback")
            GuildFactory(name="Zzz Active Guild", is_active=True)
            first = GuildFactory(name="Aaa Active Guild", is_active=True)
            assert _demo_guild_slug(member) == {"slug": first.slug}

        def it_raises_when_no_active_guild_exists():
            member = _member("dg-none")
            GuildFactory(name="Only Inactive Guild", is_active=False)
            with pytest.raises(ValueError, match="No active guild"):
                _demo_guild_slug(member)

    def describe__demo_class_slug():
        def it_returns_the_soonest_bookable_class_slug():
            from classes.factories import ClassOfferingFactory, ClassSessionFactory
            from classes.models import ClassOffering

            member = _member("dc-1")
            offering = ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED, is_private=False)
            ClassSessionFactory(
                class_offering=offering,
                starts_at=timezone.now() + timedelta(days=7),
                ends_at=timezone.now() + timedelta(days=7, hours=2),
            )
            assert _demo_class_slug(member) == {"slug": offering.slug}

        def it_raises_when_no_bookable_class_exists():
            member = _member("dc-none")
            with pytest.raises(ValueError, match="No bookable class"):
                _demo_class_slug(member)

    def describe__instructor_class_pk():
        def it_returns_the_members_newest_class_pk():
            from classes.factories import ClassOfferingFactory

            member = _member("ic-1")
            ClassOfferingFactory(instructor=member)
            newest = ClassOfferingFactory(instructor=member)
            assert _instructor_class_pk(member) == {"pk": newest.pk}

        def it_raises_when_the_member_owns_no_class():
            member = _member("ic-none")
            with pytest.raises(ValueError, match="owns no class"):
                _instructor_class_pk(member)


def describe__tour_payload():
    def it_keeps_a_plain_highlight_step_backward_compatible():
        member = _member("pl-plain")
        payload = _tour_payload(TOURS["member-welcome"], member, autostart=False)
        first = payload["steps"][0]
        assert first["navigate"] is None
        assert first["tab_set"] is None
        assert first["click"] is None
        assert first["wait_for"] is None
        assert first["page_path"] == reverse("hub_home")
        assert payload["resume_step"] == 0
        assert payload["autostart"] is False

    def it_resolves_navigate_to_a_relative_href_and_carries_page_path_forward():
        member = _member("pl-nav")
        steps = _tour_payload(TOURS["member-welcome"], member, autostart=True)["steps"]
        catalog = next(s for s in steps if s["target"] == '[data-help-key="catalog.filter"]')
        assert catalog["navigate"] == reverse("classes:public_list")
        assert catalog["page_path"] == reverse("classes:public_list")
        card = next(s for s in steps if s["target"] == '[data-help-key="catalog.class-card"]')
        assert card["navigate"] is None  # inherits the catalog page
        assert card["page_path"] == reverse("classes:public_list")

    def it_folds_a_static_query_into_the_navigate_href():
        admin = _member("pl-q", fog_role=Member.FogRole.ADMIN)
        steps = _tour_payload(TOURS["admin"], admin, autostart=False)["steps"]
        refunds = next(s for s in steps if s["target"] == '[data-help-key="admin.refunds"]')
        assert refunds["navigate"] == reverse("billing_admin_dashboard") + "?tab=payments"
        recon = next(s for s in steps if s["target"] == '[data-help-key="admin.reconciliation"]')
        assert recon["navigate"] == reverse("billing_admin_dashboard") + "?tab=reconciliation"

    def it_resolves_a_callable_query_with_the_member():
        from classes.factories import ClassOfferingFactory

        member = _member("pl-cq")
        offering = ClassOfferingFactory(instructor=member)
        steps = _tour_payload(TOURS["instructor"], member, autostart=False)["steps"]
        compose = next(s for s in steps if s["target"] == '[data-help-key="announcements.compose"]')
        assert compose["navigate"] == reverse("hub_compose") + f"?audience=class%3A{offering.pk}&lock=1"

    def it_serializes_tab_set_as_a_list():
        lead = _lead("pl-tab")
        steps = _tour_payload(TOURS["guild-lead"], lead, autostart=False)["steps"]
        orient = next(s for s in steps if s["target"] == '[data-help-key="guild.run-orientations"]')
        assert orient["tab_set"] == ["section", "orientations"]
        assert orient["navigate"] is None

    def it_drops_steps_whose_resolver_raises_and_keeps_the_rest():
        # An instructor who owns no class: the roster, waitlist, and compose stops
        # (each with a class resolver) drop; create + submit survive.
        member = _member("pl-drop")
        targets = [s["target"] for s in _tour_payload(TOURS["instructor"], member, autostart=False)["steps"]]
        assert '[data-help-key="teach.roster-table"]' not in targets
        assert '[data-help-key="teach.roster-waitlist"]' not in targets
        assert '[data-help-key="announcements.compose"]' not in targets
        assert '[data-help-key="teach.submit-for-review"]' in targets

    def it_raises_on_a_cross_origin_navigate_at_build_time():
        member = _member("pl-x")
        bad = Tour(
            key="member-welcome",  # a registered key so state_url reverses
            title="Bad",
            entry_url_name="hub_home",
            audience=lambda m: True,
            steps=(TourStep(target=None, title="X", body="x", navigate="https://book.example.com/classes/"),),
        )
        with pytest.raises(ValueError, match="cross-origin"):
            _tour_payload(bad, member, autostart=False)
