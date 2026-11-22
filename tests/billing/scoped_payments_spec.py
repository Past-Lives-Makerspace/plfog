"""BDD specs for Payments and Reports scoped to what each viewer may see (#1294).

One makerspace with two guilds, Wood and Metal, each with a paid class registration, orientation,
equipment reservation, late fee, cubby invoice and My Tab purchase, plus classes taught by an
instructor who leads nothing. Each persona opens the same pages and sees only their own rows.
"""

from __future__ import annotations

import csv
import io
from decimal import Decimal
from typing import Any

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.utils import timezone

from billing.models import BillingSettings, LateCancellationFee, TabCharge, TabEntrySplit
from billing.payments_panel import build_payments_ledger, parse_window
from billing.reconciliation import RecipientKind, build_reconciliation
from billing.scope import PaymentsScope
from membership.models import AdminCapability, Member, OrientationBooking
from tests.billing.factories import (
    LateCancellationFeeFactory,
    TabChargeFactory,
    TabEntryFactory,
    TabEntrySplitFactory,
    TabFactory,
)
from tests.membership.factories import (
    CubbyFactory,
    CubbyPaymentFactory,
    CubbyRentalFactory,
    EquipmentFactory,
    EquipmentReservationFactory,
    GuildFactory,
    GuildStaffMembershipFactory,
    MemberFactory,
    OrientationBookingFactory,
    OrientationSlotFactory,
    ShelfUnitFactory,
)

pytestmark = pytest.mark.django_db

_DASHBOARD = "/billing/admin/dashboard/"
_PAYMENTS = f"{_DASHBOARD}?tab=payments"
_REPORTS = f"{_DASHBOARD}?tab=reconciliation"
_PAYMENTS_TABLE = "/billing/admin/payments/table/"
_PAYMENTS_CSV = "/billing/admin/payments/export/csv/"
_REPORTS_TABLE = "/billing/admin/reconciliation/table/"
_REPORTS_CSV = "/billing/admin/reconciliation/export/csv/"
_REPORTS_PRINT = "/billing/admin/reconciliation/print/"


def _user(username: str) -> User:
    return User.objects.create_user(username=username, password="pass", email=f"{username}@example.com")


def _login(client: Client, user: User) -> Member:
    client.login(username=user.username, password="pass")
    return user.member  # type: ignore[attr-defined]


def _teacher(username: str) -> User:
    """A member granted teaching, as the teaching portal's gate admits."""
    user = _user(username)
    Member.objects.filter(user=user).update(
        instructor_slug=username, instructor_oriented_at=timezone.now(), status=Member.Status.ACTIVE
    )
    return user


def _registration(*, guild: Any, title: str, instructors: list[Member], cents: int = 10000) -> Any:
    from classes.factories import CategoryFactory, ClassOfferingFactory, RegistrationFactory

    offering = ClassOfferingFactory(category=CategoryFactory(guild=guild), instructor=instructors[0], title=title)
    if len(instructors) > 1:
        offering.set_instructors(instructors)
    registration = RegistrationFactory(class_offering=offering, amount_paid_cents=cents)
    registration.confirmed_at = timezone.now()
    registration.save(update_fields=["confirmed_at"])
    return registration


def _orientation(guild: Any, cents: int = 4000) -> OrientationBooking:
    return OrientationBookingFactory(
        slot=OrientationSlotFactory(guild=guild), amount_paid_cents=cents, status=OrientationBooking.Status.CONFIRMED
    )


def _reservation(guild: Any, name: str, cents: int = 3000) -> Any:
    return EquipmentReservationFactory(
        equipment=EquipmentFactory(guild=guild, name=name), amount_paid_cents=cents, stripe_payment_id=f"pi_{name}"
    )


def _late_fee(guild: Any, name: str) -> LateCancellationFee:
    reservation = EquipmentReservationFactory(equipment=EquipmentFactory(guild=guild, name=name), status="cancelled")
    return LateCancellationFeeFactory(
        orientation_booking=None,
        reservation=reservation,
        member=reservation.member,
        status=LateCancellationFee.Status.PAID,
        paid_at=timezone.now(),
    )


def _cubby_payment(guild: Any) -> Any:
    rental = CubbyRentalFactory(cubby=CubbyFactory(unit=ShelfUnitFactory(guild=guild)))
    return CubbyPaymentFactory(rental=rental, payee=MemberFactory(), amount_cents=2000)


