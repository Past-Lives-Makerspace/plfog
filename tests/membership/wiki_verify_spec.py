"""Specs for verification: who may give a green check, and what giving one does (spec B).

The permission matrix here is against spec A's shipped ``can_verify_wiki_page``; spec B
consumes that helper and never redefines it. Everything else is ``WikiPage.verify`` /
``.unverify``, which are B's.
"""

from __future__ import annotations

from unittest import mock

import pytest
from django.contrib.auth.models import User
from django.test import RequestFactory
from django.utils import timezone

from django.conf import settings

from core.models import EventDelivery, SiteActivity
from membership.models import Member, WikiPage, WikiVerificationError, _wiki_role_label
from membership.permissions import can_verify_wiki_page
from tests.membership.factories import (
    GuildFactory,
    GuildStaffMembershipFactory,
    MemberFactory,
    MembershipPlanFactory,
    WikiPageFactory,
    WikiRevisionFactory,
)

pytestmark = pytest.mark.django_db


class _ViewAs:
    """The shape ``request.view_as`` carries, so the preview-aware helpers can read it."""

    def __init__(self, *, is_admin: bool = False, is_guild_officer: bool = False, is_member: bool = True) -> None:
        self.is_admin = is_admin
        self.is_guild_officer = is_guild_officer
        self.is_member = is_member


def _request_for(member: Member | None, *, view_as: _ViewAs | None = None) -> object:
    """A bare request carrying a linked member and a ``view_as`` role."""
    request = RequestFactory().get("/")
    if member is None:
        from django.contrib.auth.models import AnonymousUser

        request.user = AnonymousUser()
    else:
        request.user = member.user
    request.view_as = view_as if view_as is not None else _ViewAs()
    return request


def _member(username: str, *, fog_role: str = Member.FogRole.MEMBER) -> Member:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, password="pass")
    member = user.member
    member.fog_role = fog_role
    member.full_legal_name = username.title()
    member.save()
    return member


def describe_can_verify_wiki_page():
    def describe_a_guild_scoped_page():
        def it_admits_the_guilds_lead(db):
            lead = _member("verify_lead")
            guild = GuildFactory(guild_lead=lead)
            page = WikiPageFactory(guild=guild)
            assert can_verify_wiki_page(_request_for(lead), page) is True

        def it_admits_a_co_lead(db):
            staff = _member("verify_colead")
            guild = GuildFactory()
            GuildStaffMembershipFactory(guild=guild, member=staff, role="co_lead")
            page = WikiPageFactory(guild=guild)
            assert can_verify_wiki_page(_request_for(staff), page) is True

        def it_admits_an_orienter(db):
            """The brief's headline case: orienters teach the machine, so they may verify."""
            orienter = _member("verify_orienter")
            guild = GuildFactory()
            GuildStaffMembershipFactory(guild=guild, member=orienter, role="orienter")
            page = WikiPageFactory(guild=guild)
            assert can_verify_wiki_page(_request_for(orienter), page) is True

        def it_admits_a_custom_titled_staff_row(db):
            tech = _member("verify_tech")
            guild = GuildFactory()
            GuildStaffMembershipFactory(guild=guild, member=tech, role="", custom_title="Studio Technician")
            page = WikiPageFactory(guild=guild)
            assert can_verify_wiki_page(_request_for(tech), page) is True

        def it_refuses_a_lead_of_a_different_guild(db):
            other_lead = _member("verify_other_lead")
            GuildFactory(guild_lead=other_lead)
            page = WikiPageFactory(guild=GuildFactory())
            assert can_verify_wiki_page(_request_for(other_lead), page) is False

        def it_refuses_a_plain_member(db):
            plain = _member("verify_plain")
            page = WikiPageFactory(guild=GuildFactory())
            assert can_verify_wiki_page(_request_for(plain), page) is False

        def it_admits_a_fog_admin(db):
            admin = _member("verify_admin", fog_role=Member.FogRole.ADMIN)
            page = WikiPageFactory(guild=GuildFactory())
            assert can_verify_wiki_page(_request_for(admin, view_as=_ViewAs(is_admin=True)), page) is True

        def it_admits_a_guild_officer(db):
            officer = _member("verify_officer", fog_role=Member.FogRole.GUILD_OFFICER)
            page = WikiPageFactory(guild=GuildFactory())
            assert can_verify_wiki_page(_request_for(officer, view_as=_ViewAs(is_guild_officer=True)), page) is True

        def it_honors_the_view_as_preview(db):
            """An admin previewing as a member sees what a member sees, and can do what one can."""
            admin = _member("verify_preview", fog_role=Member.FogRole.ADMIN)
            page = WikiPageFactory(guild=GuildFactory())
            assert can_verify_wiki_page(_request_for(admin, view_as=_ViewAs()), page) is False

    def describe_a_space_wide_page():
        def it_refuses_a_guild_lead(db):
            lead = _member("verify_sw_lead")
            GuildFactory(guild_lead=lead)
            page = WikiPageFactory(guild=None)
            assert can_verify_wiki_page(_request_for(lead), page) is False

        def it_admits_a_fog_admin(db):
            admin = _member("verify_sw_admin", fog_role=Member.FogRole.ADMIN)
            page = WikiPageFactory(guild=None)
            assert can_verify_wiki_page(_request_for(admin, view_as=_ViewAs(is_admin=True)), page) is True

    def describe_an_official_page():
        def it_refuses_everyone_including_an_admin(db):
            """Official outranks Guild verified, so a Verify control there would be a trap."""
            admin = _member("verify_official_admin", fog_role=Member.FogRole.ADMIN)
            lead = _member("verify_official_lead")
            guild = GuildFactory(guild_lead=lead)
            page = WikiPageFactory(guild=guild, official=True)
            assert can_verify_wiki_page(_request_for(admin, view_as=_ViewAs(is_admin=True)), page) is False
            assert can_verify_wiki_page(_request_for(lead), page) is False


