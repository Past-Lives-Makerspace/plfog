"""Edit and delete a posted thread message (#1140), on every thread and on kiln tickets.

One pair of views serves the four shared threads (``core.threads``), keyed by the message
model's ``THREAD_KIND``: ``/threads/<kind>/<pk>/edit/`` and ``/threads/<kind>/<pk>/delete/``.
The kiln app reuses :func:`edit_message` and :func:`delete_message` from its own URLs, so a
guest's kiln pages stay under ``/kiln/``.

Who may: the message's author, or an admin (``membership.permissions.can_change_message``,
the actual role, so a view as preview neither grants nor removes it). Anyone else who can see
the thread gets a 403; anyone who cannot gets a 404, as if it did not exist. Neither view tells
anyone: no email, push, bell or Discord. Both answer the inline htmx forms with the one message
(redrawn, or nothing once deleted) and a toast; a plain POST lands back on ``next``.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from typing import TYPE_CHECKING, Any

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from core.htmx import wants_fragment
from core.threads import EmptyMessageError, ThreadMessage, thread_message_model
from hub.forms import ThreadMessageEditForm
from hub.toast import trigger_toast
from membership.permissions import can_change_message, is_actual_admin

if TYPE_CHECKING:
    from collections.abc import Collection
    from typing import Protocol

    from core.models import FeedbackComment
    from classes.models import ClassMessage
    from membership.models import EquipmentReservationMessage, OrientationBookingMessage

    class ChangeableMessage(Protocol):
        """What the edit and delete views need of a message: a thread message or a kiln ticket message."""

        @property
        def author_user_id(self) -> int | None: ...

        @property
        def photo_ids(self) -> list[int]: ...

        def edit(self, *, body: str, remove_photo_ids: Collection[int] = ()) -> bool: ...

        def remove(self) -> None: ...


#: Renders the one message again after an edit: ``None`` for the saved message, else the refused form.
MessageRenderer = Callable[[ThreadMessageEditForm | None], HttpResponse]


def _back(request: HttpRequest, default: str) -> str:
    """The posted ``next`` when it is a path on this site, else ``default``."""
    from hub.views import _safe_next

    return _safe_next(request, default)


def check_may_change(request: HttpRequest, message: ChangeableMessage, *, sees_thread: bool) -> None:
    """Refuse anyone but the author or an admin: a 404 to whoever cannot see the thread, else a 403.

    An admin (the actual role) passes even where a preview would hide the thread.

    Raises:
        Http404: The viewer cannot see this thread.
        PermissionDenied: They see it, but did not write this message.
    """
    if is_actual_admin(request):
        return
    if not sees_thread:
        raise Http404("No such message.")
    if not can_change_message(request, message.author_user_id):
        raise PermissionDenied("Only its author or an admin may change this message.")


def edit_message(
    request: HttpRequest,
    message: ChangeableMessage,
    *,
    render_message: MessageRenderer,
    back: str,
    body_max_length: int | None = None,
    auto_id: str = "id_%s",
) -> HttpResponse:
    """Save an edit the gate already allowed: the new text, and the photos marked to remove.

    A refused edit (no text and no photo left, too long, an unknown photo, or a last photo removed
    in another tab meanwhile) redraws the message with its box open and the reason; without htmx it
    lands on ``back`` with the reason. A message deleted meanwhile is a 404.
    """
    form = ThreadMessageEditForm(
        request.POST, photo_ids=message.photo_ids, body_max_length=body_max_length, auto_id=auto_id
    )
    fragment = wants_fragment(request)
    if not form.is_valid():
        if fragment:
            return render_message(form)
        messages.error(request, f"Not saved. {form.error_summary()}")
        return redirect(back)
    try:
        message.edit(body=form.cleaned_data["body"], remove_photo_ids=form.cleaned_data["remove_photos"])
    except ObjectDoesNotExist:
        raise Http404("The message was deleted meanwhile.") from None
    except EmptyMessageError:
        # Its last photo went in another tab between the form's check and the save.
        form.add_error("body", "Write something, or keep a photo.")
        if fragment:
            return render_message(form)
        messages.error(request, f"Not saved. {form.error_summary()}")
        return redirect(back)
    if fragment:
        response = render_message(None)
        trigger_toast(response, "Saved.")
        return response
    messages.success(request, "Saved.")
    return redirect(back)


def delete_message(request: HttpRequest, message: ChangeableMessage, *, back: str) -> HttpResponse:
    """Delete a message the gate already allowed, with its photos; an empty answer swaps it out of the thread."""
    message.remove()
    if wants_fragment(request):
        response = HttpResponse("")
        trigger_toast(response, "Deleted.")
        return response
    messages.success(request, "Deleted.")
    return redirect(back)


# --- The four shared threads ------------------------------------------------------------


def _sees_feedback(request: HttpRequest, message: FeedbackComment) -> bool:
    """A feedback request's thread is its sender's (admins pass before this is asked)."""
    return message.request.user_id == request.user.pk


def _sees_class(request: HttpRequest, message: ClassMessage) -> bool:
    """A class's thread is whoever ``ClassOffering.message_side`` places: its instructors and reviewers."""
    return message.class_offering.message_side(request.user) is not None


