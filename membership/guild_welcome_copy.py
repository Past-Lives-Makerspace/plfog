"""Standard (default) copy for the per-guild join welcome email.

The welcome email is ON by default: when a member deliberately joins a guild (the
"Join This Guild" button with the welcome box checked, or the Discord ``/join-guild``
command) they get a warm welcome, whether or not the guild wrote their own. A guild MAY
still customize the subject and body on the Welcome Email tab of their guild editor; when
they leave those blank, the standard copy below stands in.

Kept in one place so it is the single source of truth: the send path and the
copy-review gallery both read it, so what a reviewer sees on copy-review.pastlives.space
is exactly what ships. The body is rendered through the rich-text email filter, so plain
sentences with line breaks are fine; the surrounding template already greets the member
by name, adds the "Welcome to <guild>" eyebrow (linked to the guild page), and supplies
the static "what you can do on your guild page" section.
"""

from __future__ import annotations

STANDARD_WELCOME_BODY = (
    "We're really glad you're here. Following a guild means you'll hear about what's happening, "
    "you'll show up on the guild roster, and you'll pick up the guild's Discord role automatically. "
    "Look around your guild page whenever you like, and come say hi in the channel. See you around the space!"
)


def standard_welcome_subject(guild_name: str) -> str:
    """The default welcome subject line for a guild with no custom subject set."""
    return f"Welcome to {guild_name}!"