def _tab_charge(wood: Any, metal: Any) -> tuple[TabCharge, Any, Any]:
    """One succeeded charge carrying a Wood purchase ($20, 80% Wood) and a Metal purchase ($50, all Metal)."""
    tab = TabFactory()
    charge = TabChargeFactory(tab=tab, status=TabCharge.Status.SUCCEEDED, amount=Decimal("70.00"))
    TabCharge.objects.filter(pk=charge.pk).update(charged_at=timezone.now())
    wood_entry = TabEntryFactory(tab=tab, tab_charge=charge, amount=Decimal("20.00"), description="Wood Glue Bottle")
    TabEntrySplitFactory(
        entry=wood_entry,
        recipient_type=TabEntrySplit.RecipientType.ADMIN,
        guild=None,
        percent=Decimal("20"),
        amount=Decimal("4.00"),
    )
    TabEntrySplitFactory(
        entry=wood_entry,
        recipient_type=TabEntrySplit.RecipientType.GUILD,
        guild=wood,
        percent=Decimal("80"),
        amount=Decimal("16.00"),
    )
    metal_entry = TabEntryFactory(tab=tab, tab_charge=charge, amount=Decimal("50.00"), description="Metal Rod Bundle")
    TabEntrySplitFactory(
        entry=metal_entry,
        recipient_type=TabEntrySplit.RecipientType.GUILD,
        guild=metal,
        percent=Decimal("100"),
        amount=Decimal("50.00"),
    )
    return charge, wood_entry, metal_entry


@pytest.fixture
def world() -> dict[str, Any]:
    """Two guilds' money, an instructor who leads nothing, and every persona's account."""
    BillingSettings.load()
    lead = _user("wood_lead")
    staff = _user("wood_staff")
    teacher = _teacher("teacher")
    lead_teacher = _teacher("lead_teacher")
    payee = MemberFactory(full_legal_name="Paula Payee")
    wood = GuildFactory(name="Wood", guild_lead=lead.member)  # type: ignore[attr-defined]
    metal = GuildFactory(name="Metal", guild_lead=lead_teacher.member)  # type: ignore[attr-defined]
    GuildStaffMembershipFactory(guild=wood, member=staff.member)  # type: ignore[attr-defined]
    charge, wood_entry, metal_entry = _tab_charge(wood, metal)
    return {
        "lead": lead,
        "staff": staff,
        "teacher": teacher,
        "lead_teacher": lead_teacher,
        "wood": wood,
        "metal": metal,
        "wood_class": _registration(guild=wood, title="Wood Spoon Carving", instructors=[payee]),
        "metal_class": _registration(guild=metal, title="Metal Forging", instructors=[payee]),
        # The teacher co-teaches a Metal class someone else is paid for, and is the payee of a class with no guild.
        "co_taught": _registration(
            guild=metal,
            title="Metal Co Taught",
            instructors=[payee, teacher.member],  # type: ignore[attr-defined]
        ),
        "own_class": _registration(guild=None, title="Teacher Own Class", instructors=[teacher.member]),  # type: ignore[attr-defined]
        # The Metal lead teaches a Wood class: the union adds it to their Metal rows.
        "lead_teaches": _registration(
            guild=wood,
            title="Wood Lead Taught",
            instructors=[lead_teacher.member],  # type: ignore[attr-defined]
        ),
        "wood_orientation": _orientation(wood),
        "metal_orientation": _orientation(metal),
        "wood_reservation": _reservation(wood, "Wood Lathe"),
        "metal_reservation": _reservation(metal, "Metal Mill"),
        "wood_late_fee": _late_fee(wood, "Wood Bandsaw"),
        "metal_late_fee": _late_fee(metal, "Metal Welder"),
        "wood_cubby": _cubby_payment(wood),
        "metal_cubby": _cubby_payment(metal),
        "charge": charge,
        "wood_entry": wood_entry,
        "metal_entry": metal_entry,
    }


def _ledger_keys(client: Client, url: str = _PAYMENTS) -> set[tuple[str, int]]:
    response = client.get(url)
    assert response.status_code == 200
    return {(row.source_kind, row.source_pk) for row in response.context["ledger"].rows}


def _report_keys(client: Client, url: str = _REPORTS) -> set[tuple[str, int]]:
    response = client.get(url)
    assert response.status_code == 200
    return {(line.source_kind, line.source_pk) for line in response.context["reconciliation"].lines}


