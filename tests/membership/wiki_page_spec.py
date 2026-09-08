"""BDD specs for the member wiki store — WikiPage and its four companions.

Covers the rules the brief locked and a later reader is most likely to break: the
fill-once slug and sticker code, the freshness clock that deliberately ignores
``updated_at``, the verified-drop rule from both sides, and the flattened search text
that keeps ``icontains`` off HTML tag names.
"""

from __future__ import annotations

import pytest
from dateutil.relativedelta import relativedelta
from django.contrib.auth.models import AnonymousUser
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db.utils import IntegrityError
from django.test import RequestFactory
from django.utils import timezone

from classes.factories import UserFactory
from core.models import SiteActivity
from hub.view_as import ROLE_ADMIN, ROLE_MEMBER, ViewAs
from membership.models import (
    RESERVED_WIKI_SLUGS,
    WikiAttachment,
    WikiError,
    WikiPage,
    WikiRevision,
)
from tests.membership.factories import (
    EquipmentFactory,
    GuildFactory,
    GuildMembershipFactory,
    GuildStaffMembershipFactory,
    MemberFactory,
    WikiAttachmentFactory,
    WikiDraftFactory,
    WikiPageFactFactory,
    WikiPageFactory,
    WikiRevisionFactory,
)

pytestmark = pytest.mark.django_db


def _request(user, *, roles: set[str] | None = None):
    request = RequestFactory().get("/")
    request.user = user
    if roles is not None:
        request.view_as = ViewAs(actual=frozenset(roles), picked=None)
    return request


