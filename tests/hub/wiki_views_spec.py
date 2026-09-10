"""BDD specs for the member wiki's reading, search and browse surfaces (spec A, PR A2).

The feature flag's dark-when-off rule, the home page's three sections and its states, the
search screen (multi-term AND, the grouped sources, every filter, and the browse an empty
query has to produce), the reading page's composition and its gates, and the sidebar swap.
"""

from __future__ import annotations

import pytest
from dateutil.relativedelta import relativedelta
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from core.models import SiteConfiguration
from membership.models import Member, WikiPage
from tests.membership.factories import (
    EquipmentFactory,
    GuildFactory,
    GuildMembershipFactory,
    MemberFactory,
    MembershipPlanFactory,
    WikiArticleFactory,
    WikiAttachmentFactory,
    WikiPageFactFactory,
    WikiPageFactory,
    WikiRevisionFactory,
)

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _wiki_on(db):
    """Every spec in this file runs with the wiki turned on; the off case is explicit."""
    config = SiteConfiguration.load()
    config.wiki_enabled = True
    config.save()
    return config


def _member_user(username: str, *, fog_role: str = Member.FogRole.MEMBER, status: str = Member.Status.ACTIVE) -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, password="pass")
    member = user.member
    member.fog_role = fog_role
    member.status = status
    member.full_legal_name = username.title()
    member.save()
    member.sync_user_permissions()
    return user


def _login(client: Client, username: str, **kwargs: str) -> User:
    user = _member_user(username, **kwargs)
    client.login(username=username, password="pass")
    return user


def _preview_as(client: Client, role: str) -> None:
    """Put an admin into the view_as preview for ``role``, the shipped session idiom."""
    session = client.session
    session["view_as_role"] = role
    session.save()


# The home page's query budget. Fixed rather than proportional to the row count: that is
# the whole point of with_fact_prefetch(), and a regression would blow straight past it.
# 33 since spec D added the moderator-only Review queue link, whose "may I, and how many
# are waiting" comes off ONE scope lookup (_review_link) rather than two.
_HOME_QUERY_BUDGET = 33


def describe_the_feature_flag():
    def it_404s_every_route_while_the_wiki_is_off(client: Client, _wiki_on):
        _wiki_on.wiki_enabled = False
        _wiki_on.save()
        _login(client, "wiki_off")
        page = WikiPageFactory()
        for url in (
            reverse("hub_wiki_home"),
            reverse("hub_wiki_search"),
            reverse("hub_wiki_new"),
            reverse("hub_wiki_drafts"),
            reverse("hub_wiki_page", args=[page.slug]),
            reverse("hub_wiki_edit", args=[page.slug]),
        ):
            assert client.get(url).status_code == 404, url

    def it_404s_the_write_posts_too(client: Client, _wiki_on):
        _wiki_on.wiki_enabled = False
        _wiki_on.save()
        _login(client, "wiki_off_post")
        page = WikiPageFactory()
        assert client.post(reverse("hub_wiki_confirm", args=[page.slug])).status_code == 404
        assert client.post(reverse("hub_wiki_quick_tip", args=[page.slug]), {"tip": "x"}).status_code == 404

    def it_requires_login(client: Client):
        response = client.get(reverse("hub_wiki_home"))
        assert response.status_code == 302
        assert "/login" in response["Location"] or "/accounts/" in response["Location"]


def describe_the_sidebar():
    def it_links_the_in_app_wiki_when_the_flag_is_on(client: Client):
        _login(client, "wiki_nav")
        response = client.get(reverse("hub_wiki_home"))
        assert response.content.count(b'href="/wiki/" class="hub-sidebar__link') == 1

    def it_shows_no_wiki_link_while_the_flag_is_off(client: Client, _wiki_on, settings):
        settings.MAKERSPACE_WIKI_URL = "https://wiki.example.org/"
        _wiki_on.wiki_enabled = False
        _wiki_on.save()
        _login(client, "wiki_nav_off")
        response = client.get(reverse("hub_home"))
        assert b'href="https://wiki.example.org/"' not in response.content