def _wood_payment_keys(w: dict[str, Any]) -> set[tuple[str, int]]:
    return {
        ("class", w["wood_class"].pk),
        ("class", w["lead_teaches"].pk),
        ("orientation", w["wood_orientation"].pk),
        ("reservation", w["wood_reservation"].pk),
        ("late_fee", w["wood_late_fee"].pk),
        ("cubby", w["wood_cubby"].pk),
        ("tab", w["wood_entry"].pk),
    }


def _wood_report_keys(w: dict[str, Any]) -> set[tuple[str, int]]:
    return {
        ("class", w["wood_class"].pk),
        ("class", w["lead_teaches"].pk),
        ("orientation", w["wood_orientation"].pk),
        ("reservation", w["wood_reservation"].pk),
        ("cubby", w["wood_cubby"].pk),
        ("tab", w["wood_entry"].pk),
    }


def _metal_report_keys(w: dict[str, Any]) -> set[tuple[str, int]]:
    return {
        ("class", w["metal_class"].pk),
        ("class", w["co_taught"].pk),
        ("orientation", w["metal_orientation"].pk),
        ("reservation", w["metal_reservation"].pk),
        ("cubby", w["metal_cubby"].pk),
        ("tab", w["metal_entry"].pk),
    }


def _recipients(client: Client) -> set[tuple[str, int | None]]:
    result = client.get(_REPORTS).context["reconciliation"]
    return {(kind.value, alloc.recipient_id) for kind, rows in result.groups.items() for alloc in rows}


