"""BDD specs for the roster's status filter: what the Registrations tab lists, and what the lists count.

Every class built here gets its own capacity and its own status mix. A single shared
offering fixture would let one arrangement of seats stand in for all of them, which is
exactly how a last-seat bug hides.
"""

from __future__ import annotations

import re

import pytest
from django.urls import reverse

from classes.factories import (
    ClassOfferingFactory,
    ClassSessionFactory,
    InstructorFactory,
    RegistrationFactory,
    UserFactory,
)
from classes.models import ClassOffering, Registration

pytestmark = pytest.mark.django_db

HTMX = {"HX-Request": "true"}

# What one extra class on the cross-class Registrations page is allowed to cost. Measured:
# 1.25 queries per class as written, 3.25 when the header read offering.seats_taken and
# offering.waitlisted_count in the loop instead. 2 sits between the two, so this fails if
# either per-class COUNT comes back without tracking an unrelated prefetch to the decimal.
PER_CLASS_QUERY_BUDGET = 2


def _login_instructor(client, username: str, slug: str):
    """Log in a teaching member and return them, so their own classes are reachable."""
    user = UserFactory(username=username)
    member = InstructorFactory(user=user, instructor_slug=slug)
    client.force_login(user)
    return member


def _seats(offering: ClassOffering, status: str, count: int, **kwargs) -> list[Registration]:
    """``count`` registrations on ``offering`` at ``status``, each with its own address."""
    return [RegistrationFactory(class_offering=offering, status=status, **kwargs) for _ in range(count)]


def _row_ids(html: str) -> list[int]:
    """The registration pks the roster table actually rendered, in order."""
    return [int(pk) for pk in re.findall(r'id="reg-row-(\d+)"', html)]


def _row_markup(html: str, pk: int) -> str:
    """One roster row's ``<tr>``, for assertions about how it is marked."""
    start = html.index(f'id="reg-row-{pk}"')
    return html[start : html.index("</tr>", start)]


def _roster_empty_state(html: str) -> str:
    """The roster table's empty-state line only.

    Scoped rather than asserted against the whole page on purpose: the in-app changelog
    renders into every hub page's context, so an entry that quotes this copy back ("a class
    ... said No registrations yet") satisfies a page-wide substring assertion and the spec
    passes on the wrong element.
    """
    at = html.index("data-roster-empty")
    start = html.index(">", at) + 1
    return html[start : html.index("</div>", start)].strip()


def _group_header(html: str, title: str) -> str:
    """The cross-class page's header row for one class, so count assertions stay scoped."""
    start = html.index(f"<strong>{title}</strong>")
    return html[start : html.index("</div>", start)]


def describe_roster_row_response():
    """The htmx row re-render after an action, which re-fetches the row it just changed.

    Putting the status filter inside the annotated base turns every one of these into a
    ``Registration.DoesNotExist`` 500, because the row has just left the filtered set.
    Cancelling is the most-used action on this surface, so that is a production outage.
    """

    def it_renders_the_cancelled_row_after_a_cancel(admin_user, client):
        client.force_login(admin_user)
        offering = ClassOfferingFactory(capacity=3)
        reg = RegistrationFactory(
            class_offering=offering,
            status=Registration.Status.CONFIRMED,
            first_name="Rosalind",
            last_name="Quaye",
        )
        response = client.post(
            reverse("classes:registration_remove", args=[reg.pk]),
            {"row": "reg", "reason": "moved away"},
            headers=HTMX,
        )
        assert response.status_code == 200
        body = response.content.decode()
        assert _row_ids(body) == [reg.pk]
        assert "Rosalind" in body
        reg.refresh_from_db()
        assert reg.status == Registration.Status.CANCELLED

    def it_renders_the_refunded_row_when_an_action_is_refused(admin_user, client):
        client.force_login(admin_user)
        offering = ClassOfferingFactory(capacity=9)
        reg = RegistrationFactory(
            class_offering=offering,
            status=Registration.Status.REFUNDED,
            first_name="Ambrose",
            last_name="Ito",
            payment_due_cents=7500,
            amount_paid_cents=7500,
        )
        response = client.post(reverse("classes:registration_remove", args=[reg.pk]), {"row": "reg"}, headers=HTMX)
        assert response.status_code == 200
        body = response.content.decode()
        assert _row_ids(body) == [reg.pk]
        assert "Ambrose" in body

    def it_leaves_the_table_answering_after_the_row_drops_out(admin_user, client):
        """The refund-done refresh target still renders, minus the row that just left."""
        client.force_login(admin_user)
        offering = ClassOfferingFactory(capacity=5)
        staying = _seats(offering, Registration.Status.CONFIRMED, 2)
        going = RegistrationFactory(class_offering=offering, status=Registration.Status.CONFIRMED)
        client.post(reverse("classes:registration_remove", args=[going.pk]), {"row": "reg"}, headers=HTMX)
        response = client.get(reverse("classes:teach_class_registrations_table", args=[offering.pk]))
        assert response.status_code == 200
        assert sorted(_row_ids(response.content.decode())) == sorted(reg.pk for reg in staying)


