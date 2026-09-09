"""BDD specs for the two wiki event types spec D owns.

``wiki.page_reported`` routes a member's complaint to the people who know the machine;
``wiki.page_verified`` tells the people who wrote a page that somebody with authority
stands behind it — the round's stated retention mechanism, and the reason spec B calls
into a registration D owns rather than shipping a fallback of its own.
"""

from __future__ import annotations

import pytest

from core.events import copy, resolvers, settings_matrix
from core.events.registry import Channel, Recipients, get_event
from core.events.rendering import render_copy, unknown_placeholders
from membership.models import GuildStaffMembership, Member, WikiRevision
from tests.membership.factories import (
    GuildFactory,
    GuildStaffMembershipFactory,
    MemberFactory,
    MembershipPlanFactory,
    WikiPageFactory,
    WikiRevisionFactory,
)

pytestmark = pytest.mark.django_db


def _linked_member(username: str, *, fog_role: str = Member.FogRole.MEMBER) -> Member:
    """A member with a linked User and a usable email, which every recipient needs."""
    from django.contrib.auth.models import User

    MembershipPlanFactory()
    user = User.objects.create_user(username=username, password="pass", email=f"{username}@example.com")
    member = user.member
    member.fog_role = fog_role
    member.full_legal_name = username.replace("_", " ").title()
    member.save()
    return member


