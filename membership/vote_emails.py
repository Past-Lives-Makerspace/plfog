"""Email sending for guild voting via Mailgun (django-anymail)."""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string

from . import vote_tokens


def send_voting_emails(
    members: list[dict[str, Any]],
    session_id: int,
    session_name: str,
    close_date: Any,
    base_url: str,
) -> dict[str, Any]:
    """Send individual voting emails to all members.

    Returns dict with sent_count and errors list.
    """
    sent_count = 0
    errors = []

    for member in members:
        if not member.get("email"):
            errors.append(f"No email for {member['name']}")
            continue

        token = vote_tokens.generate_vote_token(member["record_id"], session_id)
        vote_url = f"{base_url}/voting/vote/{token}"

        subject = f"Vote for your guilds - {session_name}"
        html_message = render_to_string(
            "membership/voting/voting_email.html",
            {
                "member_name": member["name"],
                "session_name": session_name,
                "close_date": close_date,
                "vote_url": vote_url,
            },
        )
        plain_message = (
            f"Hi {member['name']},\n\n"
            f"It's time to vote for your top 3 guilds for {session_name}!\n\n"
            f"Vote here: {vote_url}\n\n"
            f"Voting closes: {close_date}\n\n"
            f"- Past Lives Makerspace"
        )

        try:
            send_mail(
                subject=subject,
                message=plain_message,
                from_email=settings.EMAIL_FROM,
                recipient_list=[member["email"]],
                html_message=html_message,
                fail_silently=False,
            )
            sent_count += 1
        except Exception as e:
            errors.append(f"Failed to send to {member['name']} ({member['email']}): {e}")

    return {"sent_count": sent_count, "errors": errors}


def send_results_email(
    recipients: list[str],
    session_name: str,
    results_data: dict[str, Any],
) -> None:
    """Send calculated results to leadership."""
    subject = f"Guild Voting Results - {session_name}"
    html_message = render_to_string(
        "membership/voting/results_email.html",
        {"session_name": session_name, "results": results_data},
    )

    lines = [f"Guild Voting Results - {session_name}", ""]
    for r in results_data["results"]:
        lines.append(
            f"{r['guild_name']}: ${r['disbursement']:.2f} "
            f"(1st:{r['votes_1st']} 2nd:{r['votes_2nd']} 3rd:{r['votes_3rd']})"
        )
    lines.extend(["", f"Total pool: ${results_data['total_pool']}", f"Votes cast: {results_data['votes_cast']}"])
    plain_message = "\n".join(lines)

    send_mail(
        subject=subject,
        message=plain_message,
        from_email=settings.EMAIL_FROM,
        recipient_list=recipients,
        html_message=html_message,
        fail_silently=False,
    )