def describe_WikiPage():
    def describe_slug():
        def it_fills_from_the_title_once():
            page = WikiPageFactory(title="SawStop Table Saw")
            assert page.slug == "sawstop-table-saw"

        def it_never_changes_on_rename():
            page = WikiPageFactory(title="SawStop Table Saw")
            page.title = "The Big Saw"
            page.save()
            page.refresh_from_db()
            assert page.slug == "sawstop-table-saw"

        def it_dedupes_a_repeated_title():
            WikiPageFactory(title="Blade Guide")
            second = WikiPageFactory(title="Blade Guide", guild=GuildFactory())
            third = WikiPageFactory(title="Blade Guide", guild=GuildFactory())
            assert second.slug == "blade-guide-2"
            assert third.slug == "blade-guide-3"

        def it_treats_a_reserved_word_as_taken():
            page = WikiPageFactory(title="Search")
            assert page.slug == "search-2"
            assert page.slug not in RESERVED_WIKI_SLUGS

        def it_falls_back_when_the_title_slugifies_to_nothing():
            page = WikiPageFactory(title="!!!")
            assert page.slug == "page"

    def describe_qr_code():
        def it_fills_once_from_the_unambiguous_alphabet():
            page = WikiPageFactory()
            assert len(page.qr_code) == 6
            assert page.qr_code == page.qr_code.upper()
            assert not set(page.qr_code) & set("01OILU")

        def it_never_changes_on_a_later_save():
            page = WikiPageFactory()
            original = page.qr_code
            page.title = "Renamed"
            page.save()
            page.refresh_from_db()
            assert page.qr_code == original

        def it_is_unique_across_pages():
            codes = {WikiPageFactory().qr_code for _ in range(5)}
            assert len(codes) == 5

        def it_raises_when_every_attempt_collides(monkeypatch):
            # A code that can never be free proves the retry loop gives up loudly
            # instead of silently saving a duplicate the sticker route would misroute.
            monkeypatch.setattr("membership.models.secrets.choice", lambda alphabet: "A")
            WikiPageFactory(title="First")
            with pytest.raises(WikiError, match="sticker code"):
                WikiPageFactory(title="Second")

    def describe_freshness():
        def it_prefers_last_checked_at():
            checked = timezone.now() - relativedelta(days=3)
            page = WikiPageFactory(last_checked_at=checked, verified_at=timezone.now())
            assert page.freshness_at == checked

        def it_falls_back_to_verified_at():
            verified = timezone.now() - relativedelta(days=5)
            page = WikiPageFactory(verified_at=verified)
            assert page.freshness_at == verified

        def it_falls_back_to_created_at():
            page = WikiPageFactory()
            assert page.freshness_at == page.created_at

        def it_ignores_a_plain_updated_at_bump():
            # The locked rule: a typo fix does not make a stale machine page accurate.
            page = WikiPageFactory(kind=WikiPage.Kind.MACHINE)
            WikiPage.objects.filter(pk=page.pk).update(created_at=timezone.now() - relativedelta(months=13))
            page.refresh_from_db()
            assert page.is_out_of_date is True
            page.title = "Touched"
            page.save()
            page.refresh_from_db()
            assert page.is_out_of_date is True

        def it_is_not_out_of_date_one_day_before_the_interval():
            page = WikiPageFactory(kind=WikiPage.Kind.MACHINE)
            WikiPage.objects.filter(pk=page.pk).update(
                created_at=timezone.now() - relativedelta(months=12) + relativedelta(days=1)
            )
            page.refresh_from_db()
            assert page.is_out_of_date is False

        def it_is_out_of_date_one_day_after_the_interval():
            page = WikiPageFactory(kind=WikiPage.Kind.MACHINE)
            WikiPage.objects.filter(pk=page.pk).update(
                created_at=timezone.now() - relativedelta(months=12) - relativedelta(days=1)
            )
            page.refresh_from_db()
            assert page.is_out_of_date is True

        def it_never_goes_stale_for_a_project():
            page = WikiPageFactory(kind=WikiPage.Kind.PROJECT)
            WikiPage.objects.filter(pk=page.pk).update(created_at=timezone.now() - relativedelta(years=8))
            page.refresh_from_db()
            assert page.review_due_at is None
            assert page.is_out_of_date is False

        def it_uses_the_longer_interval_for_a_howto():
            page = WikiPageFactory(kind=WikiPage.Kind.HOWTO)
            WikiPage.objects.filter(pk=page.pk).update(created_at=timezone.now() - relativedelta(months=13))
            page.refresh_from_db()
            assert page.is_out_of_date is False

    def describe_verification_is_aged():
        def it_is_false_for_an_unverified_page():
            assert WikiPageFactory().verification_is_aged is False

        def it_is_false_at_eleven_months():
            page = WikiPageFactory(verified=True)
            page.verified_at = timezone.now() - relativedelta(months=11)
            assert page.verification_is_aged is False

        def it_is_true_at_thirteen_months():
            page = WikiPageFactory(verified=True)
            page.verified_at = timezone.now() - relativedelta(months=13)
            assert page.verification_is_aged is True

        def it_never_silently_unverifies():
            page = WikiPageFactory(verified=True)
            page.verified_at = timezone.now() - relativedelta(months=30)
            page.save()
            page.refresh_from_db()
            assert page.status == WikiPage.Status.GUILD_VERIFIED
            assert page.verified_by is not None

    def describe_confirm_still_accurate():
        def it_stamps_both_fields():
            page = WikiPageFactory()
            member = MemberFactory()
            page.confirm_still_accurate(member)
            page.refresh_from_db()
            assert page.last_checked_by == member
            assert page.last_checked_at is not None

        def it_changes_no_status_and_writes_no_revision():
            page = WikiPageFactory(verified=True)
            page.confirm_still_accurate(MemberFactory())
            page.refresh_from_db()
            assert page.status == WikiPage.Status.GUILD_VERIFIED
            assert page.revisions.count() == 0

    def describe_apply_edit():
        def it_drops_a_verified_page_to_community_for_a_plain_member():
            page = WikiPageFactory(verified=True)
            page.apply_edit(editor=MemberFactory(), editor_may_verify=False, title=page.title, body="<p>New.</p>")
            page.refresh_from_db()
            assert page.status == WikiPage.Status.COMMUNITY
            assert page.verified_by is None
            assert page.verified_at is None
            assert page.unverified_reason == "Edited since it was verified."

        def it_keeps_the_verification_and_resets_the_clock_for_staff():
            verifier = MemberFactory()
            page = WikiPageFactory(verified=True, verified_by=verifier)
            verified_at = page.verified_at
            editor = MemberFactory()
            page.apply_edit(editor=editor, editor_may_verify=True, title=page.title, body="<p>New.</p>")
            page.refresh_from_db()
            assert page.status == WikiPage.Status.GUILD_VERIFIED
            assert page.verified_at == verified_at
            assert page.last_checked_by == editor
            assert page.unverified_reason == ""

        def it_records_no_reason_on_a_community_page():
            page = WikiPageFactory()
            page.apply_edit(editor=MemberFactory(), editor_may_verify=False, title=page.title, body="<p>New.</p>")
            page.refresh_from_db()
            assert page.status == WikiPage.Status.COMMUNITY
            assert page.unverified_reason == ""

        def it_writes_exactly_one_revision_holding_the_pre_edit_state():
            page = WikiPageFactory(title="Before", body="<p>Old body.</p>", verified=True)
            WikiPageFactFactory(page=page, label="Blade", value="10 inch")
            revision = page.apply_edit(
                editor=MemberFactory(), editor_may_verify=False, title="After", body="<p>New body.</p>"
            )
            assert page.revisions.count() == 1
            assert revision.title == "Before"
            assert revision.body == "<p>Old body.</p>"
            assert revision.status == WikiPage.Status.GUILD_VERIFIED
            assert revision.facts == [{"label": "Blade", "value": "10 inch"}]

        def it_stamps_the_editor_and_the_body_clock():
            page = WikiPageFactory()
            editor = MemberFactory()
            page.apply_edit(editor=editor, editor_may_verify=False, title="T", body="<p>B</p>")
            page.refresh_from_db()
            assert page.updated_by == editor
            assert page.body_edited_at is not None

        def it_logs_an_activity_row():
            page = WikiPageFactory()
            page.apply_edit(editor=MemberFactory(), editor_may_verify=False, title="T", body="<p>B</p>")
            assert SiteActivity.objects.filter(kind=SiteActivity.Kind.WIKI_PAGE_EDITED).count() == 1

        def it_refuses_an_archived_page():
            page = WikiPageFactory(archived=True)
            with pytest.raises(WikiError, match="archived"):
                page.apply_edit(editor=MemberFactory(), editor_may_verify=False, title="T", body="<p>B</p>")

        def it_never_writes_is_published():
            # The seam with spec D: a member must not be able to publish a held-back
            # Safety page by opening Edit and saving.
            page = WikiPageFactory(is_published=False)
            page.apply_edit(editor=MemberFactory(), editor_may_verify=False, title="T", body="<p>B</p>")
            page.refresh_from_db()
            assert page.is_published is False

    def describe_search_text():
        def it_includes_fact_labels_and_values():
            page = WikiPageFactory(body="<p>Nothing useful.</p>")
            WikiPageFactFactory(page=page, label="Blade", value="10 inch carbide")
            page.rebuild_search_text()
            assert "Blade" in page.search_text
            assert "10 inch carbide" in page.search_text

        def it_includes_attachment_labels():
            page = WikiPageFactory()
            WikiAttachmentFactory(page=page, label="Blade change steps")
            page.rebuild_search_text()
            assert "Blade change steps" in page.search_text

        def it_excludes_html_tag_names():
            page = WikiPageFactory(body="<p>Use a <strong>sharp</strong> blade.</p>")
            page.refresh_from_db()
            assert "strong" not in page.search_text
            assert "sharp" in page.search_text

        def it_rebuilds_on_a_full_save_but_not_on_update_fields():
            page = WikiPageFactory(body="<p>Original.</p>")
            WikiPage.objects.filter(pk=page.pk).update(body="<p>Changed elsewhere.</p>")
            page.refresh_from_db()
            page.save(update_fields=["title"])
            page.refresh_from_db()
            assert "Changed elsewhere" not in page.search_text

    def describe_reading_helpers():
        def it_returns_the_lead_text_untrimmed_when_short():
            page = WikiPageFactory(body="<p>Short body.</p>")
            assert page.lead_text() == "Short body."

        def it_trims_a_long_lead_text_on_a_word_boundary():
            page = WikiPageFactory(body="<p>" + ("word " * 80) + "</p>")
            lead = page.lead_text(limit=50)
            assert lead.endswith("…")
            assert len(lead) <= 51

        def it_marks_the_first_hit_in_a_snippet():
            page = WikiPageFactory(title="Saw", body="<p>Change the blade before cutting.</p>")
            page.refresh_from_db()
            assert "<mark>blade</mark>" in page.search_snippet("blade")

        def it_falls_back_to_the_lead_when_only_the_title_hit():
            page = WikiPageFactory(title="Saw", body="<p>Nothing relevant here.</p>")
            page.refresh_from_db()
            assert page.search_snippet("zzzz") == "Nothing relevant here."

        def it_escapes_before_marking():
            page = WikiPageFactory(title="X", body="alert <script> blade")
            page.refresh_from_db()
            snippet = page.search_snippet("blade")
            assert "&lt;script&gt;" in snippet
            assert "<script>" not in snippet

        def it_brackets_a_mid_body_hit_with_ellipses():
            page = WikiPageFactory(title="X", body="a" * 300 + " blade " + "b" * 300)
            page.refresh_from_db()
            snippet = page.search_snippet("blade")
            assert snippet.startswith("…")
            assert snippet.endswith("…")

        def it_builds_a_toc_from_a_rich_editor_body():
            # The bug the render-time heading ids fix: a Quill body used to have no TOC.
            page = WikiPageFactory(body="<h2>Setup</h2><p>x</p><h3>Blade</h3><p>y</p>")
            assert page.toc() == [(2, "setup", "Setup"), (3, "blade", "Blade")]

        def it_returns_an_empty_toc_with_no_headings():
            assert WikiPageFactory(body="<p>Just prose.</p>").toc() == []

    def describe_dunder_str():
        def it_names_the_page_and_its_kind():
            page = WikiPageFactory(title="SawStop", kind=WikiPage.Kind.MACHINE)
            assert str(page) == "SawStop (Machine or tool)"

    def describe_revision_count():
        def it_counts_saved_versions():
            page = WikiPageFactory()
            WikiRevisionFactory.create_batch(3, page=page)
            assert page.revision_count == 3

    def describe_the_machine_page_constraint():
        def it_refuses_a_second_machine_page_for_one_tool():
            tool = EquipmentFactory()
            WikiPageFactory(kind=WikiPage.Kind.MACHINE, equipment=tool, title="First")
            with pytest.raises(IntegrityError):
                WikiPageFactory(kind=WikiPage.Kind.MACHINE, equipment=tool, title="Second")

        def it_allows_a_non_machine_page_for_the_same_tool():
            tool = EquipmentFactory()
            WikiPageFactory(kind=WikiPage.Kind.MACHINE, equipment=tool, title="Machine page")
            other = WikiPageFactory(kind=WikiPage.Kind.HOWTO, equipment=tool, title="How to page")
            assert other.pk is not None