def describe_a_guild_lead():
    def it_sees_only_its_guild_s_payments(client: Client, world: dict[str, Any]):
        _login(client, world["lead"])
        assert _ledger_keys(client) == _wood_payment_keys(world)

    def it_sees_each_purchase_of_its_products_never_the_whole_tab_charge(client: Client, world: dict[str, Any]):
        _login(client, world["lead"])
        rows = client.get(f"{_PAYMENTS}&source=tab").context["ledger"].rows
        assert [(row.item, row.amount_cents, row.status, row.tab_pk) for row in rows] == [
            ("Wood Glue Bottle", 2000, "paid", None)
        ]

    def it_sees_totals_of_its_rows_only(client: Client, world: dict[str, Any]):
        _login(client, world["lead"])
        ledger = client.get(_PAYMENTS).context["ledger"]
        assert ledger.collected_cents == sum(row.amount_cents for row in ledger.rows)
        assert ledger.collected_cents == 10000 + 10000 + 4000 + 3000 + world["wood_late_fee"].amount_cents + 2000 + 2000

    def it_lists_its_cubby_rent_and_counts_a_refunded_one_as_refunded(client: Client, world: dict[str, Any]):
        from membership.models import CubbyPayment

        CubbyPayment.objects.filter(pk=world["wood_cubby"].pk).update(stripe_refund_id="re_cubby")
        _login(client, world["lead"])
        response = client.get(f"{_PAYMENTS}&source=cubby")
        ledger = response.context["ledger"]
        assert [(row.source_pk, row.status, row.direct_refunded_cents, row.can_refund) for row in ledger.rows] == [
            (world["wood_cubby"].pk, "refunded", 2000, False)
        ]
        assert (ledger.collected_cents, ledger.refunded_cents, ledger.net_cents) == (2000, 2000, 0)
        body = response.content.decode()
        assert "− $20.00 refunded" in body
        assert 'data-payments-source="cubby"' in body

    def it_gets_payments_and_reports_and_no_other_tab(client: Client, world: dict[str, Any]):
        _login(client, world["lead"])
        response = client.get(_DASHBOARD)
        assert response.context["active_tab"] == "payments"
        assert response.context["dashboard_tabs"] == [("payments", "Payments"), ("reconciliation", "Reports")]
        for tab in ("overview", "open-tabs"):
            assert client.get(f"{_DASHBOARD}?tab={tab}").context["active_tab"] == "payments"
        for tab in ("settings", "stripe"):
            assert client.get(f"{_DASHBOARD}?tab={tab}").status_code == 403

    def it_keeps_the_tab_ledger_tabs_away_while_my_tab_is_on(client: Client, world: dict[str, Any]):
        from tests.features import turn_on

        turn_on("my_tab")
        _login(client, world["lead"])
        response = client.get(_DASHBOARD)
        assert response.context["active_tab"] == "payments"
        assert "overview" not in dict(response.context["dashboard_tabs"])

    def it_sees_its_guild_s_reports_and_share(client: Client, world: dict[str, Any]):
        _login(client, world["lead"])
        assert _report_keys(client) == _wood_report_keys(world)
        assert _recipients(client) == {("guild", world["wood"].pk)}
        result = client.get(_REPORTS).context["reconciliation"]
        tab_line = next(line for line in result.lines if line.source_kind == "tab")
        assert (tab_line.gross_cents, tab_line.viewer_share_cents) == (2000, 1600)
        assert result.viewer_share_total_cents == sum(line.viewer_share_cents for line in result.lines)
        cubby_line = next(line for line in result.lines if line.source_kind == "cubby")
        assert cubby_line.viewer_share_cents == 1700
        assert result.grand_total_cents == sum(line.net_cents for line in result.lines)

    def it_reads_reports_with_no_treasurer_controls(client: Client, world: dict[str, Any]):
        _login(client, world["lead"])
        body = client.get(_REPORTS).content.decode()
        assert "data-reports-scoped" in body
        assert "data-recon-viewer-share" in body
        for marker in (
            "recon-snapshot-modal",
            "data-recon-mark-all",
            "reconciliation/adjust/",
            "data-payouts-attention",
        ):
            assert marker not in body, marker

    def it_shows_no_refund_control(client: Client, world: dict[str, Any]):
        _login(client, world["lead"])
        response = client.get(_PAYMENTS)
        assert response.context["viewer_has_refund_authority"] is False
        assert "refund/form/" not in response.content.decode()

    def it_cannot_reach_another_guild_with_a_crafted_filter(client: Client, world: dict[str, Any]):
        _login(client, world["lead"])
        metal = world["metal"].pk
        for query in (f"&guild={metal}", f"&guilds={metal}", f"&source=orientation&guild={metal}"):
            keys = _ledger_keys(client, _PAYMENTS + query)
            assert not keys & {("orientation", world["metal_orientation"].pk), ("class", world["metal_class"].pk)}
        assert not _report_keys(client, f"{_REPORTS}&guild={metal}") & _metal_report_keys(world)
        assert _ledger_keys(client, f"{_PAYMENTS}&source=class&guild={metal}") == {
            ("class", world["wood_class"].pk),
            ("class", world["lead_teaches"].pk),
        }

    def it_exports_only_its_rows(client: Client, world: dict[str, Any]):
        _login(client, world["lead"])
        payments = b"".join(client.get(_PAYMENTS_CSV).streaming_content).decode()  # type: ignore[attr-defined]
        assert "Wood Spoon Carving" in payments
        assert "Metal Forging" not in payments
        assert "Metal Rod Bundle" not in payments
        report = list(csv.reader(io.StringIO(b"".join(client.get(_REPORTS_CSV).streaming_content).decode())))  # type: ignore[attr-defined]
        assert [row[1] for row in report[1:]] == ["Wood"]

    def it_serves_the_table_refreshes_and_print_scoped(client: Client, world: dict[str, Any]):
        _login(client, world["lead"])
        assert {(row.source_kind, row.source_pk) for row in client.get(_PAYMENTS_TABLE).context["ledger"].rows} == (
            _wood_payment_keys(world)
        )
        table = client.get(_REPORTS_TABLE)
        assert {(line.source_kind, line.source_pk) for line in table.context["reconciliation"].lines} == (
            _wood_report_keys(world)
        )
        printed = client.get(_REPORTS_PRINT)
        assert printed.status_code == 200
        body = printed.content.decode()
        assert "Your Report" in body
        assert "Metal" not in body

    def it_follows_the_retired_reports_url_to_its_reports(client: Client, world: dict[str, Any]):
        _login(client, world["lead"])
        response = client.get("/billing/admin/reports/", follow=True)
        assert response.status_code == 200
        assert response.context["active_tab"] == "reconciliation"


def describe_guild_staff():
    def it_sees_what_its_lead_sees(client: Client, world: dict[str, Any]):
        _login(client, world["staff"])
        assert _ledger_keys(client) == _wood_payment_keys(world)
        assert _report_keys(client) == _wood_report_keys(world)
        assert _recipients(client) == {("guild", world["wood"].pk)}