def describe_the_registrations_tab():
    def describe_a_class_with_cancelled_and_waitlisted_rows():
        """Twelve seats, ten confirmed, two cancelled, three waitlisted — the spec's own class."""

        @pytest.fixture
        def crowded(client):
            member = _login_instructor(client, "roster-crowded@example.com", "roster-crowded")
            offering = ClassOfferingFactory(instructor=member, capacity=12)
            confirmed = _seats(offering, Registration.Status.CONFIRMED, 10)
            cancelled = _seats(offering, Registration.Status.CANCELLED, 2)
            waitlisted = _seats(offering, Registration.Status.WAITLISTED, 3)
            return offering, confirmed, cancelled, waitlisted

        def it_lists_only_the_people_holding_a_seat(crowded, client):
            offering, confirmed, _cancelled, _waitlisted = crowded
            response = client.get(reverse("classes:teach_class_registrations", args=[offering.pk]))
            assert response.status_code == 200
            assert sorted(_row_ids(response.content.decode())) == sorted(reg.pk for reg in confirmed)

        def it_shows_the_cancelled_rows_when_asked(crowded, client):
            offering, confirmed, cancelled, _waitlisted = crowded
            url = reverse("classes:teach_class_registrations", args=[offering.pk])
            response = client.get(url, {"show_cancelled": "1"})
            listed = _row_ids(response.content.decode())
            assert len(listed) == 12
            assert sorted(listed) == sorted(reg.pk for reg in confirmed + cancelled)

        def it_keeps_the_cancelled_rows_visually_marked(crowded, client):
            offering, _confirmed, cancelled, _waitlisted = crowded
            url = reverse("classes:teach_class_registrations", args=[offering.pk])
            body = client.get(url, {"show_cancelled": "1"}).content.decode()
            for reg in cancelled:
                assert "opacity:0.5" in _row_markup(body, reg.pk)

        def it_leaves_the_waitlist_to_its_own_tab(crowded, client):
            offering, _confirmed, _cancelled, waitlisted = crowded
            url = reverse("classes:teach_class_registrations", args=[offering.pk])
            shown = _row_ids(client.get(url, {"show_cancelled": "1"}).content.decode())
            assert not [reg.pk for reg in waitlisted if reg.pk in shown]
            waitlist = client.get(reverse("classes:teach_class_waitlist", args=[offering.pk]))
            waitlist_body = waitlist.content.decode()
            for reg in waitlisted:
                assert f'id="wl-row-{reg.pk}"' in waitlist_body

        def it_names_the_number_of_rows_it_is_hiding(crowded, client):
            offering, _confirmed, _cancelled, _waitlisted = crowded
            response = client.get(reverse("classes:teach_class_registrations", args=[offering.pk]))
            assert "Show 2 cancelled" in response.content.decode()

        def it_offers_the_way_back_once_they_are_shown(crowded, client):
            offering, _confirmed, _cancelled, _waitlisted = crowded
            url = reverse("classes:teach_class_registrations", args=[offering.pk])
            body = client.get(url, {"show_cancelled": "1"}).content.decode()
            assert "Hide 2 cancelled" in body
            assert "Show 2 cancelled" not in body

    def it_offers_no_toggle_when_nothing_is_hidden(client):
        member = _login_instructor(client, "roster-clean@example.com", "roster-clean")
        offering = ClassOfferingFactory(instructor=member, capacity=4)
        _seats(offering, Registration.Status.CONFIRMED, 4)
        response = client.get(reverse("classes:teach_class_registrations", args=[offering.pk]))
        assert "pl-roster-filter" not in response.content.decode()

    def it_still_offers_the_toggle_when_every_row_is_cancelled(client):
        """The empty-looking roster is the one that most needs to say who is missing."""
        member = _login_instructor(client, "roster-empty@example.com", "roster-empty")
        offering = ClassOfferingFactory(instructor=member, capacity=5)
        cancelled = _seats(offering, Registration.Status.CANCELLED, 2)
        refunded = _seats(offering, Registration.Status.REFUNDED, 1)
        url = reverse("classes:teach_class_registrations", args=[offering.pk])
        body = client.get(url).content.decode()
        assert _row_ids(body) == []
        assert "Show 3 cancelled" in body
        assert _roster_empty_state(body) == "Nobody is holding a seat right now."
        opened = _row_ids(client.get(url, {"show_cancelled": "1"}).content.decode())
        assert sorted(opened) == sorted(reg.pk for reg in cancelled + refunded)

    def it_ignores_a_junk_toggle_value(client):
        member = _login_instructor(client, "roster-junk@example.com", "roster-junk")
        offering = ClassOfferingFactory(instructor=member, capacity=2)
        _seats(offering, Registration.Status.CONFIRMED, 1)
        _seats(offering, Registration.Status.CANCELLED, 1)
        url = reverse("classes:teach_class_registrations", args=[offering.pk])
        response = client.get(url, {"show_cancelled": "yes please"})
        assert len(_row_ids(response.content.decode())) == 1

    def it_carries_the_toggle_into_the_refund_refresh_url(client):
        """Otherwise a refund would quietly collapse an opened roster back to seat-holders."""
        member = _login_instructor(client, "roster-refresh@example.com", "roster-refresh")
        offering = ClassOfferingFactory(instructor=member, capacity=6)
        _seats(offering, Registration.Status.CONFIRMED, 2)
        _seats(offering, Registration.Status.CANCELLED, 1)
        url = reverse("classes:teach_class_registrations", args=[offering.pk])
        table_url = reverse("classes:teach_class_registrations_table", args=[offering.pk])
        assert f'hx-get="{table_url}"' in client.get(url).content.decode()
        opened = client.get(url, {"show_cancelled": "1"}).content.decode()
        assert f'hx-get="{table_url}?show_cancelled=1"' in opened

    def it_serves_the_widened_table_on_its_own_endpoint(client):
        member = _login_instructor(client, "roster-table@example.com", "roster-table")
        offering = ClassOfferingFactory(instructor=member, capacity=8)
        confirmed = _seats(offering, Registration.Status.CONFIRMED, 3)
        refunded = _seats(offering, Registration.Status.REFUNDED, 2)
        url = reverse("classes:teach_class_registrations_table", args=[offering.pk])
        assert sorted(_row_ids(client.get(url).content.decode())) == sorted(reg.pk for reg in confirmed)
        widened = _row_ids(client.get(url, {"show_cancelled": "1"}).content.decode())
        assert sorted(widened) == sorted(reg.pk for reg in confirmed + refunded)


