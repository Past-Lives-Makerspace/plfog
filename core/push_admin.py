"""Admin push diagnostics: inspect a member's push devices and send a test push.

Cross-model orchestration (User + FcmDevice + PushSubscription + the two send
primitives) that backs the ``/admin/push-test/`` support tool — a staffer can check
whether a member's phone/browser is actually registered for push and fire a canned
test at it. Best-effort like the senders it wraps; never raises to the view.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.contrib.auth.models import User

from allauth.account.models import EmailAddress

from core.fcm import send_fcm
from core.models import FcmDevice, PushSubscription
from core.push import send_web_push

_TEST_TITLE = "Test notification"
_TEST_BODY = "You're set. If you can see this, push notifications are working."


@dataclass(frozen=True)
class PushStatus:
    """A member's registered push devices, newest first."""

    fcm_devices: list[FcmDevice]
    web_subscriptions: int

    @property
    def has_any(self) -> bool:
        return bool(self.fcm_devices) or self.web_subscriptions > 0

    @property
    def total_devices(self) -> int:
        return len(self.fcm_devices) + self.web_subscriptions


@dataclass(frozen=True)
class TestSendResult:
    """The tally from firing a test push at every one of a member's devices."""

    delivered: int
    attempted: int

    @property
    def all_delivered(self) -> bool:
        return self.attempted > 0 and self.delivered == self.attempted


def resolve_user(email: str) -> User | None:
    """The account behind ``email`` — matching a linked alias first, then a bare User."""
    address = EmailAddress.objects.filter(email__iexact=email.strip()).select_related("user").first()
    if address is not None:
        return address.user
    return User.objects.filter(email__iexact=email.strip()).first()


def status_for(user: User) -> PushStatus:
    """Every push device ``user`` has registered (native app tokens + browser subs)."""
    return PushStatus(
        fcm_devices=list(FcmDevice.objects.filter(user=user).order_by("-updated_at")),
        web_subscriptions=PushSubscription.objects.filter(user=user).count(),
    )


def send_test_push(user: User, *, url: str) -> TestSendResult:
    """Fire the canned test push at every one of ``user``'s devices; report the tally.

    A dead token is reaped by the sender mid-loop (and counts as not delivered), so the
    result doubles as a live cleanup pass.
    """
    delivered = 0
    attempted = 0
    for subscription in PushSubscription.objects.filter(user=user):
        attempted += 1
        if send_web_push(subscription, title=_TEST_TITLE, body=_TEST_BODY, url=url):
            delivered += 1
    for device in FcmDevice.objects.filter(user=user):
        attempted += 1
        if send_fcm(device, title=_TEST_TITLE, body=_TEST_BODY, url=url):
            delivered += 1
    return TestSendResult(delivered=delivered, attempted=attempted)