def describe_WikiPage_verify():
    @pytest.fixture
    def guild_and_lead(db):
        lead = _member("verify_model_lead")
        guild = GuildFactory(name="Woodworking", guild_lead=lead)
        return guild, lead

    def it_sets_every_verification_field(db, guild_and_lead, stub_page_verified_event):
        guild, lead = guild_and_lead
        page = WikiPageFactory(guild=guild)
        page.verify(lead, note="Checked the blade guard.")
        page.refresh_from_db()
        assert page.status == WikiPage.Status.GUILD_VERIFIED
        assert page.verified_by_id == lead.pk
        assert page.verified_at is not None
        assert page.verified_note == "Checked the blade guard."
        assert page.verified_role_label == "Woodworking lead"

    def it_also_stamps_the_freshness_clock_and_the_name_on_it(db, guild_and_lead, stub_page_verified_event):
        """Writing only the timestamp would leave A's byline naming the PREVIOUS checker."""
        guild, lead = guild_and_lead
        other = MemberFactory()
        page = WikiPageFactory(guild=guild, last_checked_by=other, last_checked_at=timezone.now())
        page.verify(lead)
        page.refresh_from_db()
        assert page.last_checked_by_id == lead.pk
        assert page.last_checked_at == page.verified_at

    def it_clears_the_unverified_reason(db, guild_and_lead, stub_page_verified_event):
        """Otherwise a green check renders directly above a line contradicting it."""
        guild, lead = guild_and_lead
        page = WikiPageFactory(guild=guild, unverified_reason="Edited since it was verified.")
        page.verify(lead)
        page.refresh_from_db()
        assert page.unverified_reason == ""

    def it_clears_the_needs_review_pair_so_the_pill_actually_changes(db, guild_and_lead, stub_page_verified_event):
        """A's pill precedence puts Needs review ABOVE Guild verified, so without this the
        button appears to do nothing: the page keeps its amber pill."""
        guild, lead = guild_and_lead
        page = WikiPageFactory(
            guild=guild,
            needs_review_since=timezone.now(),
            needs_review_reason="The fence measurement is wrong.",
        )
        page.verify(lead)
        page.refresh_from_db()
        assert page.needs_review_since is None
        assert page.needs_review_reason == ""
        modifier, label, _tooltip = page.status_pill
        assert (modifier, label) == ("ok", "Guild verified")

    def it_writes_no_revision(db, guild_and_lead, stub_page_verified_event):
        """Spec A's handoff says to write one. Do not. Spec D's contributor resolver reads
        wiki_revisions, so a verification revision would enrol every verifier as a
        permanent contributor of every page they verify."""
        guild, lead = guild_and_lead
        page = WikiPageFactory(guild=guild)
        WikiRevisionFactory(page=page)
        before = page.revisions.count()
        page.verify(lead)
        assert page.revisions.count() == before

    def it_logs_the_activity_row(db, guild_and_lead, stub_page_verified_event):
        guild, lead = guild_and_lead
        page = WikiPageFactory(guild=guild)
        page.verify(lead)
        row = SiteActivity.objects.filter(kind=SiteActivity.Kind.WIKI_PAGE_VERIFIED).get()
        assert row.payload["guild"] == guild.pk
        assert row.payload["note"] is False

    def it_truncates_a_role_label_that_would_overflow_its_column(db, stub_page_verified_event):
        """verified_role_label is 80 chars; Guild.name is 255 and a custom staff title is
        60, so the two together cross it. Postgres raises DataError and the Verify tap
        500s with the page left unverified; SQLite accepts the oversize string silently,
        which is why CI cannot see this. Run this spec on Postgres."""
        tech = _member("label_overflow")
        guild = GuildFactory(name="Fiber Arts and Textiles and Bookbinding and Papermaking Guild")
        GuildStaffMembershipFactory(
            guild=guild, member=tech, role="", custom_title="Assistant Studio Technician and Safety Coordinator"
        )
        page = WikiPageFactory(guild=guild)
        page.verify(tech)
        page.refresh_from_db()
        assert len(page.verified_role_label) == 80

    def it_truncates_a_long_note(db, guild_and_lead, stub_page_verified_event):
        guild, lead = guild_and_lead
        page = WikiPageFactory(guild=guild)
        page.verify(lead, note="x" * 400)
        page.refresh_from_db()
        assert len(page.verified_note) == 280

    def it_emits_the_page_verified_event_in_spec_Ds_shape(db, guild_and_lead):
        """B calls this event and registers nothing: brief §9.1 gives it to spec D, whose
        PR merges first. The call shape is what B owns, so the call shape is what is
        asserted here."""
        guild, lead = guild_and_lead
        page = WikiPageFactory(guild=guild)
        with mock.patch("core.events.emit.emit") as emit:
            page.verify(lead)
        assert emit.call_count == 1
        args, kwargs = emit.call_args
        assert args[0] == "wiki.page_verified"
        assert kwargs["context"]["verifier_role"] == "Woodworking lead"
        assert kwargs["context"]["actor_member_pk"] == lead.pk
        assert kwargs["context"]["guild_name"] == "Woodworking"
        assert kwargs["period"] == f"wiki_verified:{page.pk}:{page.verified_at:%Y%m%d}"
        # Spec D's copy renders page_url as the ONLY link, in the text body and as the CTA
        # button's href. A bare path dead ends in a mail client, and this notification is
        # the round's whole retention mechanism.
        assert kwargs["context"]["page_url"].startswith(settings.MEMBER_BASE_URL)
        assert kwargs["context"]["page_url"].endswith(page.get_absolute_url())
        # Every placeholder spec D documents for this event has to be present, so B's dict
        # and D's copy cannot drift into a "[missing: ...]" marker in a live email.
        assert set(kwargs["context"]) >= {
            "page",
            "actor_member_pk",
            "member_name",
            "page_title",
            "page_url",
            "verifier_name",
            "verifier_role",
            "guild_name",
        }

    def it_buckets_the_dedupe_period_by_the_day(db, guild_and_lead, stub_page_verified_event):
        """Bucketing by the second mailed the contributors once per staff edit forever."""
        guild, lead = guild_and_lead
        page = WikiPageFactory(guild=guild)
        page.verify(lead)
        first = EventDelivery.objects.filter(event_key="wiki.page_verified").count()
        WikiPage.objects.filter(pk=page.pk).update(verified_at=page.verified_at + timezone.timedelta(minutes=5))
        page.refresh_from_db()
        page.verify(lead)
        assert EventDelivery.objects.filter(event_key="wiki.page_verified").count() == first

    def it_names_the_makerspace_for_a_space_wide_page(db, guild_and_lead):
        _guild, lead = guild_and_lead
        page = WikiPageFactory(guild=None)
        with mock.patch("core.events.emit.emit") as emit:
            page.verify(lead)
        assert emit.call_args.kwargs["context"]["guild_name"] == "the makerspace"

    def describe_a_second_tap_within_a_minute():
        def it_is_a_quiet_no_op(db, guild_and_lead, stub_page_verified_event):
            guild, lead = guild_and_lead
            page = WikiPageFactory(guild=guild)
            page.verify(lead, note="First note.")
            first_at = page.verified_at
            page.verify(lead, note="Second note.")
            page.refresh_from_db()
            assert page.verified_at == first_at
            assert page.verified_note == "First note."
            assert SiteActivity.objects.filter(kind=SiteActivity.Kind.WIKI_PAGE_VERIFIED).count() == 1

        def it_does_re_verify_once_the_window_has_passed(db, guild_and_lead, stub_page_verified_event):
            guild, lead = guild_and_lead
            page = WikiPageFactory(guild=guild)
            page.verify(lead)
            WikiPage.objects.filter(pk=page.pk).update(verified_at=timezone.now() - timezone.timedelta(minutes=5))
            page.refresh_from_db()
            page.verify(lead, note="Still right.")
            page.refresh_from_db()
            assert page.verified_note == "Still right."

        def it_does_not_swallow_a_different_verifier(db, guild_and_lead, stub_page_verified_event):
            guild, lead = guild_and_lead
            second = MemberFactory()
            GuildStaffMembershipFactory(guild=guild, member=second, role="orienter")
            page = WikiPageFactory(guild=guild)
            page.verify(lead)
            page.verify(second)
            page.refresh_from_db()
            assert page.verified_by_id == second.pk

    def describe_an_official_page():
        def it_refuses_with_the_sentence_the_member_reads(db, guild_and_lead):
            guild, lead = guild_and_lead
            page = WikiPageFactory(guild=guild, official=True)
            with pytest.raises(WikiVerificationError) as excinfo:
                page.verify(lead)
            assert str(excinfo.value) == "Official pages are set by admins, not verified."

    def describe_an_archived_page():
        def it_refuses(db, guild_and_lead):
            guild, lead = guild_and_lead
            page = WikiPageFactory(guild=guild, archived=True)
            with pytest.raises(WikiVerificationError) as excinfo:
                page.verify(lead)
            assert str(excinfo.value) == "This page is archived."


