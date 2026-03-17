"""Signals for the membership app."""

from __future__ import annotations

import logging
from typing import Any

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models.signals import post_save
from django.dispatch import receiver

logger = logging.getLogger(__name__)

User = get_user_model()


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def ensure_user_has_member(sender: type, instance: Any, **kwargs: Any) -> None:
    """Auto-create a Member record for any user who doesn't have one."""
    from .models import Member, MembershipPlan

    try:
        instance.member
        return
    except Member.DoesNotExist:
        pass

    plan = MembershipPlan.objects.first()
    if plan is None:
        logger.warning(
            "Cannot auto-create Member for user %s: no MembershipPlan exists.",
            instance.username,
        )
        return

    name = instance.get_full_name() or instance.username
    Member.objects.create(
        user=instance,
        full_legal_name=name,
        email=instance.email or "",
        membership_plan=plan,
        status=Member.Status.ACTIVE,
    )
    logger.info("Auto-created Member for user %s.", instance.username)
