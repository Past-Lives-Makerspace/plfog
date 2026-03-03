"""BDD-style tests for the FavoriteEvent model."""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.db import IntegrityError

from membership.models import FavoriteEvent, Guild

User = get_user_model()

pytestmark = pytest.mark.django_db


def describe_favorite_event():
    def it_creates_a_favorite():
        user = User.objects.create_user(username="fav_user", password="test")
        guild = Guild.objects.create(name="Test Guild")
        ct = ContentType.objects.get_for_model(Guild)
        fav = FavoriteEvent.objects.create(user=user, content_type=ct, object_id=guild.pk)
        assert fav.content_object == guild
        assert str(fav.user) == "fav_user"

    def it_has_str_representation():
        user = User.objects.create_user(username="fav_str_user", password="test")
        guild = Guild.objects.create(name="Str Guild")
        ct = ContentType.objects.get_for_model(Guild)
        fav = FavoriteEvent.objects.create(user=user, content_type=ct, object_id=guild.pk)
        assert str(fav) == f"fav_str_user ★ Makerspace Membership | Guild #{guild.pk}"

    def it_enforces_unique_together():
        user = User.objects.create_user(username="fav_user2", password="test")
        guild = Guild.objects.create(name="Test Guild 2")
        ct = ContentType.objects.get_for_model(Guild)
        FavoriteEvent.objects.create(user=user, content_type=ct, object_id=guild.pk)
        with pytest.raises(IntegrityError):
            FavoriteEvent.objects.create(user=user, content_type=ct, object_id=guild.pk)