def describe_the_frozen_role_label():
    def it_reads_the_staff_role_for_an_orienter(db, stub_page_verified_event):
        orienter = _member("label_orienter")
        guild = GuildFactory(name="Woodworking")
        GuildStaffMembershipFactory(guild=guild, member=orienter, role="orienter")
        page = WikiPageFactory(guild=guild)
        page.verify(orienter)
        assert page.verified_role_label == "Woodworking orienter"

    def it_reads_Admin_for_someone_reaching_in_from_outside_the_guild(db, stub_page_verified_event):
        admin = _member("label_admin", fog_role=Member.FogRole.ADMIN)
        page = WikiPageFactory(guild=GuildFactory(name="Woodworking"))
        page.verify(admin)
        assert page.verified_role_label == "Admin"

    def it_reads_Admin_for_a_space_wide_page(db, stub_page_verified_event):
        admin = _member("label_sw_admin", fog_role=Member.FogRole.ADMIN)
        page = WikiPageFactory(guild=None)
        page.verify(admin)
        assert page.verified_role_label == "Admin"

    def it_stays_empty_rather_than_claiming_Admin_for_an_unrecognized_verifier(db):
        """The fallthrough used to be the literal "Admin", so anybody reaching a guild page
        by some authority other than lead-or-staff was frozen into the page as an
        administrator forever, in the one string this feature exists to make trustworthy."""
        plain = _member("label_plain")
        guild = GuildFactory(name="Woodworking")
        assert _wiki_role_label(plain, guild) == ""
        assert _wiki_role_label(plain, None) == ""

    def it_still_says_Admin_for_a_real_admin_or_officer(db):
        assert _wiki_role_label(_member("label_real_admin", fog_role=Member.FogRole.ADMIN), None) == "Admin"
        officer = _member("label_real_officer", fog_role=Member.FogRole.GUILD_OFFICER)
        assert _wiki_role_label(officer, GuildFactory(name="Woodworking")) == "Admin"

    def it_survives_the_verifiers_staff_row_being_deleted(db, stub_page_verified_event):
        """The whole point of denormalizing: the credit is a statement about the past."""
        orienter = _member("label_departing")
        guild = GuildFactory(name="Woodworking")
        staff = GuildStaffMembershipFactory(guild=guild, member=orienter, role="orienter")
        page = WikiPageFactory(guild=guild)
        page.verify(orienter)
        staff.delete()
        page.refresh_from_db()
        assert page.verified_role_label == "Woodworking orienter"


