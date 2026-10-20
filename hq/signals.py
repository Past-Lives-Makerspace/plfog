"""The HQ's file cleanup on delete: a ledger receipt or a flyer leaves storage once the delete that removed its row
commits, however the row went (its own Delete, or a hub deleted with everything on it)."""

from __future__ import annotations

from functools import partial
from typing import Any

from django.db import transaction
from django.db.models import Model
from django.db.models.signals import post_delete
from django.dispatch import receiver

from core.files import delete_if_unreferenced
from hq.models import FlyerSlot, HubFlyer, LedgerEntry


def _delete_after_commit(model: type[Model], field_name: str, name: str) -> None:
    """Delete the stored file ``name`` once this transaction commits, unless another row still points at it."""
    if name:
        transaction.on_commit(partial(delete_if_unreferenced, model, field_name, name))


@receiver(post_delete, sender=LedgerEntry, dispatch_uid="hq_ledger_entry_receipt")
def _delete_receipt(sender: type[LedgerEntry], instance: LedgerEntry, **kwargs: Any) -> None:
    _delete_after_commit(LedgerEntry, "receipt", instance.receipt.name or "")


@receiver(post_delete, sender=HubFlyer, dispatch_uid="hq_hub_flyer_files")
def _delete_flyers(sender: type[HubFlyer], instance: HubFlyer, **kwargs: Any) -> None:
    for slot in FlyerSlot.values:
        _delete_after_commit(HubFlyer, slot, getattr(instance, slot).name or "")
