"""BDD specs for the admin Voting surface — tabbed IA + auditable snapshots.

Covers the seven @fog_admin_required hub views (overview / history /
history_detail / snapshots / settings / snapshot_take / snapshot_delete),
their gating, the analyzer filters, the empty states, and snapshot deletion.

See docs/superpowers/plans/2026-06-25-voting-admin-tabs-and-audit.md.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.template.loader import get_template
from django.test import Client
from django.urls import reverse

from membership.models import FundingSnapshot, Member
from tests.membership.factories import (
    FundingSnapshotFactory,
    GuildFactory,
    MemberFactory,
    MembershipPlanFactory,
    VotePreferenceFactory,
)

User = get_user_model()

pytestmark = pytest.mark.django_db


@pytest.fixture()
def admin_client():
    user = User.objects.create_superuser("vadmin", "vadmin@x.com", "p")
    client = Client()
    client.force_login(user)
    return client


@pytest.fixture()
def member_client():
    MembershipPlanFactory()  # the auto-provision signal needs a plan to create a Member
    user = User.objects.create_user("vmember", email="vmember@x.com", password="p")
    client = Client()
    client.force_login(user)
    return client


def _mixed_votes():
    """A paying standard member and a non-paying guild officer, each with a vote."""
    g1, g2, g3 = GuildFactory(name="Ceramics"), GuildFactory(name="Textiles"), GuildFactory(name="Wood")
    paying = MemberFactory(
        member_type=Member.MemberType.STANDARD,
        fog_role=Member.FogRole.MEMBER,
        full_legal_name="Alice Standard",
    )
    officer = MemberFactory(
        member_type=Member.MemberType.WORK_TRADE,
        fog_role=Member.FogRole.GUILD_OFFICER,
        full_legal_name="Oscar Officer",
    )
    VotePreferenceFactory(member=paying, guild_1st=g1, guild_2nd=g2, guild_3rd=g3)
    VotePreferenceFactory(member=officer, guild_1st=g2, guild_2nd=g3, guild_3rd=g1)
    return paying, officer


def describe_gating():
    def it_redirects_anonymous_to_login(client):
        assert client.get(reverse("hub_admin_voting_overview")).status_code == 302

    def it_forbids_non_admin_on_every_tab(member_client):
        for url in [
            reverse("hub_admin_voting_overview"),
            reverse("hub_admin_voting_history"),
            reverse("hub_admin_voting_history_detail", args=[1]),
            reverse("hub_admin_voting_snapshots"),
            reverse("hub_admin_voting_settings"),
        ]:
            assert member_client.get(url).status_code == 403

    def it_forbids_non_admin_on_post_actions(member_client):
        assert member_client.post(reverse("hub_admin_voting_snapshot_take")).status_code == 403
        assert member_client.post(reverse("hub_admin_voting_snapshot_delete", args=[1])).status_code == 403


def describe_overview():
    def it_renders_stats_and_leaders(admin_client):
        _mixed_votes()
        resp = admin_client.get(reverse("hub_admin_voting_overview"))
        assert resp.status_code == 200
        assert "stats" in resp.context
        assert b"Current vote leaders" in resp.content
        assert b"Ceramics" in resp.content

    def it_shows_empty_state_with_no_votes(admin_client):
        resp = admin_client.get(reverse("hub_admin_voting_overview"))
        assert resp.status_code == 200
        assert b"No votes cast this cycle yet." in resp.content
        assert b"Current vote leaders" not in resp.content

    def it_includes_the_spec2_banner_placeholder_comment():
        source = Path(get_template("hub/admin/voting_overview.html").origin.name).read_text(encoding="utf-8")
        assert "SPEC 2 HOOK" in source


def describe_settings():
    def it_renders_the_shell_for_admin(admin_client):
        resp = admin_client.get(reverse("hub_admin_voting_settings"))
        assert resp.status_code == 200
        assert b"Voting settings live here" in resp.content


def describe_history():
    def it_lists_snapshots_newest_first(admin_client):
        first = FundingSnapshotFactory(cycle_label="Older Cycle")
        second = FundingSnapshotFactory(cycle_label="Newer Cycle")
        content = admin_client.get(reverse("hub_admin_voting_history")).content.decode()
        assert content.index(second.cycle_label) < content.index(first.cycle_label)

    def it_shows_empty_state_with_link_to_snapshots(admin_client):
        resp = admin_client.get(reverse("hub_admin_voting_history"))
        assert b"No funding snapshots yet" in resp.content
        assert reverse("hub_admin_voting_snapshots").encode() in resp.content

    def it_renders_the_source_badge(admin_client):
        FundingSnapshotFactory(cycle_label="June 2026")
        resp = admin_client.get(reverse("hub_admin_voting_history"))
        assert b"Manual" in resp.content

    def it_renders_a_per_row_delete_button_per_snapshot(admin_client):
        a = FundingSnapshotFactory(cycle_label="Cycle A")
        b = FundingSnapshotFactory(cycle_label="Cycle B")
        content = admin_client.get(reverse("hub_admin_voting_history")).content.decode()
        assert f"del-snapshot-{a.pk}" in content
        assert f"del-snapshot-{b.pk}" in content


def describe_history_detail():
    def _stored(admin_client):
        _mixed_votes()
        snap = FundingSnapshot.take(minimum_pool=Decimal("1000"))
        assert snap is not None
        return snap

    def it_renders_immutable_audit_from_raw_votes(admin_client):
        snap = _stored(admin_client)
        content = admin_client.get(reverse("hub_admin_voting_history_detail", args=[snap.pk])).content
        assert b"Alice Standard" in content
        assert b"Oscar Officer" in content
        assert b"Per-guild allocation" in content
        assert b"Individual votes" in content

    def it_applies_filters_via_get(admin_client):
        snap = _stored(admin_client)
        url = reverse("hub_admin_voting_history_detail", args=[snap.pk])
        resp = admin_client.get(url, {"member_type": Member.MemberType.STANDARD})
        assert resp.context["total_count"] == 1
        assert b"Alice Standard" in resp.content
        assert b"Oscar Officer" not in resp.content

    def it_filters_by_paying_tristate_via_get(admin_client):
        snap = _stored(admin_client)
        url = reverse("hub_admin_voting_history_detail", args=[snap.pk])
        yes = admin_client.get(url, {"is_paying": "yes"})
        assert yes.context["paying_count"] == 1
        assert yes.context["non_paying_count"] == 0
        no = admin_client.get(url, {"is_paying": "no"})
        assert no.context["paying_count"] == 0
        assert no.context["non_paying_count"] == 1

    def it_filters_by_guild_role_via_get(admin_client):
        g1, g2, g3 = GuildFactory(name="A"), GuildFactory(name="B"), GuildFactory(name="C")
        lead = MemberFactory(full_legal_name="Lena Lead")
        plain = MemberFactory(full_legal_name="Pat Plain")
        VotePreferenceFactory(member=lead, guild_1st=g1, guild_2nd=g2, guild_3rd=g3)
        VotePreferenceFactory(member=plain, guild_1st=g1, guild_2nd=g2, guild_3rd=g3)
        GuildFactory(name="Led", guild_lead=lead)
        snap = FundingSnapshot.take(minimum_pool=Decimal("0"))
        assert snap is not None
        url = reverse("hub_admin_voting_history_detail", args=[snap.pk])
        resp = admin_client.get(url, {"is_guild_lead": "yes"})
        assert resp.context["total_count"] == 1
        assert b"Lena Lead" in resp.content
        assert b"Pat Plain" not in resp.content

    def it_shows_no_match_empty_state(admin_client):
        snap = _stored(admin_client)
        url = reverse("hub_admin_voting_history_detail", args=[snap.pk])
        resp = admin_client.get(url, {"member_type": Member.MemberType.VOLUNTEER})
        assert b"No votes match these filters." in resp.content

    def it_shows_legacy_banner_for_empty_raw_votes(admin_client):
        snap = FundingSnapshot.objects.create(
            cycle_label="Legacy",
            contributor_count=5,
            funding_pool=Decimal("50.00"),
            minimum_pool=Decimal("0.00"),
            raw_votes=[],
            results={"total_pool": "50.00", "votes_cast": 5, "results": []},
        )
        resp = admin_client.get(reverse("hub_admin_voting_history_detail", args=[snap.pk]))
        assert resp.status_code == 200
        assert resp.context["is_legacy"] is True
        assert b"before per-vote history was stored" in resp.content

    def it_404s_unknown_pk(admin_client):
        assert admin_client.get(reverse("hub_admin_voting_history_detail", args=[999999])).status_code == 404

    def it_renders_a_header_delete_button(admin_client):
        snap = _stored(admin_client)
        content = admin_client.get(reverse("hub_admin_voting_history_detail", args=[snap.pk])).content.decode()
        assert f"del-snapshot-{snap.pk}" in content
        assert "Delete Snapshot" in content


def describe_snapshots():
    def it_renders_live_audit_and_dry_run_totals(admin_client):
        _mixed_votes()
        resp = admin_client.get(reverse("hub_admin_voting_snapshots"))
        assert resp.status_code == 200
        assert "calc" in resp.context
        assert b"Alice Standard" in resp.content
        assert b"Take Snapshot" in resp.content

    def it_take_creates_snapshot_and_redirects_to_detail(admin_client):
        _mixed_votes()
        resp = admin_client.post(
            reverse("hub_admin_voting_snapshot_take"),
            {"title": "Test", "minimum_pool": "1000"},
        )
        assert resp.status_code == 302
        snap = FundingSnapshot.objects.get()
        assert resp.url == reverse("hub_admin_voting_history_detail", args=[snap.pk])

    def it_take_with_no_votes_warns_and_stays(admin_client):
        resp = admin_client.post(reverse("hub_admin_voting_snapshot_take"), {})
        assert resp.status_code == 302
        assert resp.url == reverse("hub_admin_voting_snapshots")
        assert FundingSnapshot.objects.count() == 0
        msgs = [str(m) for m in get_messages(resp.wsgi_request)]
        assert any("nothing to snapshot" in m for m in msgs)

    def it_take_uses_title_and_minimum_pool(admin_client):
        _mixed_votes()
        admin_client.post(
            reverse("hub_admin_voting_snapshot_take"),
            {"title": "Q2 2026", "minimum_pool": "500"},
        )
        snap = FundingSnapshot.objects.get()
        assert snap.cycle_label == "Q2 2026"
        assert snap.minimum_pool == Decimal("500.00")

    def it_shows_no_votes_cast_message_when_empty(admin_client):
        resp = admin_client.get(reverse("hub_admin_voting_snapshots"))
        assert b"No votes cast this cycle yet \xe2\x80\x94 nothing to snapshot." in resp.content

    def it_shows_no_match_message_when_filtered_empty(admin_client):
        _mixed_votes()
        resp = admin_client.get(reverse("hub_admin_voting_snapshots"), {"member_type": Member.MemberType.VOLUNTEER})
        assert b"No votes match these filters." in resp.content
        assert b"nothing to snapshot" not in resp.content

    def it_has_no_delete_controls(admin_client):
        _mixed_votes()
        content = admin_client.get(reverse("hub_admin_voting_snapshots")).content
        assert b"del-snapshot-" not in content
        assert b"open-confirm" not in content


def describe_delete():
    def _stored(admin_client):
        _mixed_votes()
        snap = FundingSnapshot.take(minimum_pool=Decimal("1000"))
        assert snap is not None
        return snap

    def it_deletes_and_redirects_to_history_with_message(admin_client):
        snap = _stored(admin_client)
        pk = snap.pk
        resp = admin_client.post(reverse("hub_admin_voting_snapshot_delete", args=[pk]))
        assert resp.status_code == 302
        assert resp.url == reverse("hub_admin_voting_history")
        assert not FundingSnapshot.objects.filter(pk=pk).exists()
        msgs = [str(m) for m in get_messages(resp.wsgi_request)]
        assert any("Deleted snapshot" in m for m in msgs)

    def it_removes_the_airtable_record(admin_client):
        snap = _stored(admin_client)
        FundingSnapshot.objects.filter(pk=snap.pk).update(airtable_record_id="recDEL123")
        with patch("airtable_sync.service.delete_snapshot_from_airtable") as mock_delete:
            admin_client.post(reverse("hub_admin_voting_snapshot_delete", args=[snap.pk]))
        mock_delete.assert_called_once_with("recDEL123")

    def it_is_post_only(admin_client):
        snap = _stored(admin_client)
        assert admin_client.get(reverse("hub_admin_voting_snapshot_delete", args=[snap.pk])).status_code == 405
        assert admin_client.get(reverse("hub_admin_voting_snapshot_take")).status_code == 405