def describe_WikiPageQuerySet():
    def describe_simple_filters():
        def it_separates_published_from_held_back():
            live = WikiPageFactory()
            WikiPageFactory(is_published=False)
            assert list(WikiPage.objects.published()) == [live]

        def it_excludes_archived_pages():
            live = WikiPageFactory()
            WikiPageFactory(archived=True)
            assert list(WikiPage.objects.not_archived()) == [live]

        def it_splits_guild_scope_from_space_wide():
            guild = GuildFactory()
            scoped = WikiPageFactory(guild=guild)
            space = WikiPageFactory()
            assert list(WikiPage.objects.for_guild(guild)) == [scoped]
            assert list(WikiPage.objects.space_wide()) == [space]

    def describe_filtered():
        def it_returns_everything_with_no_facets():
            WikiPageFactory.create_batch(3)
            assert WikiPage.objects.filtered().count() == 3

        def it_narrows_by_guild():
            guild = GuildFactory()
            wanted = WikiPageFactory(guild=guild)
            WikiPageFactory()
            assert list(WikiPage.objects.filtered(guild=guild)) == [wanted]

        def it_narrows_by_kind():
            wanted = WikiPageFactory(kind=WikiPage.Kind.MATERIAL)
            WikiPageFactory(kind=WikiPage.Kind.HOWTO)
            assert list(WikiPage.objects.filtered(kind=WikiPage.Kind.MATERIAL)) == [wanted]

        def it_narrows_to_overdue_pages_when_stale():
            fresh = WikiPageFactory(kind=WikiPage.Kind.MACHINE)
            stale = WikiPageFactory(kind=WikiPage.Kind.MACHINE, title="Old one")
            WikiPage.objects.filter(pk=stale.pk).update(created_at=timezone.now() - relativedelta(months=14))
            assert list(WikiPage.objects.filtered(stale=True)) == [stale]
            assert fresh not in WikiPage.objects.filtered(stale=True)

    def describe_search():
        def it_returns_nothing_for_an_empty_query():
            WikiPageFactory()
            assert list(WikiPage.objects.search("")) == []
            assert list(WikiPage.objects.search("   ")) == []

        def it_ands_multiple_terms():
            both = WikiPageFactory(title="Table saw blade guide")
            WikiPageFactory(title="Table saw fence")
            results = list(WikiPage.objects.search("saw blade"))
            assert results == [both]

        def it_matches_the_guild_name():
            guild = GuildFactory(name="Woodworking")
            page = WikiPageFactory(guild=guild, title="Finishing")
            assert list(WikiPage.objects.search("Woodworking")) == [page]

        def it_matches_the_equipment_name():
            tool = EquipmentFactory(name="Planer")
            page = WikiPageFactory(equipment=tool, title="Flattening")
            assert list(WikiPage.objects.search("Planer")) == [page]

        def it_leads_with_the_authoritative_answer():
            WikiPageFactory(title="Blade community")
            WikiPageFactory(title="Blade verified", verified=True)
            WikiPageFactory(title="Blade official", official=True)
            statuses = [p.status for p in WikiPage.objects.search("blade")]
            assert statuses == [
                WikiPage.Status.OFFICIAL,
                WikiPage.Status.GUILD_VERIFIED,
                WikiPage.Status.COMMUNITY,
            ]

    def describe_needs_review():
        def it_includes_a_page_past_its_interval():
            page = WikiPageFactory(kind=WikiPage.Kind.MACHINE)
            WikiPage.objects.filter(pk=page.pk).update(created_at=timezone.now() - relativedelta(months=13))
            assert list(WikiPage.objects.needs_review()) == [page]

        def it_excludes_a_fresh_page():
            WikiPageFactory(kind=WikiPage.Kind.MACHINE)
            assert list(WikiPage.objects.needs_review()) == []

        def it_never_includes_a_project():
            page = WikiPageFactory(kind=WikiPage.Kind.PROJECT)
            WikiPage.objects.filter(pk=page.pk).update(created_at=timezone.now() - relativedelta(years=9))
            assert list(WikiPage.objects.needs_review()) == []

        def it_includes_a_reported_page_regardless_of_age():
            page = WikiPageFactory(needs_review_since=timezone.now())
            assert list(WikiPage.objects.needs_review()) == [page]

        def it_measures_from_the_freshness_clock_not_creation():
            page = WikiPageFactory(kind=WikiPage.Kind.MACHINE)
            WikiPage.objects.filter(pk=page.pk).update(
                created_at=timezone.now() - relativedelta(months=20),
                last_checked_at=timezone.now(),
            )
            assert list(WikiPage.objects.needs_review()) == []

    def describe_visible_for():
        def it_shows_everything_to_effective_staff():
            WikiPageFactory(is_published=False)
            WikiPageFactory(archived=True)
            request = _request(UserFactory(), roles={ROLE_ADMIN, ROLE_MEMBER})
            assert WikiPage.objects.visible_for(request).count() == 2

        def it_hides_held_back_and_archived_pages_from_a_plain_member():
            live = WikiPageFactory()
            WikiPageFactory(is_published=False)
            WikiPageFactory(archived=True)
            user = UserFactory(username="plain@example.com")
            request = _request(user, roles={ROLE_MEMBER})
            assert list(WikiPage.objects.visible_for(request)) == [live]

        def it_includes_the_members_own_held_back_page():
            user = UserFactory(username="author@example.com")
            mine = WikiPageFactory(is_published=False, created_by=user.member, title="Mine")
            WikiPageFactory(is_published=False, title="Someone elses")
            request = _request(user, roles={ROLE_MEMBER})
            assert list(WikiPage.objects.visible_for(request)) == [mine]

        def it_includes_a_held_back_page_in_a_guild_the_member_leads():
            user = UserFactory(username="lead@example.com")
            guild = GuildFactory(guild_lead=user.member)
            held = WikiPageFactory(is_published=False, guild=guild, title="Held")
            request = _request(user, roles={ROLE_MEMBER})
            assert held in WikiPage.objects.visible_for(request)

        def it_includes_a_held_back_page_in_a_guild_the_member_staffs():
            user = UserFactory(username="staff@example.com")
            guild = GuildFactory()
            GuildStaffMembershipFactory(guild=guild, member=user.member)
            held = WikiPageFactory(is_published=False, guild=guild, title="Held")
            request = _request(user, roles={ROLE_MEMBER})
            assert held in WikiPage.objects.visible_for(request)

        def it_hides_a_held_back_page_in_a_guild_the_member_only_joined():
            # A held-back page is one the safety gate is holding for a second read.
            # Every joined member seeing it would be that gate not existing. The
            # membership row is the point of this spec: without it this proves only
            # that a stranger cannot see the page.
            user = UserFactory(username="joiner@example.com")
            guild = GuildFactory()
            GuildMembershipFactory(guild=guild, member=user.member)
            held = WikiPageFactory(is_published=False, guild=guild, title="Held")
            request = _request(user, roles={ROLE_MEMBER})
            assert guild.memberships.filter(member=user.member).exists()
            assert held not in WikiPage.objects.visible_for(request)

        def it_shows_only_live_pages_to_a_request_with_no_member():
            live = WikiPageFactory()
            WikiPageFactory(is_published=False)
            request = _request(AnonymousUser(), roles={ROLE_MEMBER})
            assert list(WikiPage.objects.visible_for(request)) == [live]

    def describe_with_fact_prefetch():
        def it_loads_a_list_without_per_row_queries(django_assert_num_queries):
            for index in range(3):
                page = WikiPageFactory(title=f"Page {index}")
                WikiPageFactFactory(page=page)
                WikiAttachmentFactory(page=page)
            with django_assert_num_queries(3):
                for page in WikiPage.objects.with_fact_prefetch():
                    list(page.facts.all())
                    list(page.attachments.all())


