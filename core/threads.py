"""The conversation thread every domain shares: one message model shape, one rule for who hears.

A thread is a list of messages between two sides of one object: the **member side** (the person
the object belongs to: a feedback sender, a member with an orientation booking) and the **staff
side** (whoever answers for Past Lives on it: the Webmasters, an orientation's orientors; an admin
is always staff side). Each domain keeps its own concrete message and photo tables (no generic
foreign key), built on :class:`ThreadMessage` and :class:`ThreadPhoto`.

Which side a message is from is derived, never stored: a message by anyone but the member side's
account is from staff, and so is one with no author (a note carried over from before threads).

The one rule: **posting a message always tells the other side, and only posting does**
(:meth:`ThreadMessage.post`). A message written as part of another action that already tells the
other side (a decline email that carries its note) is saved with :meth:`ThreadMessage.write`, which
tells nobody. Each concrete model fills in the small contract below: whose account is the member
side, what each side signs as, and how each side is told.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar, Self

from django.conf import settings
from django.db import models, transaction
from django.utils import timezone

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from datetime import datetime

    from django.core.files.uploadedfile import UploadedFile


class ThreadMessage(models.Model):
    """One message on a thread, from the member side or the staff side.

    Concrete models add the foreign key to the object the thread is on, and implement
    :attr:`member_user_id`, :attr:`member_side_name`, :meth:`_tell_member_side`,
    :meth:`_tell_staff_side` and :meth:`told_sentence`. Their photo model has a foreign key
    to them with ``related_name="photos"``.
    """

    #: What a staff message with no author, or by someone with no name, signs as.
    STAFF_SIGNATURE: ClassVar[str] = "Past Lives"

    # Set by notify_other_side: whether posting it told anyone. Not a column.
    notified: bool = False

    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        help_text="Who wrote it. Blank for a note carried over from before the thread existed.",
    )
    body = models.TextField(help_text="What they wrote.")
    created_at = models.DateTimeField(default=timezone.now, help_text="When it was posted.")

    class Meta:
        abstract = True
        ordering = ["created_at", "pk"]

    # --- The contract each concrete thread fills in --------------------------------------

    @property
    def member_user_id(self) -> int | None:
        """The member side's account: whoever writes as it is the member side."""
        raise NotImplementedError

    @property
    def member_side_name(self) -> str:
        """What the member side's messages sign as for someone on the staff side."""
        raise NotImplementedError

    @property
    def staff_signature(self) -> str:
        """What a staff message signs as with no named author; the tag beside a named one."""
        return self.STAFF_SIGNATURE

    def _tell_member_side(self) -> bool:
        """Tell the member side about this staff message. Returns whether anyone was told."""
        raise NotImplementedError

    def _tell_staff_side(self) -> bool:
        """Tell the staff side about this member message. Returns whether anyone was told."""
        raise NotImplementedError

    def told_sentence(self) -> str:
        """Who posting this told, as the toast's second sentence ("Jo was notified."), or ""."""
        raise NotImplementedError

    def after_write(self) -> None:
        """Bookkeeping a concrete thread does when a message is saved (none by default)."""

    # --- Shared behaviour -----------------------------------------------------------------

    @property
    def is_from_staff(self) -> bool:
        """Written by anyone but the member side's account, or carried over with no author."""
        return self.author_id is None or self.author_id != self.member_user_id

    @property
    def author_name(self) -> str:
        """The name the thread shows: the member side's name, a staff author's name, else the signature."""
        if not self.is_from_staff:
            return self.member_side_name
        from membership.names import user_display_name

        return (user_display_name(self.author) if self.author is not None else "") or self.staff_signature

    @property
    def shows_staff_tag(self) -> bool:
        """A staff message signed with a person's name carries the signature as a tag; the signature alone does not."""
        return self.is_from_staff and self.author_name != self.staff_signature

    @property
    def thread_photos(self) -> list[ThreadPhoto]:
        """This message's photos, oldest first (reads a ``prefetch_related("messages__photos")``)."""
        return list(getattr(self, "photos").all())

    def notify_other_side(self) -> bool:
        """Tell whoever is on the other side of this message; the one rule for every posting path.

        Returns:
            Whether anyone was told; also kept on :attr:`notified`.
        """
        self.notified = self._tell_member_side() if self.is_from_staff else self._tell_staff_side()
        return self.notified

    @classmethod
    def write(cls, *, author: Any, body: str, photos: Sequence[UploadedFile] = (), **thread: Any) -> Self:
        """Save a message and its photos on a thread and tell nobody.

        For a message that rides on another action's own notice (a decline's note in the
        decline email). Anything a person posts on its own goes through :meth:`post`.

        Args:
            author: Who wrote it (a ``User``), or ``None`` for a carried over note.
            body: What they wrote; surrounding blank space is trimmed.
            photos: Photos posted with it, already validated by the form.
            **thread: The concrete model's foreign key to the object the thread is on.

        Returns:
            The saved message.
        """
        message = cls._default_manager.create(author=author, body=body.strip(), **thread)
        for photo in photos:
            photo.seek(0)
            getattr(message, "photos").create(image=photo)
        message.after_write()
        return message

    @classmethod
    def post(cls, *, author: Any, body: str, photos: Sequence[UploadedFile] = (), **thread: Any) -> Self:
        """Post a message on a thread and tell the other side, every time.

        The message, its photos and the notice share one transaction, so a failure saves
        nothing. Its ``notified`` says whether anyone was told (:meth:`notify_other_side`).
        """
        with transaction.atomic():
            message = cls.write(author=author, body=body, photos=photos, **thread)
            message.notify_other_side()
        return message


class ThreadPhoto(models.Model):
    """One photo or screenshot posted with a :class:`ThreadMessage`.

    Concrete models add the foreign key to their message model, with ``related_name="photos"``.
    """

    image = models.ImageField(upload_to="threads/%Y/%m/", help_text="The uploaded photo, on the default storage.")
    created_at = models.DateTimeField(auto_now_add=True, help_text="When it was uploaded.")

    class Meta:
        abstract = True
        ordering = ["pk"]


class ThreadOpening:
    """The member side's first words when they live on the object itself, not as a message.

    A feedback request's own message and photos open its thread. It reads like a member side
    :class:`ThreadMessage` to ``components/thread.html``, which draws it first; it is never
    posted and tells nobody. The name is asked for only when the page shows it (a member sees
    "You"), so building the opening costs no query.
    """

    is_from_staff = False
    shows_staff_tag = False
    staff_signature = ""
    pk = None

    def __init__(self, *, name: Callable[[], str], body: str, created_at: datetime, photos: list[Any]) -> None:
        self._name = name
        self.body = body
        self.created_at = created_at
        self.thread_photos = photos

    @property
    def author_name(self) -> str:
        """The member side's name, read when the thread shows it."""
        return self._name()
