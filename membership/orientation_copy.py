"""Standard (default) copy for the post-orientation thank-you email.

The thank-you email is ON by default: when a member finishes an orientation they get a
thank-you, whether or not the guild wrote their own. A guild MAY still customize the
subject and body in their Orientations settings; when they leave those blank, the
standard copy below stands in.

Kept in one place so it is the single source of truth: the send path and the
copy-review gallery both read it, so what a reviewer sees on copy-review.pastlives.space
is exactly what ships. The body is rendered through the rich-text email filter, so plain
sentences with line breaks are fine; the surrounding template already greets the member
by name and adds the "Thanks for orienting with <guild>" eyebrow.
"""

from __future__ import annotations

STANDARD_THANKYOU_BODY = (
    "You're all set. Your orientation is complete and you're cleared to start using the space. "
    "Come by during open studio hours, ask in the guild channel any time you have a question, "
    "and we'll see you around. Welcome to the guild!"
)


def standard_thankyou_subject(guild_name: str) -> str:
    """The default thank-you subject line for a guild with no custom subject set."""
    return f"Thanks for getting oriented with {guild_name}!"