def describe_create_page():
    def it_writes_the_page_its_facts_and_a_first_revision():
        author = MemberFactory()
        page = WikiPage.objects.create_page(
            title="Bandsaw",
            kind=WikiPage.Kind.MACHINE,
            author=author,
            facts=[("Blade", "1/4 inch"), ("Max cut", "6 inch")],
            body="<h2>Setup</h2>",
        )
        assert page.slug == "bandsaw"
        assert [(f.label, f.value) for f in page.facts.all()] == [("Blade", "1/4 inch"), ("Max cut", "6 inch")]
        revision = page.revisions.get()
        assert revision.note == "Created"
        assert revision.facts == [
            {"label": "Blade", "value": "1/4 inch"},
            {"label": "Max cut", "value": "6 inch"},
        ]

    def it_makes_the_facts_searchable():
        page = WikiPage.objects.create_page(
            title="Bandsaw",
            kind=WikiPage.Kind.MACHINE,
            author=MemberFactory(),
            facts=[("Blade", "carbide")],
        )
        assert "carbide" in page.search_text

    def it_writes_a_revision_when_there_are_no_facts():
        page = WikiPage.objects.create_page(title="Plain", kind=WikiPage.Kind.HOWTO, author=MemberFactory())
        assert page.revisions.count() == 1
        assert page.facts.count() == 0

    def it_logs_an_activity_row():
        WikiPage.objects.create_page(title="Bandsaw", kind=WikiPage.Kind.MACHINE, author=MemberFactory())
        assert SiteActivity.objects.filter(kind=SiteActivity.Kind.WIKI_PAGE_CREATED).count() == 1

    def it_accepts_an_explicit_status():
        page = WikiPage.objects.create_page(
            title="Shop rules",
            kind=WikiPage.Kind.REFERENCE,
            author=MemberFactory(),
            status=WikiPage.Status.OFFICIAL,
        )
        assert page.status == WikiPage.Status.OFFICIAL

    def it_defaults_to_community():
        page = WikiPage.objects.create_page(title="Anything", kind=WikiPage.Kind.HOWTO, author=MemberFactory())
        assert page.status == WikiPage.Status.COMMUNITY

    def it_stamps_the_body_clock_for_a_member_authored_page():
        # Blank body_edited_at is the seeder's licence to overwrite the body, so a page a
        # member wrote must never look untouched.
        page = WikiPage.objects.create_page(title="Mine", kind=WikiPage.Kind.HOWTO, author=MemberFactory())
        assert page.body_edited_at is not None

    def it_leaves_the_body_clock_blank_for_a_seeded_page():
        page = WikiPage.objects.create_page(title="Seeded page", kind=WikiPage.Kind.MACHINE, author=None)
        assert page.body_edited_at is None

    def it_allows_a_seeder_page_with_no_author():
        page = WikiPage.objects.create_page(title="Seeded", kind=WikiPage.Kind.MACHINE, author=None)
        assert page.created_by is None
        assert page.revisions.get().author is None

    def it_refuses_a_duplicate_title_in_the_same_scope():
        guild = GuildFactory()
        WikiPage.objects.create_page(title="Finishing", kind=WikiPage.Kind.HOWTO, author=None, guild=guild)
        with pytest.raises(WikiError, match="already exists"):
            WikiPage.objects.create_page(title="finishing", kind=WikiPage.Kind.HOWTO, author=None, guild=guild)

    def it_names_the_scope_in_the_duplicate_message():
        WikiPage.objects.create_page(title="Finishing", kind=WikiPage.Kind.HOWTO, author=None)
        with pytest.raises(WikiError, match="space wide"):
            WikiPage.objects.create_page(title="Finishing", kind=WikiPage.Kind.HOWTO, author=None)

    def it_allows_the_same_title_in_a_different_scope():
        WikiPage.objects.create_page(title="Finishing", kind=WikiPage.Kind.HOWTO, author=None)
        page = WikiPage.objects.create_page(
            title="Finishing", kind=WikiPage.Kind.HOWTO, author=None, guild=GuildFactory()
        )
        assert page.pk is not None

    def it_ignores_an_archived_page_when_checking_for_a_duplicate():
        WikiPageFactory(title="Finishing", archived=True)
        page = WikiPage.objects.create_page(title="Finishing", kind=WikiPage.Kind.HOWTO, author=None)
        assert page.pk is not None

    def it_can_hold_a_page_back():
        page = WikiPage.objects.create_page(
            title="Safety", kind=WikiPage.Kind.REFERENCE, author=MemberFactory(), is_published=False
        )
        assert page.is_published is False