def describe_wiki_home():
    def it_shows_the_empty_state_before_anybody_writes(client: Client):
        _login(client, "home_empty")
        response = client.get(reverse("hub_wiki_home"))
        assert response.status_code == 200
        assert b"Nothing here yet. Start with the machine you know best." in response.content

    def it_lists_recently_updated_pages(client: Client):
        _login(client, "home_recent")
        WikiPageFactory(title="Finishing Walnut")
        response = client.get(reverse("hub_wiki_home"))
        assert b"Recently Updated" in response.content
        assert b"Finishing Walnut" in response.content

    def it_lists_the_members_own_guild_pages(client: Client):
        user = _login(client, "home_guild")
        guild = GuildFactory(name="Woodworking")
        GuildMembershipFactory(guild=guild, member=user.member)
        WikiPageFactory(title="Guild Only Page", guild=guild)
        response = client.get(reverse("hub_wiki_home"))
        assert b'pl-wp-section__title">Your Guilds' in response.content
        assert b"Guild Only Page" in response.content

    def it_hides_the_your_guilds_section_for_a_member_in_no_guild(client: Client):
        _login(client, "home_noguild")
        WikiPageFactory(title="Anything")
        response = client.get(reverse("hub_wiki_home"))
        # The sidebar has its own guild list, so assert on the wiki section heading itself.
        assert b'pl-wp-section__title">Your Guilds' not in response.content

    def it_lists_machines_in_their_own_section(client: Client):
        _login(client, "home_machines")
        WikiPageFactory(title="Table Saw", kind=WikiPage.Kind.MACHINE)
        response = client.get(reverse("hub_wiki_home"))
        assert b"Machines" in response.content

    def it_shows_the_empty_filter_state(client: Client):
        _login(client, "home_filtered")
        WikiPageFactory(title="Some Page", kind=WikiPage.Kind.HOWTO)
        response = client.get(reverse("hub_wiki_home"), {"kind": "material"})
        assert b"Nothing matches those filters." in response.content

    def it_ignores_an_unknown_kind_rather_than_erroring(client: Client):
        _login(client, "home_badkind")
        WikiPageFactory(title="Some Page")
        response = client.get(reverse("hub_wiki_home"), {"kind": "bogus"})
        assert response.status_code == 200
        assert b"Some Page" in response.content

    def it_narrows_to_space_wide_pages(client: Client):
        _login(client, "home_spacewide")
        guild = GuildFactory(name="Print")
        WikiPageFactory(title="Guild Scoped", guild=guild)
        WikiPageFactory(title="Everyone Page", guild=None)
        response = client.get(reverse("hub_wiki_home"), {"guild": "space-wide"})
        assert b"Everyone Page" in response.content
        assert b"Guild Scoped" not in response.content

    def it_treats_an_unknown_guild_slug_as_matching_nothing(client: Client):
        _login(client, "home_badguild")
        WikiPageFactory(title="Some Page")
        response = client.get(reverse("hub_wiki_home"), {"guild": "no-such-guild"})
        assert response.status_code == 200
        assert b"Nothing matches those filters." in response.content

    def describe_the_old_wiki_card():
        def it_appears_while_the_link_toggle_is_on(client: Client, _wiki_on, settings):
            settings.MAKERSPACE_WIKI_URL = "https://wiki.example.org/"
            _wiki_on.wiki_link_enabled = True
            _wiki_on.save()
            _login(client, "home_oldwiki")
            response = client.get(reverse("hub_wiki_home"))
            assert b"The Old Wiki" in response.content

        def it_disappears_when_the_toggle_is_off(client: Client, _wiki_on, settings):
            settings.MAKERSPACE_WIKI_URL = "https://wiki.example.org/"
            _wiki_on.wiki_link_enabled = False
            _wiki_on.save()
            _login(client, "home_nooldwiki")
            response = client.get(reverse("hub_wiki_home"))
            assert b"The Old Wiki" not in response.content