def describe_the_tab_badge():
    def it_agrees_with_the_number_of_rows_on_the_tab(client):
        """A different mix again: four confirmed, one mid-payment, two gone, one queued."""
        member = _login_instructor(client, "badge-mix@example.com", "badge-mix")
        offering = ClassOfferingFactory(instructor=member, capacity=7)
        _seats(offering, Registration.Status.CONFIRMED, 4)
        _seats(offering, Registration.Status.PENDING, 1)
        _seats(offering, Registration.Status.CANCELLED, 2)
        _seats(offering, Registration.Status.WAITLISTED, 1)
        response = client.get(reverse("classes:teach_class_registrations", args=[offering.pk]))
        assert response.context["seat_taken_count"] == 5
        assert len(_row_ids(response.content.decode())) == 5
        assert "Registrations (5)" in response.content.decode()

    def it_reads_the_same_number_in_the_header(client):
        member = _login_instructor(client, "badge-header@example.com", "badge-header")
        offering = ClassOfferingFactory(instructor=member, capacity=10)
        _seats(offering, Registration.Status.CONFIRMED, 3)
        _seats(offering, Registration.Status.REFUNDED, 4)
        response = client.get(reverse("classes:teach_class_registrations", args=[offering.pk]))
        assert "3 / 10 registered" in response.content.decode()

    def it_does_not_count_the_waitlist_against_the_seats(client):
        member = _login_instructor(client, "badge-waitlist@example.com", "badge-waitlist")
        offering = ClassOfferingFactory(instructor=member, capacity=2)
        _seats(offering, Registration.Status.CONFIRMED, 2)
        _seats(offering, Registration.Status.WAITLISTED, 6)
        response = client.get(reverse("classes:teach_class_registrations", args=[offering.pk]))
        assert response.context["seat_taken_count"] == 2
        assert response.context["waitlist_count"] == 6


