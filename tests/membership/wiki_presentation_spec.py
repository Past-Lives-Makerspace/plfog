"""BDD specs for the WikiPage presentation helpers spec A's reading surfaces render from.

The one status pill (and its precedence), the plain-language status note beside it, the
quiet attribute line, the locked Official block's context, the quick-tip body append, and
the related-pages neighbour list.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from dateutil.relativedelta import relativedelta
from django.urls import reverse
from django.utils import timezone

from membership.models import Equipment, Member, WikiError, WikiPage
from tests.membership.factories import (
    EquipmentFactory,
    GuildFactory,
    MemberFactory,
    OrientationTypeFactory,
    WikiPageFactory,
)

pytestmark = pytest.mark.django_db


def describe_WikiPage_get_absolute_url():
    def it_points_at_the_page_route(db):
        page = WikiPageFactory(title="Table Saw")
        assert page.get_absolute_url() == reverse("hub_wiki_page", args=[page.slug])


def describe_WikiPage_status_pill():
    def it_reads_community_by_default(db):
        modifier, label, tooltip = WikiPageFactory().status_pill
        assert (modifier, label) == ("neutral", "Community")
        assert "Helpful, not official" in tooltip

    def it_reads_official_for_an_official_page(db):
        modifier, label, _ = WikiPageFactory(official=True).status_pill
        assert (modifier, label) == ("primary", "Official")

    def it_reads_guild_verified_for_a_fresh_check(db):
        modifier, label, _ = WikiPageFactory(verified=True).status_pill
        assert (modifier, label) == ("ok", "Guild verified")

    def describe_when_the_check_is_over_a_year_old():
        def it_fades_to_grey_and_names_the_month(db):
            page = WikiPageFactory(verified=True)
            page.verified_at = timezone.now() - relativedelta(months=13)
            page.save()
            modifier, label, _ = page.status_pill
            assert modifier == "neutral"
            assert label.startswith("Verified ")
            # Never silently un-verified — only the chip's weight changes.
            assert page.status == WikiPage.Status.GUILD_VERIFIED

    def describe_when_the_review_clock_has_run_out():
        def it_outranks_the_status(db):
            page = WikiPageFactory(kind=WikiPage.Kind.MACHINE, official=True)
            WikiPage.objects.filter(pk=page.pk).update(created_at=timezone.now() - relativedelta(months=14))
            page.refresh_from_db()
            modifier, label, _ = page.status_pill
            assert (modifier, label) == ("warn", "Out of date")

    def describe_when_a_report_is_open():
        def it_outranks_everything(db):
            page = WikiPageFactory(verified=True, needs_review_since=timezone.now())
            modifier, label, _ = page.status_pill
            assert (modifier, label) == ("warn", "Needs review")


def describe_WikiPage_status_note():
    def it_is_blank_on_a_plain_community_page(db):
        assert WikiPageFactory().status_note == ""

    def it_explains_a_dropped_verification_first(db):
        page = WikiPageFactory(unverified_reason="Edited since it was verified.")
        assert page.status_note == "Edited since it was verified. Waiting for someone to check it again."

    def it_explains_an_open_report(db):
        page = WikiPageFactory(needs_review_since=timezone.now())
        assert page.status_note == "Someone reported a problem with this page."

    def it_names_the_month_a_stale_page_was_last_checked(db):
        page = WikiPageFactory(kind=WikiPage.Kind.MACHINE)
        checked = timezone.now() - relativedelta(months=14)
        WikiPage.objects.filter(pk=page.pk).update(last_checked_at=checked)
        page.refresh_from_db()
        assert page.status_note == f"Nobody has checked this since {timezone.localtime(checked):%B %Y}."


def describe_WikiPage_attribute_line():
    def it_names_the_kind_and_the_guild(db):
        guild = GuildFactory(name="Woodworking")
        page = WikiPageFactory(kind=WikiPage.Kind.MACHINE, guild=guild)
        assert page.attribute_line == "Machine or tool · Woodworking"

    def it_says_space_wide_when_there_is_no_guild(db):
        page = WikiPageFactory(kind=WikiPage.Kind.HOWTO, guild=None)
        assert page.attribute_line == "How to do something · Space wide"


def describe_WikiPage_official_block_context():
    def it_is_none_without_an_equipment_link(db):
        assert WikiPageFactory().official_block_context(MemberFactory()) is None

    def it_reads_the_register_and_never_the_page(db):
        guild = GuildFactory(name="Woodworking")
        equipment = EquipmentFactory(name="SawStop", guild=guild, location_note="Back wall")
        page = WikiPageFactory(kind=WikiPage.Kind.MACHINE, equipment=equipment, guild=guild)
        member = MemberFactory(status=Member.Status.ACTIVE)
        block = page.official_block_context(member)
        assert block["equipment"] == equipment
        assert block["guild"] == guild
        assert block["location_note"] == "Back wall"
        assert block["access_state"] == Equipment.AccessState.OK
        assert block["access_line"] == "You are set up for this tool."

    def it_says_orientation_is_needed_when_it_is(db):
        equipment = EquipmentFactory(required_orientation=OrientationTypeFactory())
        page = WikiPageFactory(kind=WikiPage.Kind.MACHINE, equipment=equipment)
        block = page.official_block_context(MemberFactory(status=Member.Status.ACTIVE))
        assert block["access_line"] == "Orientation needed before you use this."

    def it_answers_for_a_signed_out_reader_without_raising(db):
        page = WikiPageFactory(kind=WikiPage.Kind.MACHINE, equipment=EquipmentFactory())
        block = page.official_block_context(None)
        assert block["access_line"] == "Your membership needs to be active to use this."


def describe_WikiPage_add_tip():
    def it_creates_the_tips_heading_on_the_first_tip(db):
        page = WikiPageFactory(body="<p>How it works.</p>")
        member = MemberFactory()
        page.add_tip(member=member, editor_may_verify=False, tip_html="<p>Keep the guard on.</p>")
        page.refresh_from_db()
        assert "<h2>Tips From Members</h2>" in page.body
        assert page.body.endswith("<p>Keep the guard on.</p>")

    def it_reuses_the_heading_on_a_second_tip(db):
        page = WikiPageFactory(body="<p>How it works.</p>")
        member = MemberFactory()
        page.add_tip(member=member, editor_may_verify=False, tip_html="<p>One.</p>")
        page.refresh_from_db()
        page.add_tip(member=member, editor_may_verify=False, tip_html="<p>Two.</p>")
        page.refresh_from_db()
        assert page.body.count("<h2>Tips From Members</h2>") == 1

    def it_writes_a_revision_labelled_as_a_tip(db):
        page = WikiPageFactory()
        page.add_tip(member=MemberFactory(), editor_may_verify=False, tip_html="<p>Tip.</p>")
        assert page.revisions.first().note == "Tip added"

    def describe_on_a_guild_verified_page():
        def it_drops_the_check_for_a_member_who_cannot_verify(db):
            page = WikiPageFactory(verified=True)
            page.add_tip(member=MemberFactory(), editor_may_verify=False, tip_html="<p>Tip.</p>")
            page.refresh_from_db()
            assert page.status == WikiPage.Status.COMMUNITY
            assert page.unverified_reason == "Edited since it was verified."

        def it_keeps_the_check_for_someone_who_could_verify(db):
            page = WikiPageFactory(verified=True)
            page.add_tip(member=MemberFactory(), editor_may_verify=True, tip_html="<p>Tip.</p>")
            page.refresh_from_db()
            assert page.status == WikiPage.Status.GUILD_VERIFIED

    def describe_when_the_body_is_already_at_its_cap():
        def it_refuses_with_member_facing_copy(db):
            page = WikiPageFactory(body="x" * 60_000)
            with pytest.raises(WikiError, match="This page is full"):
                page.add_tip(member=MemberFactory(), editor_may_verify=False, tip_html="<p>Tip.</p>")


def describe_WikiPage_related_pages():
    def it_finds_pages_of_the_same_kind(db):
        page = WikiPageFactory(kind=WikiPage.Kind.HOWTO)
        neighbour = WikiPageFactory(kind=WikiPage.Kind.HOWTO)
        assert neighbour in page.related_pages()

    def it_finds_pages_in_the_same_guild(db):
        guild = GuildFactory()
        page = WikiPageFactory(kind=WikiPage.Kind.HOWTO, guild=guild)
        neighbour = WikiPageFactory(kind=WikiPage.Kind.MACHINE, guild=guild)
        assert neighbour in page.related_pages()

    def it_never_includes_itself(db):
        page = WikiPageFactory()
        assert page not in page.related_pages()

    def it_excludes_archived_and_held_back_pages(db):
        page = WikiPageFactory(kind=WikiPage.Kind.HOWTO)
        archived = WikiPageFactory(kind=WikiPage.Kind.HOWTO, archived=True)
        held = WikiPageFactory(kind=WikiPage.Kind.HOWTO, is_published=False)
        related = page.related_pages()
        assert archived not in related
        assert held not in related

    def it_caps_the_list(db):
        page = WikiPageFactory(kind=WikiPage.Kind.HOWTO)
        for _ in range(5):
            WikiPageFactory(kind=WikiPage.Kind.HOWTO)
        assert len(page.related_pages()) == 3

    def it_orders_the_most_recently_touched_first(db):
        page = WikiPageFactory(kind=WikiPage.Kind.HOWTO)
        older = WikiPageFactory(kind=WikiPage.Kind.HOWTO, title="Older")
        newer = WikiPageFactory(kind=WikiPage.Kind.HOWTO, title="Newer")
        WikiPage.objects.filter(pk=older.pk).update(updated_at=timezone.now() - timedelta(days=5))
        assert page.related_pages()[0] == newer
