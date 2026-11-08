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

A posted message can be changed afterwards (#1140): its author or an admin edits its text and
removes photos (:meth:`ThreadMessage.edit`, stamping ``edited_at``) or deletes it with its photos
(:meth:`ThreadMessage.remove`). Neither tells anyone. Who may is
``membership.permissions.can_change_message``; the views are ``hub.thread_views``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar, Self

from django.conf import settings
from django.db import models, transaction
from django.urls import reverse
from django.utils import timezone

from core.files import delete_files_after_commit

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Sequence
    from datetime import datetime

    from django.core.files.uploadedfile import UploadedFile


class EmptyMessageError(ValueError):
    """An edit would leave a message with no text and no photo, which posting never allows."""


#: What a bell row about a deleted message reads instead of its words (#1140).
DELETED_NOTICE = "This message was deleted."
#: What a bell row reads once its message was edited down to a photo alone.
PHOTO_ONLY_NOTICE = "Sent a photo."


def rewrite_notices(period: str, old: str, new: str | None) -> int:
    """Rewrite the bell rows a message's notice wrote, found by that notice's own ``period``.

    Every thread notice carries the message's words in the bell row's body (alone, or after the
    author's name on a kiln ticket), so an edit swaps the old words for the new ones (``new``)
    and a delete (``None``) for a neutral line. Rows are matched on the exact period the notice
    was emitted with, never on their text; within a matched row the old words are replaced where
    they appear, and a row whose body was clipped below them is replaced whole. Nothing is sent;
    email and push already delivered keep the old words.

    Returns:
        How many rows were rewritten.
    """
    from core.models import Notification

    if not period:
        return 0
    text = DELETED_NOTICE if new is None else (new or PHOTO_ONLY_NOTICE)
    rows = list(Notification.objects.filter(period=period))
    for row in rows:
        body = row.body.replace(old, text, 1) if old and old in row.body else text
        row.body = body if len(body) <= 500 else body[:499].rstrip() + "\u2026"
    Notification.objects.bulk_update(rows, ["body"])
    return len(rows)


class ThreadMessage(models.Model):
    """One message on a thread, from the member side or the staff side.

    Concrete models add the foreign key to the object the thread is on, and implement
    :attr:`member_user_ids`, :attr:`member_side_name`, :meth:`_tell_member_side`,
    :meth:`_tell_staff_side` and :meth:`told_sentence`. Their photo model has a foreign key
    to them with ``related_name="photos"``.
    """

    #: What a staff message with no author, or by someone with no name, signs as.
    STAFF_SIGNATURE: ClassVar[str] = "Past Lives"

    #: The thread's name in the edit and delete URLs (``hub.thread_views``); unique per concrete model.
    THREAD_KIND: ClassVar[str] = ""

    #: The ``period`` its notice is emitted with, formatted with ``pk``: one message notifies once,
    #: and its bell rows are found again by it (:func:`rewrite_notices`).
    NOTICE_PERIOD: ClassVar[str] = ""

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
    edited_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When its author or an admin last changed it; blank when it reads as posted.",
    )

    class Meta:
        abstract = True
        ordering = ["created_at", "pk"]

    # --- The contract each concrete thread fills in --------------------------------------

    @property
    def member_user_ids(self) -> set[int]:
        """The member side's accounts: whoever writes as one of them is the member side.

        One account for a feedback sender or a booking's member; several for a thread whose
        member side is a group (a class's equal instructors).
        """
        raise NotImplementedError

    @property
    def member_side_name(self) -> str:
        """What this member side message signs as: the member's name, or its author's in a group."""
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

    def after_remove(self) -> None:
        """Bookkeeping a concrete thread does once a message is deleted (none by default)."""

    # --- Shared behaviour -----------------------------------------------------------------

    @property
    def is_from_staff(self) -> bool:
        """Written by anyone but the member side's accounts, or carried over with no author."""
        return self.author_id is None or self.author_id not in self.member_user_ids

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

    @property
    def author_user_id(self) -> int | None:
        """The account that wrote it, which may change it (``membership.permissions.can_change_message``)."""
        return self.author_id

    @property
    def notice_period(self) -> str:
        return self.NOTICE_PERIOD.format(pk=self.pk)

    @property
    def dom_id(self) -> str:
        """The element id of this message in a thread, unique across every thread on a page."""
        return f"thread-msg-{self.THREAD_KIND}-{self.pk}"

    @property
    def dom_selector(self) -> str:
        """The CSS selector of this message's element, which an edit or a delete swaps."""
        return f"[data-thread-dom='{self.dom_id}']"

    @property
    def edit_url(self) -> str:
        return reverse("hub_thread_message_edit", args=[self.THREAD_KIND, self.pk])

    @property
    def delete_url(self) -> str:
        return reverse("hub_thread_message_delete", args=[self.THREAD_KIND, self.pk])

    @property
    def photo_ids(self) -> list[int]:
        """The pks of this message's photos: what an edit may remove."""
        return [photo.pk for photo in self.thread_photos]

    @property
    def edit_photos(self) -> list[tuple[int, str]]:
        """Each photo as ``(pk, url)``, for the edit box's remove controls (``components/thread_message_edit.html``)."""
        return [(photo.pk, photo.image.url) for photo in self.thread_photos]

    def edit(self, *, body: str, remove_photo_ids: Collection[int] = ()) -> bool:
        """Change this message's text and remove some of its photos, telling nobody.

        Stamps ``edited_at`` when anything changed and puts the new words in the bell rows its
        notice wrote (:func:`rewrite_notices`). The row is locked and its photos read again, so a
        delete or a photo removal that committed meanwhile is seen. The removed photos' files leave
        storage once the edit commits. An edit that changes nothing writes nothing.

        Args:
            body: The new text; surrounding blank space is trimmed.
            remove_photo_ids: Pks of this message's photos to remove; any other pk is ignored.

        Returns:
            Whether anything changed.

        Raises:
            EmptyMessageError: The message would be left with no text and no photo.
            ObjectDoesNotExist: The message was deleted meanwhile (the model's ``DoesNotExist``).
        """
        body = body.strip()
        wanted = set(remove_photo_ids)
        model = type(self)
        getattr(self, "_prefetched_objects_cache", {}).pop("photos", None)
        with transaction.atomic():
            if not model._default_manager.select_for_update().filter(pk=self.pk).exists():
                raise model.DoesNotExist(f"{model.__name__} {self.pk} was deleted.")  # type: ignore[attr-defined]
            photos = list(getattr(self, "photos").all())
            removing = [photo for photo in photos if photo.pk in wanted]
            if not body and len(removing) == len(photos):
                raise EmptyMessageError("A message needs some text or a photo.")
            if body == self.body and not removing:
                return False
            old = self.body
            self.body = body
            self.edited_at = timezone.now()
            self.save(update_fields=["body", "edited_at"])
            if removing:
                getattr(self, "photos").filter(pk__in=[photo.pk for photo in removing]).delete()
                delete_files_after_commit([photo.image for photo in removing])
            rewrite_notices(self.notice_period, old, body)
        getattr(self, "_prefetched_objects_cache", {}).pop("photos", None)
        return True

    def remove(self) -> None:
        """Delete this message and its photos for everyone, telling nobody.

        The photo files leave storage once the delete commits; then the thread's own bookkeeping
        runs (:meth:`after_remove`), so nothing still counts the message as unread, and the bell
        rows its notice wrote lose its words (:func:`rewrite_notices`).
        """
        period, old = self.notice_period, self.body
        with transaction.atomic():
            files = [photo.image for photo in getattr(self, "photos").all()]
            self.delete()
            self.after_remove()
            rewrite_notices(period, old, None)
            delete_files_after_commit(files)

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


def thread_message_model(kind: str) -> type[ThreadMessage]:
    """The concrete message model whose :attr:`ThreadMessage.THREAD_KIND` is ``kind``.

    Raises:
        LookupError: No thread is called ``kind``.
    """
    for model in ThreadMessage.__subclasses__():
        if model.THREAD_KIND == kind:
            return model
    raise LookupError(f"No thread called {kind!r}.")


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
    edited_at = None

    def __init__(
        self, *, author_id: int | None, name: Callable[[], str], body: str, created_at: datetime, photos: list[Any]
    ) -> None:
        self.author_id = author_id
        self._name = name
        self.body = body
        self.created_at = created_at
        self.thread_photos = photos

    @property
    def author_name(self) -> str:
        """The member side's name, read when the thread shows it."""
        return self._name()