def describe_the_class_lists():
    def it_counts_only_seat_holders_on_the_instructor_list(client):
        member = _login_instructor(client, "list-teach@example.com", "list-teach")
        offering = ClassOfferingFactory(instructor=member, capacity=12)
        _seats(offering, Registration.Status.CONFIRMED, 10)
        _seats(offering, Registration.Status.CANCELLED, 2)
        _seats(offering, Registration.Status.WAITLISTED, 3)
        response = client.get(reverse("classes:teach_dashboard"))
        row = next(c for c in response.context["classes"] if c.pk == offering.pk)
        assert row.registration_count == 10
        assert "10/12" in response.content.decode()

    def it_counts_only_seat_holders_on_the_admin_list(admin_user, client):
        client.force_login(admin_user)
        offering = ClassOfferingFactory(capacity=12, status=ClassOffering.Status.PUBLISHED)
        _seats(offering, Registration.Status.CONFIRMED, 10)
        _seats(offering, Registration.Status.CANCELLED, 2)
        _seats(offering, Registration.Status.WAITLISTED, 3)
        response = client.get(reverse("classes:admin_classes"))
        row = next(c for c in response.context["page"] if c.pk == offering.pk)
        assert row.registration_count == 10
        assert "10/12" in response.content.decode()

    def it_agrees_with_spots_remaining(admin_user, client):
        """The whole point: the list and the register page cannot disagree about being full."""
        client.force_login(admin_user)
        offering = ClassOfferingFactory(capacity=5, status=ClassOffering.Status.PUBLISHED)
        _seats(offering, Registration.Status.CONFIRMED, 3)
        _seats(offering, Registration.Status.PENDING, 1)
        _seats(offering, Registration.Status.REFUNDED, 3)
        _seats(offering, Registration.Status.WAITLISTED, 2)
        response = client.get(reverse("classes:admin_classes"))
        row = next(c for c in response.context["page"] if c.pk == offering.pk)
        assert row.registration_count == 4
        assert offering.spots_remaining == 1
        assert row.registration_count + offering.spots_remaining == offering.capacity

    def it_is_not_inflated_by_the_sessions_join(admin_user, client):
        """Two sessions alongside a filtered Count is where a missing distinct=True shows up."""
        from datetime import timedelta

        from django.utils import timezone

        client.force_login(admin_user)
        offering = ClassOfferingFactory(capacity=4, status=ClassOffering.Status.PUBLISHED)
        ClassSessionFactory(class_offering=offering, starts_at=timezone.now())
        ClassSessionFactory(class_offering=offering, starts_at=timezone.now() + timedelta(days=1))
        _seats(offering, Registration.Status.CONFIRMED, 3)
        _seats(offering, Registration.Status.REFUNDED, 1)
        response = client.get(reverse("classes:admin_classes"))
        row = next(c for c in response.context["page"] if c.pk == offering.pk)
        assert row.registration_count == 3

    def it_counts_only_seat_holders_on_the_class_screen_query(client):
        from classes.views import _class_screen_offering

        member = _login_instructor(client, "screen-count@example.com", "screen-count")
        offering = ClassOfferingFactory(instructor=member, capacity=6)
        _seats(offering, Registration.Status.PENDING, 2)
        _seats(offering, Registration.Status.CANCELLED, 3)
        _seats(offering, Registration.Status.WAITLISTED, 1)
        assert _class_screen_offering(offering.pk).registration_count == 2


