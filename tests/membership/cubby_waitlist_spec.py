"""BDD specs for the cubby waitlist (#980): membership/cubby_waitlist.py and every road that opens a cubby.

Joining (once per member per guild, only while every cubby is taken), leaving and rejoining at the
back, a place in line; one opening telling exactly the member first in line, once, after the change
commits; a retried webhook telling nobody new; an empty list sending nothing; and each path that frees
a cubby: a rental ending (the webhook, End Rental, a member's cancel of an assigned rental, unpaid,
account deletion), a hold deleted (expired, the cancel page, the sweep, a failed rent), Out of Service
turned off, a cubby added, a cubby or its shelf restored, and the Cubbies tab switched on.

Stripe is faked the way tests/membership/cubby_rentals_spec.py fakes it. Names are factory strings no
changelog could contain (STANDARDS.md section 8).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.core import mail
from django.db import IntegrityError, transaction
from django.utils import timezone

from core.models import Notification
from membership import cubbies, cubby_rentals, cubby_waitlist
from membership.cubby_waitlist import WaitlistError
from membership.models import Cubby, CubbyRental, CubbyWaitlistEntry, Guild, Member, ShelfUnit
from tests.billing.factories import TabFactory
from tests.membership.factories import (
    CubbyFactory,
    CubbyRentalFactory,
    GuildFactory,
    MembershipPlanFactory,
    ShelfUnitFactory,
)

pytestmark = pytest.mark.django_db

Status = CubbyRental.Status
_PERIOD_END = int(datetime(2026, 11, 27, 18, 0, tzinfo=UTC).timestamp())


def _member(username: str) -> Member:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, email=f"{username}@example.com", password="x")
    member = user.member
    member.status = Member.Status.ACTIVE
    member.full_legal_name = f"{username.title()} Fennelwick"
    member.save(update_fields=["status", "full_legal_name"])
    TabFactory(member=member, stripe_customer_id=f"cus_{username}")
    return member


class Shelf:
    """A guild of two cubbies, A1 and A2, both rented: every cubby is taken."""

    def __init__(self) -> None:
        self.guild = GuildFactory(name="Quarrystone Guild", show_cubbies_tab=True)
        self.unit = ShelfUnitFactory(guild=self.guild, name="Kiln Wall")
        self.a1 = CubbyFactory(unit=self.unit, label="A1", row=1, column=1, floor_cents=1500, suggested_cents=2000)
        self.a2 = CubbyFactory(unit=self.unit, label="A2", row=1, column=2, floor_cents=1500, suggested_cents=2000)
        self.r1 = CubbyRentalFactory(cubby=self.a1, member=_member("tenantone"))
        self.r2 = CubbyRentalFactory(cubby=self.a2, member=_member("tenanttwo"))

    def wait(self, *usernames: str) -> list[CubbyWaitlistEntry]:
        """Members who joined the list one minute apart, the first first."""
        start = timezone.now() - timedelta(hours=1)
        return [
            CubbyWaitlistEntry.objects.create(
                guild=self.guild, member=_member(name), joined_at=start + timedelta(minutes=index)
            )
            for index, name in enumerate(usernames)
        ]


@pytest.fixture
def shelf() -> Shelf:
    return Shelf()


def _told() -> list[str]:
    """Who heard a cubby open: the bell rows' owners, by username."""
    return sorted(
        Notification.objects.filter(title__startswith="A cubby just opened at").values_list("user__username", flat=True)
    )


def _snapshot(rental: CubbyRental, **overrides: Any) -> dict[str, Any]:
    snapshot: dict[str, Any] = {
        "id": rental.stripe_subscription_id or f"sub_{rental.pk}",
        "status": "active",
        "customer": "cus_tenant",
        "metadata": {
            "kind": "cubby_rental",
            "rental_pk": str(rental.pk),
            "cubby_pk": str(rental.cubby_id),
            "member_pk": str(rental.member_id),
        },
        "cancel_at_period_end": False,
        "cancel_at": None,
        "default_payment_method": "",
        "latest_invoice": f"in_{rental.pk}",
        "item_id": "si_cub",
        "product_id": "prod_cub",
        "unit_amount": rental.amount_cents,
        "current_period_end": _PERIOD_END,
    }
    snapshot.update(overrides)
    return snapshot


