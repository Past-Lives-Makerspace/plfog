"""BDD specs for the create_api_bot_user management command."""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.management import call_command
from rest_framework.authtoken.models import Token

User = get_user_model()


def describe_create_api_bot_user():
    def it_creates_superuser(db, capsys):
        call_command("create_api_bot_user")

        user = User.objects.get(username="fog-bot")
        assert user.email == "fog-bot@pastlives.space"
        assert user.is_superuser is True
        assert user.is_staff is True

    def it_is_idempotent(db, capsys):
        call_command("create_api_bot_user")
        first_token = Token.objects.get(user__username="fog-bot").key
        capsys.readouterr()

        call_command("create_api_bot_user")
        second_token = Token.objects.get(user__username="fog-bot").key

        assert first_token == second_token
        assert User.objects.filter(username="fog-bot").count() == 1

    def it_prints_token(db, capsys):
        call_command("create_api_bot_user")

        captured = capsys.readouterr()
        token = Token.objects.get(user__username="fog-bot")
        assert f"Token: {token.key}" in captured.out
