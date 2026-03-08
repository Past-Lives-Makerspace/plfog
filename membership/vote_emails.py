"""Email sending for guild voting results via Mailgun (django-anymail)."""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string


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