def _free_a1_hold(shelf: Shelf, **kwargs: Any) -> CubbyRental:
    """A1's rental gone, and a hold of a third member's on it instead."""
    CubbyRental.objects.filter(pk=shelf.r1.pk).update(status=Status.ENDED)
    kwargs.setdefault("stripe_checkout_session_id", "cs_wait_1")
    return CubbyRental.objects.create(
        cubby=shelf.a1,
        member=_member(kwargs.pop("username", "holder")),
        amount_cents=2200,
        status=Status.PENDING_PAYMENT,
        **kwargs,
    )


# ── The model ────────────────────────────────────────────────────────────────


def describe_CubbyWaitlistEntry():
    def it_allows_one_waiting_entry_per_member_per_guild_but_any_number_past(shelf: Shelf):
        (entry,) = shelf.wait("dupe")
        with pytest.raises(IntegrityError), transaction.atomic():
            CubbyWaitlistEntry.objects.create(guild=shelf.guild, member=entry.member)
        CubbyWaitlistEntry.objects.filter(pk=entry.pk).update(left_at=timezone.now())
        CubbyWaitlistEntry.objects.create(guild=shelf.guild, member=entry.member)
        assert CubbyWaitlistEntry.objects.filter(member=entry.member).count() == 2

    def it_names_itself_its_confirm_and_its_remove_url(shelf: Shelf):
        (entry,) = shelf.wait("named")
        assert str(entry) == "Named Fennelwick waiting for a Quarrystone Guild cubby"
        assert entry.is_waiting
        assert entry.remove_confirm_id == f"cubby-wait-remove-{entry.pk}"
        assert entry.remove_url == f"/cubbies/waitlist/{entry.pk}/remove/"
        entry.notified_at = timezone.now()
        assert not entry.is_waiting


# ── Joining, leaving and the line ────────────────────────────────────────────


def describe_join():
    def it_puts_the_member_at_the_back_while_every_cubby_is_taken(shelf: Shelf):
        shelf.wait("first")
        entry = cubby_waitlist.join(shelf.guild, _member("second"))
        assert entry.is_waiting
        assert cubby_waitlist.place_in_line(entry) == 2

    def it_keeps_one_entry_when_they_join_twice(shelf: Shelf):
        member = _member("twice")
        first = cubby_waitlist.join(shelf.guild, member)
        assert cubby_waitlist.join(shelf.guild, member) == first
        assert CubbyWaitlistEntry.objects.count() == 1

    def it_lets_a_member_who_already_rents_here_join(shelf: Shelf):
        entry = cubby_waitlist.join(shelf.guild, shelf.r1.member)
        assert entry.member == shelf.r1.member

    def it_counts_held_out_of_service_and_archived_cubbies_as_taken(shelf: Shelf):
        CubbyRental.objects.filter(pk=shelf.r1.pk).update(status=Status.PENDING_PAYMENT)
        CubbyRental.objects.filter(pk=shelf.r2.pk).update(status=Status.ENDED)
        Cubby.objects.filter(pk=shelf.a2.pk).update(out_of_service=True)
        CubbyFactory(unit=shelf.unit, label="A3", row=1, column=3, archived_at=timezone.now())
        archived_unit = ShelfUnitFactory(guild=shelf.guild, archived_at=timezone.now())
        CubbyFactory(unit=archived_unit, label="Z1")
        assert cubby_waitlist.is_full(shelf.guild)
        cubby_waitlist.join(shelf.guild, _member("taken"))

    def it_refuses_while_a_cubby_is_open(shelf: Shelf):
        CubbyRental.objects.filter(pk=shelf.r2.pk).update(status=Status.ENDED)
        with pytest.raises(WaitlistError, match="rent it instead"):
            cubby_waitlist.join(shelf.guild, _member("early"))
        assert not CubbyWaitlistEntry.objects.exists()

    def it_refuses_while_the_tab_is_off_or_there_are_no_cubbies(shelf: Shelf):
        Guild.objects.filter(pk=shelf.guild.pk).update(show_cubbies_tab=False)
        with pytest.raises(WaitlistError, match="isn't renting cubbies"):
            cubby_waitlist.join(shelf.guild, _member("tab_off"))
        empty = GuildFactory(show_cubbies_tab=True)
        with pytest.raises(WaitlistError, match="isn't renting cubbies"):
            cubby_waitlist.join(empty, _member("no_cubbies"))
        assert not cubby_waitlist.is_full(empty)