def describe_WikiPageFact():
    def it_reads_as_a_key_and_value():
        fact = WikiPageFactFactory(label="Blade", value="10 inch")
        assert str(fact) == "Blade: 10 inch"

    def it_orders_by_sort_order_then_pk():
        page = WikiPageFactory()
        second = WikiPageFactFactory(page=page, sort_order=2, label="B")
        first = WikiPageFactFactory(page=page, sort_order=1, label="A")
        assert list(page.facts.all()) == [first, second]


def describe_WikiRevision():
    def it_names_the_page_and_the_timestamp():
        revision = WikiRevisionFactory()
        assert revision.page.title in str(revision)

    def it_defaults_to_a_plain_save():
        assert WikiRevisionFactory().kind == WikiRevision.Kind.SAVE

    def it_carries_the_kinds_spec_d_writes():
        assert WikiRevision.Kind.REVERT == "revert"
        assert WikiRevision.Kind.CONFLICT_DRAFT == "conflict_draft"

    def it_reaches_its_author_through_the_reverse_accessor():
        # The single most load-bearing related_name in the round: spec D's "Kate
        # verified your page" resolver is one query only because this exists.
        author = MemberFactory()
        page = WikiPageFactory()
        WikiRevisionFactory(page=page, author=author)
        assert list(author.wiki_revisions.all()) == list(page.revisions.all())

    def it_orders_newest_first():
        page = WikiPageFactory()
        older = WikiRevisionFactory(page=page, note="older")
        newer = WikiRevisionFactory(page=page, note="newer")
        assert list(page.revisions.all()) == [newer, older]


