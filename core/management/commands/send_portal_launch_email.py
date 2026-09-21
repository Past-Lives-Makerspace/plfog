"""Email every active member that the Member Portal is live, through one of two doors.

A member who has signed in before gets the announcement with a sign-in button, as a
``site_announcement`` broadcast (email, in-app bell, Discord only with ``--discord``). A
member who has never signed in gets the same announcement as an activation invite: the
forced ``member.login_invite`` email, their address pre-filled on the login-code page. The
copy and the rollout table live in :mod:`core.launch_email`.

SAFETY GUARD, as ``send_release_email``: without ``--confirm`` it sends NOTHING. It resolves
both audiences read-only and prints the counts. Both sends share one delivery-ledger period,
so a confirmed re-run reaches only whoever was missed and emails nobody twice. An invite
that lands also stamps the welcome ledger, so the 6 AM welcome automation does not send the
same person a second sign-in link the next morning.

Usage (Render one-off job):
    python manage.py send_portal_launch_email                      # dry run: counts only
    python manage.py send_portal_launch_email --test you@x.com     # both variants to one inbox
    python manage.py send_portal_launch_email --confirm            # the real send
    python manage.py send_portal_launch_email --confirm --discord  # and post to Discord
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from django.core.management.base import BaseCommand, CommandError

if TYPE_CHECKING:
    from core.events.channels import Channel
    from membership.models import Member


def _email_count(pairs: list[tuple[int, Channel]]) -> int:
    """How many ``(user, channel)`` entries are the EMAIL channel."""
    from core.events.channels import Channel

    return sum(1 for _, channel in pairs if channel is Channel.EMAIL)


class Command(BaseCommand):
    help = (
        "Email every active member that the Member Portal is live: an activation invite to anyone who "
        "has never signed in, the announcement to everyone else. Requires --confirm to actually send."
    )

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--confirm",
            action="store_true",
            help="REQUIRED to actually send. Without it, prints both audience counts and sends nothing.",
        )
        parser.add_argument(
            "--test",
            metavar="EMAIL",
            default="",
            help="Send both variants to this one address (skipping the notification spine) and nothing else.",
        )
        parser.add_argument(
            "--discord",
            action="store_true",
            help="Also post the announcement to Discord (off by default).",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        from core.events import resolvers
        from core.events.registry import get_event
        from core.launch_email import send_launch_announcement, send_launch_previews
        from membership.models import Member

        if options["test"]:
            to_addr: str = options["test"].strip()
            if "@" not in to_addr:
                raise CommandError(f"--test must be an email address, got: {to_addr!r}")
            send_launch_previews(to_addr)
            self.stdout.write(self.style.SUCCESS(f"Both launch email variants sent to {to_addr}."))
            return

        # Read-only audience resolution: the SAME resolver the broadcast uses, so the dry-run
        # count equals the send's. Resolving recipients sends nothing.
        announcement_count = len(resolvers.resolve(get_event("site_announcement").recipient, {}))
        invitees = list(Member.objects.awaiting_first_sign_in())
        discord = "on" if options["discord"] else "off"

        if not options["confirm"]:
            self.stdout.write("DRY RUN: no email sent. Re-run with --confirm to send.")
            self.stdout.write(f"  Announcement (signed in before):     {announcement_count} member(s)")
            self.stdout.write(f"  Activation invite (never signed in): {len(invitees)} member(s)")
            self.stdout.write(f"  Discord: {discord}")
            return

        result = send_launch_announcement(discord=options["discord"])
        self.stdout.write(
            self.style.SUCCESS(
                f"Announcement: emailed {_email_count(result.delivered)} of {result.recipient_count} "
                f"signed-in member(s); {_email_count(result.skipped_duplicates)} already sent. Discord: {discord}."
            )
        )
        self._send_invites(invitees)

    def _send_invites(self, invitees: list[Member]) -> None:
        """Invite each never-signed-in member, one failure never aborting the rest."""
        from core.launch_email import send_launch_invite

        sent = already = undelivered = skipped = 0
        for member in invitees:
            try:
                result = send_launch_invite(member)
            except Exception as exc:  # noqa: BLE001 - one bad member must not abort the batch
                skipped += 1
                self.stderr.write(self.style.ERROR(f"  ✗ invite {member.pk} ({member.display_name}): {exc}"))
                continue
            if _email_count(result.delivered):
                sent += 1
                # The same stamp the admin's "send login invite" button leaves: tomorrow's
                # welcome automation must not send this person a second sign-in link.
                member.record_welcome_sent()
            elif _email_count(result.skipped_duplicates):
                already += 1
            else:
                undelivered += 1
                self.stderr.write(
                    self.style.WARNING(
                        f"  ! invite {member.pk} ({member.display_name}): not delivered, re-run to retry"
                    )
                )
        summary = (
            f"Activation invites: sent {sent}; {already} already sent; {undelivered} not delivered; skipped {skipped}."
        )
        style = self.style.SUCCESS if not (undelivered or skipped) else self.style.WARNING
        self.stdout.write(style(summary))
