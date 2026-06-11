"""Signals for the membership app."""

from __future__ import annotations

import logging
from typing import Any

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models.signals import post_save
from django.dispatch import receiver

from membership.models import VotePreference

logger = logging.getLogger(__name__)

User = get_user_model()


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def ensure_user_has_member(sender: type, instance: Any, created: bool, **kwargs: Any) -> None:
    """Auto-create or link a Member record for any user who doesn't have one.

    After linking (or creating) a Member, also promotes any pre-signup
    ``MemberEmail`` staging rows for that member into
    ``allauth.account.EmailAddress`` so the user can log in via any of them.
    See ``docs/superpowers/specs/2026-04-07-user-email-aliases-design.md``.

    Gated on ``created=True``: every branch of this signal is only meaningful
    on the first save of a User. Re-running ``migrate_to_user`` on subsequent
    saves was a 1.4.0 bug — it would force-re-promote ``Member._pre_signup_email``
    to primary and silently revert any other primary the member or admin had
    set via allauth, because allauth's ``set_as_primary`` calls ``user.save()``
    internally. Skipping non-creation saves keeps allauth's primary stable.
    """
    if not created:
        return

    from .models import Member, MemberEmail, MembershipPlan

    # On created=True the user was just saved; no Member can yet reference it
    # via the OneToOne (admin save_model and test fixtures both link the Member
    # AFTER this signal has returned). Skip the "already has member" branch.

    email = getattr(instance, "email", "") or ""
    if email:
        # Check primary email on Member
        try:
            member = Member.objects.get(_pre_signup_email__iexact=email, user__isnull=True)
            member.user = instance
            member.full_legal_name = instance.get_full_name() or member.full_legal_name or instance.username
            member.status = Member.Status.ACTIVE
            member.save(update_fields=["user", "full_legal_name", "status"])
            logger.info("Linked existing Member (primary email) to user %s.", instance.username)
            MemberEmail.objects.migrate_to_user(instance)
            return
        except Member.DoesNotExist:
            pass

        # Check email aliases (pre-signup staging table)
        try:
            alias = MemberEmail.objects.select_related("member").get(email__iexact=email, member__user__isnull=True)
            member = alias.member
            member.user = instance
            member.full_legal_name = instance.get_full_name() or member.full_legal_name or instance.username
            member.status = Member.Status.ACTIVE
            member.save(update_fields=["user", "full_legal_name", "status"])
            logger.info("Linked existing Member (alias email %s) to user %s.", email, instance.username)
            MemberEmail.objects.migrate_to_user(instance)
            return
        except MemberEmail.DoesNotExist:
            pass

    # No pre-existing member found; create one
    try:
        plan = MembershipPlan.objects.order_by("pk").earliest("pk")
    except MembershipPlan.DoesNotExist:
        logger.warning(
            "Cannot auto-create Member for user %s: no MembershipPlan exists.",
            instance.username,
        )
        return

    name = instance.get_full_name() or instance.username
    Member.objects.create(
        user=instance,
        full_legal_name=name,
        _pre_signup_email=instance.email or "",
        membership_plan=plan,
        status=Member.Status.ACTIVE,
    )
    MemberEmail.objects.migrate_to_user(instance)
    logger.info("Auto-created Member for user %s with plan '%s'.", instance.username, plan.name)


@receiver(post_save, sender=VotePreference)
def _log_vote_activity(sender: type, instance: VotePreference, created: bool, **kwargs: Any) -> None:
    from core.models import SiteActivity

    kind = SiteActivity.Kind.VOTE_SUBMITTED if created else SiteActivity.Kind.VOTE_CHANGED
    actor = instance.member.user if instance.member_id else None
    SiteActivity.log(kind, actor=actor, target=instance.member)