def describe_the_instructor_registrations_page():
    """The cross-class page lists waitlisted people on purpose: its checkboxes reach them.

    So its header cannot be one number. Every spec here carries a waitlisted row, because
    that is the only arrangement in which a header that rolls seats and the queue into one
    total can be told apart from one that names them separately.
    """

    def it_names_the_seats_and_the_queue_separately(client):
        member = _login_instructor(client, "page-count@example.com", "page-count")
        offering = ClassOfferingFactory(instructor=member, capacity=12, title="Blade Smithing")
        _seats(offering, Registration.Status.CONFIRMED, 10)
        _seats(offering, Registration.Status.CANCELLED, 2)
        _seats(offering, Registration.Status.WAITLISTED, 3)
        response = client.get(reverse("classes:teach_registrations"))
        group = next(g for g in response.context["class_groups"] if g["offering"].pk == offering.pk)
        assert group["seats_taken"] == 10
        assert group["waitlist_count"] == 3
        header = _group_header(response.content.decode(), "Blade Smithing")
        assert "10 registered, 3 waitlisted" in header
        # The rolled-up total every other surface disagrees with.
        assert "13" not in header

    def it_agrees_with_the_per_class_surfaces_on_the_same_class(client):
        """Acceptance criterion 1's class, read on this page instead of the per-class tab."""
        member = _login_instructor(client, "page-agree@example.com", "page-agree")
        offering = ClassOfferingFactory(instructor=member, capacity=12)
        _seats(offering, Registration.Status.CONFIRMED, 10)
        _seats(offering, Registration.Status.CANCELLED, 2)
        _seats(offering, Registration.Status.WAITLISTED, 3)
        page = client.get(reverse("classes:teach_registrations"))
        group = next(g for g in page.context["class_groups"] if g["offering"].pk == offering.pk)
        tab = client.get(reverse("classes:teach_class_registrations", args=[offering.pk]))
        assert group["seats_taken"] == tab.context["seat_taken_count"] == offering.seats_taken == 10
        assert group["waitlist_count"] == tab.context["waitlist_count"] == 3

    def it_does_not_spend_a_count_query_per_class(client, django_assert_max_num_queries):
        """The header's two numbers come off rows already fetched, not two COUNTs per class.

        This page loops over every class the member has ever taught, so reading
        ``offering.seats_taken`` and ``offering.waitlisted_count`` in the loop body is 2N
        round trips for numbers already in memory. The growth per class is what is asserted,
        not an absolute total: the loop legitimately spends queries on each class's own rows,
        and pinning the total would break every time an unrelated prefetch moved.
        """
        member = _login_instructor(client, "page-nplus1@example.com", "page-nplus1")
        url = reverse("classes:teach_registrations")

        def _add_class() -> None:
            offering = ClassOfferingFactory(instructor=member, capacity=12)
            _seats(offering, Registration.Status.CONFIRMED, 2)
            _seats(offering, Registration.Status.WAITLISTED, 1)
            _seats(offering, Registration.Status.CANCELLED, 1)

        def _cost() -> int:
            with django_assert_max_num_queries(500) as captured:
                client.get(url)
            return len(captured.captured_queries)

        # Built up rather than torn down: Registration.class_offering is PROTECT, so the
        # classes measured first cannot be deleted to measure a smaller set afterwards.
        _add_class()
        one_class = _cost()
        for _ in range(4):
            _add_class()
        per_class = (_cost() - one_class) / 4
        assert per_class <= PER_CLASS_QUERY_BUDGET, f"per class query cost is {per_class}"

    def it_keeps_listing_the_waitlisted_people_it_can_email(client):
        """Ruling: they stay on this page. The header stops implying they hold seats."""
        member = _login_instructor(client, "page-rows@example.com", "page-rows")
        offering = ClassOfferingFactory(instructor=member, capacity=4)
        confirmed = _seats(offering, Registration.Status.CONFIRMED, 1)
        waitlisted = _seats(offering, Registration.Status.WAITLISTED, 2)
        response = client.get(reverse("classes:teach_registrations"))
        group = next(g for g in response.context["class_groups"] if g["offering"].pk == offering.pk)
        listed = {reg.pk for reg in group["registrations"]}
        assert listed == {reg.pk for reg in confirmed + waitlisted}
        assert group["can_email_any"] is True

    def it_drops_the_waitlist_half_when_nobody_is_queued(client):
        member = _login_instructor(client, "page-noqueue@example.com", "page-noqueue")
        offering = ClassOfferingFactory(instructor=member, capacity=9, title="Cold Forging")
        _seats(offering, Registration.Status.CONFIRMED, 5)
        _seats(offering, Registration.Status.CANCELLED, 4)
        response = client.get(reverse("classes:teach_registrations"))
        group = next(g for g in response.context["class_groups"] if g["offering"].pk == offering.pk)
        assert group["seats_taken"] == 5
        assert group["waitlist_count"] == 0
        header = _group_header(response.content.decode(), "Cold Forging")
        assert "5 registered" in header
        assert "waitlisted" not in header

    def it_leaves_the_header_alone_when_the_cancelled_rows_are_shown(client):
        """Opening the toggle lists more rows. It does not give anyone a seat."""
        member = _login_instructor(client, "page-toggle@example.com", "page-toggle")
        offering = ClassOfferingFactory(instructor=member, capacity=3)
        _seats(offering, Registration.Status.CONFIRMED, 1)
        _seats(offering, Registration.Status.CANCELLED, 2)
        _seats(offering, Registration.Status.WAITLISTED, 1)
        response = client.get(reverse("classes:teach_registrations"), {"show_cancelled": "1"})
        group = next(g for g in response.context["class_groups"] if g["offering"].pk == offering.pk)
        assert group["seats_taken"] == 1
        assert group["waitlist_count"] == 1
        assert len(group["registrations"]) == 4
        body = response.content.decode()
        assert "1 registered, 1 waitlisted" in _group_header(body, offering.title)
        assert "Hide 2 cancelled" in body

    def it_names_the_hidden_rows_across_every_class(client):
        member = _login_instructor(client, "page-total@example.com", "page-total")
        first = ClassOfferingFactory(instructor=member, capacity=2)
        second = ClassOfferingFactory(instructor=member, capacity=7)
        _seats(first, Registration.Status.CANCELLED, 1)
        _seats(first, Registration.Status.WAITLISTED, 2)
        _seats(second, Registration.Status.REFUNDED, 3)
        _seats(second, Registration.Status.CONFIRMED, 2)
        response = client.get(reverse("classes:teach_registrations"))
        assert "Show 4 cancelled" in response.content.decode()


