"""Two members racing for one cubby (#978): one reaches Stripe, the other is told it was just rented.

Needs PostgreSQL: SQLite writes no FOR UPDATE, so CI's postgres-only job runs it (STANDARDS.md 8).
The gap between the check and the hold is widened inside the lock; without the guild and cubby
row locks both members would see the cubby free, and the database's one-live-rental constraint
would be the only thing between them.
"""

from __future__ import annotations

import threading
import time
from typing import Any
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.db import connection

from membership import cubby_rentals
from membership.models import CubbyRental, Member
from tests.billing.factories import TabFactory
from tests.membership.factories import CubbyFactory, GuildFactory, MembershipPlanFactory, ShelfUnitFactory


def _member(username: str) -> Member:
    user = User.objects.create_user(username=username, email=f"{username}@example.com", password="x")
    member = user.member
    member.status = Member.Status.ACTIVE
    member.save(update_fields=["status"])
    TabFactory(member=member, stripe_customer_id=f"cus_{username}")
    return member


def describe_two_members_racing_for_one_cubby():
    @pytest.mark.postgres_only
    @pytest.mark.django_db(transaction=True)
    def it_lets_one_reach_stripe_and_tells_the_other_it_was_just_rented():
        MembershipPlanFactory()
        guild = GuildFactory(show_cubbies_tab=True)
        cubby = CubbyFactory(unit=ShelfUnitFactory(guild=guild), label="A3", floor_cents=1500, suggested_cents=2000)
        racers = [_member(f"racer{n}") for n in range(2)]
        start = threading.Barrier(len(racers))
        outcomes: list[str] = []
        real_problem = cubby_rentals.amount_problem

        def slow_problem(*args: Any, **kwargs: Any) -> str:
            # Inside the lock, after the cubby was found free: without the lock both would pass here.
            problem = real_problem(*args, **kwargs)
            time.sleep(0.3)
            return problem

        def racer(member: Member) -> None:
            try:
                start.wait()
                cubby_rentals.start_rental(cubby, member, 2200)
                outcomes.append("stripe")
            except cubby_rentals.CubbyTakenError as refusal:
                outcomes.append(str(refusal))
            finally:
                connection.close()

        session = {"id": "cs_race", "url": "https://checkout.stripe.example/cs_race"}
        with (
            patch.object(cubby_rentals, "amount_problem", slow_problem),
            patch("billing.stripe_utils.create_subscription_checkout_session", return_value=session) as create,
        ):
            threads = [threading.Thread(target=racer, args=(member,)) for member in racers]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

        assert sorted(outcomes) == ["Someone just rented A3. Pick another open cubby.", "stripe"]
        assert create.call_count == 1
        assert CubbyRental.objects.live().filter(cubby=cubby).count() == 1
