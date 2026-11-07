"""The Activity Feed's category map (#1114): every SiteActivity.Kind sits in exactly one group."""

from collections import Counter

from core.models import SITE_ACTIVITY_CATEGORIES, SiteActivity


def _placements() -> Counter[str]:
    return Counter(kind.value for _label, kinds in SITE_ACTIVITY_CATEGORIES.values() for kind in kinds)


def describe_site_activity_categories():
    def it_places_every_kind_in_a_category():
        missing = [kind.value for kind in SiteActivity.Kind if kind.value not in _placements()]
        assert not missing, f"SiteActivity kinds with no category in SITE_ACTIVITY_CATEGORIES: {missing}"

    def it_places_no_kind_twice():
        twice = [kind for kind, n in _placements().items() if n > 1]
        assert not twice, f"SiteActivity kinds in more than one category: {twice}"

    def it_names_only_real_kinds():
        assert set(_placements()) <= set(SiteActivity.Kind.values)

    def it_offers_the_groups_the_ticket_names_in_order():
        assert [label for label, _kinds in SITE_ACTIVITY_CATEGORIES.values()] == [
            "Accounts and sign in",
            "Voting",
            "Classes and teaching",
            "Orientations",
            "Money",
            "Guilds and announcements",
            "Spaces",
            "Meetings",
            "Wiki",
        ]

    def it_puts_late_fees_and_reconciliation_under_money():
        _label, money = SITE_ACTIVITY_CATEGORIES["money"]
        assert SiteActivity.Kind.LATE_FEE_CHARGED in money
        assert SiteActivity.Kind.RECONCILIATION_SNAPSHOT_TAKEN in money
