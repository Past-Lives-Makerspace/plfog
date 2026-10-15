"""The Door Code card on the member portal home page (#813).

``DoorCodeCard.for_request`` decides what one viewer gets: the code for an active member, the
code plus the Change control and the changer's name for an admin, and nothing for anyone else.
Both gates read ``request.view_as``, so an admin previewing as Guest sees what a guest sees.
``DoorCodeForm`` is the admin's one field; ``hub.door_code_views`` saves it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from django import forms

from core.models import SiteConfiguration
from membership.names import user_name_or_email

if TYPE_CHECKING:
    from django.contrib.auth.models import User
    from django.http import HttpRequest

DOOR_CODE_MAX_LENGTH = SiteConfiguration._meta.get_field("door_code").max_length
BLANK_MESSAGE = "Enter a door code."


@dataclass(frozen=True)
class DoorCodeCard:
    """What the home page's Door Code card shows one viewer.

    ``code`` is blank while none is set; the template shows members no card then and shows
    admins a prompt to set one. ``changed_by`` is filled for admins only.
    """

    code: str
    changed_at: datetime | None
    can_change: bool
    changed_by: str = ""

    @classmethod
    def for_request(cls, request: HttpRequest) -> DoorCodeCard | None:
        """The card for this viewer, or None when they may not see the code.

        Only a viewer holding the member role sees it, and ``compute_actual_roles`` gives
        that role to an ACTIVE member (or a superuser) alone: an unlinked account, a guest,
        an invited, former or suspended member never does. An admin previewing a role below
        admin loses the Change control; previewing Guest loses the card.
        """
        view_as = request.view_as  # type: ignore[attr-defined]  # ViewAsMiddleware sets it
        if not view_as.is_member:
            return None
        config = SiteConfiguration.load()
        can_change = bool(view_as.is_admin)
        changed_by = ""
        if can_change and config.door_code_changed_by_id is not None:
            changed_by = user_name_or_email(config.door_code_changed_by)  # type: ignore[arg-type]  # id checked
        return cls(
            code=config.door_code,
            changed_at=config.door_code_changed_at,
            can_change=can_change,
            changed_by=changed_by,
        )

    @property
    def is_shown(self) -> bool:
        """Members see the card only once a code is set; admins always, to set one."""
        return bool(self.code) or self.can_change


class DoorCodeForm(forms.Form):
    """The admin's Change form: any short text, trimmed, 1 to 20 characters."""

    door_code = forms.CharField(
        max_length=DOOR_CODE_MAX_LENGTH,
        strip=True,
        label="Door code",
        help_text=f"Up to {DOOR_CODE_MAX_LENGTH} characters. Members see it on their home page as soon as you save.",
        error_messages={
            "required": BLANK_MESSAGE,
            "max_length": f"Keep the door code to {DOOR_CODE_MAX_LENGTH} characters or fewer.",
        },
        widget=forms.TextInput(attrs={"autocomplete": "off", "spellcheck": "false"}),
    )

    def save(self, *, by: User, now: datetime) -> SiteConfiguration:
        """Store the code with who saved it and when."""
        config = SiteConfiguration.load()
        config.set_door_code(self.cleaned_data["door_code"], by=by, now=now)
        return config
