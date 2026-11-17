"""The monthly Kiln Use Overview for the kiln crew (#1228): the figures, the email and the command."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, timedelta
from decimal import Decimal
from io import StringIO
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from django.core import mail
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from core.events.registry import KILN_USE_REPORT_MONTHLY, Recipients, get_event
from core.models import Notification
from kiln.models import KilnFiring, KilnTicket
from kiln.services import (
    COMMERCIAL_GLAZE,
    NO_CLAY,
    OTHER_CLAY,
    SELF_MADE_GLAZE,
    KilnUseReport,
    cubic_inches_label,
    kiln_use_in_app_body,
    kiln_use_report,
    kiln_use_subject,
)
from membership.models import Guild, Member
from membership.wiki_guild import previous_month_window
from tests.kiln.conftest import set_kiln_open
from tests.kiln.factories import ClayOptionFactory, GlazeOptionFactory, KilnTicketFactory
from tests.membership.factories import GuildFactory, GuildStaffMembershipFactory, MemberFactory

pytestmark = pytest.mark.django_db

PACIFIC = ZoneInfo("America/Los_Angeles")
# January 2026, the month a run on 1 February 2026 reports. Pacific Standard Time all month,
# so 4 PM Pacific on the 31st is already 1 February in UTC.
JANUARY = previous_month_window(date(2026, 2, 1))
MID_JANUARY = datetime(2026, 1, 15, 12, 0, tzinfo=PACIFIC)

_next_number = iter(range(1, 10_000))


def _firing(firing_type: str = "bisque", *, unloaded_at: datetime | None = MID_JANUARY) -> KilnFiring:
    """A firing the crew loaded and, unless ``unloaded_at`` is None, unloaded at that moment."""
    loader = MemberFactory()
    return KilnFiring.objects.create(
        firing_type=firing_type,
        number=next(_next_number),
        loaded_by=loader,
        unloaded_by=loader if unloaded_at is not None else None,
        unloaded_at=unloaded_at,
    )


def _fired(
    firing: KilnFiring,
    *,
    maker: Member | None = None,
    size: tuple[str, str, str] | None = ("1", "1", "1"),
    quantity: int | None = None,
    **answers: Any,
) -> KilnTicket:
    """A ticket that came out of ``firing``, fired at the firing's unload like ``unload_kiln`` stamps it."""
    height, width, length = (Decimal(d) for d in size) if size else (None, None, None)
    return KilnTicketFactory(
        maker=maker or MemberFactory(),
        status=KilnTicket.Status.FIRED,
        firing_type=firing.firing_type,
        firing=firing,
        fired_at=firing.unloaded_at,
        height_in=height,
        width_in=width,
        length_in=length,
        quantity=quantity,
        **answers,
    )


def _january() -> KilnUseReport:
    report = kiln_use_report(*JANUARY)
    assert report is not None
    return report


def _uses(rows: tuple[Any, ...]) -> list[tuple[str, Decimal]]:
    return [(row.name, row.cubic_inches) for row in rows]


def describe_kiln_ticket_kiln_use():
    def it_is_height_times_width_times_length():
        assert KilnTicket(height_in=Decimal("4"), width_in=Decimal("6"), length_in=Decimal("6.5")).kiln_use == Decimal(
            "156"
        )

    def it_multiplies_a_set_by_its_pieces():
        ticket = KilnTicket(height_in=Decimal("2"), width_in=Decimal("3"), length_in=Decimal("4"), quantity=5)
        assert ticket.kiln_use == Decimal("120")

    @pytest.mark.parametrize("blank", ["height_in", "width_in", "length_in"])
    def it_is_None_while_any_size_is_blank(blank):
        sizes = {"height_in": Decimal("2"), "width_in": Decimal("3"), "length_in": Decimal("4"), blank: None}
        assert KilnTicket(quantity=3, **sizes).kiln_use is None


def describe_cubic_inches_label():
    def it_rounds_to_whole_cubic_inches_with_separators():
        assert cubic_inches_label(Decimal("1535.5")) == "1,536"
        assert cubic_inches_label(Decimal("12.49")) == "12"