def describe_wiki_search():
    def it_ands_the_terms(client: Client):
        _login(client, "search_and")
        WikiPageFactory(title="Table Saw Blades", body="Changing a blade on the table saw.")
        WikiPageFactory(title="Bandsaw", body="Nothing about tables here.")
        response = client.get(reverse("hub_wiki_search"), {"q": "table blade"})
        assert b"Table Saw Blades" in response.content
        assert b"Bandsaw" not in response.content

    def it_browses_everything_when_the_query_is_empty(client: Client):
        _login(client, "search_browse")
        WikiPageFactory(title="Browsable Page")
        response = client.get(reverse("hub_wiki_search"))
        assert b"Browsable Page" in response.content
        assert b"Everything in the wiki, newest first." in response.content

    def it_browses_a_guild_with_no_query(client: Client):
        _login(client, "search_seeall_guild")
        guild = GuildFactory(name="Woodworking", slug="woodworking")
        WikiPageFactory(title="In The Guild", guild=guild)
        WikiPageFactory(title="Somewhere Else")
        response = client.get(reverse("hub_wiki_search"), {"guild": "woodworking"})
        assert b"In The Guild" in response.content
        assert b"Somewhere Else" not in response.content
        assert b"1 page in Woodworking." in response.content

    def it_browses_a_kind_with_no_query(client: Client):
        _login(client, "search_seeall_kind")
        WikiPageFactory(title="A Machine", kind=WikiPage.Kind.MACHINE)
        WikiPageFactory(title="A How To", kind=WikiPage.Kind.HOWTO)
        response = client.get(reverse("hub_wiki_search"), {"kind": "machine"})
        assert b"A Machine" in response.content
        assert b"A How To" not in response.content

    def it_narrows_to_overdue_pages_with_stale(client: Client):
        _login(client, "search_stale")
        fresh = WikiPageFactory(title="Fresh Machine", kind=WikiPage.Kind.MACHINE)
        stale = WikiPageFactory(title="Stale Machine", kind=WikiPage.Kind.MACHINE)
        WikiPage.objects.filter(pk=stale.pk).update(created_at=timezone.now() - relativedelta(months=14))
        response = client.get(reverse("hub_wiki_search"), {"stale": "1"})
        assert b"Stale Machine" in response.content
        assert fresh.title.encode() not in response.content

    def it_drops_a_guild_filter_when_scope_is_all(client: Client):
        _login(client, "search_scopeall")
        guild = GuildFactory(name="Woodworking", slug="woodworking")
        WikiPageFactory(title="In The Guild", guild=guild)
        WikiPageFactory(title="Somewhere Else")
        response = client.get(reverse("hub_wiki_search"), {"guild": "woodworking", "scope": "all"})
        assert b"Somewhere Else" in response.content

    def it_treats_an_unknown_guild_slug_as_matching_nothing(client: Client):
        _login(client, "search_badguild")
        WikiPageFactory(title="Some Page")
        response = client.get(reverse("hub_wiki_search"), {"guild": "no-such-guild"})
        assert response.status_code == 200
        assert b"Nothing matches those filters." in response.content

    def it_shows_the_out_of_date_pill_in_a_result_row(client: Client):
        _login(client, "search_pill")
        stale = WikiPageFactory(title="Old Machine", kind=WikiPage.Kind.MACHINE)
        WikiPage.objects.filter(pk=stale.pk).update(created_at=timezone.now() - relativedelta(months=14))
        response = client.get(reverse("hub_wiki_search"), {"q": "Old Machine"})
        assert b"Out of date" in response.content

    def it_escapes_a_snippet_exactly_once(client: Client):
        _login(client, "search_escape")
        WikiPageFactory(title="Scripty", body="<p>danger &lt;script&gt;alert(1)&lt;/script&gt; here</p>")
        response = client.get(reverse("hub_wiki_search"), {"q": "danger"})
        assert b"<script>alert(1)" not in response.content
        assert b"<mark>danger</mark>" in response.content

    def describe_the_help_center_group():
        def it_folds_the_help_center_in(client: Client):
            _login(client, "search_help")
            WikiArticleFactory(title="Booking An Orientation", body="How to book an orientation.")
            WikiPageFactory(title="Orientation Bench", body="An orientation happens here.")
            response = client.get(reverse("hub_wiki_search"), {"q": "orientation"})
            assert b"Booking An Orientation" in response.content
            assert b"Orientation Bench" in response.content

        def it_shows_only_the_help_group_with_source_help(client: Client):
            _login(client, "search_sourcehelp")
            WikiArticleFactory(title="Booking An Orientation", body="How to book an orientation.")
            WikiPageFactory(title="Orientation Bench", body="An orientation happens here.")
            response = client.get(reverse("hub_wiki_search"), {"q": "orientation", "source": "help"})
            assert b"Booking An Orientation" in response.content
            assert b"Orientation Bench" not in response.content

        def it_is_skipped_when_the_help_center_is_off(client: Client, _wiki_on):
            _wiki_on.help_page_enabled = False
            _wiki_on.save()
            _login(client, "search_helpoff")
            WikiArticleFactory(title="Booking An Orientation", body="How to book an orientation.")
            response = client.get(reverse("hub_wiki_search"), {"q": "orientation"})
            assert b"Booking An Orientation" not in response.content

        def it_counts_only_the_wiki_with_source_wiki(client: Client):
            _login(client, "search_sourcewiki")
            WikiArticleFactory(title="Orientation Guide", body="How to book an orientation.")
            WikiPageFactory(title="Orientation Bench", body="An orientation happens here.")
            response = client.get(reverse("hub_wiki_search"), {"q": "orientation", "source": "wiki"})
            assert b"1 page match" in response.content
            assert b"Orientation Guide" not in response.content

        def it_answers_nothing_for_spec_es_group_until_it_ships(client: Client):
            _login(client, "search_sourcepolicies")
            WikiPageFactory(title="Orientation Bench", body="An orientation happens here.")
            response = client.get(reverse("hub_wiki_search"), {"q": "orientation", "source": "policies"})
            assert response.status_code == 200
            assert b"0 pages match" in response.content

        def it_ignores_an_unknown_source(client: Client):
            _login(client, "search_sourcebogus")
            WikiPageFactory(title="Orientation Bench", body="An orientation happens here.")
            response = client.get(reverse("hub_wiki_search"), {"q": "orientation", "source": "bogus"})
            assert b"Orientation Bench" in response.content

        def it_is_skipped_on_a_browse(client: Client):
            _login(client, "search_helpbrowse")
            WikiArticleFactory(title="Booking An Orientation", body="How to book an orientation.")
            response = client.get(reverse("hub_wiki_search"))
            assert b"Booking An Orientation" not in response.content

    def describe_the_source_chip_row():
        def it_is_absent_when_only_one_source_produced_a_group(client: Client):
            _login(client, "search_onechip")
            WikiPageFactory(title="Only Wiki", body="only here")
            response = client.get(reverse("hub_wiki_search"), {"q": "only"})
            assert b'aria-label="Filter by source"' not in response.content

        def it_appears_once_two_sources_answer(client: Client):
            _login(client, "search_twochips")
            WikiArticleFactory(title="Orientation Guide", body="orientation help")
            WikiPageFactory(title="Orientation Bench", body="orientation bench")
            response = client.get(reverse("hub_wiki_search"), {"q": "orientation"})
            assert b'aria-label="Filter by source"' in response.content

    def describe_a_zero_result_search():
        def it_renders_spec_bs_empty_partial(client: Client):
            _login(client, "search_zero")
            response = client.get(reverse("hub_wiki_search"), {"q": "nothing at all matches this"})
            # Asserted on the include, not on the placeholder copy, so spec B swapping the
            # file's contents in does not turn this red.
            assert "hub/partials/_wiki_search_empty.html" in [t.name for t in response.templates]

        def it_does_not_render_the_empty_partial_on_a_filtered_browse(client: Client):
            _login(client, "search_zerobrowse")
            response = client.get(reverse("hub_wiki_search"), {"kind": "material"})
            assert "hub/partials/_wiki_search_empty.html" not in [t.name for t in response.templates]
            assert b"Nothing matches those filters." in response.content

    def it_paginates_past_twenty_rows(client: Client):
        _login(client, "search_paged")
        for index in range(22):
            WikiPageFactory(title=f"Paged Page {index}")
        response = client.get(reverse("hub_wiki_search"))
        assert response.context["page"].paginator.num_pages == 2