def describe_WikiPage_unverify():
    def it_clears_the_credit_and_drops_to_community(db, stub_page_verified_event):
        lead = _member("unverify_lead")
        guild = GuildFactory(name="Woodworking", guild_lead=lead)
        page = WikiPageFactory(guild=guild)
        page.verify(lead, note="Looked right to me.")
        checked_at = page.last_checked_at
        page.unverify(lead)
        page.refresh_from_db()
        assert page.status == WikiPage.Status.COMMUNITY
        assert page.verified_by_id is None
        assert page.verified_at is None
        assert page.verified_note == ""
        assert page.verified_role_label == ""
        # The clock is NOT rewound: removing a verification says nothing about freshness.
        assert page.last_checked_at == checked_at

    def it_never_raises_spec_Ds_amber_banner(db, stub_page_verified_event):
        """Removing a verification is not a report; doing so would put a page in D's queue
        with no WikiReport behind it."""
        lead = _member("unverify_noreport")
        guild = GuildFactory(guild_lead=lead)
        page = WikiPageFactory(guild=guild)
        page.verify(lead)
        page.unverify(lead)
        page.refresh_from_db()
        assert page.needs_review_since is None

    def it_logs_a_removal_and_emits_nothing(db, stub_page_verified_event):
        lead = _member("unverify_logs")
        guild = GuildFactory(guild_lead=lead)
        page = WikiPageFactory(guild=guild, status=WikiPage.Status.GUILD_VERIFIED)
        with mock.patch("core.events.emit.emit") as emit:
            page.unverify(lead)
        assert emit.call_count == 0
        row = SiteActivity.objects.filter(kind=SiteActivity.Kind.WIKI_PAGE_VERIFIED).get()
        assert row.payload["removed"] is True

    def it_never_wipes_spec_As_edited_since_warning(db):
        """unverified_reason is A's "edited since it was verified" signal, the only mark
        that unreviewed text is sitting on the page. A remove=1 on a page that was never
        verified used to blank it."""
        lead = _member("unverify_keeps_reason")
        guild = GuildFactory(guild_lead=lead)
        page = WikiPageFactory(guild=guild, unverified_reason="Edited since it was verified.")
        page.unverify(lead)
        page.refresh_from_db()
        assert page.unverified_reason == "Edited since it was verified."

    def it_is_a_quiet_no_op_on_a_page_that_was_never_verified(db):
        lead = _member("unverify_never")
        guild = GuildFactory(guild_lead=lead)
        page = WikiPageFactory(guild=guild)
        page.unverify(lead)
        page.refresh_from_db()
        assert page.status == WikiPage.Status.COMMUNITY
        assert SiteActivity.objects.filter(kind=SiteActivity.Kind.WIKI_PAGE_VERIFIED).count() == 0

    def it_refuses_an_archived_page_just_as_verify_does(db):
        """The pair has to be symmetric; verify() 400s here and unverify() used to mutate."""
        lead = _member("unverify_archived")
        guild = GuildFactory(guild_lead=lead)
        page = WikiPageFactory(guild=guild, status=WikiPage.Status.GUILD_VERIFIED, archived=True)
        with pytest.raises(WikiVerificationError):
            page.unverify(lead)
        page.refresh_from_db()
        assert page.status == WikiPage.Status.GUILD_VERIFIED
