"""The portal activity log on Discord (#1330): one line per happening, in a leadership only channel.

The one seam is :meth:`core.models.SiteActivity.log`: a row whose kind is in
:data:`core.models.ACTIVITY_LOG_KINDS` queues :func:`post_activity` once its transaction
commits. The task posts the row's Activity Feed wording (:attr:`SiteActivity.line`), who did
it, and an absolute link to the thing in the portal, to Site Settings' Activity log webhook.
Through the durable queue, never inline: a slow or failing Discord never reaches the page or
job that wrote the row (#845). Blank webhook or staging posts nothing. A post Discord could not
take for now (a timeout, a 5xx, a 429) is retried a few times with backoff.
"""

from __future__ import annotations

import logging
import re
from datetime import timedelta
from typing import TYPE_CHECKING

from django.tasks import task
from django.utils import timezone

from core.events.discord import PostOutcome, escape_discord_markdown, post_text
from core.tasks import enqueue_at_on_commit
from core.urls_util import member_absolute_url

if TYPE_CHECKING:
    from core.models import SiteActivity

logger = logging.getLogger(__name__)

#: Between the parts of a line: what happened, who, and the link.
SEPARATOR = " · "

#: The row's payload key that records its line was handed to Discord, so a second run posts nothing.
POSTED_KEY = "activity_log_posted"

#: How long each retry waits, in order; one retry per entry, so a line is tried at most four times.
RETRY_DELAYS = (timedelta(minutes=1), timedelta(minutes=5), timedelta(minutes=25))
MAX_ATTEMPTS = len(RETRY_DELAYS) + 1

# The brackets and parentheses of a masked link, ``[text](url)``: escaped in member text so a
# class titled "[Pay here](https://...)" shows as typed, never as a link.
_LINK_CHARS_RE = re.compile(r"([\[\]()])")


def escape_line_text(text: str) -> str:
    """``text`` as Discord should show it, as typed: no bold, code or masked link from a member's words."""
    return _LINK_CHARS_RE.sub(r"\\\1", escape_discord_markdown(text))


def activity_link(activity: SiteActivity) -> str:
    """The absolute portal link for the row's target, or "" when it has none.

    A target says where it lives with ``activity_url`` (a path or an absolute URL), as it
    words its own line with ``describe_activity``.
    """
    activity_url = getattr(activity.target, "activity_url", None)
    if activity_url is None:
        return ""
    return member_absolute_url(activity_url)


def activity_name(activity: SiteActivity) -> str:
    """Who the line names: never a legal name and never an email address.

    An actor with a member row by their preferred name only (:attr:`Member.activity_log_name`,
    ``Member #<id>`` without one), an account with none as ``User #<id>``. With no actor, the
    target's ``activity_log_name`` (a registrant), else what the feed says: "Not recorded" or
    "System".
    """
    actor = activity.actor
    if actor is not None:
        member = getattr(actor, "member", None)
        return member.activity_log_name if member is not None else f"User #{actor.pk}"
    if name := getattr(activity.target, "activity_log_name", ""):
        return str(name)
    return "Not recorded" if activity.line.actor_unrecorded else "System"


def activity_headline(activity: SiteActivity) -> str:
    """The row's headline for Discord: the target's ``describe_activity_log`` when it names a member, else the feed's."""
    describe = getattr(activity.target, "describe_activity_log", None)
    return describe(activity).headline if describe is not None else activity.line.headline


def activity_message(activity: SiteActivity) -> str:
    """The one Discord line for ``activity``: its headline, who acted, and its link in angle brackets.

    The feed's wording, with every member named by preferred name only (:func:`activity_headline`,
    :func:`activity_name`).

    The headline and the name are escaped (:func:`escape_line_text`), so a member's words show as
    typed; the link sits in ``<>`` so Discord shows no preview card under a log line.
    """
    parts = [escape_line_text(activity_headline(activity)), escape_line_text(activity_name(activity))]
    link = activity_link(activity)
    if link:
        parts.append(f"<{link}>")
    return SEPARATOR.join(parts)


@task
def post_activity(activity_id: int, attempt: int = 1) -> None:
    """Post one activity row's line to the Activity log webhook; blank posts nothing.

    Safe to run twice: the row is claimed first by a conditional update that marks its payload
    (:data:`POSTED_KEY`), and only the run that claimed it posts. When Discord could not take it
    for now (:attr:`PostOutcome.RETRY`) the claim is released and the line queued again after
    :data:`RETRY_DELAYS`; past :data:`MAX_ATTEMPTS`, or on a refusal no retry would change, it
    logs an error and stops. A row deleted before the worker reached it posts nothing. Never
    raises: Discord's answers are outcomes, not errors.

    Args:
        activity_id: The :class:`~core.models.SiteActivity` row.
        attempt: Which try this is, from 1.
    """
    from core.models import SiteActivity, SiteConfiguration

    webhook = SiteConfiguration.load().discord_activity_webhook_url.strip()
    if not webhook:
        return
    activity = SiteActivity.objects.select_related("actor__member", "target_ct").filter(pk=activity_id).first()
    if activity is None:
        return
    unclaimed = SiteActivity.objects.filter(pk=activity.pk).exclude(payload__has_key=POSTED_KEY)
    if not unclaimed.update(payload={**activity.payload, POSTED_KEY: True}):
        return
    outcome = post_text(webhook, activity_message(activity))
    if outcome is PostOutcome.RETRY and attempt < MAX_ATTEMPTS:
        SiteActivity.objects.filter(pk=activity.pk).update(payload=activity.payload)
        enqueue_at_on_commit(post_activity, timezone.now() + RETRY_DELAYS[attempt - 1], activity.pk, attempt + 1)
        return
    if outcome in (PostOutcome.RETRY, PostOutcome.REFUSED):
        logger.error(
            "Activity log gave up on activity %s (%s) after %s attempt(s): Discord answered %s.",
            activity.pk,
            activity.kind,
            attempt,
            outcome,
        )