def describe_leave():
    def it_takes_them_off_and_rejoining_puts_them_at_the_back(shelf: Shelf):
        first, _second = shelf.wait("leaver", "stayer")
        assert cubby_waitlist.leave(shelf.guild, first.member) is True
        assert cubby_waitlist.entry_for(shelf.guild, first.member) is None
        assert cubby_waitlist.leave(shelf.guild, first.member) is False
        again = cubby_waitlist.join(shelf.guild, first.member)
        assert cubby_waitlist.place_in_line(again) == 2

    def it_is_removed_by_staff_once(shelf: Shelf):
        (entry,) = shelf.wait("removed")
        assert cubby_waitlist.remove(entry) is True
        assert cubby_waitlist.remove(entry) is False
        assert cubby_waitlist.waiting_count(shelf.guild) == 0

    def it_leaves_every_list_for_a_deleted_account(shelf: Shelf):
        (entry,) = shelf.wait("goneaway")
        other = GuildFactory(show_cubbies_tab=True)
        CubbyWaitlistEntry.objects.create(guild=other, member=entry.member)
        cubby_waitlist.leave_everywhere(entry.member)
        assert not CubbyWaitlistEntry.objects.waiting().exists()


def describe_the_line():
    def it_counts_places_from_one_breaking_a_tie_by_who_was_saved_first(shelf: Shelf):
        first, second, third = shelf.wait("p_one", "p_two", "p_three")
        CubbyWaitlistEntry.objects.filter(pk=third.pk).update(joined_at=second.joined_at)
        third.refresh_from_db()
        assert [cubby_waitlist.place_in_line(e) for e in (first, second, third)] == [1, 2, 3]
        assert [row.entry for row in cubbies.WaitlistRow.for_guild(shelf.guild)] == [first, second, third]
        assert cubby_waitlist.waiting_count(shelf.guild) == 3

    def it_leaves_deactivated_and_deleted_members_out_of_places_the_list_and_the_count(shelf: Shelf):
        # notify_next skips them, so they must not hold a place nobody can move past (#980 review).
        inactive, deleted, live = shelf.wait("asleep", "erased", "awake")
        User.objects.filter(pk=inactive.member.user_id).update(is_active=False)
        Member.objects.filter(pk=deleted.member_id).update(deleted_at=timezone.now())
        assert cubby_waitlist.place_in_line(live) == 1
        assert [row.entry for row in cubbies.WaitlistRow.for_guild(shelf.guild)] == [live]
        assert cubby_waitlist.waiting_count(shelf.guild) == 1

    def it_moves_everyone_up_when_someone_ahead_leaves(shelf: Shelf):
        first, second = shelf.wait("up_one", "up_two")
        cubby_waitlist.leave(shelf.guild, first.member)
        assert cubby_waitlist.place_in_line(second) == 1

    def it_draws_a_staff_row_and_its_remove_sentence(shelf: Shelf):
        (entry,) = shelf.wait("rowan")
        (row,) = cubbies.WaitlistRow.for_guild(shelf.guild)
        assert (row.place, row.name, row.initials) == (1, "Rowan Fennelwick", "RF")
        assert row.joined == cubbies._short_day(entry.joined_at)
        assert row.remove_message == (
            "Rowan leaves the waitlist and everyone behind them moves up one. We won't tell Rowan."
        )

    def it_words_a_remove_for_a_member_with_no_name(shelf: Shelf):
        (entry,) = shelf.wait("noname")
        row = cubbies.WaitlistRow(entry=entry, place=1, name="", initials="", joined="")
        assert row.remove_message.startswith(" leaves the waitlist")