def describe_an_instructor():
    def it_sees_every_class_they_teach_and_nothing_else(client: Client, world: dict[str, Any]):
        _login(client, world["teacher"])
        taught = {("class", world["co_taught"].pk), ("class", world["own_class"].pk)}
        assert _ledger_keys(client) == taught
        assert _report_keys(client) == taught

    def it_sees_its_own_share_which_is_nothing_where_a_co_instructor_is_paid(client: Client, world: dict[str, Any]):
        member = _login(client, world["teacher"])
        result = client.get(_REPORTS).context["reconciliation"]
        shares = {line.source_pk: line.viewer_share_cents for line in result.lines}
        assert shares[world["co_taught"].pk] == 0
        assert shares[world["own_class"].pk] == 7000
        assert _recipients(client) == {("instructor", member.pk)}
        assert result.producers_total_cents == 7000
        assert result.viewer_share_total_cents == 7000

    def it_has_no_tab_rows_and_no_guild_payout_card(client: Client, world: dict[str, Any]):
        _login(client, world["teacher"])
        assert client.get(f"{_PAYMENTS}&source=tab").context["ledger"].rows == ()
        assert "Guild Payout (Tab Charges)" not in client.get(_REPORTS).content.decode()


def describe_a_guild_lead_who_teaches():
    def it_sees_the_union_of_its_guild_and_its_classes(client: Client, world: dict[str, Any]):
        member = _login(client, world["lead_teacher"])
        payments = _ledger_keys(client)
        assert ("class", world["lead_teaches"].pk) in payments
        assert ("class", world["metal_class"].pk) in payments
        assert ("class", world["wood_class"].pk) not in payments
        assert _report_keys(client) == _metal_report_keys(world) | {("class", world["lead_teaches"].pk)}
        assert _recipients(client) == {("guild", world["metal"].pk), ("instructor", member.pk)}

    def it_lists_a_class_in_its_guild_that_it_teaches_once(client: Client, world: dict[str, Any]):
        from classes.models import Registration

        member = _login(client, world["lead_teacher"])
        both = _registration(guild=world["metal"], title="Metal Lead Taught", instructors=[member])
        rows = [row for row in client.get(_PAYMENTS).context["ledger"].rows if row.source_kind == "class"]
        assert [row.source_pk for row in rows].count(both.pk) == 1
        assert Registration.objects.filter(pk=both.pk).exists()


def describe_a_billing_administrator():
    def it_sees_every_payment_as_before(client: Client, world: dict[str, Any]):
        member = _login(client, _user("biller"))
        member.admin_capabilities.create(capability=AdminCapability.Capability.BILLING_APPROVER)
        everything = build_payments_ledger(window=parse_window("", ""))
        assert _ledger_keys(client) == {(row.source_kind, row.source_pk) for row in everything.rows}
        assert ("tab", world["charge"].pk) in _ledger_keys(client)
        assert {("cubby", world["wood_cubby"].pk), ("cubby", world["metal_cubby"].pk)} <= _ledger_keys(client)
        assert client.get(_PAYMENTS).context["payments_scoped"] is False

    def it_still_has_no_reconciliation_when_it_runs_nothing(client: Client, world: dict[str, Any]):
        member = _login(client, _user("biller2"))
        member.admin_capabilities.create(capability=AdminCapability.Capability.BILLING_APPROVER)
        assert client.get(_REPORTS).status_code == 403
        assert "reconciliation" not in dict(client.get(_DASHBOARD).context["dashboard_tabs"])

    def it_gets_its_own_guild_s_reports_when_it_leads_one(client: Client, world: dict[str, Any]):
        member = _login(client, world["lead"])
        member.admin_capabilities.create(capability=AdminCapability.Capability.BILLING_APPROVER)
        assert client.get(_PAYMENTS).context["payments_scoped"] is False
        assert _report_keys(client) == _wood_report_keys(world)


def describe_an_admin():
    def it_sees_every_payment_and_the_full_reconciliation(client: Client, world: dict[str, Any]):
        User.objects.create_superuser(username="boss", password="pass", email="boss@example.com")
        client.login(username="boss", password="pass")
        everything = build_payments_ledger(window=parse_window("", ""), viewer_is_admin=True)
        assert _ledger_keys(client) == {(row.source_kind, row.source_pk) for row in everything.rows}
        full = build_reconciliation(window=parse_window("", ""))
        assert _report_keys(client) == {(line.source_kind, line.source_pk) for line in full.lines}
        response = client.get(_REPORTS)
        assert response.context["reconciliation_scoped"] is False
        assert response.context["reconciliation"].groups[RecipientKind.PL]
        assert dict(response.context["dashboard_tabs"])["reconciliation"] == "Reconciliation"
        assert "recon-snapshot-modal" in response.content.decode()


