"""Retaking a month's snapshot keeps its frozen guild voting cents (#983)."""

from __future__ import annotations

from datetime import date, datetime
from datetime import timezone as dt_timezone
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from billing.models import Payout, ReconciliationSnapshot
from billing.payments_panel import PanelWindow
from billing.reconciliation import RecipientKind
from tests.membership.factories import GuildFactory, MemberFactory, VotePreferenceFactory

pytestmark = pytest.mark.django_db

AUGUST = PanelWindow(start=date(2026, 8, 1), end=date(2026, 8, 31))


def _paid_in_august(*, cents: int, slug: str, guild, instructor=None):
    from classes.factories import CategoryFactory, ClassOfferingFactory, ClassSessionFactory, RegistrationFactory

    offering = ClassOfferingFactory(slug=slug, category=CategoryFactory(guild=guild), instructor=instructor)
    when = datetime(2026, 8, 15, 18, tzinfo=dt_timezone.utc)
    ClassSessionFactory(class_offering=offering, starts_at=when)
    return RegistrationFactory(class_offering=offering, amount_paid_cents=cents, confirmed_at=when)


def _voting(snapshot: ReconciliationSnapshot) -> dict[str, int]:
    return {
        row["label"]: row["voting_cents"]
        for group in snapshot.results["groups"]
        if group["kind"] == RecipientKind.GUILD.value
        for row in group["rows"]
    }


@pytest.fixture
def frozen_august():
    """An August snapshot holding a demo class and a voting split, which then moves on."""
    wood, metal, clay = GuildFactory(name="Wood"), GuildFactory(name="Metal"), GuildFactory(name="Clay")
    voter = MemberFactory()
    VotePreferenceFactory(member=voter, guild_1st=wood, guild_2nd=metal, guild_3rd=clay)
    _paid_in_august(cents=10000, slug="real-bowls", guild=wood, instructor=MemberFactory())
    demo = _paid_in_august(cents=40000, slug="demo-cart-bowls", guild=wood, instructor=MemberFactory())
    # Freeze it the way August was frozen, before demo classes were left out.
    demo.class_offering.slug = "cart-bowls"
    demo.class_offering.save(update_fields=["slug"])
    old = ReconciliationSnapshot.take(period_start=AUGUST.start, period_end=AUGUST.end, title="August 2026")
    demo.class_offering.slug = "demo-cart-bowls"
    demo.class_offering.save(update_fields=["slug"])
    # Votes change after the month closed: a fresh projection would differ from the frozen one.
    VotePreferenceFactory(member=MemberFactory(), guild_1st=clay, guild_2nd=metal, guild_3rd=wood)
    return old


def describe_retake():
    def it_replaces_the_snapshot_without_demo_money(frozen_august):
        assert frozen_august.grand_total_cents == 50000

        new = frozen_august.retake()

        assert new.grand_total_cents == 10000
        assert list(ReconciliationSnapshot.objects.values_list("pk", flat=True)) == [new.pk]
        assert new.title == "August 2026"

    def it_copies_the_voting_cents_from_the_snapshot_it_replaces(frozen_august):
        from billing.reconciliation import _project_voting_cents

        before = _voting(frozen_august)
        wood = GuildFactory._meta.model.objects.get(name="Wood")
        assert _project_voting_cents()[wood.pk] != before["Wood"]  # a fresh projection has moved on

        new = frozen_august.retake()

        assert _voting(new) == before
        assert before["Wood"] > 0

    def it_rebinds_a_pending_stripe_payout_to_the_new_snapshot(frozen_august):
        payout = Payout.objects.filter(registration__class_offering__slug="real-bowls").first()
        Payout.objects.filter(pk=payout.pk).update(status=Payout.Status.PENDING, counted_as_stripe_in=frozen_august)

        new = frozen_august.retake()

        payout.refresh_from_db()
        assert payout.counted_as_stripe_in_id == new.pk


def describe_latest_for():
    def it_raises_when_the_month_has_no_snapshot():
        with pytest.raises(ValueError, match="No reconciliation snapshot"):
            ReconciliationSnapshot.latest_for(period_start=AUGUST.start, period_end=AUGUST.end)


def describe_retake_command():
    def it_prints_old_and_new_totals_on_a_dry_run_and_writes_nothing(frozen_august):
        out = StringIO()

        call_command("retake_reconciliation_snapshot", "--month", "2026-08", "--dry-run", stdout=out)

        text = out.getvalue()
        assert "$500.00" in text
        assert "Retaken now it would total $100.00" in text
        assert "CHANGED" not in text
        assert list(ReconciliationSnapshot.objects.values_list("pk", flat=True)) == [frozen_august.pk]

    def it_retakes_the_month(frozen_august):
        out = StringIO()

        call_command("retake_reconciliation_snapshot", "--month", "2026-08", stdout=out)

        assert not ReconciliationSnapshot.objects.filter(pk=frozen_august.pk).exists()
        assert "Took snapshot" in out.getvalue()
        assert "CHANGED" not in out.getvalue()

    def it_flags_a_guild_whose_voting_would_change():
        from django.core.management import load_command_class

        command = load_command_class("billing", "retake_reconciliation_snapshot")
        command.stdout = StringIO()
        group = {"kind": RecipientKind.GUILD.value, "rows": [{"label": "Wood", "voting_cents": 100}]}
        command._write_voting({"groups": [group]}, {"groups": []})

        assert "Wood: $1.00 -> $0.00  CHANGED" in command.stdout.getvalue()

    def it_reads_an_old_row_without_a_voting_figure_as_zero_and_says_so():
        from django.core.management import load_command_class

        command = load_command_class("billing", "retake_reconciliation_snapshot")
        command.stdout = StringIO()
        old_row = {"kind": RecipientKind.GUILD.value, "rows": [{"label": "Wood"}]}
        new_row = {"kind": RecipientKind.GUILD.value, "rows": [{"label": "Wood", "voting_cents": 0}]}
        command._write_voting({"groups": [old_row]}, {"groups": [new_row]})

        assert "Wood: $0.00 -> $0.00  (old snapshot has no voting figure; read as $0.00)" in command.stdout.getvalue()

    def it_rejects_a_bad_month():
        with pytest.raises(CommandError, match="YYYY-MM"):
            call_command("retake_reconciliation_snapshot", "--month", "August", stdout=StringIO())

    def it_fails_loudly_when_the_month_has_no_snapshot():
        with pytest.raises(CommandError, match="No reconciliation snapshot"):
            call_command("retake_reconciliation_snapshot", "--month", "2026-08", stdout=StringIO())