# ── A cubby opening ──────────────────────────────────────────────────────────


def describe_notify_next():
    def it_tells_the_member_first_in_line_once_and_takes_them_off(shelf: Shelf):
        first, second = shelf.wait("ada", "bram")
        CubbyRental.objects.filter(pk=shelf.r1.pk).update(status=Status.ENDED)
        told = cubby_waitlist.notify_next(shelf.a1.pk)
        assert told == first
        first.refresh_from_db()
        assert first.notified_at is not None and first.notified_cubby == shelf.a1
        assert not first.is_waiting
        assert cubby_waitlist.place_in_line(second) == 1
        assert _told() == ["ada"]
        (email,) = mail.outbox
        assert email.subject == "A cubby just opened at Quarrystone Guild"
        assert email.to == ["ada@example.com"]
        body = email.alternatives[0][0]
        assert "Rent It Now" in body and "?tab=cubbies" in body and "first come, first served" in body
        assert "A1 on the Kiln Wall" in body
        assert "Rent it now: " in email.body
        notice = Notification.objects.get(user=first.member.user)
        assert notice.body == "Cubby A1 on the Kiln Wall is open. Whoever rents it first gets it."
        assert notice.url == f"/guilds/{shelf.guild.slug}/?tab=cubbies"

    def it_tells_the_next_member_for_the_next_opening(shelf: Shelf):
        shelf.wait("cora", "dell")
        CubbyRental.objects.filter(cubby__unit=shelf.unit).update(status=Status.ENDED)
        cubby_waitlist.notify_next(shelf.a1.pk)
        cubby_waitlist.notify_next(shelf.a2.pk)
        assert _told() == ["cora", "dell"]

    def it_sends_nothing_with_nobody_waiting(shelf: Shelf):
        CubbyRental.objects.filter(pk=shelf.r1.pk).update(status=Status.ENDED)
        assert cubby_waitlist.notify_next(shelf.a1.pk) is None
        assert mail.outbox == [] and _told() == []

    def it_tells_nobody_when_the_cubby_is_not_open_any_more(shelf: Shelf):
        shelf.wait("late")
        assert cubby_waitlist.notify_next(shelf.a1.pk) is None  # rented again before the pick
        CubbyRental.objects.filter(pk=shelf.r1.pk).update(status=Status.ENDED)
        Guild.objects.filter(pk=shelf.guild.pk).update(show_cubbies_tab=False)
        assert cubby_waitlist.notify_next(shelf.a1.pk) is None  # the tab is off: nobody can rent it
        assert _told() == [] and cubby_waitlist.waiting_count(shelf.guild) == 1

    def it_passes_over_a_member_who_left_between_the_read_and_the_claim(shelf: Shelf):
        # The read finds the first member; they leave before the claim lands, so the next is told (#980 review).
        first, second = shelf.wait("left_late", "told_instead")
        CubbyRental.objects.filter(pk=shelf.r1.pk).update(status=Status.ENDED)
        real_claim = cubby_waitlist._claim

        def leave_then_claim(entry: CubbyWaitlistEntry, cubby: Cubby) -> bool:
            if entry.pk == first.pk:
                cubby_waitlist.leave(shelf.guild, first.member)
            return real_claim(entry, cubby)

        with patch("membership.cubby_waitlist._claim", side_effect=leave_then_claim):
            assert cubby_waitlist.notify_next(shelf.a1.pk) == second
        first.refresh_from_db()
        assert first.notified_at is None and first.left_at is not None
        assert _told() == ["told_instead"]
        assert [message.to for message in mail.outbox] == [["told_instead@example.com"]]

    def it_tells_nobody_when_everyone_left_before_the_claim(shelf: Shelf):
        (only,) = shelf.wait("gone_too")
        CubbyRental.objects.filter(pk=shelf.r1.pk).update(status=Status.ENDED)
        real_claim = cubby_waitlist._claim

        def leave_then_claim(entry: CubbyWaitlistEntry, cubby: Cubby) -> bool:
            cubby_waitlist.leave(shelf.guild, only.member)
            return real_claim(entry, cubby)

        with patch("membership.cubby_waitlist._claim", side_effect=leave_then_claim):
            assert cubby_waitlist.notify_next(shelf.a1.pk) is None
        assert _told() == [] and mail.outbox == []

    def it_skips_a_member_whose_account_was_deleted_or_switched_off(shelf: Shelf):
        deleted, inactive, live = shelf.wait("deleted_one", "inactive_one", "live_one")
        Member.objects.filter(pk=deleted.member_id).update(deleted_at=timezone.now())
        User.objects.filter(pk=inactive.member.user_id).update(is_active=False)
        CubbyRental.objects.filter(pk=shelf.r1.pk).update(status=Status.ENDED)
        assert cubby_waitlist.notify_next(shelf.a1.pk) == live
        assert _told() == ["live_one"]


