"""Specs for membership.vote_analyzer — the pure analyzer helpers lifted out of
the old Django-admin snapshot analyzer.

See docs/superpowers/plans/2026-06-25-voting-admin-tabs-and-audit.md.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from membership.models import Member
from membership.vote_analyzer import apply_filters, parse_is_paying, parse_minimum_pool, serialize_live_votes
from tests.membership.factories import (
    GuildFactory,
    GuildStaffMembershipFactory,
    MemberFactory,
    VotePreferenceFactory,
)


def _row(**overrides: Any) -> dict[str, Any]:
    """A vote-dict in the shape the analyzer consumes, with sane defaults."""
    base: dict[str, Any] = {
        "member_id": 1,
        "member_name": "Voter",
        "member_type": Member.MemberType.STANDARD,
        "fog_role": Member.FogRole.MEMBER,
        "is_paying": True,
        "is_guild_lead": False,
        "is_guild_staff": False,
        "guild_1st_name": "A",
        "guild_2nd_name": "B",
        "guild_3rd_name": "C",
    }
    base.update(overrides)
    return base


def describe_serialize_live_votes():
    def it_only_includes_signed_up_members(db):
        g1, g2, g3 = GuildFactory(name="A"), GuildFactory(name="B"), GuildFactory(name="C")
        signed_up = MemberFactory(full_legal_name="Signed Up")
        unlinked = MemberFactory(user=None, full_legal_name="Never Signed Up")
        VotePreferenceFactory(member=signed_up, guild_1st=g1, guild_2nd=g2, guild_3rd=g3)
        VotePreferenceFactory(member=unlinked, guild_1st=g1, guild_2nd=g2, guild_3rd=g3, signed_up=False)

        rows = serialize_live_votes()

        names = {r["member_name"] for r in rows}
        assert names == {"Signed Up"}

    def it_tags_guild_leads_and_staff(db):
        g1, g2, g3 = GuildFactory(name="A"), GuildFactory(name="B"), GuildFactory(name="C")
        lead = MemberFactory(full_legal_name="Lead Person")
        staff = MemberFactory(full_legal_name="Staff Person")
        plain = MemberFactory(full_legal_name="Plain Person")
        VotePreferenceFactory(member=lead, guild_1st=g1, guild_2nd=g2, guild_3rd=g3)
        VotePreferenceFactory(member=staff, guild_1st=g1, guild_2nd=g2, guild_3rd=g3)
        VotePreferenceFactory(member=plain, guild_1st=g1, guild_2nd=g2, guild_3rd=g3)
        led_guild = GuildFactory(name="Led Guild", guild_lead=lead)
        GuildStaffMembershipFactory(member=staff, guild=led_guild)

        rows = {r["member_name"]: r for r in serialize_live_votes()}

        assert rows["Lead Person"]["is_guild_lead"] is True
        assert rows["Lead Person"]["is_guild_staff"] is False
        assert rows["Staff Person"]["is_guild_staff"] is True
        assert rows["Staff Person"]["is_guild_lead"] is False
        assert rows["Plain Person"]["is_guild_lead"] is False
        assert rows["Plain Person"]["is_guild_staff"] is False


def describe_apply_filters():
    def _filter(rows, **kw):
        defaults = {
            "member_types": [],
            "fog_roles": [],
            "is_paying": None,
            "is_guild_lead": None,
            "is_guild_staff": None,
        }
        defaults.update(kw)
        return apply_filters(rows, **defaults)

    def it_filters_by_member_type():
        rows = [
            _row(member_name="Std", member_type=Member.MemberType.STANDARD),
            _row(member_name="WT", member_type=Member.MemberType.WORK_TRADE),
        ]
        result = _filter(rows, member_types=[Member.MemberType.STANDARD])
        assert [r["member_name"] for r in result] == ["Std"]

    def it_filters_by_fog_role():
        rows = [
            _row(member_name="M", fog_role=Member.FogRole.MEMBER),
            _row(member_name="O", fog_role=Member.FogRole.GUILD_OFFICER),
        ]
        result = _filter(rows, fog_roles=[Member.FogRole.GUILD_OFFICER])
        assert [r["member_name"] for r in result] == ["O"]

    def it_filters_by_paying_tristate():
        rows = [_row(member_name="Pay", is_paying=True), _row(member_name="Free", is_paying=False)]
        assert [r["member_name"] for r in _filter(rows, is_paying=True)] == ["Pay"]
        assert [r["member_name"] for r in _filter(rows, is_paying=False)] == ["Free"]
        assert {r["member_name"] for r in _filter(rows, is_paying=None)} == {"Pay", "Free"}

    def it_filters_guild_leads_only():
        rows = [
            _row(member_name="Lead", is_guild_lead=True),
            _row(member_name="NotLead", is_guild_lead=False),
        ]
        result = _filter(rows, is_guild_lead=True)
        assert [r["member_name"] for r in result] == ["Lead"]

    def it_filters_guild_staff_only():
        rows = [
            _row(member_name="Staff", is_guild_staff=True),
            _row(member_name="NotStaff", is_guild_staff=False),
        ]
        result = _filter(rows, is_guild_staff=True)
        assert [r["member_name"] for r in result] == ["Staff"]

    def it_treats_legacy_rows_missing_lead_keys_as_not_matching():
        legacy_row = {
            "member_name": "Legacy",
            "member_type": Member.MemberType.STANDARD,
            "fog_role": Member.FogRole.MEMBER,
            "is_paying": True,
            "guild_1st_name": "A",
            "guild_2nd_name": "B",
            "guild_3rd_name": "C",
        }
        assert _filter([legacy_row], is_guild_lead=True) == []
        assert _filter([legacy_row], is_guild_staff=True) == []

    def it_returns_empty_when_nothing_matches():
        rows = [_row(member_type=Member.MemberType.STANDARD)]
        assert _filter(rows, member_types=[Member.MemberType.VOLUNTEER]) == []


def describe_parse_minimum_pool():
    def it_falls_back_on_blank_or_invalid_or_negative():
        assert parse_minimum_pool("") == Decimal("1000")
        assert parse_minimum_pool(None) == Decimal("1000")
        assert parse_minimum_pool("abc") == Decimal("1000")
        assert parse_minimum_pool("-100") == Decimal("1000")

    def it_returns_a_valid_value():
        assert parse_minimum_pool("500") == Decimal("500")
        assert parse_minimum_pool("0") == Decimal("0")


def describe_parse_is_paying():
    def it_maps_yes_no_and_both():
        assert parse_is_paying("yes") is True
        assert parse_is_paying("no") is False
        assert parse_is_paying("") is None
        assert parse_is_paying("anything-else") is None
