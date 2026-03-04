from __future__ import annotations

import stripe
from django.conf import settings

from .models import Buyable


def get_stripe_key() -> str:
    return settings.STRIPE_SECRET_KEY


def create_checkout_session(
    buyable: Buyable,
    quantity: int,
    success_url: str,
    cancel_url: str,
) -> stripe.checkout.Session:
    stripe.api_key = get_stripe_key()
    return stripe.checkout.Session.create(
        payment_method_types=["card"],
        line_items=[
            {
                "price_data": {
                    "currency": "usd",
                    "product_data": {"name": buyable.name},
                    "unit_amount": int(buyable.unit_price * 100),
                },
                "quantity": quantity,
            }
        ],
        mode="payment",
        success_url=success_url,
        cancel_url=cancel_url,
    )
