"""BDD specs for guild pages views."""

from __future__ import annotations

import re

import pytest
from django.contrib.auth.models import User
from django.test import Client

from tests.billing.factories import BillingSettingsFactory, ProductFactory, TabFactory
from tests.membership.factories import GuildFactory, MembershipPlanFactory


def _linked_user(client: Client, *, username: str = "u1", guild=None) -> tuple:
    """Create a user + auto-linked Member + Tab (with a saved card) + login."""
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, password="pass")
    member = user.member
    tab = TabFactory(member=member, stripe_payment_method_id="pm_test", stripe_customer_id="cus_test")
    client.login(username=username, password="pass")
    return user, tab


@pytest.mark.django_db
def describe_guild_detail():
    def it_is_accessible_to_anonymous_guests(client: Client):
        guild = GuildFactory()
        response = client.get(f"/guilds/{guild.slug}/")
        assert response.status_code == 200

    def it_shows_guild_name(client: Client):
        User.objects.create_user(username="viewer", password="pass")
        guild = GuildFactory(name="Woodworking Guild")
        client.login(username="viewer", password="pass")
        response = client.get(f"/guilds/{guild.slug}/")
        assert response.status_code == 200
        assert b"Woodworking Guild" in response.content

    def it_shows_about_text(client: Client):
        User.objects.create_user(username="v2", password="pass")
        guild = GuildFactory(about="We love wood.")
        client.login(username="v2", password="pass")
        response = client.get(f"/guilds/{guild.slug}/")
        assert b"We love wood." in response.content

    def it_shows_placeholder_when_about_is_blank(client: Client):
        User.objects.create_user(username="v3", password="pass")
        guild = GuildFactory(about="")
        client.login(username="v3", password="pass")
        response = client.get(f"/guilds/{guild.slug}/")
        assert b"Nothing here yet" in response.content

    def it_shows_no_products_placeholder_when_empty(client: Client):
        User.objects.create_user(username="v5", password="pass")
        guild = GuildFactory()
        client.login(username="v5", password="pass")
        response = client.get(f"/guilds/{guild.slug}/")
        assert b"No products listed yet" in response.content

    def describe_product_cards():
        def it_shows_add_to_cart_button_when_member_can_add(client: Client):
            BillingSettingsFactory()
            guild = GuildFactory()
            ProductFactory(guild=guild, name="Laser Time")
            _linked_user(client)
            response = client.get(f"/guilds/{guild.slug}/")
            assert b"Add to Cart" in response.content

        def it_hides_add_button_when_no_payment_method(client: Client):
            MembershipPlanFactory()
            guild = GuildFactory()
            ProductFactory(guild=guild)
            user = User.objects.create_user(username="nocard_grid", password="pass")
            TabFactory(member=user.member, stripe_payment_method_id="")
            client.login(username="nocard_grid", password="pass")
            response = client.get(f"/guilds/{guild.slug}/")
            assert b"Add to Cart" not in response.content
            assert b"saved payment method" in response.content

    def describe_member_none_branches():
        def it_renders_without_cart_when_user_has_no_member(client: Client):
            guild = GuildFactory()
            user = User.objects.create_user(username="nomember", password="pass")
            from membership.models import Member

            Member.objects.filter(user=user).delete()
            client.login(username="nomember", password="pass")

            response = client.get(f"/guilds/{guild.slug}/")

            assert response.status_code == 200
            assert response.context["tab"] is None

    def describe_get_involved_subscription_states():
        def it_shows_the_join_button_to_a_non_member(client: Client):
            guild = GuildFactory()
            _linked_user(client)
            response = client.get(f"/guilds/{guild.slug}/")
            assert b"Join This Guild" in response.content

        def it_drops_the_old_settings_pointer_copy_for_a_non_member(client: Client):
            # The hero Join now owns "how do I get updates," so the Get Involved panel no
            # longer nags a non-member with the "Want announcements ... Settings" paragraph.
            guild = GuildFactory()
            _linked_user(client)
            response = client.get(f"/guilds/{guild.slug}/")
            assert b"Want announcements from this guild?" not in response.content

        def it_shows_the_updates_line_and_manage_link_to_a_subscriber(client: Client):
            from membership.models import GuildMembership

            guild = GuildFactory()
            user, _ = _linked_user(client)
            GuildMembership.objects.create(guild=guild, member=user.member)
            response = client.get(f"/guilds/{guild.slug}/")
            assert b"You get this guild's updates" in response.content
            assert b"Manage in Settings" in response.content
            assert b"Want announcements from this guild?" not in response.content

        def it_shows_no_subscription_line_to_unlinked_accounts(client: Client):
            guild = GuildFactory()
            user = User.objects.create_user(username="unlinked_join", password="pass")
            from membership.models import Member

            Member.objects.filter(user=user).delete()
            client.login(username="unlinked_join", password="pass")
            response = client.get(f"/guilds/{guild.slug}/")
            # Assert absence by the button class, not the bare label (the changelog text
            # "Join This Guild" renders into every page's context).
            assert b"pl-guild-cta__join" not in response.content
            assert b"Want announcements from this guild?" not in response.content

    def describe_stat_chips():
        def it_hides_member_and_class_chips_when_zero(client: Client):
            guild = GuildFactory()
            _linked_user(client)
            response = client.get(f"/guilds/{guild.slug}/")
            assert b"0 member" not in response.content
            assert b"0 class" not in response.content

        def it_shows_the_member_chip_when_the_guild_has_members(client: Client):
            from membership.models import GuildMembership

            guild = GuildFactory()
            user, _ = _linked_user(client)
            GuildMembership.objects.create(guild=guild, member=user.member)
            response = client.get(f"/guilds/{guild.slug}/")
            assert b"1 member" in response.content

        def it_renders_the_member_count_as_a_plain_badge_not_a_directory_link(client: Client):
            from membership.models import GuildMembership

            guild = GuildFactory()
            user, _ = _linked_user(client)
            GuildMembership.objects.create(guild=guild, member=user.member)
            response = client.get(f"/guilds/{guild.slug}/")
            html = response.content.decode()
            assert '<span class="hub-badge">1 member</span>' in html
            # The count chip is plain text, not a link (the directory ?guild= link that now
            # appears elsewhere on the page is the join modal's "your profile" benefit row).
            assert "1 member</a>" not in html

    def describe_watch_section():
        _WATCH_URL = "https://www.youtube.com/watch?v=YE7VzlLtp-4"

        def _watch_iframe(client: Client) -> str:
            """Render a guild with a video and return its Watch ``<iframe>`` tag."""
            guild = GuildFactory(youtube_url=_WATCH_URL)
            _linked_user(client)
            body = client.get(f"/guilds/{guild.slug}/").content.decode()
            match = re.search(r"<iframe[^>]*youtube-nocookie\.com/embed/YE7VzlLtp-4[^>]*>", body)
            assert match, "the guild Watch iframe did not render"
            return match.group(0)

        def it_embeds_the_video_on_the_privacy_mode_host(client: Client):
            assert "youtube-nocookie.com/embed/YE7VzlLtp-4" in _watch_iframe(client)

        def it_sets_a_referrerpolicy_so_the_player_can_identify_the_site(client: Client):
            # Django's default Referrer-Policy is same-origin, so YouTube receives no
            # Referer for the embed and cannot tell who is framing it — the player then
            # refuses to play with "Video player configuration error / Error 153".
            # This attribute overrides the document policy for the iframe alone.
            assert 'referrerpolicy="strict-origin-when-cross-origin"' in _watch_iframe(client)

    def describe_faq_tab():
        def it_shows_a_faq_tab_when_the_guild_has_faqs(client: Client):
            from membership.models import GuildFAQItem

            guild = GuildFactory()
            _linked_user(client)
            GuildFAQItem.objects.create(guild=guild, question="Why?", answer="Because.", sort_order=0)
            response = client.get(f"/guilds/{guild.slug}/")
            assert b"section = 'faq'" in response.content
            assert b"Why?" in response.content

        def it_sets_a_referrerpolicy_on_a_faq_answer_video(client: Client):
            from membership.models import GuildFAQItem

            guild = GuildFactory()
            _linked_user(client)
            GuildFAQItem.objects.create(
                guild=guild,
                question="How do I start?",
                answer="Watch this.",
                video_url="https://www.youtube.com/watch?v=YE7VzlLtp-4",
                sort_order=0,
            )
            body = client.get(f"/guilds/{guild.slug}/").content.decode()
            match = re.search(r"<iframe[^>]*youtube-nocookie\.com/embed/YE7VzlLtp-4[^>]*>", body)
            assert match, "the FAQ answer video iframe did not render"
            assert 'referrerpolicy="strict-origin-when-cross-origin"' in match.group(0)

        def it_hides_the_faq_tab_when_the_guild_has_no_faqs(client: Client):
            guild = GuildFactory()
            _linked_user(client)
            response = client.get(f"/guilds/{guild.slug}/")
            assert b"section = 'faq'" not in response.content

        def it_renders_a_custom_faq_label_in_the_tab_and_heading(client: Client):
            from membership.models import GuildFAQItem

            guild = GuildFactory(faq_label="Ceramics Info")
            _linked_user(client)
            GuildFAQItem.objects.create(guild=guild, question="Why?", answer="Because.", sort_order=0)
            response = client.get(f"/guilds/{guild.slug}/")
            body = response.content.decode()
            # Assert the two specific render locations (FAQ tab button + section heading)
            # rather than a global substring count — the label can also legitimately appear
            # in unrelated page chrome (e.g. the changelog modal quoting it as an example).
            assert ">Ceramics Info</button>" in body
            assert '<h2 class="pl-guild-section__h2">Ceramics Info</h2>' in body

        def it_falls_back_to_FAQ_when_the_label_is_blank(client: Client):
            from membership.models import GuildFAQItem

            guild = GuildFactory(faq_label="")
            _linked_user(client)
            GuildFAQItem.objects.create(guild=guild, question="Why?", answer="Because.", sort_order=0)
            response = client.get(f"/guilds/{guild.slug}/")
            assert '<h2 class="pl-guild-section__h2">FAQ</h2>' in response.content.decode()

        def it_renders_a_markdown_link_in_the_answer_as_a_clickable_anchor(client: Client):
            from membership.models import GuildFAQItem

            guild = GuildFactory()
            _linked_user(client)
            GuildFAQItem.objects.create(
                guild=guild,
                question="Where are the docs?",
                answer="See the [handbook](https://example.com/handbook).",
                sort_order=0,
            )
            response = client.get(f"/guilds/{guild.slug}/")
            body = response.content.decode()
            assert 'href="https://example.com/handbook"' in body
            assert ">handbook</a>" in body
            # Wrapped in the shared Markdown container, like meeting notes.
            assert '<div class="pl-md">' in body

        def it_autolinks_a_pasted_url_in_the_answer(client: Client):
            from membership.models import GuildFAQItem

            guild = GuildFactory()
            _linked_user(client)
            GuildFAQItem.objects.create(
                guild=guild, question="Link?", answer="Visit https://example.com/guide today.", sort_order=0
            )
            response = client.get(f"/guilds/{guild.slug}/")
            assert 'href="https://example.com/guide"' in response.content.decode()

        def it_sanitizes_script_injection_in_the_answer(client: Client):
            from membership.models import GuildFAQItem

            guild = GuildFactory()
            _linked_user(client)
            GuildFAQItem.objects.create(
                guild=guild, question="Safe?", answer="Hi <script>alert('x')</script> there.", sort_order=0
            )
            response = client.get(f"/guilds/{guild.slug}/")
            body = response.content.decode()
            # The <script> tag is stripped by bleach; its payload survives only as inert text.
            assert "<script>alert" not in body
            assert "<p>Hi alert('x') there.</p>" in body