def _sees_booking(request: HttpRequest, message: OrientationBookingMessage) -> bool:
    """A booking's thread is its member's and whoever may run the booking, as ``orientation_message`` lets post."""
    from hub.views import _get_member, _require_can_manage_booking

    booking = message.booking
    member = _get_member(request)
    if member is not None and booking.member_id == member.pk:
        return True
    return _require_can_manage_booking(request, booking) is None


def _sees_reservation(request: HttpRequest, message: EquipmentReservationMessage) -> bool:
    """A reservation's thread is its member's and its managers' (``reservation_thread_side``)."""
    from hub.reservation_bookings import reservation_thread_side

    return reservation_thread_side(request, message.reservation) is not None


#: Who can see each thread kind's messages: anyone else is answered 404.
_SEES: dict[str, Callable[[HttpRequest, Any], bool]] = {
    "feedback": _sees_feedback,
    "class": _sees_class,
    "booking": _sees_booking,
    "reservation": _sees_reservation,
}

_ThreadView = Callable[[HttpRequest, ThreadMessage], HttpResponse]


def thread_message_change_required(view: _ThreadView) -> Callable[..., HttpResponse]:
    """Resolve ``kind`` and ``pk`` to the message and let through only its author or an admin.

    Signed out visitors go to sign in, a GET is refused (405), an unknown kind or message is a
    404, and :func:`check_may_change` answers everyone else.
    """

    @wraps(view)
    def wrapped(request: HttpRequest, kind: str, pk: int) -> HttpResponse:
        try:
            model = thread_message_model(kind)
        except LookupError:
            raise Http404("No such thread.") from None
        message = model._default_manager.select_related("author").filter(pk=pk).first()
        if message is None:
            raise Http404("No such message.")
        check_may_change(request, message, sees_thread=_SEES[kind](request, message))
        return view(request, message)

    return login_required(require_POST(wrapped))


def _render_thread_message(
    request: HttpRequest, message: ThreadMessage, form: ThreadMessageEditForm | None
) -> HttpResponse:
    """The one message as ``components/thread.html`` draws it, read again so its photos are current."""
    # Every concrete thread's photo table is related_name="photos" (core.threads), which the abstract base cannot name.
    messages_of_kind = type(message)._default_manager.select_related("author")
    fresh = messages_of_kind.prefetch_related("photos").filter(pk=message.pk).first()  # type: ignore[misc]
    if fresh is None:
        raise Http404("The message was deleted meanwhile.")
    return render(
        request,
        "components/thread_message.html",
        {
            "message": fresh,
            "viewer_is_staff": request.POST.get("viewer_is_staff") == "1",
            "refused_edit": form,
            "thread_next": _back(request, ""),
        },
    )


@thread_message_change_required
def thread_message_edit(request: HttpRequest, message: ThreadMessage) -> HttpResponse:
    """POST: save an edit of one thread message and redraw it with "edited"; tells nobody."""
    return edit_message(
        request,
        message,
        render_message=lambda form: _render_thread_message(request, message, form),
        back=_back(request, "/"),
        auto_id=f"{message.dom_id}-%s",
    )


@thread_message_change_required
def thread_message_delete(request: HttpRequest, message: ThreadMessage) -> HttpResponse:
    """POST: delete one thread message and its photos for everyone; tells nobody."""
    return delete_message(request, message, back=_back(request, "/"))
