"""BDD-style tests for core.files.delete_orphan_on_replace."""

from __future__ import annotations

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from core.files import delete_orphan_on_replace
from tests.membership.factories import GuildFactory


def _png_bytes() -> bytes:
    # 1x1 transparent PNG — small enough to pass the size validator.
    return (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\xcf"
        b"\xc0\x00\x00\x00\x03\x00\x01\x5b\x0d\xc1\x6a\x00\x00\x00\x00IEND\xae"
        b"B`\x82"
    )


@pytest.mark.django_db
def describe_delete_orphan_on_replace():
    def it_returns_early_on_create():
        # A Guild with no pk — helper should not query and not raise.
        from membership.models import Guild

        guild = Guild(name="Unsaved")

        delete_orphan_on_replace(guild, "banner_image")  # must not raise

    def it_returns_early_when_field_is_unchanged():
        guild = GuildFactory(banner_image=SimpleUploadedFile("keep.png", _png_bytes(), content_type="image/png"))
        original_name = guild.banner_image.name
        storage = guild.banner_image.storage

        # Call without changing banner_image — the original file must remain.
        delete_orphan_on_replace(guild, "banner_image")

        assert storage.exists(original_name)
        storage.delete(original_name)  # cleanup

    def it_deletes_the_old_file_when_replaced():
        guild = GuildFactory(banner_image=SimpleUploadedFile("old.png", _png_bytes(), content_type="image/png"))
        old_name = guild.banner_image.name
        storage = guild.banner_image.storage
        assert storage.exists(old_name)

        # Simulate the user replacing the image: assign a new file on the in-memory
        # instance before save() runs. The helper compares against the DB value.
        guild.banner_image = SimpleUploadedFile("new.png", _png_bytes(), content_type="image/png")
        delete_orphan_on_replace(guild, "banner_image")

        assert not storage.exists(old_name)
        # The new file hasn't been persisted yet (save() hasn't run); clean it up in-memory.
        guild.banner_image.close()

    def it_deletes_the_old_file_when_cleared():
        guild = GuildFactory(banner_image=SimpleUploadedFile("bye.png", _png_bytes(), content_type="image/png"))
        old_name = guild.banner_image.name
        storage = guild.banner_image.storage

        # Simulate the user clearing the field.
        guild.banner_image = None
        delete_orphan_on_replace(guild, "banner_image")

        assert not storage.exists(old_name)

    def it_returns_when_instance_vanished_between_query_and_save():
        from membership.models import Guild

        # Craft a Guild that *looks* saved (has a pk) but does not exist in the DB,
        # so the inner .get() raises DoesNotExist and the helper must return quietly.
        ghost = Guild(pk=999_999, name="Ghost")

        delete_orphan_on_replace(ghost, "banner_image")  # must not raise


@pytest.mark.django_db
def describe_delete_if_unreferenced():
    def it_deletes_a_file_no_other_row_points_at():
        from core.files import delete_if_unreferenced
        from membership.models import Guild

        guild = GuildFactory(banner_image=SimpleUploadedFile("solo.png", _png_bytes(), content_type="image/png"))
        name = guild.banner_image.name
        storage = guild.banner_image.storage

        assert delete_if_unreferenced(Guild, "banner_image", name, exclude_pk=guild.pk) is True
        assert not storage.exists(name)

    def it_keeps_a_file_a_sibling_row_still_shares():
        from core.files import delete_if_unreferenced
        from membership.models import Guild

        first = GuildFactory(banner_image=SimpleUploadedFile("shared.png", _png_bytes(), content_type="image/png"))
        shared_name = first.banner_image.name
        storage = first.banner_image.storage
        # Second guild points at the exact same stored object — what de-duplication produces.
        second = GuildFactory()
        Guild.objects.filter(pk=second.pk).update(banner_image=shared_name)

        assert delete_if_unreferenced(Guild, "banner_image", shared_name, exclude_pk=first.pk) is False
        assert storage.exists(shared_name)

        storage.delete(shared_name)  # cleanup

    def it_ignores_an_empty_name():
        from core.files import delete_if_unreferenced
        from membership.models import Guild

        assert delete_if_unreferenced(Guild, "banner_image", "") is False


@pytest.mark.django_db
def describe_delete_orphan_on_replace_with_shared_files():
    def it_keeps_the_old_file_when_another_row_still_uses_it():
        from membership.models import Guild

        first = GuildFactory(banner_image=SimpleUploadedFile("dupe.png", _png_bytes(), content_type="image/png"))
        shared_name = first.banner_image.name
        storage = first.banner_image.storage
        second = GuildFactory()
        Guild.objects.filter(pk=second.pk).update(banner_image=shared_name)

        first.banner_image = SimpleUploadedFile("fresh.png", _png_bytes(), content_type="image/png")
        delete_orphan_on_replace(first, "banner_image")

        assert storage.exists(shared_name)
        first.banner_image.close()
        storage.delete(shared_name)  # cleanup
