"""Which review step fires which notice, and who hears it: the admin flow map's data.

A mapping module, not an engine. It is deliberately NOT an introspection framework: nothing
here walks :mod:`classes.emails` looking for senders, parses an AST, or asks a decorator to
register anything. It is a table of :class:`Notice` rows and one resolver that joins them to
things that already exist:

* the **pipeline**, for the steps. :func:`review_flow` walks ``ReviewPipeline.steps``, so the
  columns and lanes on the page are the ones :meth:`classes.models.ClassOffering.review_pipeline`
  draws on the review screen and in every review email. A lead-less category has no
  ``guild_lead`` lane and the map silently drops that lane's notices with it.
* the **event registry**, for the audience and the email opt-out. ``Recipients`` and
  :func:`core.events.copy.audience_description` are what say "the guild's lead and staff" or
  "the CMS Administrators (holders only)", so re-pointing an event at a different resolver
  changes this page without anyone editing it.
* the **sender itself**, for the trigger function. ``Notice.sender`` holds the real callable,
  not its name, so a renamed or deleted sender is an ImportError here rather than a stale
  line on a page nobody re-reads.

What stays hand-written, and what happens when you forget it: the **step** a notice hangs off,
the **event keys** it rides, its **gallery key** (the preview anchor), whether it writes a bell
row, and its plain-language trigger sentence. ``classes/spec/review_map_spec.py`` pins every
one of those to something real — the sender's own source for the event keys and the email-only
cases, the email gallery for the preview anchor, the gallery's own completeness guard for
"somebody added a review email and did not add it here". **Add a review email and no map row
and the spec fails**; that is the whole arrangement, and it is a test rather than a framework
on purpose.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from classes import emails
from core.events.copy import audience_description
from core.events.registry import Channel, get_event

if TYPE_CHECKING:
    from classes.models import PipelineStep, ReviewPipeline

# The published email gallery. ``.github/workflows/copy-review.yml`` builds it from
# ``tests/e2e/email_gallery`` and writes this host into the Pages CNAME; each card is anchored
# at ``#<gallery key>``, which is what makes a preview link a fragment rather than a path.
GALLERY_URL = "https://copy-review.pastlives.space/"

# Placeholder ids for the sample offering :func:`reference_pipeline` draws. Nothing is saved
# and nothing reads them back: ``required_review_roles`` only asks whether a category, a guild
# and a guild lead are SET, so the sample needs ids that exist rather than ids that mean
# anything.
_SAMPLE_ID = 1


@dataclass(frozen=True)
class Notice:
    """One email (and, where it writes one, one bell row) the review flow fires.

    Args:
        step: The :class:`~classes.models.PipelineStep` key this hangs off — ``submitted``,
            ``guild_lead``, ``admin`` or ``live``. A key no lane carries never renders.
        title: The card heading on the map.
        trigger: Plain-language "sent when…", in the reviewer's words rather than the code's.
        sender: The real function that emits it. Held as the callable, not a name.
        event_keys: Every registry key this notice can ride. More than one where the outcome
            picks the event (the decision notice rides approved OR changes-requested).
        gallery_key: The email gallery's card id, which is also its preview anchor.
        in_app: True when the sender writes a bell row alongside the email.
        note: The one caveat a reviewer needs and the trigger sentence cannot carry.
        audience: Overrides the event's resolver audience. Allowed ONLY where the sender
            empties the fan-out (``recipient_user_ids=set()``), which the spec enforces.
    """

    step: str
    title: str
    trigger: str
    sender: Callable[..., Any]
    event_keys: tuple[str, ...]
    gallery_key: str
    in_app: bool
    note: str = ""
    audience: str = ""


# Read top to bottom, this is the flow: both lanes open at submit, either may answer first,
# and every decision reports back to the instructor. Order within a step is delivery order.
NOTICES: tuple[Notice, ...] = (
    Notice(
        step="submitted",
        title="Review Request (Guild Lead)",
        trigger="The instructor submits the class. Both lanes open at this moment and neither waits for the other.",
        sender=emails.send_guild_lead_review_request,
        event_keys=("class_review_requested",),
        gallery_key="review_request",
        in_app=True,
        note="Carries a tokenized link, so a lead can answer without a hub login.",
    ),
    Notice(
        step="submitted",
        title="Review Request (Admin)",
        trigger=(
            "The instructor submits the class. This lane opens on every submission, "
            "not only where the category has no guild lead."
        ),
        sender=emails.send_admin_review_request,
        event_keys=("class_validation_requested",),
        gallery_key="admin_review_request",
        in_app=True,
        note="Links the logged-in review screen and carries no bearer token.",
    ),
    Notice(
        step="submitted",
        title="Your Class Is In Review (Instructor)",
        trigger=(
            "The instructor submits the class. One email per submission rather than one per "
            "reviewer, so a guilded submit never lands twice."
        ),
        sender=emails._emit_instructor_review_explainer,
        event_keys=("class_review_requested",),
        gallery_key="review_submitted_instructor",
        in_app=False,
        note="Email only. It names both reviewers and what each of them checks.",
        audience="The submitting instructor.",
    ),
    Notice(
        step="guild_lead",
        title="Review Reminder (Guild Lead)",
        trigger=(
            "An admin uses Remind Lead while this lane is still open. It delivers once a day; "
            "a second click the same day sends nothing."
        ),
        sender=emails.send_guild_lead_review_reminder,
        event_keys=("class_review_requested",),
        gallery_key="review_request",
        in_app=True,
        note="The same email as the request at submit. The instructor is not told again.",
    ),
    Notice(
        step="guild_lead",
        title="Last Approval Needed (Admin)",
        trigger="The guild lead approves while the admin lane is still open, so the admin is the only answer left.",
        sender=emails.send_admin_validation_request,
        event_keys=("class_validation_requested",),
        gallery_key="admin_validation_request",
        in_app=True,
        note="News about the other lane. The admin lane was already emailed at submit.",
    ),
    Notice(
        step="guild_lead",
        title="Review Decision (Instructor)",
        trigger="The guild lead approves, asks for changes, or turns the dates down.",
        sender=emails.send_class_review_decision,
        event_keys=("instructor_class_approved", "instructor_changes_requested"),
        gallery_key="review_decision",
        in_app=True,
        note=(
            "The subject line varies by outcome. The bell row is written on the two outcomes that "
            "pinged the instructor before: the class going live, and changes requested."
        ),
    ),
    Notice(
        step="admin",
        title="Review Decision (Instructor)",
        trigger=(
            "The admin approves and publishes, approves and holds for the room check, asks for changes, or declines."
        ),
        sender=emails.send_class_review_decision,
        event_keys=("instructor_class_approved", "instructor_changes_requested"),
        gallery_key="review_decision",
        in_app=True,
        note=(
            "Holding reads differently from publishing, so the email says which of the two the "
            "instructor is waiting on."
        ),
    ),
)


@dataclass(frozen=True)
class ResolvedEvent:
    """One registry event behind a notice, read at render time rather than written down."""

    key: str
    label: str
    audience: str
    email_default: str


@dataclass(frozen=True)
class ResolvedNotice:
    """A :class:`Notice` joined to the registry and the gallery, ready for the template."""

    title: str
    trigger: str
    note: str
    sender: str
    in_app: bool
    audience: str
    events: tuple[ResolvedEvent, ...]
    preview_url: str


@dataclass(frozen=True)
class FlowStage:
    """One lane of the pipeline and everything it fires. ``notices`` is empty for a lane that
    sends nothing of its own, which Live genuinely does."""

    step: PipelineStep
    notices: tuple[ResolvedNotice, ...]


def _resolve_event(key: str) -> ResolvedEvent:
    """Read one event out of the registry: its label, its audience, and its email default.

    Raises:
        ValueError: If the event declares no EMAIL channel. Every notice on this page is an
            email, so an event that has lost its EMAIL channel means the map is describing a
            send that can no longer happen — worth a loud failure rather than a blank cell.
    """
    event = get_event(key)
    spec = event.channel(Channel.EMAIL)
    if spec is None:
        raise ValueError(f"Event '{key}' declares no EMAIL channel, but the review flow map lists it as an email.")
    return ResolvedEvent(
        key=event.key,
        label=event.label,
        audience=audience_description(event),
        email_default=spec.default.value,
    )


def _resolve(notice: Notice) -> ResolvedNotice:
    """Join one row of :data:`NOTICES` to the registry, the sender, and the gallery."""
    events = tuple(_resolve_event(key) for key in notice.event_keys)
    return ResolvedNotice(
        title=notice.title,
        trigger=notice.trigger,
        note=notice.note,
        sender=f"{notice.sender.__module__}.{notice.sender.__name__}",
        in_app=notice.in_app,
        # dict.fromkeys rather than a set: two events that resolve to the same audience say it
        # once, and the order stays the order the notice declared its events in.
        audience=notice.audience or " ".join(dict.fromkeys(event.audience for event in events)),
        events=events,
        preview_url=f"{GALLERY_URL}#{notice.gallery_key}",
    )


def review_flow(pipeline: ReviewPipeline) -> tuple[FlowStage, ...]:
    """One stage per lane of ``pipeline``, each carrying the notices that lane fires.

    The pipeline decides the shape, which is the point: a lead-less category draws no
    ``guild_lead`` lane, so its three notices drop out of the map without a branch here.
    """
    return tuple(
        FlowStage(step=step, notices=tuple(_resolve(n) for n in NOTICES if n.step == step.key))
        for step in pipeline.steps
    )


def reference_pipeline() -> "ReviewPipeline":
    """The canonical two-lane strip, drawn by the real :meth:`ClassOffering.review_pipeline`.

    The map is about the process rather than about one class, so it reads a sample offering:
    a draft in a category whose guild has a lead, which is the shape that opens both review
    lanes. A draft on purpose — a pending sample would draw the admin lane as the open one and
    the guild lead's as not yet reached, which is exactly the queue this flow stopped being.

    Nothing is saved and nothing is queried. The approvals are prefetched from an empty
    queryset, which Django answers without touching the database — and which also means the
    sample can never pick up a real class's rows through its placeholder id.
    """
    from django.db.models import Prefetch, prefetch_related_objects

    from classes.models import Category, ClassApproval, ClassOffering
    from membership.models import Guild

    guild = Guild(pk=_SAMPLE_ID, name="", guild_lead_id=_SAMPLE_ID)
    category = Category(pk=_SAMPLE_ID, name="")
    category.guild = guild
    offering = ClassOffering(pk=_SAMPLE_ID, status=ClassOffering.Status.DRAFT)
    offering.category = category
    prefetch_related_objects([offering], Prefetch("approvals", queryset=ClassApproval.objects.none()))
    return offering.review_pipeline()