def describe_kiln_use_report():
    def describe_the_month():
        def it_is_None_when_no_firing_was_unloaded_in_it():
            _firing(unloaded_at=None)  # still in the kiln
            _firing(unloaded_at=datetime(2026, 2, 3, 9, 0, tzinfo=PACIFIC))
            assert kiln_use_report(*JANUARY) is None

        def it_names_the_month_and_the_days_it_covers():
            _firing()
            report = _january()
            assert report.month_label == "January 2026"
            assert report.dates_label == "January 1 to January 31, 2026"

        def it_counts_a_firing_unloaded_after_4_PM_Pacific_on_the_last_day_in_that_month():
            late = datetime(2026, 1, 31, 16, 30, tzinfo=PACIFIC)
            assert late.astimezone(ZoneInfo("UTC")).date() == date(2026, 2, 1)
            _fired(_firing("glaze", unloaded_at=late), quantity=3)
            report = _january()
            assert (report.glaze_runs, report.pieces_fired) == (1, 3)

            february = kiln_use_report(*previous_month_window(date(2026, 3, 1)))
            assert february is None

        def it_leaves_out_a_firing_unloaded_just_after_midnight_Pacific_on_the_1st():
            _firing()
            _fired(_firing(unloaded_at=datetime(2026, 2, 1, 0, 30, tzinfo=PACIFIC)), quantity=4)
            report = _january()
            assert (report.bisque_runs, report.pieces_fired) == (1, 0)

    def describe_total_kiln_use():
        def it_counts_bisque_runs_glaze_runs_and_pieces_fired():
            first, second, glaze = _firing("bisque"), _firing("bisque"), _firing("glaze")
            _fired(first, quantity=4)
            _fired(second)
            _fired(glaze, quantity=2)
            report = _january()
            assert (report.bisque_runs, report.glaze_runs, report.pieces_fired) == (2, 1, 7)

        def it_leaves_out_a_piece_sent_back_to_the_queue():
            firing = _firing()
            _fired(firing, quantity=2)
            KilnTicketFactory(status=KilnTicket.Status.SUBMITTED, firing_type="bisque", quantity=5)
            assert _january().pieces_fired == 2

        def it_leaves_out_a_ticket_deleted_before_the_run():
            firing = _firing()
            _fired(firing, quantity=2)
            _fired(firing, quantity=5).delete()
            report = _january()
            assert (report.bisque_runs, report.pieces_fired) == (1, 2)

    def describe_per_member():
        def it_lists_each_maker_with_pieces_and_cubic_inches_largest_first():
            small, large = MemberFactory(full_legal_name="Ana Small"), MemberFactory(full_legal_name="Bo Large")
            firing = _firing()
            _fired(firing, maker=small, size=("2", "2", "2"), quantity=3)
            _fired(firing, maker=large, size=("10", "8", "8"))
            _fired(firing, maker=small, size=("1", "1", "1"))
            rows = _january().makers
            assert [(row.name, row.pieces, row.cubic_inches) for row in rows] == [
                ("Bo Large", 1, Decimal("640")),
                ("Ana Small", 4, Decimal("25")),
            ]

        def it_breaks_a_tie_on_pieces_then_name():
            firing = _firing()
            _fired(firing, maker=MemberFactory(full_legal_name="Cy"), size=("2", "1", "1"))
            _fired(firing, maker=MemberFactory(full_legal_name="Ari"), size=("2", "1", "1"))
            _fired(firing, maker=MemberFactory(full_legal_name="Zed"), size=("1", "1", "1"), quantity=2)
            assert [row.name for row in _january().makers] == ["Zed", "Ari", "Cy"]

        def it_leaves_out_a_member_with_no_pieces_fired_that_month():
            _fired(_firing(), maker=MemberFactory(full_legal_name="In January"))
            _fired(
                _firing(unloaded_at=datetime(2025, 12, 20, 12, 0, tzinfo=PACIFIC)),
                maker=MemberFactory(full_legal_name="In December"),
            )
            assert [row.name for row in _january().makers] == ["In January"]

        def it_counts_the_pieces_of_a_ticket_with_a_blank_size_but_no_volume():
            maker = MemberFactory(full_legal_name="Dee")
            firing = _firing()
            _fired(firing, maker=maker, size=("2", "2", "2"))
            _fired(firing, maker=maker, size=None, quantity=4)
            report = _january()
            assert [(row.pieces, row.cubic_inches) for row in report.makers] == [(5, Decimal("8"))]
            assert report.unmeasured_tickets == 1

    def describe_clay_use():
        def it_sums_each_list_clay_and_puts_every_other_clay_under_Other_last():
            buff, porcelain = ClayOptionFactory(name="Buff"), ClayOptionFactory(name="Porcelain")
            firing = _firing()
            _fired(firing, size=("2", "2", "2"), clay=buff)
            _fired(firing, size=("3", "3", "3"), clay=porcelain)
            _fired(firing, size=("2", "2", "2"), clay=buff, quantity=2)
            _fired(firing, size=("10", "10", "10"), clay_other=True, clay_other_name="B-Mix")
            _fired(firing, size=("1", "1", "1"), clay_other=True, clay_other_name="Red Art")
            _fired(firing, size=None, clay=porcelain)
            assert _uses(_january().clays) == [
                ("Porcelain", Decimal("27")),
                ("Buff", Decimal("24")),
                (OTHER_CLAY, Decimal("1001")),
            ]

        def it_puts_a_ticket_with_no_clay_under_not_given():
            _fired(_firing(), size=("1", "2", "3"))
            assert _uses(_january().clays) == [(NO_CLAY, Decimal("6"))]

    def describe_glaze_use():
        def it_counts_a_pieces_whole_kiln_use_under_each_glaze_on_it():
            celadon, tenmoku = GlazeOptionFactory(name="Celadon"), GlazeOptionFactory(name="Tenmoku")
            firing = _firing("glaze")
            both = _fired(firing, size=("2", "2", "2"), quantity=2, glaze_studio=True, glaze_commercial=True)
            both.studio_glazes.set([celadon, tenmoku])
            one = _fired(firing, size=("3", "3", "3"), glaze_studio=True)
            one.studio_glazes.set([tenmoku])
            _fired(firing, size=("1", "1", "5"), glaze_self_made=True)
            assert _uses(_january().glazes) == [
                ("Tenmoku", Decimal("43")),
                ("Celadon", Decimal("16")),
                (COMMERCIAL_GLAZE, Decimal("16")),
                (SELF_MADE_GLAZE, Decimal("5")),
            ]

        def it_counts_no_glaze_for_a_bisque_piece():
            _fired(_firing("bisque"), size=("2", "2", "2"), glaze_commercial=True)
            assert _january().glazes == ()

        def it_skips_studio_glazes_left_on_a_piece_whose_studio_box_is_unticked():
            ticket = _fired(_firing("glaze"), size=("2", "2", "2"), glaze_self_made=True)
            ticket.studio_glazes.set([GlazeOptionFactory(name="Leftover")])
            assert _uses(_january().glazes) == [(SELF_MADE_GLAZE, Decimal("8"))]

    def it_reads_the_month_in_a_fixed_number_of_queries(django_assert_max_num_queries):
        firing = _firing("glaze")
        for _ in range(5):
            ticket = _fired(firing, maker=MemberFactory(), clay=ClayOptionFactory(), glaze_studio=True)
            ticket.studio_glazes.set([GlazeOptionFactory()])
        with django_assert_max_num_queries(5):
            kiln_use_report(*JANUARY)


