"""The portal activity log on Discord (#1330): one line per happening, in a leadership only channel.

The one seam is :meth:`core.models.SiteActivity.log`: a row whose kind is in
:data:`core.models.ACTIVITY_LOG_KINDS` queues :func:`post_activity` once its transaction
commits. The task posts the row's Activity Feed wording (:attr:`SiteActivity.line`), who did
it, and an absolute link to the thing in the portal, to Site Settings' Activity log webhook.
Through the durable queue, never inline: a slow or failing Discord never reaches the page or
job that wrote the row (#845). Blank webhook or staging posts nothing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.tasks import task

from core.events.discord import escape_discord_markdown, post_text
from core.urls_util import member_absolute_url

if TYPE_CHECKING:
    from core.models import SiteActivity

#: Between the parts of a line: what happened, who, and the link.
SEPARATOR = " · "


def activity_link(activity: SiteActivity) -> str:
    """The absolute portal link for the row's target, or "" when it has none.

    A target says where it lives with ``activity_url`` (a path or an absolute URL), as it
    words its own line with ``describe_activity``.
    """
    activity_url = getattr(activity.target, "activity_url", None)
    if activity_url is None:
        return ""
    return member_absolute_url(activity_url)


def activity_message(activity: SiteActivity) -> str:
    """The one Discord line for ``activity``: its feed headline, who acted, and its link in angle brackets.

    The headline and the name are escaped so a name with an asterisk shows as typed; the link
    sits in ``<>`` so Discord shows no preview card under a log line.
    """
    parts = [escape_discord_markdown(activity.line.headline), escape_discord_markdown(activity.by)]
    link = activity_link(activity)
    if link:
        parts.append(f"<{link}>")
    return SEPARATOR.join(parts)


#: The row's payload key that records its line was handed to Discord, so a second run posts nothing.
POSTED_KEY = "activity_log_posted"


@task
def post_activity(activity_id: int) -> None:
    """Post one activity row's line to the Activity log webhook; blank posts nothing.

    Safe to run twice: the row is claimed first by a conditional update that marks its payload
    (:data:`POSTED_KEY`), and only the run that claimed it posts. A row deleted before the
    worker reached it posts nothing. Best effort, like every Discord post: a failed post is
    logged and not retried.
    """
    from core.models import SiteActivity, SiteConfiguration

    webhook = SiteConfiguration.load().discord_activity_webhook_url.strip()
    if not webhook:
        return
    activity = SiteActivity.objects.select_related("actor__member", "target_ct").filter(pk=activity_id).first()
    if activity is None:
        return
    claimed = (
        SiteActivity.objects.filter(pk=activity.pk)
        .exclude(payload__has_key=POSTED_KEY)
        .update(payload={**activity.payload, POSTED_KEY: True})
    )
    if not claimed:
        return
    post_text(webhook, activity_message(activity))
