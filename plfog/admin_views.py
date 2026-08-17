"""Custom admin views."""

from __future__ import annotations

from django.contrib import admin, messages
from django.contrib.admin.views.decorators import staff_member_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from allauth.account.models import EmailAddress

from core.models import Invite
from membership import email_aliases
from membership.forms import AddEmailAliasForm, InviteMemberForm
from membership.models import Member


@staff_member_required
def site_announcement(request: HttpRequest) -> HttpResponse:
    """Admin view to broadcast a site-wide announcement to all active members.

    On POST it fires the ``site_announcement`` event on the notification spine: an
    in-app bell row for every active member, an opt-out email, and a single Discord
    broadcast. The event itself logs the ``site_announcement`` SiteActivity (its
    registry ``activity_kind``), so no separate ``SiteActivity.log`` is needed here.
    """
    from hub.forms import SiteAnnouncementForm

    if request.method == "POST":
        form = SiteAnnouncementForm(request.POST)
        if form.is_valid():
            from core.events.channels import Channel, Message
            from core.events.emit import emit

            title = form.cleaned_data["title"]
            body = form.cleaned_data["body"]
            # Absolute URL — Discord embeds and email links need a full host, not a
            # bare "/". The admin is served on the member host, so this resolves to
            # the hub home.
            site_url = request.build_absolute_uri("/")
            # A custom phone-notification text overrides ONLY the push channel — the
            # bell/email/Discord still render from the announcement body. Left blank,
            # push falls back to the event's own (footer-free) push copy.
            push_message = form.cleaned_data["push_message"]
            push_override: dict[Channel, Message] = {}
            if push_message:
                push_override[Channel.PUSH] = Message(
                    title=title, body=push_message, url=site_url, trigger_kind="site_announcement"
                )
            result = emit(
                "site_announcement",
                actor=request.user if request.user.is_authenticated else None,
                context={
                    "member_name": "there",
                    "announcement_title": title,
                    "announcement_body": body,
                    "site_url": site_url,
                },
                url=site_url,
                messages=push_override or None,
                # Unique per send — every other emit() caller keys its idempotency
                # window. Without it all site announcements collapse onto one
                # EventDelivery slot and only the first ever delivers.
                period=f"site:{timezone.now():%Y%m%d%H%M%S%f}",
                # Honor the "Also post to Discord" checkbox — unchecking it must
                # silence the broadcast (the field is always present in cleaned_data
                # since it's required=False with initial=True).
                suppress_broadcast=not form.cleaned_data["post_to_discord"],
            )
            messages.success(request, f"Announcement sent to {result.recipient_count} member(s).")
            return redirect("admin:index")
    else:
        form = SiteAnnouncementForm()
    context = {**admin.site.each_context(request), "form": form}
    return render(request, "admin/site_announcement.html", context)


@staff_member_required
def invite_member(request: HttpRequest) -> HttpResponse:
    """Admin view to invite a new member by email."""
    if request.method == "POST":
        form = InviteMemberForm(request.POST)
        if form.is_valid():
            try:
                Invite.create_and_send(
                    email=form.cleaned_data["email"],
                    invited_by=request.user,
                )
                messages.success(request, f"Invite sent to {form.cleaned_data['email']}.")
                return redirect("admin:membership_member_changelist")
            except ValueError as e:
                messages.error(request, str(e))
    else:
        form = InviteMemberForm()
    context = {**admin.site.each_context(request), "form": form}
    return render(request, "admin/membership/invite_member.html", context)


# ---------------------------------------------------------------------------
# Member email aliases — admin management page
# ---------------------------------------------------------------------------
#
# Dedicated page at /admin/members/<pk>/aliases/ that lets staff manage
# allauth.EmailAddress rows for a linked Member's User. Mirrors the Snapshot
# Analyzer pattern (GET page + POST action endpoints, all redirecting back).
#
# See docs/superpowers/specs/2026-04-11-admin-email-aliases-design.md.