def describe_wiki_scope_leadership():
    def it_resolves_a_guild_page_to_the_lead_and_every_staff_role(db):
        lead = _linked_member("scope_lead")
        orienter = _linked_member("scope_orienter")
        guild = GuildFactory(guild_lead=lead)
        GuildStaffMembershipFactory(guild=guild, member=orienter, role=GuildStaffMembership.Role.ORIENTER)
        recipients = resolvers.wiki_scope_leadership({"guild": guild})
        assert {user.pk for user, _reason in recipients} == {lead.user.pk, orienter.user.pk}

    def it_does_not_reach_an_admin_who_is_not_on_that_guild(db):
        lead = _linked_member("scope_lead2")
        admin = _linked_member("scope_admin_off", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory(guild_lead=lead)
        recipients = resolvers.wiki_scope_leadership({"guild": guild})
        assert admin.user.pk not in {user.pk for user, _reason in recipients}

    def describe_a_space_wide_page():
        def it_resolves_to_the_admins_and_to_no_guild_lead(db):
            admin = _linked_member("scope_admin", fog_role=Member.FogRole.ADMIN)
            lead = _linked_member("scope_lead3")
            GuildFactory(guild_lead=lead)
            recipients = resolvers.wiki_scope_leadership({"guild": None})
            assert {user.pk for user, _reason in recipients} == {admin.user.pk}

    def describe_a_guild_with_nobody_on_it():
        def it_falls_back_to_the_admins_rather_than_landing_nowhere(db):
            admin = _linked_member("scope_admin2", fog_role=Member.FogRole.ADMIN)
            guild = GuildFactory(guild_lead=None)
            recipients = resolvers.wiki_scope_leadership({"guild": guild})
            assert {user.pk for user, _reason in recipients} == {admin.user.pk}

    def it_fails_loudly_on_a_missing_guild_key(db):
        with pytest.raises(KeyError):
            resolvers.wiki_scope_leadership({})


def describe_wiki_page_contributors():
    def it_resolves_every_distinct_revision_author(db):
        page = WikiPageFactory()
        first = _linked_member("contrib_one")
        second = _linked_member("contrib_two")
        WikiRevisionFactory(page=page, author=first)
        WikiRevisionFactory(page=page, author=first)
        WikiRevisionFactory(page=page, author=second)
        verifier = _linked_member("contrib_verifier")
        recipients = resolvers.wiki_page_contributors({"page": page, "actor_member_pk": verifier.pk})
        assert {user.pk for user, _reason in recipients} == {first.user.pk, second.user.pk}

    def it_excludes_the_verifier(db):
        page = WikiPageFactory()
        verifier = _linked_member("contrib_self")
        WikiRevisionFactory(page=page, author=verifier)
        recipients = resolvers.wiki_page_contributors({"page": page, "actor_member_pk": verifier.pk})
        assert recipients == []

    def it_includes_the_author_of_an_unmerged_conflict_draft(db):
        # Somebody whose text lost a race still wrote for this page.
        page = WikiPageFactory()
        loser = _linked_member("contrib_loser")
        WikiRevisionFactory(page=page, author=loser, kind=WikiRevision.Kind.CONFLICT_DRAFT)
        verifier = _linked_member("contrib_verifier2")
        recipients = resolvers.wiki_page_contributors({"page": page, "actor_member_pk": verifier.pk})
        assert {user.pk for user, _reason in recipients} == {loser.user.pk}

    def it_survives_a_deleted_account(db):
        page = WikiPageFactory()
        WikiRevisionFactory(page=page, author=None)
        WikiRevisionFactory(page=page, author=MemberFactory())
        verifier = _linked_member("contrib_verifier3")
        assert resolvers.wiki_page_contributors({"page": page, "actor_member_pk": verifier.pk}) == []

    def it_fails_loudly_on_a_missing_page_key(db):
        with pytest.raises(KeyError):
            resolvers.wiki_page_contributors({"actor_member_pk": 1})


def describe_the_registry_wiring():
    @pytest.mark.parametrize("key", ["wiki.page_reported", "wiki.page_proposed", "wiki.page_verified"])
    def it_registers_the_key(key):
        # get_event raises KeyError on an unknown key, so spec B's Verify button 500s
        # until this registration exists.
        assert get_event(key).key == key

    @pytest.mark.parametrize("key", ["wiki.page_reported", "wiki.page_proposed", "wiki.page_verified"])
    def it_declares_no_discord_broadcast(key):
        # A report names a member's mistake; broadcasting it to a guild channel is a
        # punishment nobody asked for.
        assert Channel.DISCORD not in {spec.channel for spec in get_event(key).channels}

    @pytest.mark.parametrize("key", ["wiki.page_reported", "wiki.page_proposed", "wiki.page_verified"])
    def it_writes_no_activity_row_from_emit(key):
        # The model methods write the rows themselves, because emit logs actor and target
        # with NO payload and the payload is the useful half here.
        assert get_event(key).activity_kind is None

    def it_defaults_all_three_to_email_as_well_as_the_bell():
        for key in ("wiki.page_reported", "wiki.page_proposed", "wiki.page_verified"):
            channels = {spec.channel: spec.default for spec in get_event(key).channels}
            assert channels[Channel.EMAIL].value == "on"

    def it_routes_a_report_to_the_scope_and_a_verification_to_the_contributors():
        assert get_event("wiki.page_reported").recipient == Recipients.WIKI_SCOPE_LEADERSHIP
        assert get_event("wiki.page_proposed").recipient == Recipients.WIKI_SCOPE_LEADERSHIP
        assert get_event("wiki.page_verified").recipient == Recipients.WIKI_PAGE_CONTRIBUTORS

    def it_groups_the_report_under_staff_and_leaves_the_verification_member_facing():
        # WIKI_PAGE_CONTRIBUTORS in STAFF_RECIPIENTS would hide the verification row from
        # the very people it exists for.
        assert Recipients.WIKI_SCOPE_LEADERSHIP in settings_matrix.STAFF_RECIPIENTS
        assert Recipients.WIKI_PAGE_CONTRIBUTORS not in settings_matrix.STAFF_RECIPIENTS
        assert settings_matrix._section_for(get_event("wiki.page_reported")) == settings_matrix.STAFF_SECTION
        assert settings_matrix._section_for(get_event("wiki.page_proposed")) == settings_matrix.STAFF_SECTION
        assert settings_matrix._section_for(get_event("wiki.page_verified")) == "Wiki"

    def it_lists_wiki_in_both_category_orders():
        # Missing from triggers.CATEGORY_ORDER, by_category silently drops both rows.
        from core import triggers

        assert "Wiki" in triggers.CATEGORY_ORDER
        assert "Wiki" in settings_matrix.CATEGORY_ORDER


def describe_the_settings_matrix_rows():
    def it_shows_the_report_row_to_a_lead_and_hides_it_from_a_plain_member(db):
        lead = _linked_member("matrix_lead")
        GuildFactory(guild_lead=lead)
        plain = _linked_member("matrix_plain")
        lead_keys = {row.event_key for _section, rows in settings_matrix.build_matrix(lead.user) for row in rows}
        plain_keys = {row.event_key for _section, rows in settings_matrix.build_matrix(plain.user) for row in rows}
        assert "wiki.page_reported" in lead_keys
        assert "wiki.page_reported" not in plain_keys

    def it_shows_the_verified_row_to_everyone(db):
        plain = _linked_member("matrix_plain2")
        keys = {row.event_key for _section, rows in settings_matrix.build_matrix(plain.user) for row in rows}
        assert "wiki.page_verified" in keys


def describe_the_curated_copy():
    @pytest.mark.parametrize("key", ["wiki.page_reported", "wiki.page_proposed", "wiki.page_verified"])
    @pytest.mark.parametrize("channel", [Channel.IN_APP, Channel.EMAIL])
    def it_renders_with_no_missing_placeholder_marker(key, channel):
        entry = copy.event_copy(key)
        block = entry.copy_for(channel)
        rendered = render_copy(
            subject=block.subject,
            body_text=block.body_text,
            body_html=block.body_html,
            context=entry.sample_context,
        )
        assert "[missing:" not in rendered.subject
        assert "[missing:" not in rendered.body_text
        assert "[missing:" not in rendered.body_html

    @pytest.mark.parametrize("key", ["wiki.page_reported", "wiki.page_proposed", "wiki.page_verified"])
    @pytest.mark.parametrize("channel", [Channel.IN_APP, Channel.EMAIL])
    def it_uses_only_documented_placeholders(key, channel):
        entry = copy.event_copy(key)
        block = entry.copy_for(channel)
        for field in (block.subject, block.body_text, block.body_html):
            assert unknown_placeholders(field, entry.placeholders) == []

    def it_links_the_subject_noun_in_every_email():
        for key in ("wiki.page_reported", "wiki.page_proposed", "wiki.page_verified"):
            entry = copy.event_copy(key)
            html = entry.copy_for(Channel.EMAIL).body_html
            assert 'href="{{ page_url }}"' in html

    def it_keeps_the_text_and_html_bodies_carrying_the_same_links():
        entry = copy.event_copy("wiki.page_reported")
        text = entry.copy_for(Channel.EMAIL).body_text
        html = entry.copy_for(Channel.EMAIL).body_html
        for placeholder in ("{{ page_url }}", "{{ review_url }}"):
            assert placeholder in text
            assert placeholder in html

    def it_documents_only_the_placeholders_the_fixed_emit_shape_supplies():
        # An extra documented placeholder spec B does not pass renders "[missing: ...]"
        # straight into a member's inbox.
        assert set(copy.event_copy("wiki.page_verified").placeholders) == {
            "page_title",
            "page_url",
            "verifier_name",
            "verifier_role",
            "guild_name",
        }


def describe_the_verified_period():
    """THE one definition of the period, so spec B cannot drift from spec D's spec text."""

    def it_changes_when_the_page_is_re_verified(db):
        from django.utils import timezone

        page = WikiPageFactory(verified=True)
        first = page.verified_event_period()
        page.verified_at = timezone.now() + timezone.timedelta(days=90)
        # A page edited and re-verified months later must deliver again; a page-only
        # period would silence it forever after the first verification.
        assert page.verified_event_period() != first
        assert page.verified_event_period().startswith(f"wiki_verified:{page.pk}:")

    def it_is_stable_for_one_verification(db):
        page = WikiPageFactory(verified=True)
        assert page.verified_event_period() == page.verified_event_period()

    def it_never_crashes_on_an_unverified_page(db):
        assert WikiPageFactory().verified_event_period().endswith(":never")