def describe_WikiAttachment():
    def it_reports_a_link():
        attachment = WikiAttachmentFactory()
        assert attachment.is_link is True
        assert attachment.is_file is False
        assert attachment.is_image is False
        assert attachment.size_label == ""

    def it_reports_an_uploaded_file():
        attachment = WikiAttachmentFactory(as_file=True)
        assert attachment.is_file is True
        assert attachment.is_link is False

    def it_recognizes_a_photo():
        attachment = WikiAttachmentFactory(
            url="",
            file=SimpleUploadedFile("shot.JPG", b"\xff\xd8\xff", content_type="image/jpeg"),
            label="Photo",
        )
        assert attachment.is_image is True

    def it_does_not_call_an_extensionless_file_a_photo():
        attachment = WikiAttachmentFactory(
            url="", file=SimpleUploadedFile("noext", b"data", content_type="application/octet-stream"), label="X"
        )
        assert attachment.is_image is False

    def it_prefers_the_label_for_the_display_name():
        assert WikiAttachmentFactory(label="Blade steps").display_name == "Blade steps"

    def it_falls_back_to_the_file_name():
        attachment = WikiAttachmentFactory(as_file=True, label="")
        assert attachment.display_name.endswith(".pdf")

    def it_falls_back_to_the_url():
        attachment = WikiAttachmentFactory(label="")
        assert attachment.display_name == "https://example.com/spec-sheet"

    def describe_size_label():
        def it_reports_bytes():
            attachment = WikiAttachmentFactory(
                url="", file=SimpleUploadedFile("tiny.pdf", b"12345", content_type="application/pdf"), label="T"
            )
            assert attachment.size_label == "5 B"

        def it_reports_kilobytes():
            attachment = WikiAttachmentFactory(
                url="", file=SimpleUploadedFile("mid.pdf", b"x" * 4096, content_type="application/pdf"), label="M"
            )
            assert attachment.size_label == "4 KB"

        def it_reports_megabytes():
            attachment = WikiAttachmentFactory(
                url="",
                file=SimpleUploadedFile("big.pdf", b"x" * (2 * 1024 * 1024), content_type="application/pdf"),
                label="B",
            )
            assert attachment.size_label == "2.0 MB"

    def it_refuses_both_a_file_and_a_link():
        page = WikiPageFactory()
        with pytest.raises(IntegrityError):
            WikiAttachment.objects.create(
                page=page,
                label="Both",
                url="https://example.com/x",
                file=SimpleUploadedFile("x.pdf", b"data", content_type="application/pdf"),
            )

    def it_names_itself_by_label_and_page():
        attachment = WikiAttachmentFactory(label="Spec sheet")
        assert str(attachment) == f"Spec sheet on {attachment.page.title}"


