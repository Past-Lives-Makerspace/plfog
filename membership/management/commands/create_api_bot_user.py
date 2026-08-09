"""Management command to create (or re-use) the fog-bot API service user."""

from __future__ import annotations

from typing import Any

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from rest_framework.authtoken.models import Token

User = get_user_model()

_BOT_USERNAME = "fog-bot"
_BOT_EMAIL = "fog-bot@pastlives.space"


class Command(BaseCommand):
    """Create or re-use the fog-bot service account and print its REST API token."""

    help = "Create the fog-bot API service user (idempotent) and print its auth token."

    def handle(self, *args: Any, **options: Any) -> None:
        user, created = User.objects.get_or_create(
            username=_BOT_USERNAME,
            defaults={"email": _BOT_EMAIL, "is_superuser": True, "is_staff": True},
        )
        if not created and (not user.is_superuser or not user.is_staff):
            user.is_superuser = True
            user.is_staff = True
            user.save(update_fields=["is_superuser", "is_staff"])
        token, _ = Token.objects.get_or_create(user=user)
        self.stdout.write(f"Token: {token.key}")