def describe_wiki_page_reading():
    def it_renders_the_page(client: Client):
        _login(client, "read_basic")
        page = WikiPageFactory(title="Table Saw")
        response = client.get(page.get_absolute_url())
        assert response.status_code == 200
        assert b"Table Saw" in response.content

    def it_renders_the_wikis_own_404_for_a_bad_slug(client: Client):
        _login(client, "read_404")
        response = client.get(reverse("hub_wiki_page", args=["no-such-page"]))
        assert response.status_code == 404
        assert b"There is no page at that address." in response.content
        assert b"Browse Past Lives classes" not in response.content

    def it_shows_quick_answers_when_there_are_facts(client: Client):
        _login(client, "read_facts")
        page = WikiPageFactory()
        WikiPageFactFactory(page=page, label="Blade", value="10 inch")
        response = client.get(page.get_absolute_url())
        assert b"Blade" in response.content
        assert b"10 inch" in response.content

    def it_nudges_an_editor_when_there_are_no_facts(client: Client):
        _login(client, "read_nofacts")
        page = WikiPageFactory()
        response = client.get(page.get_absolute_url())
        assert b"No quick answers yet." in response.content

    def it_shows_a_reader_no_quick_answers_card_at_all(client: Client):
        # Quick Answers is opt-in on the editor, so a page with none is an ordinary good
        # page rather than an unfinished one. A whole card whose only content is a sentence
        # saying it is empty is noise to somebody who cannot do anything about it.
        _login(client, "read_nofacts_reader", status=Member.Status.FORMER)
        page = WikiPageFactory()
        response = client.get(page.get_absolute_url())
        assert response.status_code == 200
        assert b"No quick answers yet." not in response.content
        assert b"pl-wp-factsblock" not in response.content

    def it_still_shows_a_reader_the_facts_a_page_does_have(client: Client):
        _login(client, "read_facts_reader", status=Member.Status.FORMER)
        page = WikiPageFactory()
        WikiPageFactFactory(page=page, label="Blade", value="10 inch")
        response = client.get(page.get_absolute_url())
        assert b"pl-wp-factsblock" in response.content
        assert b"10 inch" in response.content

    def it_shows_a_reader_no_empty_files_and_photos_card_either(client: Client):
        # Same rule as Quick Answers next door: attachments are optional, so an empty card
        # is the normal state of a good page and the sentence saying so is noise to
        # somebody who cannot attach anything.
        _login(client, "read_noattach_reader", status=Member.Status.FORMER)
        page = WikiPageFactory()
        response = client.get(page.get_absolute_url())
        assert response.status_code == 200
        assert b"Nothing attached yet." not in response.content
        assert b'id="wiki-attachments"' not in response.content

    def it_keeps_the_card_and_its_swap_target_for_an_editor(client: Client):
        # #wiki-attachments is the quick-photo modal's hx-target. That modal is gated on
        # can_edit, so gating the card the same way keeps the target present exactly when
        # something can fire at it — but only if this holds.
        _login(client, "read_noattach_editor")
        page = WikiPageFactory()
        response = client.get(page.get_absolute_url())
        assert b'id="wiki-attachments"' in response.content
        assert b"Nothing attached yet." in response.content

    def it_still_shows_a_reader_the_attachments_a_page_does_have(client: Client):
        _login(client, "read_attach_reader", status=Member.Status.FORMER)
        page = WikiPageFactory()
        WikiAttachmentFactory(page=page, label="The Manual")
        response = client.get(page.get_absolute_url())
        assert b'id="wiki-attachments"' in response.content
        assert b"The Manual" in response.content

    def it_renders_the_official_block_from_equipment(client: Client):
        _login(client, "read_official")
        equipment = EquipmentFactory(name="SawStop")
        page = WikiPageFactory(kind=WikiPage.Kind.MACHINE, equipment=equipment)
        response = client.get(page.get_absolute_url())
        assert b"From the equipment register. Members cannot edit this." in response.content

    def it_omits_the_official_block_without_equipment(client: Client):
        _login(client, "read_noofficial")
        page = WikiPageFactory()
        response = client.get(page.get_absolute_url())
        assert b"From the equipment register" not in response.content

    def it_shows_the_toc_chip_row_with_two_headings(client: Client):
        _login(client, "read_toc")
        page = WikiPageFactory(body="<h2>First Part</h2><p>a</p><h2>Second Part</h2><p>b</p>")
        response = client.get(page.get_absolute_url())
        assert b'aria-label="On this page"' in response.content

    def it_hides_the_toc_with_one_heading(client: Client):
        _login(client, "read_notoc")
        page = WikiPageFactory(body="<h2>Only One</h2><p>a</p>")
        response = client.get(page.get_absolute_url())
        assert b'aria-label="On this page"' not in response.content

    def it_offers_to_start_an_empty_page(client: Client):
        _login(client, "read_emptybody")
        page = WikiPageFactory(body="")
        response = client.get(page.get_absolute_url())
        assert b"Nobody has written this one yet. You probably know something." in response.content

    def it_lists_attachments(client: Client):
        _login(client, "read_attach")
        page = WikiPageFactory()
        WikiAttachmentFactory(page=page, label="Blade Change Steps")
        response = client.get(page.get_absolute_url())
        assert b"Blade Change Steps" in response.content

    def it_shows_the_attachments_empty_state(client: Client):
        _login(client, "read_noattach")
        page = WikiPageFactory()
        response = client.get(page.get_absolute_url())
        assert b"Nothing attached yet." in response.content

    def it_carries_the_print_head_in_the_dom(client: Client):
        _login(client, "read_printhead")
        page = WikiPageFactory()
        response = client.get(page.get_absolute_url())
        assert b'class="pl-wp-printhead"' in response.content

    def it_names_who_started_and_edited_it(client: Client):
        _login(client, "read_byline")
        author = MemberFactory(full_legal_name="Dana Reyes")
        page = WikiPageFactory(created_by=author, updated_by=author)
        WikiRevisionFactory(page=page, author=author)
        response = client.get(page.get_absolute_url())
        assert b"Started by Dana Reyes" in response.content
        # Spec D turned the count into the link to the page history, which is the one
        # member-visible entry point to it.
        assert b"1 version saved</a>." in response.content

    def it_exposes_spec_ds_action_slot(client: Client):
        _login(client, "read_slot")
        page = WikiPageFactory()
        response = client.get(page.get_absolute_url())
        # The block itself renders nothing here; spec D fills it with Report.
        assert response.status_code == 200

    def describe_the_status_note():
        def it_explains_a_dropped_verification(client: Client):
            _login(client, "read_note_unverified")
            page = WikiPageFactory(unverified_reason="Edited since it was verified.")
            response = client.get(page.get_absolute_url())
            assert b"Edited since it was verified. Waiting for someone to check it again." in response.content

        def it_explains_an_open_report(client: Client):
            _login(client, "read_note_report")
            page = WikiPageFactory(needs_review_since=timezone.now())
            response = client.get(page.get_absolute_url())
            assert b"Someone reported a problem with this page." in response.content

        def it_says_nothing_on_a_plain_community_page(client: Client):
            _login(client, "read_note_none")
            page = WikiPageFactory()
            response = client.get(page.get_absolute_url())
            assert b"pl-wp-statusnote" not in response.content

    def describe_an_official_page():
        def it_shows_a_member_no_edit_affordance_at_all(client: Client):
            _login(client, "read_official_member")
            page = WikiPageFactory(official=True)
            response = client.get(page.get_absolute_url())
            assert b"+ Add A Photo" not in response.content
            assert b"+ Add A Tip" not in response.content
            assert reverse("hub_wiki_edit", args=[page.slug]).encode() not in response.content

        def it_still_offers_still_accurate(client: Client):
            _login(client, "read_official_confirm")
            page = WikiPageFactory(official=True)
            response = client.get(page.get_absolute_url())
            assert b"Still accurate" in response.content

        def it_gives_an_admin_the_edit_affordance(client: Client):
            _login(client, "read_official_admin", fog_role=Member.FogRole.ADMIN)
            page = WikiPageFactory(official=True)
            response = client.get(page.get_absolute_url())
            assert reverse("hub_wiki_edit", args=[page.slug]).encode() in response.content

    def describe_an_archived_page():
        def it_still_resolves_and_explains_itself(client: Client):
            _login(client, "read_archived")
            archiver = MemberFactory(full_legal_name="Kate Owens")
            page = WikiPageFactory(archived=True, archived_by=archiver)
            response = client.get(page.get_absolute_url())
            assert response.status_code == 200
            # Spec D replaced A's inline note with the tombstone, whose first line names the
            # PERSON: a removal that reads as weather is the failure the brief names.
            assert b"Removed by Kate Owens on" in response.content
            assert b"Replaced by the new guide." in response.content

        def it_hides_the_whole_action_row_including_still_accurate(client: Client):
            _login(client, "read_archived_actions")
            page = WikiPageFactory(archived=True)
            response = client.get(page.get_absolute_url())
            assert b"Still accurate" not in response.content
            assert b"pl-wp-actionbar" not in response.content

    def describe_a_page_the_safety_gate_is_holding():
        def it_404s_for_a_member_who_is_not_the_author(client: Client):
            _login(client, "read_held_other")
            page = WikiPageFactory(is_published=False)
            assert client.get(page.get_absolute_url()).status_code == 404

        def it_resolves_for_its_own_author(client: Client):
            user = _login(client, "read_held_author")
            page = WikiPageFactory(is_published=False, created_by=user.member)
            assert client.get(page.get_absolute_url()).status_code == 200

    def describe_the_confirm_cue():
        def it_renders_for_an_active_member(client: Client):
            _login(client, "read_cue")
            page = WikiPageFactory()
            response = client.get(page.get_absolute_url(), {"confirm": "1"})
            assert b"Does this page still match what is in the space today?" in response.content

        def it_does_not_render_on_an_archived_page(client: Client):
            _login(client, "read_cue_archived")
            page = WikiPageFactory(archived=True)
            response = client.get(page.get_absolute_url(), {"confirm": "1"})
            assert b"Does this page still match" not in response.content

        def it_ignores_an_unknown_parameter_value(client: Client):
            _login(client, "read_cue_bogus")
            page = WikiPageFactory()
            response = client.get(page.get_absolute_url(), {"confirm": "banana"})
            assert response.status_code == 200
            assert b"Does this page still match" not in response.content

    def describe_the_phone_action_bar():
        def it_pads_the_page_exactly_when_the_bar_is_rendered(client: Client):
            _login(client, "read_bar")
            page = WikiPageFactory()
            response = client.get(page.get_absolute_url())
            assert b"pl-wp-has-actionbar" in response.content
            assert b"pl-wp-actionbar" in response.content

        def it_offers_no_edit_affordance_on_an_official_page(client: Client):
            # Spec D put Report in the bar's third slot, and any active member gets it — so
            # the bar itself is present on an Official page while every EDIT affordance in
            # it is absent, which is the locked rule ("no edit affordance at all, not a
            # disabled one") rather than "no bar".
            _login(client, "read_bar_official")
            page = WikiPageFactory(official=True)
            response = client.get(page.get_absolute_url())
            assert b"Add Photo" not in response.content
            assert b"Add Tip" not in response.content
            assert reverse("hub_wiki_edit", args=[page.slug]).encode() not in response.content
            assert b"Report a Problem" in response.content

    def it_shows_checked_today_instead_of_the_button_after_a_confirm(client: Client):
        user = _login(client, "read_checked")
        page = WikiPageFactory()
        page.confirm_still_accurate(user.member)
        response = client.get(page.get_absolute_url())
        assert b"Checked today" in response.content


