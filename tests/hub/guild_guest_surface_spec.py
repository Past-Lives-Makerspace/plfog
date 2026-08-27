"""BDD specs for the guest guild page rendered on the guilds surface (guilds.pastlives.app).

Covers the reviewer MUST/SHOULD-FIXes: roster privacy (#1), absolute class links + no
Teach button (#3), no editor affordances for a lead (#4), lead-contact privacy (#7),
buyables fully suppressed (#9), guest chrome, the login-gated join/leave/orientation
affordances, and the in-place action redirects staying relative.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.test import Client, override_settings

from classes.factories import ClassOfferingFactory
from membership.models import GuildMembership
from tests.billing.factories import ProductFactory
from tests.membership.factories import (
    GuildFactory,
    GuildOrientationSettingsFactory,
    MemberFactory,
    MembershipPlanFactory,
    OrientationSlotFactory,
)

pytestmark = pytest.mark.django_db

GUILDS_HOST = "guilds.pastlives.app"
GUILDS_SETTINGS = dict(
    ALLOWED_HOSTS=["guilds.pastlives.app", "members.pastlives.space", "book.pastlives.space", "testserver"],
    GUILDS_HOSTS=["guilds.pastlives.app"],
    GUILDS_BASE_URL="https://guilds.pastlives.app",
    MEMBER_BASE_URL="https://members.pastlives.app",
    BOOK_BASE_URL="https://book.pastlives.space",
)


def _login_member(client: Client, *, username: str = "gm", name: str = "Viewer Member") -> tuple[User, object]:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, password="pw", email=f"{username}@example.com")
    member = user.member
    member.full_legal_name = name
    member.save()
    client.force_login(user)
    return user, member


def _guest_get(client: Client, guild):
    return client.get(f"/guilds/{guild.slug}/", HTTP_HOST=GUILDS_HOST)


def describe_guest_guild_page():
    @pytest.fixture(autouse=True)
    def _guilds_settings():
        with override_settings(**GUILDS_SETTINGS):
            yield

    def it_renders_200_for_anonymous_guests(client: Client):
        guild = GuildFactory(name="Woodworking")
        assert _guest_get(client, guild).status_code == 200

    def describe_roster_privacy():  # MUST-FIX #1
        def it_hides_member_names_from_anonymous_guests(client: Client):
            guild = GuildFactory(name="Roster Guild", show_members=True)
            member = MemberFactory(full_legal_name="Rosa Roster", show_in_directory=True)
            GuildMembership.objects.create(guild=guild, member=member)
            body = _guest_get(client, guild).content
            assert b"1 member" in body
            assert b"Rosa Roster" not in body

        def it_shows_member_names_to_a_logged_in_member(client: Client):
            guild = GuildFactory(name="Roster Guild", show_members=True)
            listed = MemberFactory(full_legal_name="Rosa Roster", show_in_directory=True)
            GuildMembership.objects.create(guild=guild, member=listed)
            _login_member(client)
            body = _guest_get(client, guild).content
            assert b"Rosa Roster" in body

    def describe_class_links_and_teach():  # MUST-FIX #3
        def it_makes_featured_class_links_absolute_to_the_book_host(client: Client):
            offering = ClassOfferingFactory(title="Intro to Turning")
            guild = GuildFactory(name="Turners", featured_class=offering)
            body = _guest_get(client, guild).content.decode()
            assert f"https://book.pastlives.space/classes/{offering.slug}/register/" in body

        def it_hides_the_teach_a_class_button_on_the_guilds_surface(client: Client):
            guild = GuildFactory(name="Turners")
            assert b"Teach a Class" not in _guest_get(client, guild).content

        def it_keeps_the_teach_a_class_button_on_the_members_surface(client: Client):
            guild = GuildFactory(name="Turners")
            _login_member(client)
            body = client.get(f"/guilds/{guild.slug}/").content  # default host -> members surface
            assert b"Teach a Class" in body

    def describe_editor_affordances():  # MUST-FIX #4
        def it_hides_editor_buttons_from_a_lead_on_the_guilds_surface(client: Client):
            # Assert on the editor endpoints (the changelog modal happens to quote the
            # phrase "Edit Guild Page", so match on the href to avoid a false positive).
            _, lead = _login_member(client, username="lead", name="Lead Person")
            guild = GuildFactory(name="Led Guild", guild_lead=lead)
            body = _guest_get(client, guild).content
            assert f"/guilds/{guild.pk}/edit/".encode() not in body  # Edit + Manage meeting notes hrefs
            assert b"openProductCreateModal" not in body  # Add Product control

        def it_shows_editor_buttons_to_the_same_lead_on_the_members_surface(client: Client):
            _, lead = _login_member(client, username="lead", name="Lead Person")
            guild = GuildFactory(name="Led Guild", guild_lead=lead)
            body = client.get(f"/guilds/{guild.slug}/").content  # members surface
            assert f"/guilds/{guild.pk}/edit/".encode() in body

    def describe_lead_contact_privacy():  # SHOULD-FIX #7
        def it_hides_lead_contact_details_from_anonymous_guests(client: Client):
            guild, lead = _guild_with_contactable_lead()
            body = _guest_get(client, guild).content
            assert lead.full_legal_name.encode() in body  # the name itself is public
            assert b"503-555-0101" not in body
            assert b"lena#4242" not in body
            assert b"I run the shop." not in body
            assert b"guild-lead-profile" not in body  # no clickable modal hook

        def it_shows_the_contact_modal_to_a_logged_in_member(client: Client):
            guild, _lead = _guild_with_contactable_lead()
            _login_member(client)
            body = _guest_get(client, guild).content
            assert b"guild-lead-profile" in body
            assert b"503-555-0101" in body

    def describe_buyables_suppressed():  # MUST-FIX #9
        def it_omits_all_cart_machinery_and_links_to_the_members_hub(client: Client):
            guild = GuildFactory(name="Shop Guild")
            ProductFactory(guild=guild, name="Kiln Time")
            body = _guest_get(client, guild).content
            assert b"guildCart(" not in body
            assert b"cart/confirm" not in body
            assert b"Add to Cart" not in body
            assert f"https://members.pastlives.app/guilds/{guild.slug}/".encode() in body

        def it_suppresses_cart_for_a_logged_in_member_too(client: Client):
            guild = GuildFactory(name="Shop Guild")
            ProductFactory(guild=guild, name="Kiln Time")
            _login_member(client)
            body = _guest_get(client, guild).content
            assert b"guildCart(" not in body
            assert b"cart/confirm" not in body

    def describe_member_count_chip():  # Minor
        def it_renders_the_count_chip_without_a_directory_link(client: Client):
            guild = GuildFactory(name="Chip Guild")
            member = MemberFactory()
            GuildMembership.objects.create(guild=guild, member=member)
            body = _guest_get(client, guild).content.decode()
            assert "1 member" in body
            assert "hub_member_directory" not in body
            assert "/members/?guild=" not in body

    def describe_chrome_and_join_affordances():
        def it_strips_the_member_sidebar_for_a_logged_in_member(client: Client):
            guild = GuildFactory(name="Chrome Guild")
            _login_member(client)
            body = _guest_get(client, guild).content
            assert b"hub-sidebar" not in body
            assert b"cp-topbar" in body

        def it_offers_a_members_hub_login_button_to_anonymous_guests(client: Client):
            guild = GuildFactory(name="Join Guild")
            body = _guest_get(client, guild).content.decode()
            assert "Log in to the members hub" in body
            assert "Join This Guild" not in body

        def it_points_a_logged_in_non_subscriber_at_settings_instead_of_a_join_form(client: Client):
            guild = GuildFactory(name="Join Guild")
            _login_member(client)
            body = _guest_get(client, guild).content.decode()
            assert "Want announcements from this guild?" in body
            assert "?tab=guilds" in body
            assert "Join This Guild" not in body
            assert "Log in to the members hub" not in body

        def it_shows_the_updates_line_and_no_leave_button_to_a_subscriber(client: Client):
            guild = GuildFactory(name="Leave Guild")
            _, member = _login_member(client)
            GuildMembership.objects.create(guild=guild, member=member)
            body = _guest_get(client, guild).content
            assert b"You get this guild's updates" in body
            assert b"Manage in Settings" in body
            assert b"Leave this guild" not in body

        def it_never_shows_leave_on_the_members_surface(client: Client):
            guild = GuildFactory(name="Leave Guild")
            _, member = _login_member(client)
            GuildMembership.objects.create(guild=guild, member=member)
            body = client.get(f"/guilds/{guild.slug}/").content  # members surface
            assert b"Leave this guild" not in body

    def describe_orientation_guest_prompt():
        def it_replaces_the_request_button_with_a_login_link_for_anon(client: Client):
            guild = GuildFactory(name="Orient Guild")
            GuildOrientationSettingsFactory(guild=guild, is_enabled=True)
            OrientationSlotFactory(guild=guild)
            body = _guest_get(client, guild).content.decode()
            assert "Log in to book" in body
            assert f"?next=/guilds/{guild.slug}/" in body

        def it_wraps_the_member_custom_time_field_in_a_themed_form_group(client: Client):
            # MUST-FIX #5: the datetime field renders inside .pl-form-group, which
            # components.css themes via color-scheme in both light and dark.
            guild = GuildFactory(name="Orient Guild")
            GuildOrientationSettingsFactory(guild=guild, is_enabled=True, allow_custom_requests=True)
            OrientationSlotFactory(guild=guild)
            _login_member(client)
            body = _guest_get(client, guild).content.decode()
            assert "pl-form-group" in body
            assert 'name="starts_at"' in body

    def describe_in_place_action_gating():
        # The join/leave endpoints are gone with the subscription reframe — the guest
        # surface has no in-place membership mutations left, so the old URLs 404.
        @pytest.mark.parametrize("action", ["join", "leave"])
        def it_404s_the_removed_membership_action_urls(client: Client, action):
            guild = GuildFactory(name="Act Guild")
            resp = client.post(f"/guilds/{guild.pk}/{action}/", HTTP_HOST=GUILDS_HOST)
            assert resp.status_code == 404

        def it_carries_next_back_to_the_guild_page_on_the_login_button(client: Client):
            guild = GuildFactory(name="Act Guild")
            body = _guest_get(client, guild).content.decode()
            assert f"?next=/guilds/{guild.slug}/" in body

    def describe_guild_visibility():
        # Every active guild is public and visible on every surface — the old
        # is_public gate is gone, so the only way to hide a guild is is_active off.
        def it_shows_an_active_guild_to_anonymous_guests(client: Client):
            guild = GuildFactory(name="Open Guild")
            assert _guest_get(client, guild).status_code == 200

        def it_shows_an_active_guild_to_a_logged_in_member_on_the_hub(client: Client):
            guild = GuildFactory(name="Open Guild")
            _login_member(client)
            resp = client.get(f"/guilds/{guild.slug}/")  # default host -> members surface
            assert resp.status_code == 200
            assert b"Open Guild" in resp.content


def _guild_with_contactable_lead(name="Contact Guild"):
    lead = MemberFactory(
        full_legal_name="Lena Lead",
        phone="503-555-0101",
        discord_handle="lena#4242",
        about_me="I run the shop.",
    )
    return GuildFactory(name=name, guild_lead=lead), lead