def describe_a_plain_member():
    @pytest.mark.parametrize(
        "url",
        [_DASHBOARD, _PAYMENTS, _REPORTS, _PAYMENTS_TABLE, _PAYMENTS_CSV, _REPORTS_TABLE, _REPORTS_CSV, _REPORTS_PRINT],
    )
    def it_is_refused_every_page(client: Client, world: dict[str, Any], url: str):
        _login(client, _user("plain"))
        assert client.get(url).status_code == 403

    def it_is_refused_the_retired_reports_url(client: Client, world: dict[str, Any]):
        _login(client, _user("plain2"))
        assert client.get("/billing/admin/reports/").status_code == 403
        assert client.get("/billing/admin/reports/export/csv/").status_code == 403


def describe_money_changing_controls():
    """A scoped viewer may look, never move money: every such endpoint answers 403."""

    def _urls(w: dict[str, Any]) -> list[str]:
        reg, booking = w["wood_class"].pk, w["wood_orientation"].pk
        reservation, fee = w["wood_reservation"].pk, w["wood_late_fee"].pk
        return [
            f"/classes/admin/registrations/{reg}/refund/",
            f"/classes/admin/registrations/{reg}/refund/form/",
            f"/billing/admin/orientations/{booking}/refund/",
            f"/billing/admin/orientations/{booking}/refund/form/",
            f"/billing/admin/reservations/{reservation}/refund/",
            f"/billing/admin/reservations/{reservation}/refund/form/",
            f"/billing/admin/late-fees/{fee}/refund/",
            f"/billing/admin/late-fees/{fee}/refund/form/",
            "/billing/admin/refunds/1/retry/",
            f"/billing/admin/retry-charge/{w['charge'].pk}/",
            f"/billing/admin/tab/{w['charge'].tab_id}/detail/",
            "/billing/admin/save-settings/",
            "/billing/admin/reconciliation/settings/save/",
            f"/billing/admin/reconciliation/adjust/class/{reg}/form/",
            f"/billing/admin/reconciliation/adjust/class/{reg}/",
            f"/billing/admin/reconciliation/clear/class/{reg}/",
            f"/billing/admin/reconciliation/paid-by-hand/class/{reg}/",
            f"/billing/admin/reconciliation/paid-by-hand/class/{reg}/undo/",
            "/billing/admin/reconciliation/snapshots/take/",
            "/billing/admin/connect-platform/save/",
        ]

    @pytest.mark.parametrize("persona", ["lead", "staff", "teacher", "lead_teacher"])
    def it_refuses_every_one_to_a_scoped_viewer(client: Client, world: dict[str, Any], persona: str):
        _login(client, world[persona])
        for url in _urls(world):
            assert client.post(url).status_code == 403, url


def describe_payments_scope():
    def it_refuses_an_unknown_source(world: dict[str, Any]):
        with pytest.raises(ValueError, match="Unknown payment source"):
            PaymentsScope(everything=True).restrict("gift_card", LateCancellationFee.objects.all())

    def it_matches_nothing_for_an_empty_scope(world: dict[str, Any]):
        from classes.models import Registration

        scope = PaymentsScope(everything=False)
        assert scope.is_empty
        assert not scope.restrict("class", Registration.objects.all()).exists()
        assert not scope.restrict("cubby", world["wood_cubby"].__class__.objects.all()).exists()

    def it_leaves_an_unscoped_reconciliation_alone(world: dict[str, Any]):
        result = build_reconciliation(window=parse_window("", ""))
        assert PaymentsScope(everything=True).scope_reconciliation(result) is result

    def it_counts_omitted_purchases_as_no_share(world: dict[str, Any]):
        from billing.models import TransactionAdjustment

        TransactionAdjustment.objects.create(source_kind="tab", source_pk=world["charge"].pk, is_omitted=True)
        scope = PaymentsScope(everything=False, guild_ids=frozenset({world["wood"].pk}))
        result = scope.scope_reconciliation(build_reconciliation(window=parse_window("", "")))
        tab_line = next(line for line in result.lines if line.source_kind == "tab")
        assert (tab_line.omitted, tab_line.viewer_share_cents) == (True, 0)
        assert result.omitted_count == 1

    def it_has_no_tab_lines_in_a_window_with_no_charges(world: dict[str, Any]):
        from datetime import date

        from billing.payments_panel import PanelWindow

        scope = PaymentsScope(everything=False, guild_ids=frozenset({world["wood"].pk}))
        old = PanelWindow(start=date(2020, 1, 1), end=date(2020, 1, 31))
        assert scope.scope_reconciliation(build_reconciliation(window=old)).lines == []