def describe_the_empty_roster():
    """A tab with no seat-holders still has to say what is actually there."""

    def it_says_how_many_are_waiting_rather_than_nothing(client):
        member = _login_instructor(client, "empty-waitlist@example.com", "empty-waitlist")
        offering = ClassOfferingFactory(instructor=member, capacity=8)
        _seats(offering, Registration.Status.WAITLISTED, 6)
        body = client.get(reverse("classes:teach_class_registrations", args=[offering.pk])).content.decode()
        assert _roster_empty_state(body) == "Nobody has a seat yet. 6 on the waitlist."

    def it_says_the_same_on_the_standalone_table(client):
        """The refund refresh serves this partial on its own, so it needs the count too."""
        member = _login_instructor(client, "empty-table@example.com", "empty-table")
        offering = ClassOfferingFactory(instructor=member, capacity=5)
        _seats(offering, Registration.Status.WAITLISTED, 1)
        url = reverse("classes:teach_class_registrations_table", args=[offering.pk])
        body = client.get(url).content.decode()
        assert _roster_empty_state(body) == "Nobody has a seat yet. 1 on the waitlist."

    def it_still_points_at_the_cancelled_rows_when_there_is_no_waitlist(client):
        member = _login_instructor(client, "empty-cancelled@example.com", "empty-cancelled")
        offering = ClassOfferingFactory(instructor=member, capacity=6)
        _seats(offering, Registration.Status.CANCELLED, 2)
        body = client.get(reverse("classes:teach_class_registrations", args=[offering.pk])).content.decode()
        assert _roster_empty_state(body) == "Nobody is holding a seat right now."
        assert "Show 2 cancelled" in body

    def it_says_nothing_yet_on_a_class_nobody_has_touched(client):
        member = _login_instructor(client, "empty-fresh@example.com", "empty-fresh")
        offering = ClassOfferingFactory(instructor=member, capacity=10)
        body = client.get(reverse("classes:teach_class_registrations", args=[offering.pk])).content.decode()
        assert _roster_empty_state(body) == "No registrations yet."