@staff_member_required
def member_aliases(request: HttpRequest, pk: int) -> HttpResponse:
    """GET — render the aliases management page for a linked member."""
    member = get_object_or_404(Member, pk=pk)
    if member.user_id is None:
        messages.info(
            request,
            "This member hasn't signed up yet. Use the Staged Emails section "
            "on the member page to manage their pre-signup addresses.",
        )
        return redirect("admin:membership_member_change", member.pk)

    aliases = EmailAddress.objects.filter(user=member.user).order_by("-primary", "email")
    add_form = AddEmailAliasForm(user=member.user)
    context = {
        **admin.site.each_context(request),
        "member": member,
        "aliases": aliases,
        "add_form": add_form,
    }
    return render(request, "admin/membership/member/aliases.html", context)


@require_POST
@staff_member_required
def member_aliases_add(request: HttpRequest, pk: int) -> HttpResponse:
    """POST — create a verified, non-primary EmailAddress for the member's User."""
    member = get_object_or_404(Member, pk=pk)
    if member.user_id is None:
        messages.error(request, "This member has no linked user.")
        return redirect("admin:membership_member_change", member.pk)

    form = AddEmailAliasForm(request.POST, user=member.user)
    if not form.is_valid():
        aliases = EmailAddress.objects.filter(user=member.user).order_by("-primary", "email")
        context = {
            **admin.site.each_context(request),
            "member": member,
            "aliases": aliases,
            "add_form": form,
        }
        return render(request, "admin/membership/member/aliases.html", context)

    for level, msg in email_aliases.add_alias(member.user, form.cleaned_data["email"]):
        getattr(messages, level)(request, msg)
    return redirect("admin_member_aliases", pk=member.pk)


@require_POST
@staff_member_required
def member_aliases_remove(request: HttpRequest, pk: int, email_pk: int) -> HttpResponse:
    """POST — delete an EmailAddress unless it's the member's only one.

    Safety rules (from spec):
    1. Cannot remove the only EmailAddress — refuse with error flash.
    2. If removing the primary and >=1 verified remains, promote the
       lowest-pk verified row via set_as_primary(conditional=False).
    3. If removing would leave the user with zero verified emails, proceed
       but flash a loud warning.
    """
    member = get_object_or_404(Member, pk=pk)
    if member.user_id is None:
        messages.error(request, "This member has no linked user.")
        return redirect("admin:membership_member_change", member.pk)

    alias = get_object_or_404(EmailAddress, pk=email_pk, user=member.user)

    for level, msg in email_aliases.remove_alias(alias):
        getattr(messages, level)(request, msg)
    return redirect("admin_member_aliases", pk=member.pk)


@require_POST
@staff_member_required
def member_aliases_set_primary(request: HttpRequest, pk: int, email_pk: int) -> HttpResponse:
    """POST — promote a verified alias to primary.

    Uses allauth's EmailAddress.set_as_primary(conditional=False), which
    demotes the current primary and updates User.email in one call.
    Unverified emails are rejected (allauth's own guard is version-dependent;
    we gate here to be sure).
    """
    member = get_object_or_404(Member, pk=pk)
    if member.user_id is None:
        messages.error(request, "This member has no linked user.")
        return redirect("admin:membership_member_change", member.pk)

    alias = get_object_or_404(EmailAddress, pk=email_pk, user=member.user)

    for level, msg in email_aliases.set_primary(alias):
        getattr(messages, level)(request, msg)
    return redirect("admin_member_aliases", pk=member.pk)


@require_POST
@staff_member_required
def member_aliases_toggle_verified(request: HttpRequest, pk: int, email_pk: int) -> HttpResponse:
    """POST — flip the verified flag on an alias.

    Warns loudly if the admin just un-verified the primary email (login
    still works until another email is promoted, but it's fragile).
    """
    member = get_object_or_404(Member, pk=pk)
    if member.user_id is None:
        messages.error(request, "This member has no linked user.")
        return redirect("admin:membership_member_change", member.pk)

    alias = get_object_or_404(EmailAddress, pk=email_pk, user=member.user)

    for level, msg in email_aliases.toggle_verified(alias):
        getattr(messages, level)(request, msg)
    return redirect("admin_member_aliases", pk=member.pk)
