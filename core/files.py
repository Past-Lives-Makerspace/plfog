"""Storage helpers for ImageField / FileField models."""

from __future__ import annotations

from django.core.files.storage import default_storage
from django.db import models


def delete_if_unreferenced(
    model: type[models.Model],
    field_name: str,
    name: str,
    *,
    exclude_pk: object = None,
) -> bool:
    """Delete a stored file, unless another row of ``model`` still points at it.

    De-duplicated images are *shared*: several rows carry the same storage key
    (see :func:`core.images.store_content_addressed`), so deleting the object
    because one row moved off it would blank the picture on every other row.
    Checks the database first and only removes a genuine orphan.

    Args:
        model: The model whose ``field_name`` column holds storage keys.
        field_name: Name of the File/ImageField column.
        name: The storage key to delete.
        exclude_pk: Primary key to ignore when looking for other references —
            the row that just moved off this key, whose column may or may not
            have been written yet, so it must never count as a reference.

    Returns:
        True when the object was deleted, False when it is still referenced.
    """
    if not name:
        return False
    # ``_base_manager``, not ``_default_manager``: on a soft-deleting model the default
    # manager filters out deleted rows, so a soft-deleted sibling still pointing at this
    # key would not count as a reference and the object would be deleted out from under
    # it. Undeleting that row would then surface a broken image.
    others = model._base_manager.filter(**{field_name: name})
    if exclude_pk is not None:
        others = others.exclude(pk=exclude_pk)
    if others.exists():
        return False
    default_storage.delete(name)
    return True


def delete_orphan_on_replace(instance: models.Model, field_name: str) -> None:
    """Delete the storage file backing ``field_name`` when its value changes.

    Call from ``Model.save()`` BEFORE ``super().save()``. On update, fetches the
    pre-save value from the database and, if the user replaced or cleared the
    file, removes the old object from storage so it does not orphan in R2.

    Safe to call on creates (returns early), when the field is unchanged, and
    when the old object is still referenced by a sibling row.
    """
    if not instance.pk:
        return
    model = type(instance)
    try:
        old_instance = model._default_manager.only(field_name).get(pk=instance.pk)
    except model.DoesNotExist:  # type: ignore[attr-defined]
        return
    old_file = getattr(old_instance, field_name)
    new_file = getattr(instance, field_name)
    new_name = getattr(new_file, "name", "") or ""
    if old_file and old_file.name and old_file.name != new_name:
        delete_if_unreferenced(model, field_name, old_file.name, exclude_pk=instance.pk)