def describe_open_for_waitlist():
    def it_waits_for_the_change_to_commit(shelf: Shelf, django_capture_on_commit_callbacks):
        shelf.wait("patient")
        with django_capture_on_commit_callbacks(execute=False) as callbacks:
            CubbyRental.objects.filter(pk=shelf.r1.pk).update(status=Status.ENDED)
            cubby_waitlist.open_for_waitlist(shelf.a1)
            assert _told() == []
        assert len(callbacks) == 1
        callbacks[0]()
        assert _told() == ["patient"]

    def it_tells_nobody_when_the_change_rolls_back(shelf: Shelf, django_capture_on_commit_callbacks):
        shelf.wait("rolled")
        with django_capture_on_commit_callbacks(execute=True) as callbacks:
            with pytest.raises(RuntimeError), transaction.atomic():
                CubbyRental.objects.filter(pk=shelf.r1.pk).update(status=Status.ENDED)
                cubby_waitlist.open_for_waitlist(shelf.a1)
                raise RuntimeError("the end failed")
        assert callbacks == [] and _told() == []

    def it_logs_a_failure_to_tell_instead_of_failing_the_change(
        shelf: Shelf, django_capture_on_commit_callbacks, caplog
    ):
        shelf.wait("unlucky")
        CubbyRental.objects.filter(pk=shelf.r1.pk).update(status=Status.ENDED)
        with (
            patch("membership.cubby_waitlist.notify_opened", side_effect=RuntimeError("mail down")),
            django_capture_on_commit_callbacks(execute=True),
        ):
            cubby_waitlist.open_for_waitlist(shelf.a1)
        assert "mail down" in caplog.text

    def it_opens_every_open_cubby_when_the_tab_comes_on(shelf: Shelf, django_capture_on_commit_callbacks):
        shelf.wait("tab_one", "tab_two", "tab_three")
        CubbyRental.objects.filter(cubby__unit=shelf.unit).update(status=Status.ENDED)
        with django_capture_on_commit_callbacks(execute=True):
            cubby_waitlist.open_all_for_waitlist(shelf.guild)
        assert _told() == ["tab_one", "tab_two"]


def describe_a_notified_member():
    def it_is_off_the_list_and_offered_the_button_again_once_the_shelf_fills(shelf: Shelf):
        (entry,) = shelf.wait("again")
        CubbyRental.objects.filter(pk=shelf.r1.pk).update(status=Status.ENDED)
        cubby_waitlist.notify_next(shelf.a1.pk)
        CubbyRentalFactory(cubby=shelf.a1)  # someone else rented it first
        tab = cubbies.CubbyTab.load(shelf.guild, entry.member)
        assert tab.waitlist_entry is None and tab.is_full and tab.offers_waitlist
        assert cubby_waitlist.place_in_line(cubby_waitlist.join(shelf.guild, entry.member)) == 1


# ── Every road that opens a cubby (acceptance criterion 3) ───────────────────


