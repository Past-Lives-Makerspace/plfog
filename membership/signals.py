"""Signals for the membership app."""

from __future__ import annotations

import logging
from typing import Any

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models.signals import post_save
from django.dispatch import receiver

from membership.models import Member, VotePreference

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

    During allauth web signup, ``migrate_to_user`` is deliberately skipped here
    because allauth's ``setup_user_email`` (called after ``save_user``) asserts
    no EmailAddress rows exist yet. The adapter sets an ``is_in_allauth_signup``
    flag; the ``user_signed_up`` handler in ``core.signals`` calls
    ``migrate_to_user`` after allauth has finished its own email setup.
    """
    if not created:
        return

    from membership.services.provisioning import is_provisioning

    # FULL suppression (Review fix #3): while ``provision_user_for_member`` runs it
    # OWNS the create → EmailAddress → migrate → link chain. This signal must be a
    # complete no-op so a case/whitespace lookup miss here can't fall through to the
    # create branch and mint a SECOND Member (a OneToOne collision on ``user``).
    if is_provisioning():
        return

    from core.allauth_state import is_in_allauth_signup

    from .models import MemberEmail

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
            if not is_in_allauth_signup():
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
            if not is_in_allauth_signup():
                MemberEmail.objects.migrate_to_user(instance)
            return
        except MemberEmail.DoesNotExist:
            pass

    _auto_create_member_for_user(instance)


def _auto_create_member_for_user(instance: Any) -> None:
    """Create a fresh Member for a User with no pre-existing match (signal helper).

    Extracted from :func:`ensure_user_has_member` so the receiver stays under the
    complexity limit. Early-returns (logging) when no MembershipPlan exists; warns
    but proceeds when the user has no email (the emailless-account case).
    """
    from core.allauth_state import is_in_allauth_signup

    from .models import MemberEmail, MembershipPlan

    try:
        plan = MembershipPlan.objects.order_by("pk").earliest("pk")
    except MembershipPlan.DoesNotExist:
        logger.warning(
            "Cannot auto-create Member for user %s: no MembershipPlan exists.",
            instance.username,
        )
        return

    name = instance.get_full_name() or instance.username
    member_email = instance.email or ""
    if not member_email:
        logger.warning(
            "Creating Member for user %s (id=%s) with NO email — this account has no "
            "usable email and will surface in Manage Members → 'Missing email'. "
            "See the member-email-integrity spec.",
            instance.username,
            instance.pk,
        )
    Member.objects.create(
        user=instance,
        full_legal_name=name,
        _pre_signup_email=member_email,
        membership_plan=plan,
        status=Member.Status.ACTIVE,
    )
    if not is_in_allauth_signup():
        MemberEmail.objects.migrate_to_user(instance)
    logger.info("Auto-created Member for user %s with plan '%s'.", instance.username, plan.name)


@receiver(post_save, sender=Member)
def auto_provision_member_user(sender: type, instance: Member, created: bool, **kwargs: Any) -> None:
    """Going-forward invariant: a newly-created ACTIVE Member gets a linked User.

    Fires only on the FIRST save of an ACTIVE member that has no user yet — the
    common case being an Airtable import (``airtable_pull`` creates members via
    ``Member.objects.create``). Provisioning is SILENT and idempotent.

    Scope guards (Review fix #1): non-ACTIVE members (INVITED placeholders,
    FORMER/SUSPENDED) are skipped — provisioning an INVITED stub would bypass
    invite acceptance, and resurrecting a cancelled person is wrong. The link path
    inside ``provision_user_for_member`` writes only ``user`` (never ``status``).

    Recursion is impossible: ``provision_user_for_member`` re-saves the member with
    ``update_fields=["user"]`` (``created=False`` → this handler returns early), and
    the User it creates runs under the provisioning guard so ``ensure_user_has_member``
    no-ops — so no new ACTIVE userless Member is ever created mid-provision.
    """
    if not created or instance.user_id is not None or instance.status != Member.Status.ACTIVE:
        return

    from membership.services.provisioning import provision_user_for_member

    provision_user_for_member(instance)


@receiver(post_save, sender=VotePreference)
def _log_vote_activity(sender: type, instance: VotePreference, created: bool, **kwargs: Any) -> None:
    from core.models import SiteActivity

    kind = SiteActivity.Kind.VOTE_SUBMITTED if created else SiteActivity.Kind.VOTE_CHANGED
    actor = instance.member.user if instance.member_id else None
    SiteActivity.log(kind, actor=actor, target=instance.member)
