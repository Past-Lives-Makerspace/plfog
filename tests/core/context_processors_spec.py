"""BDD-style tests for core.context_processors."""

import pytest
from django.contrib.auth.models import AnonymousUser, User
from django.test import RequestFactory
from django.urls import resolve, reverse
from django.utils import timezone

from core.context_processors import (
    app_version,
    feature_flags,
    google_analytics,
    registration_mode,
    surface,
    theme,
    tour_runtime,
)
from core.models import SiteConfiguration, TourState
from membership.models import Member
from plfog.version import CHANGELOG, VERSION

pytestmark = pytest.mark.django_db


def describe_registration_mode():
    def it_returns_true_when_open():
        config = SiteConfiguration.load()
        config.registration_mode = SiteConfiguration.RegistrationMode.OPEN
        config.save()

        rf = RequestFactory()
        request = rf.get("/")
        result = registration_mode(request)
        assert result == {"registration_is_open": True}

    def it_returns_false_when_invite_only():
        config = SiteConfiguration.load()
        config.registration_mode = SiteConfiguration.RegistrationMode.INVITE_ONLY
        config.save()

        rf = RequestFactory()
        request = rf.get("/")
        result = registration_mode(request)
        assert result == {"registration_is_open": False}

    def it_defaults_to_invite_only():
        rf = RequestFactory()
        request = rf.get("/")
        result = registration_mode(request)
        assert result == {"registration_is_open": False}


def describe_app_version():
    def it_returns_version_string():
        rf = RequestFactory()
        request = rf.get("/")
        result = app_version(request)
        assert result["app_version"] == VERSION

    def it_returns_changelog_list():
        rf = RequestFactory()
        request = rf.get("/")
        result = app_version(request)
        assert isinstance(result["changelog"], list)
        assert len(result["changelog"]) >= 1
        assert result["changelog"][0]["version"] == CHANGELOG[0]["version"]


def describe_feature_flags():
    def it_defaults_both_switches_on_with_the_note():
        rf = RequestFactory()
        request = rf.get("/")
        result = feature_flags(request)
        assert result["my_tab_enabled"] is True
        assert result["class_registration_enabled"] is True
        assert result["guild_welcome_email_enabled"] is True
        assert (
            result["class_registration_disabled_note"]
            == SiteConfiguration._meta.get_field("class_registration_disabled_note").default
        )

    def it_reflects_toggled_values():
        config = SiteConfiguration.load()
        config.my_tab_enabled = False
        config.class_registration_enabled = False
        config.class_registration_disabled_note = "Call the studio."
        config.help_page_enabled = False
        config.wiki_link_enabled = False
        config.instructor_discount_codes_enabled = True
        config.guild_welcome_email_enabled = False
        config.save()

        rf = RequestFactory()
        request = rf.get("/")
        result = feature_flags(request)
        assert result == {
            "my_tab_enabled": False,
            "class_registration_enabled": False,
            "class_registration_disabled_note": "Call the studio.",
            "help_page_enabled": False,
            "wiki_link_enabled": False,
            "instructor_discount_codes_enabled": True,
            "guild_welcome_email_enabled": False,
        }


def describe_google_analytics():
    def it_returns_empty_when_id_not_configured():
        rf = RequestFactory()
        request = rf.get("/")
        result = google_analytics(request)
        assert result == {"google_analytics_measurement_id": ""}

    def it_returns_measurement_id_when_configured():
        config = SiteConfiguration.load()
        config.google_analytics_measurement_id = "G-TEST123"
        config.save()

        rf = RequestFactory()
        request = rf.get("/")
        result = google_analytics(request)
        assert result == {"google_analytics_measurement_id": "G-TEST123"}

    def it_returns_measurement_id_on_admin_paths_too():
        # FOG is measured end to end, staff back-office activity included. There is
        # deliberately no path exclusion here.
        config = SiteConfiguration.load()
        config.google_analytics_measurement_id = "G-TEST123"
        config.save()

        rf = RequestFactory()
        request = rf.get("/admin/")
        result = google_analytics(request)
        assert result == {"google_analytics_measurement_id": "G-TEST123"}


