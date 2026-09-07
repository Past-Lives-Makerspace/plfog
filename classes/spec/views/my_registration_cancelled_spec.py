"""BDD specs for the registrant self-serve page after a class cancel."""

from __future__ import annotations

from datetime import timedelta

from django.urls import reverse
from django.utils import timezone

from classes.factories import ClassOfferingFactory, ClassSessionFactory, RegistrationFactory
from classes.models import ClassOffering, Registration


def _page(client, registration):
    return client.get(reverse("classes:my_registration", kwargs={"token": registration.self_serve_token}))


def describe_my_registration_on_a_cancelled_class():
    def it_shows_the_state_card_with_the_reason_and_hides_the_cancel_button(client, db):
        offering = ClassOfferingFactory(
            status=ClassOffering.Status.CANCELLED,
            title="Wheel Throwing",
            cancellation_reason="The kiln broke",
        )
        start = timezone.now() + timedelta(days=3)
        ClassSessionFactory(class_offering=offering, starts_at=start, ends_at=start + timedelta(hours=2))
        registration = RegistrationFactory(class_offering=offering, status=Registration.Status.CONFIRMED)
        resp = _page(client, registration)
        assert resp.status_code == 200
        html = resp.content.decode()
        assert resp.context["class_cancelled"] is True
        assert resp.context["can_self_cancel"] is False
        assert "This class was cancelled. The kiln broke. If you paid, a refund is on its way from our staff." in html
        assert "Class cancelled" in html
        assert reverse("classes:my_registration_cancel", kwargs={"token": registration.self_serve_token}) not in html
        assert reverse("classes:public_list") in html
        assert reverse("classes:public_class_detail", kwargs={"slug": offering.slug}) not in html

    def it_keeps_the_registration_status_for_the_refund_panel(client, db):
        offering = ClassOfferingFactory(status=ClassOffering.Status.CANCELLED, cancellation_reason="Flooded")
        registration = RegistrationFactory(
            class_offering=offering, status=Registration.Status.CONFIRMED, amount_paid_cents=5000
        )
        _page(client, registration)
        registration.refresh_from_db()
        assert registration.status == Registration.Status.CONFIRMED

    def it_is_unchanged_for_a_live_class(client, db):
        offering = ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED)
        start = timezone.now() + timedelta(days=3)
        ClassSessionFactory(class_offering=offering, starts_at=start, ends_at=start + timedelta(hours=2))
        registration = RegistrationFactory(class_offering=offering, status=Registration.Status.CONFIRMED)
        resp = _page(client, registration)
        html = resp.content.decode()
        assert resp.context["class_cancelled"] is False
        assert resp.context["can_self_cancel"] is True
        assert "This class was cancelled." not in html
        assert reverse("classes:my_registration_cancel", kwargs={"token": registration.self_serve_token}) in html
        assert reverse("classes:public_class_detail", kwargs={"slug": offering.slug}) in html