def describe_the_copy():
    def it_names_the_month_in_the_subject():
        _firing()
        assert kiln_use_subject(_january()) == "Kiln Use Overview: January 2026"

    def it_writes_the_bell_line_with_plurals():
        _firing("bisque")
        _fired(_firing("glaze"))
        assert kiln_use_in_app_body(_january()) == "January 2026: 1 bisque run, 1 glaze run, 1 piece fired."

        _firing("bisque")
        _fired(_firing("glaze"), quantity=2)
        _firing("glaze")
        assert kiln_use_in_app_body(_january()) == "January 2026: 2 bisque runs, 3 glaze runs, 3 pieces fired."

    def it_is_a_kiln_crew_event():
        event = get_event(KILN_USE_REPORT_MONTHLY)
        assert event.recipient is Recipients.KILN_CREW
        assert event.category == "Kiln tickets"


def describe_send_kiln_use_report():
    @pytest.fixture
    def on(monkeypatch) -> Callable[[date], None]:
        """Make ``timezone.localdate()`` (no argument) answer ``day``; a date of a datetime still converts."""
        real = timezone.localdate

        def _set(day: date) -> None:
            monkeypatch.setattr(
                timezone, "localdate", lambda value=None, tz=None: day if value is None else real(value, tz)
            )

        return _set

    @pytest.fixture
    def lead(make_member, kiln_guild: Guild) -> Member:
        member = make_member()
        kiln_guild.guild_lead = member
        kiln_guild.save(update_fields=["guild_lead"])
        return member

    @pytest.fixture
    def january(lead, crew) -> KilnFiring:
        firing = _firing("glaze")
        _fired(firing, maker=lead, size=("4", "6", "6"), quantity=2, clay=ClayOptionFactory(name="Buff"))
        _fired(firing, maker=crew, size=None, clay_other=True, clay_other_name="B-Mix", glaze_commercial=True)
        return firing

    def _run(*args: str) -> str:
        out = StringIO()
        call_command("send_kiln_use_report", *args, stdout=out)
        return out.getvalue()

    def _addressed() -> set[str]:
        return {address for message in mail.outbox for address in message.to}

    def it_sends_nothing_on_a_day_other_than_the_1st(on, january):
        on(date(2026, 2, 2))
        assert "Not the 1st" in _run()
        assert mail.outbox == []

    def it_sends_last_months_report_on_the_1st(on, january, lead):
        on(date(2026, 2, 1))
        assert "Sent the kiln use report for January 2026." in _run()
        message = next(m for m in mail.outbox if lead.primary_email in m.to)
        assert message.subject.endswith("Kiln Use Overview: January 2026")

    def it_sends_last_month_on_any_day_with_force(lead, crew):
        month_start, _end = previous_month_window()
        _fired(_firing(unloaded_at=month_start + timedelta(days=2)), maker=crew)
        _run("--force")
        assert lead.primary_email in _addressed()

    def it_reaches_the_kiln_crew_and_nobody_else(on, january, lead, crew, make_member):
        admin = make_member(fog_role=Member.FogRole.ADMIN)
        other_lead = make_member()
        GuildFactory(name="Wood Guild", slug="wood-guild", guild_lead=other_lead)
        GuildStaffMembershipFactory(guild=GuildFactory(name="Print", slug="print"), member=make_member())
        on(date(2026, 2, 1))
        _run()
        assert _addressed() == {lead.primary_email, crew.primary_email}
        bells = set(Notification.objects.filter(trigger=KILN_USE_REPORT_MONTHLY).values_list("user_id", flat=True))
        assert bells == {lead.user_id, crew.user_id}
        assert admin.user_id not in bells

    def it_delivers_one_email_per_person_when_run_twice(on, january):
        on(date(2026, 2, 1))
        _run()
        sent = len(mail.outbox)
        assert sent == 2
        _run("--force")
        assert len(mail.outbox) == sent
        assert Notification.objects.filter(trigger=KILN_USE_REPORT_MONTHLY).count() == 2

    def it_says_so_and_sends_nothing_for_a_month_with_no_firings(on, lead, crew):
        _firing(unloaded_at=datetime(2026, 2, 3, 9, 0, tzinfo=PACIFIC))
        on(date(2026, 2, 1))
        assert "No firings were unloaded in January 2026, so nothing was sent." in _run()
        assert mail.outbox == []

    def it_sends_while_kiln_tickets_are_switched_off(on, january, lead):
        set_kiln_open(False)
        on(date(2026, 2, 1))
        _run()
        assert lead.primary_email in _addressed()

    def it_fails_loudly_when_the_send_raises(on, january, monkeypatch):
        def _explode(report: KilnUseReport) -> None:
            raise RuntimeError("mail server down")

        # Patched where the command looks it up: it imported the name at load time.
        monkeypatch.setattr("kiln.management.commands.send_kiln_use_report.send_kiln_use_report", _explode)
        on(date(2026, 2, 1))
        with pytest.raises(CommandError, match="January 2026 failed: mail server down"):
            _run()

    def it_writes_both_bodies_with_the_figures_and_the_kiln_log_link(on, january, lead):
        on(date(2026, 2, 1))
        _run()
        message = next(m for m in mail.outbox if lead.primary_email in m.to)
        html = next(body for body, _mime in message.alternatives)
        for body in (message.body, html):
            assert "Past Lives Ceramics Guild" in body
            assert "January 1 to January 31, 2026" in body
            assert "/kiln/log/" in body
            assert "?tab=kiln" in body
            assert "1 ticket had a size missing" in body
            assert "A piece with several glazes counts its whole kiln use under each one." in body
            assert "288" in body  # 4 x 6 x 6 x 2 under the lead and under Buff
        assert f"{lead.display_name}: 2 pieces, 288 cu in" in message.body
        assert "Other: 0 cu in" in message.body
        assert "Commercial: 0 cu in" in message.body