def describe_the_review_round_fixes():
    """One spec per defect an independent review of PR #340 found."""

    def describe_a_page_the_safety_gate_is_holding():
        def it_refuses_every_write_route_and_not_only_the_read(client: Client):
            # The gate lives in can_edit_wiki_page, so the five write routes and spec D's
            # future ones inherit it. Gating the index route and leaving the real URLs open
            # is brief section 3's named failure, and it had happened here.
            _login(client, "held_writes")
            page = WikiPageFactory(is_published=False)
            assert client.get(page.get_absolute_url()).status_code == 404
            assert client.get(reverse("hub_wiki_edit", args=[page.slug])).status_code == 403
            assert (
                client.post(
                    reverse("hub_wiki_autosave", args=[page.slug]), {"field": "title", "value": "x"}
                ).status_code
                == 403
            )
            assert client.post(reverse("hub_wiki_quick_tip", args=[page.slug]), {"tip": "x"}).status_code == 403
            assert client.post(reverse("hub_wiki_quick_photo", args=[page.slug]), {"caption": "x"}).status_code == 403
            assert client.post(reverse("hub_wiki_image_upload", args=[page.slug])).status_code == 403
            assert client.post(reverse("hub_wiki_confirm", args=[page.slug])).status_code == 404

        def it_still_lets_its_own_author_write(client: Client):
            user = _login(client, "held_author_writes")
            page = WikiPageFactory(is_published=False, created_by=user.member)
            assert client.get(reverse("hub_wiki_edit", args=[page.slug])).status_code == 200
            assert client.post(reverse("hub_wiki_confirm", args=[page.slug])).status_code == 200

        def it_still_lets_staff_write(client: Client):
            _login(client, "held_staff_writes", fog_role=Member.FogRole.ADMIN)
            page = WikiPageFactory(is_published=False)
            assert client.get(reverse("hub_wiki_edit", args=[page.slug])).status_code == 200

    def describe_the_home_pages_query_count():
        def it_does_not_grow_with_the_number_of_rows(client: Client, django_assert_num_queries):
            # Every card renders guild.name and the updated_by byline. Before with_fact_prefetch
            # this page cost two queries per row and grew with the wiki.
            user = _login(client, "home_queries")
            guild = GuildFactory(name="Woodworking")
            GuildMembershipFactory(guild=guild, member=user.member)
            WikiPageFactory(title="One", guild=guild, kind=WikiPage.Kind.MACHINE)
            baseline = len(client.get(reverse("hub_wiki_home")).context["recent_pages"])
            assert baseline == 1
            for index in range(12):
                WikiPageFactory(
                    title=f"Row {index}",
                    guild=GuildFactory(name=f"Guild {index}"),
                    created_by=MemberFactory(),
                    kind=WikiPage.Kind.MACHINE,
                )
            with django_assert_num_queries(_HOME_QUERY_BUDGET):
                response = client.get(reverse("hub_wiki_home"))
            assert response.status_code == 200

    def describe_the_search_count_line():
        def it_reports_the_real_help_total_past_the_page_cap(client: Client):
            _login(client, "count_helpcap")
            for index in range(22):
                WikiArticleFactory(title=f"Orientation Guide {index}", body="orientation help")
            response = client.get(reverse("hub_wiki_search"), {"q": "orientation", "source": "help"})
            assert b"22 pages match" in response.content

    def describe_view_as():
        def it_gives_an_admin_previewing_as_guest_no_write_affordance(client: Client):
            _login(client, "viewas_guest", fog_role=Member.FogRole.ADMIN)
            page = WikiPageFactory()
            _preview_as(client, "guest")
            response = client.get(page.get_absolute_url())
            assert b"Still accurate" not in response.content
            assert client.get(reverse("hub_wiki_drafts")).status_code == 403
            assert client.get(reverse("hub_wiki_create", args=["howto"])).status_code == 403

    def describe_the_reading_pages_byline():
        def it_counts_the_revisions_once_for_both_renders(client: Client):
            _login(client, "byline_once")
            page = WikiPageFactory()
            WikiRevisionFactory(page=page)
            response = client.get(page.get_absolute_url())
            # Rendered twice (wide line + phone disclosure), counted once.
            assert response.content.count(b"1 version saved</a>.") == 2
            assert response.context["revision_count"] == 1
