"""factory-boy factories for the core app."""

from __future__ import annotations

import secrets

import factory
from django.contrib.auth import get_user_model
from django.utils import timezone

from core.models import (
    Announcement,
    CopyReviewComment,
    QueuedDiscordMessage,
    ScheduledJobState,
    ScheduledTaskRun,
    TransactionalEmailLog,
    UserProfile,
)
from core.scheduled_jobs import Trigger

User = get_user_model()


class CoreUserFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = User
        django_get_or_create = ("username",)

    username = factory.Sequence(lambda n: f"profileuser{n}")
    email = factory.LazyAttribute(lambda o: f"{o.username}@example.com")


class UserProfileFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = UserProfile

    user = factory.SubFactory(CoreUserFactory)
    preferred_name = ""
    pronouns = ""
    phone = ""


class ScheduledTaskRunFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = ScheduledTaskRun

    task_key = factory.Sequence(lambda n: f"job_{n}")
    status = ScheduledTaskRun.Status.OK
    trigger = Trigger.SCHEDULED


class ScheduledJobStateFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = ScheduledJobState
        django_get_or_create = ("task_key",)

    task_key = factory.Sequence(lambda n: f"job_{n}")
    enabled = True


# TEMPORARY — remove on/after 2026-08-10. Copy-review gallery anonymous comments.
class CopyReviewCommentFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = CopyReviewComment

    section_key = factory.Sequence(lambda n: f"public-account--overview-{n}")
    author_name = factory.Sequence(lambda n: f"Reviewer {n}")
    body = "The heading copy reads well; consider softening the button label."
    edit_token = factory.LazyFunction(lambda: secrets.token_hex(16))


class AnnouncementFactory(factory.django.DjangoModelFactory):
    """A live Home announcement (#889): on, with no dates."""

    class Meta:
        model = Announcement

    body = factory.Sequence(lambda n: f"<p>Zorblax notice {n}</p>")


class QueuedDiscordMessageFactory(factory.django.DjangoModelFactory):
    """A Discord @everyone ping waiting for review in Spotlight (#943), from an unlinked account."""

    class Meta:
        model = QueuedDiscordMessage

    discord_message_id = factory.Sequence(lambda n: str(900000 + n))
    discord_channel_id = "111"
    discord_url = factory.LazyAttribute(lambda o: f"https://discord.com/channels/1/111/{o.discord_message_id}")
    original_text = factory.Sequence(lambda n: f"Quorbling the kiln shelves tonight {n}")
    line = factory.LazyAttribute(lambda o: f"<p>{o.original_text} (Discord Dee)</p>")
    sender_discord_id = "555"
    sender_name = "Discord Dee"
    sent_at = factory.LazyFunction(timezone.now)


class TransactionalEmailLogFactory(factory.django.DjangoModelFactory):
    """One row in the Email Log. Pass ``sent_at`` to backdate it, since ``created_at`` is auto_now_add."""

    class Meta:
        model = TransactionalEmailLog
        skip_postgeneration_save = True

    to_email = factory.Sequence(lambda n: f"recipient{n}@example.com")
    subject = factory.Sequence(lambda n: f"Plimsoll notice {n}")
    trigger_kind = "billing.receipt"
    status = TransactionalEmailLog.Status.SENT

    @factory.post_generation
    def sent_at(obj: TransactionalEmailLog, create: bool, extracted: object, **kwargs: object) -> None:  # noqa: N805
        if extracted is not None:
            TransactionalEmailLog.objects.filter(pk=obj.pk).update(created_at=extracted)
            obj.refresh_from_db()