def describe_WikiDraft():
    def it_names_the_page_it_belongs_to():
        draft = WikiDraftFactory()
        assert draft.page.title in str(draft)

    def it_names_a_not_yet_created_page_by_its_typed_title():
        draft = WikiDraftFactory(page=None, title="Half typed")
        assert "Half typed" in str(draft)

    def it_falls_back_when_a_new_draft_has_no_title_yet():
        draft = WikiDraftFactory(page=None, title="")
        assert "New page" in str(draft)

    def it_allows_one_draft_per_member_per_page():
        page = WikiPageFactory()
        author = MemberFactory()
        WikiDraftFactory(page=page, author=author)
        with pytest.raises(IntegrityError):
            WikiDraftFactory(page=page, author=author)

    def it_allows_one_new_page_draft_per_member_per_kind():
        author = MemberFactory()
        WikiDraftFactory(page=None, author=author, kind=WikiPage.Kind.HOWTO)
        with pytest.raises(IntegrityError):
            WikiDraftFactory(page=None, author=author, kind=WikiPage.Kind.HOWTO)

    def it_lists_a_members_drafts_newest_first():
        author = MemberFactory()
        older = WikiDraftFactory(author=author)
        newer = WikiDraftFactory(author=author)
        WikiDraftFactory()
        from membership.models import WikiDraft

        assert list(WikiDraft.objects.for_member(author)) == [newer, older]