@pytest.fixture
def committed(django_capture_on_commit_callbacks):
    """Run one change as if its transaction committed, so its on_commit notices go out before the next line."""

    def run(change: Any, *args: Any, **kwargs: Any) -> Any:
        with django_capture_on_commit_callbacks(execute=True):
            return change(*args, **kwargs)

    return run


def describe_each_way_a_cubby_opens():
    @patch("billing.stripe_utils.retrieve_subscription")
    def it_tells_one_member_when_stripe_ends_a_rental_and_nobody_new_on_the_retry(mock_sub, shelf: Shelf, committed):
        shelf.wait("hook_one", "hook_two")
        mock_sub.return_value = _snapshot(shelf.r1, status="canceled")
        committed(cubby_rentals.settle_subscription, shelf.r1.stripe_subscription_id)
        committed(cubby_rentals.settle_subscription, shelf.r1.stripe_subscription_id)  # Stripe sends it again
        assert _told() == ["hook_one"]

    @patch("billing.stripe_utils.cancel_subscription")
    @patch("billing.stripe_utils.retrieve_subscription")
    def it_waits_for_an_unpaid_rental_to_end(mock_sub, mock_cancel, shelf: Shelf, committed):
        shelf.wait("unpaid_one")
        mock_sub.return_value = _snapshot(shelf.r1, status="unpaid")
        committed(cubby_rentals.settle_subscription, shelf.r1.stripe_subscription_id)
        assert _told() == []  # Past Due still holds the cubby
        mock_sub.return_value = _snapshot(shelf.r1, status="canceled")
        committed(cubby_rentals.settle_subscription, shelf.r1.stripe_subscription_id)
        assert _told() == ["unpaid_one"]

    @patch("billing.stripe_utils.cancel_subscription")
    @patch("billing.stripe_utils.retrieve_subscription")
    def it_tells_one_member_when_staff_end_a_rental_and_nobody_when_its_webhook_follows(
        mock_sub, mock_cancel, shelf: Shelf, committed
    ):
        shelf.wait("staff_one", "staff_two")
        committed(cubby_rentals.end_rental, shelf.r1, reason="Moving out.")
        mock_sub.return_value = _snapshot(shelf.r1, status="canceled")
        committed(cubby_rentals.settle_subscription, shelf.r1.stripe_subscription_id)
        assert _told() == ["staff_one"]

    def it_tells_one_member_when_a_member_cancels_an_assigned_rental(shelf: Shelf, committed):
        shelf.wait("assign_one")
        assigned = CubbyRentalFactory(
            cubby=CubbyFactory(unit=shelf.unit, label="A3", row=1, column=3),
            source=CubbyRental.Source.COMPED,
            amount_cents=0,
            stripe_subscription_id=None,
        )
        committed(cubby_rentals.set_cancelling, assigned, cancel=True)
        assert _told() == ["assign_one"]

    @patch("billing.stripe_utils.expire_checkout_session")
    @patch("billing.stripe_utils.cancel_subscription")
    def it_frees_a_deleted_accounts_cubbies_for_others_and_takes_them_off_every_list(
        mock_cancel, mock_expire, shelf: Shelf, committed
    ):
        leaving = shelf.r1.member
        CubbyWaitlistEntry.objects.create(
            guild=shelf.guild, member=leaving, joined_at=timezone.now() - timedelta(days=1)
        )
        shelf.wait("next_up")
        CubbyRental.objects.filter(pk=shelf.r2.pk).update(status=Status.ENDED)
        CubbyRental.objects.create(cubby=shelf.a2, member=leaving, amount_cents=2200, status=Status.PENDING_PAYMENT)
        committed(cubby_rentals.end_for_deleted_account, leaving)
        assert _told() == ["next_up"]  # A1 told next_up; A2 found nobody else waiting
        assert cubby_waitlist.entry_for(shelf.guild, leaving) is None

    @patch("billing.stripe_utils.retrieve_subscription")
    @patch("billing.stripe_utils.cancel_subscription")
    def it_tells_one_member_when_the_webhook_ends_a_deleted_accounts_rental_mid_deletion(
        mock_cancel, mock_sub, shelf: Shelf, committed
    ):
        # The cancel's own customer.subscription.deleted is settled before the deletion's update
        # runs: whichever ends the rental opens the cubby, the other changes nothing (#980 review).
        shelf.wait("once_one", "once_two")
        mock_sub.return_value = _snapshot(shelf.r1, status="canceled")
        mock_cancel.side_effect = lambda **kwargs: cubby_rentals.settle_subscription(shelf.r1.stripe_subscription_id)
        committed(cubby_rentals.end_for_deleted_account, shelf.r1.member)
        shelf.r1.refresh_from_db()
        assert shelf.r1.status == Status.ENDED
        assert _told() == ["once_one"]
        assert cubby_waitlist.waiting_count(shelf.guild) == 1

    def it_tells_one_member_when_an_expired_checkout_releases_a_hold_and_nobody_on_the_retry(shelf: Shelf, committed):
        hold = _free_a1_hold(shelf)
        shelf.wait("expired_one", "expired_two")
        payload = {"metadata": {"kind": "cubby_rental", "rental_pk": str(hold.pk)}}
        committed(cubby_rentals.release_expired_hold, payload)
        committed(cubby_rentals.release_expired_hold, payload)
        assert _told() == ["expired_one"]

    @patch("billing.stripe_utils.expire_checkout_session")
    @patch("billing.stripe_utils.retrieve_checkout_session")
    def it_tells_one_member_when_the_cancel_page_releases_a_hold(mock_session, mock_expire, shelf: Shelf, committed):
        hold = _free_a1_hold(shelf)
        shelf.wait("cancel_one")
        mock_session.return_value = {"status": "open", "subscription": ""}
        assert committed(cubby_rentals.release_hold_if_unpaid, hold) == "released"
        assert _told() == ["cancel_one"]

    @patch("billing.stripe_utils.retrieve_checkout_session")
    def it_tells_one_member_when_the_sweep_releases_a_hold(mock_session, shelf: Shelf, committed):
        hold = _free_a1_hold(shelf)
        CubbyRental.objects.filter(pk=hold.pk).update(created_at=timezone.now() - timedelta(hours=3))
        shelf.wait("sweep_one")
        mock_session.return_value = {"status": "expired", "subscription": ""}
        assert committed(cubby_rentals.release_stale_holds) == (1, 0)
        assert _told() == ["sweep_one"]

    @patch("billing.stripe_utils.create_subscription_checkout_session", side_effect=RuntimeError("stripe down"))
    def it_tells_one_member_when_a_rent_fails_and_its_hold_is_dropped(mock_create, shelf: Shelf, committed):
        CubbyRental.objects.filter(pk=shelf.r1.pk).update(status=Status.ENDED)
        shelf.wait("failed_one")
        renter = _member("unlucky_renter")
        with pytest.raises(RuntimeError):
            committed(cubby_rentals.start_rental, shelf.a1, renter, 2200)
        assert _told() == ["failed_one"]

    @patch("billing.stripe_utils.create_subscription_checkout_session")
    def it_tells_nobody_when_a_member_replaces_their_own_stale_hold(mock_create, shelf: Shelf, committed):
        hold = _free_a1_hold(shelf, stripe_checkout_session_id=None)
        CubbyRental.objects.filter(pk=hold.pk).update(created_at=timezone.now() - timedelta(minutes=10))
        shelf.wait("not_yet")
        mock_create.return_value = {"id": "cs_new", "url": "https://checkout.stripe.example/cs_new"}
        url = committed(cubby_rentals.start_rental, shelf.a1, hold.member, 2200)
        assert url == "https://checkout.stripe.example/cs_new"
        assert _told() == []

    def it_tells_one_member_when_a_replaced_hold_is_followed_by_a_refused_rent(shelf: Shelf, committed):
        hold = _free_a1_hold(shelf, stripe_checkout_session_id=None)
        CubbyRental.objects.filter(pk=hold.pk).update(created_at=timezone.now() - timedelta(minutes=10))
        shelf.wait("refused_one")
        with pytest.raises(cubby_rentals.AmountError):
            committed(cubby_rentals.start_rental, shelf.a1, hold.member, 500)
        assert _told() == ["refused_one"]

    def it_tells_one_member_when_both_tries_release_a_hold_of_the_members_own(shelf: Shelf, committed):
        first = _free_a1_hold(shelf, stripe_checkout_session_id=None)
        stale = timezone.now() - timedelta(minutes=10)
        CubbyRental.objects.filter(pk=first.pk).update(created_at=stale)
        first.refresh_from_db()
        shelf.wait("both_one")
        second = CubbyRental(pk=first.pk + 1000, cubby=shelf.a1, member=first.member, created_at=stale)
        with (
            patch("membership.cubby_rentals._hold", side_effect=[(None, first), (None, second)]),
            pytest.raises(cubby_rentals.CubbyTakenError),
        ):
            committed(cubby_rentals.start_rental, shelf.a1, first.member, 2200)
        assert _told() == ["both_one"]

    def it_tells_one_member_when_out_of_service_is_turned_off(shelf: Shelf, committed):
        closed = CubbyFactory(unit=shelf.unit, label="A3", row=1, column=3, out_of_service=True)
        shelf.wait("service_one", "service_two")
        closed.note = "Fixed the shelf"
        committed(cubbies.save_cubby, closed)  # still out of service: nothing opens
        assert _told() == []
        closed.out_of_service = False
        committed(cubbies.save_cubby, closed)
        closed.note = "Freshly oiled"
        committed(cubbies.save_cubby, closed)  # already open: no second opening
        assert _told() == ["service_one"]

    def it_tells_nobody_when_a_rented_cubby_comes_back_into_service(shelf: Shelf, committed):
        shelf.wait("never")
        Cubby.objects.filter(pk=shelf.a1.pk).update(out_of_service=True)
        shelf.a1.refresh_from_db()
        shelf.a1.out_of_service = False
        committed(cubbies.save_cubby, shelf.a1)
        assert _told() == []

    def it_tells_one_member_when_a_cubby_is_added_in_service(shelf: Shelf, committed):
        shelf.wait("added_one", "added_two")
        committed(cubbies.save_cubby, Cubby(unit=shelf.unit, label="B1", row=2, column=1, out_of_service=True))
        assert _told() == []
        committed(cubbies.save_cubby, Cubby(unit=shelf.unit, label="B2", row=2, column=2))
        assert _told() == ["added_one"]

    def it_tells_one_member_when_an_archived_cubby_is_restored(shelf: Shelf, committed):
        archived = CubbyFactory(unit=shelf.unit, label="A3", row=1, column=3, archived_at=timezone.now())
        closed = CubbyFactory(
            unit=shelf.unit, label="A4", row=1, column=4, archived_at=timezone.now(), out_of_service=True
        )
        shelf.wait("restored_one", "restored_two")
        committed(cubbies.restore_cubby, closed)
        assert _told() == []
        committed(cubbies.restore_cubby, archived)
        assert _told() == ["restored_one"]

    def it_tells_one_member_per_cubby_when_a_shelf_is_restored(shelf: Shelf, committed):
        unit = ShelfUnitFactory(guild=shelf.guild, name="Back Wall", archived_at=timezone.now())
        CubbyFactory(unit=unit, label="C1", row=1, column=1)
        CubbyFactory(unit=unit, label="C2", row=1, column=2)
        CubbyFactory(unit=unit, label="C3", row=1, column=3, out_of_service=True)
        shelf.wait("unit_one", "unit_two", "unit_three")
        committed(cubbies.restore_unit, unit)
        assert _told() == ["unit_one", "unit_two"]

    def it_tells_nobody_when_a_shelf_is_resized(shelf: Shelf, committed):
        shelf.wait("resize")
        committed(cubbies.resize_unit, shelf.unit, name="Kiln Wall", rows=4, columns=4)
        assert ShelfUnit.objects.get(pk=shelf.unit.pk).rows == 4
        assert _told() == []
