"""Register routes under the class (#1260, part 3): a run's sign up lives at its class's address."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.urls import reverse
from django.utils import timezone

from classes.factories import CategoryFactory, ClassOfferingFactory, ClassSessionFactory, RegistrationFactory
from classes.models import ClassOffering, Registration

pytestmark = pytest.mark.django_db


def _run(slug: str, *, days: int = 7, category=None, **extra) -> ClassOffering:
    extra.setdefault("title", "Picture Framing")
    run = ClassOfferingFactory(
        slug=slug,
        category=category or CategoryFactory(),
        status=ClassOffering.Status.PUBLISHED,
        price_cents=8500,
        **extra,
    )
    start = timezone.now() + timedelta(days=days)
    ClassSessionFactory(class_offering=run, starts_at=start, ends_at=start + timedelta(hours=2))
    return run


def _sign_up() -> dict[str, str]:
    return {
        "first_name": "Sam",
        "last_name": "Smith",
        "email": "sam@example.com",
        "liability_signature": "Sam Smith",
        "accepts_liability": "on",
    }


def describe_the_register_routes():
    def it_puts_each_run_under_its_class(client):
        first = _run("picture-framing-2026-12-01")
        later = _run("picture-framing-2027-01-05", days=40, category=first.category)

        assert first.register_path == f"/classes/picture-framing/register/{first.pk}/"
        assert later.register_path == f"/classes/picture-framing/register/{later.pk}/"
        assert reverse("classes:register_success", kwargs=later.register_kwargs).endswith(
            f"/picture-framing/register/{later.pk}/success/"
        )
        assert client.get(later.register_path).context["offering"] == later

    def it_is_not_found_under_another_class(client):
        run = _run("framing-1")
        other = _run("mat-cutting", title="Mat Cutting")

        response = client.get(reverse("classes:register", kwargs={"class_slug": other.class_record.slug, "pk": run.pk}))

        assert response.status_code == 404

    def it_is_not_found_for_a_run_nobody_may_see(client):
        draft = ClassOfferingFactory(title="Secret Class", slug="secret-class")

        assert client.get(draft.register_path).status_code == 404

    @patch("billing.stripe_utils.create_class_checkout_session")
    def it_returns_a_new_checkout_to_the_new_routes(mock_checkout, client):
        mock_checkout.return_value = {"id": "cs_test_1", "url": "https://checkout.stripe.com/c/pay/cs_test_1"}
        run = _run("picture-framing-2026-12-01")

        client.post(run.register_path, data=_sign_up())

        token = Registration.objects.get(class_offering=run).self_serve_token
        kwargs = mock_checkout.call_args.kwargs
        assert kwargs["success_url"].endswith(f"/classes/picture-framing/register/{run.pk}/success/?reg={token}")
        assert kwargs["cancel_url"].endswith(f"/classes/picture-framing/register/{run.pk}/cancelled/?reg={token}")
        # Stripe metadata still names the run, as the webhook and reports read it.
        assert kwargs["metadata"]["class_slug"] == "picture-framing-2026-12-01"


def describe_an_old_register_address():
    @pytest.mark.parametrize(
        ("suffix", "route", "query"),
        [
            ("register/", "register", "waitlist_token=abc123"),
            ("register/success/", "register_success", "reg=tok"),
            ("register/cancelled/", "register_cancelled", "reg=tok"),
        ],
    )
    def it_moves_permanently_to_the_runs_new_route_keeping_the_query(client, suffix, route, query):
        run = _run("picture-framing-2026-12-01")

        response = client.get(f"/classes/picture-framing-2026-12-01/{suffix}?{query}")

        assert response.status_code == 301
        assert response["Location"] == reverse(f"classes:{route}", kwargs=run.register_kwargs) + f"?{query}"

    def it_keeps_a_sent_form_a_post_with_its_body(client):
        run = _run("picture-framing-2026-12-01")

        response = client.post("/classes/picture-framing-2026-12-01/register/?waitlist=1", data=_sign_up())

        assert response.status_code == 308
        assert response["Location"] == f"{run.register_path}?waitlist=1"

    def it_still_moves_a_visit_with_a_301(client):
        _run("picture-framing-2026-12-01")

        assert client.get("/classes/picture-framing-2026-12-01/register/").status_code == 301

    def it_moves_without_a_query_when_there_is_none(client):
        run = _run("picture-framing-2026-12-01")

        response = client.get("/classes/picture-framing-2026-12-01/register/")

        assert response["Location"] == run.register_path

    def it_is_not_found_for_a_run_nobody_may_see(client):
        ClassOfferingFactory(title="Secret Class", slug="secret-class")

        assert client.get("/classes/secret-class/register/").status_code == 404

    def it_lets_a_mailed_waitlist_claim_land(client):
        run = _run("picture-framing-2026-12-01", capacity=1)
        RegistrationFactory(class_offering=run, status=Registration.Status.CONFIRMED)
        waiting = RegistrationFactory(class_offering=run, status=Registration.Status.WAITLISTED)

        response = client.get(
            f"/classes/picture-framing-2026-12-01/register/?waitlist_token={waiting.self_serve_token}", follow=True
        )

        assert response.redirect_chain[0] == (f"{run.register_path}?waitlist_token={waiting.self_serve_token}", 301)
        assert response.status_code == 200