def describe_surface():
    def it_reports_public_when_request_surface_is_public(settings):
        rf = RequestFactory()
        request = rf.get("/")
        request.surface = "public"
        result = surface(request)
        assert result == {
            "surface": "public",
            "is_public_surface": True,
            "is_guilds_surface": False,
            "is_guest_surface": True,
            "MEMBER_HOST": settings.MEMBER_HOST,
            "MEMBER_BASE_URL": settings.MEMBER_BASE_URL,
            "BOOK_BASE_URL": settings.BOOK_BASE_URL,
            "GUILDS_BASE_URL": settings.GUILDS_BASE_URL,
            "SIGNAGE_BASE_URL": settings.SIGNAGE_BASE_URL,
            "is_signage_surface": False,
            "guilds_page_base": "hub/base.html",
            "signage_page_base": "hub/base.html",
            "parent_template": "classes/base_public.html",
        }

    def it_reports_guilds_when_request_surface_is_guilds(settings):
        rf = RequestFactory()
        request = rf.get("/")
        request.surface = "guilds"
        result = surface(request)
        assert result == {
            "surface": "guilds",
            "is_public_surface": False,
            "is_guilds_surface": True,
            "is_guest_surface": True,
            "MEMBER_HOST": settings.MEMBER_HOST,
            "MEMBER_BASE_URL": settings.MEMBER_BASE_URL,
            "BOOK_BASE_URL": settings.BOOK_BASE_URL,
            "GUILDS_BASE_URL": settings.GUILDS_BASE_URL,
            "SIGNAGE_BASE_URL": settings.SIGNAGE_BASE_URL,
            "is_signage_surface": False,
            "guilds_page_base": "guilds/base_public.html",
            "signage_page_base": "hub/base.html",
            "parent_template": "guilds/base_public.html",
        }

    def it_reports_members_when_request_surface_is_members(settings):
        rf = RequestFactory()
        request = rf.get("/")
        request.surface = "members"
        result = surface(request)
        assert result == {
            "surface": "members",
            "is_public_surface": False,
            "is_guilds_surface": False,
            "is_guest_surface": False,
            "MEMBER_HOST": settings.MEMBER_HOST,
            "MEMBER_BASE_URL": settings.MEMBER_BASE_URL,
            "BOOK_BASE_URL": settings.BOOK_BASE_URL,
            "GUILDS_BASE_URL": settings.GUILDS_BASE_URL,
            "SIGNAGE_BASE_URL": settings.SIGNAGE_BASE_URL,
            "is_signage_surface": False,
            "guilds_page_base": "hub/base.html",
            "signage_page_base": "hub/base.html",
            "parent_template": "base.html",
        }

    def it_reports_signage_when_request_surface_is_signage(settings):
        rf = RequestFactory()
        request = rf.get("/")
        request.surface = "signage"
        result = surface(request)
        assert result == {
            "surface": "signage",
            "is_public_surface": False,
            "is_guilds_surface": False,
            "is_guest_surface": False,
            "MEMBER_HOST": settings.MEMBER_HOST,
            "MEMBER_BASE_URL": settings.MEMBER_BASE_URL,
            "BOOK_BASE_URL": settings.BOOK_BASE_URL,
            "GUILDS_BASE_URL": settings.GUILDS_BASE_URL,
            "SIGNAGE_BASE_URL": settings.SIGNAGE_BASE_URL,
            "is_signage_surface": True,
            "guilds_page_base": "hub/base.html",
            "signage_page_base": "signage/base.html",
            "parent_template": "base.html",
        }

    def it_defaults_to_members_when_attribute_missing(settings):
        # If the middleware did not run (e.g. a unit test bypassing it), the
        # context processor should default to the safer chrome.
        rf = RequestFactory()
        request = rf.get("/")
        result = surface(request)
        assert result == {
            "surface": "members",
            "is_public_surface": False,
            "is_guilds_surface": False,
            "is_guest_surface": False,
            "MEMBER_HOST": settings.MEMBER_HOST,
            "MEMBER_BASE_URL": settings.MEMBER_BASE_URL,
            "BOOK_BASE_URL": settings.BOOK_BASE_URL,
            "GUILDS_BASE_URL": settings.GUILDS_BASE_URL,
            "SIGNAGE_BASE_URL": settings.SIGNAGE_BASE_URL,
            "is_signage_surface": False,
            "guilds_page_base": "hub/base.html",
            "signage_page_base": "hub/base.html",
            "parent_template": "base.html",
        }


def describe_theme():
    def it_exposes_empty_domain_by_default(settings):
        # Default (local dev): host-only cookie, no domain attribute.
        settings.THEME_COOKIE_DOMAIN = ""
        rf = RequestFactory()
        request = rf.get("/")
        assert theme(request) == {"theme_cookie_domain": ""}

    def it_exposes_the_configured_parent_domain(settings):
        # Production scopes the theme cookie to the .pastlives.app registrable
        # domain so the choice is shared across the hub and guilds surfaces.
        settings.THEME_COOKIE_DOMAIN = ".pastlives.app"
        rf = RequestFactory()
        request = rf.get("/")
        assert theme(request) == {"theme_cookie_domain": ".pastlives.app"}


def describe_notification_badge():
    def it_counts_unread_for_authenticated_user(client):
        from django.contrib.auth.models import User
        from django.test import RequestFactory

        from core.context_processors import notification_badge
        from core.models import Notification

        user = User.objects.create_user(username="b", email="b@example.com")
        Notification.objects.create(user=user, trigger="x", title="t", body="b")
        request = RequestFactory().get("/")
        request.user = user
        assert notification_badge(request)["unread_notification_count"] == 1

    def it_returns_zero_for_anonymous_user():
        from django.contrib.auth.models import AnonymousUser
        from django.test import RequestFactory

        from core.context_processors import notification_badge

        request = RequestFactory().get("/")
        request.user = AnonymousUser()
        assert notification_badge(request)["unread_notification_count"] == 0


def _tour_member(name, **fields):
    user = User.objects.create_user(username=name, email=f"{name}@example.com")
    member = Member.objects.get(user=user)  # auto-provisioned by ensure_user_has_member
    fields.setdefault("welcome_dismissed_at", timezone.now())
    for key, value in fields.items():
        setattr(member, key, value)
    member.save()
    return member


def _tour_request(path, user, *, method="GET", with_resolver=True):
    factory = RequestFactory()
    request = factory.get(path) if method == "GET" else factory.post(path)
    request.user = user
    if with_resolver:
        request.resolver_match = resolve(path.split("?")[0])
    return request


def describe_tour_runtime():
    def it_returns_empty_for_an_anonymous_visitor():
        request = _tour_request(reverse("hub_home"), AnonymousUser())
        ctx = tour_runtime(request)
        assert ctx["tour_json"] is None
        assert ctx["show_tour_offer"] is False

    def it_returns_empty_for_a_user_without_a_member():
        user = User.objects.create_user(username="tr-nomember", email="tr-nomember@example.com")
        Member.objects.filter(user=user).delete()
        assert tour_runtime(_tour_request(reverse("hub_home"), user))["tour_json"] is None

    def it_offers_on_an_entry_page_and_writes_the_offered_row():
        member = _tour_member("tr-entry")
        ctx = tour_runtime(_tour_request(reverse("hub_home"), member.user))
        assert ctx["show_tour_offer"] is True
        assert ctx["tour_json"]["key"] == "member-welcome"
        assert TourState.objects.status_for(member.user, "member-welcome") == TourState.Status.OFFERED

    def it_returns_empty_on_a_non_entry_page_without_a_tour_param():
        member = _tour_member("tr-nonentry")
        assert tour_runtime(_tour_request(reverse("hub_help"), member.user))["tour_json"] is None

    def it_autostarts_and_clamps_the_resume_step_on_any_page_without_writing_a_row():
        member = _tour_member("tr-resume")
        request = _tour_request(f"{reverse('hub_help')}?tour=member-welcome&step=99", member.user)
        ctx = tour_runtime(request)
        assert ctx["tour_autostart"] is True
        assert ctx["tour_json"]["resume_step"] == len(ctx["tour_json"]["steps"]) - 1
        assert TourState.objects.count() == 0

    def it_defaults_the_resume_step_to_zero_for_a_non_integer():
        member = _tour_member("tr-badstep")
        request = _tour_request(f"{reverse('hub_home')}?tour=member-welcome&step=abc", member.user)
        assert tour_runtime(request)["tour_json"]["resume_step"] == 0

    def it_ignores_a_foreign_tour_param_but_still_offers_the_entry_tour():
        member = _tour_member("tr-foreign")  # not a guild lead
        request = _tour_request(f"{reverse('hub_home')}?tour=guild-lead", member.user)
        ctx = tour_runtime(request)
        assert ctx["tour_autostart"] is False
        assert ctx["tour_json"]["key"] == "member-welcome"
        assert ctx["show_tour_offer"] is True

    def it_returns_empty_on_a_non_get_request():
        member = _tour_member("tr-post")
        request = _tour_request(reverse("hub_home"), member.user, method="POST")
        assert tour_runtime(request)["tour_json"] is None
        assert TourState.objects.count() == 0

    def it_handles_a_request_without_a_resolver_match():
        member = _tour_member("tr-noresolve")
        request = _tour_request("/anything/", member.user, with_resolver=False)
        assert tour_runtime(request)["tour_json"] is None
