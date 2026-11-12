"""Get Set Up: the checklist a member just granted a permission finishes by doing the real thing (#1167).

Each permission that teaches itself has one :class:`PermissionSetupSpec` in :func:`specs`: its
steps (:class:`SetupStep`), the words on the card and a ``facts`` function that reads what the
member has already done (:class:`SetupFacts`). The card (``hub/partials/_permission_setup.html``),
the Got It, Skip, Hide and Show views (``hub/permission_setup_views.py``) and the grant email are
the same for every permission, so a permission joins by adding its spec here, a wiki page linked
through :attr:`WikiPage.guide_for` and a Guide tab on its page; nothing else changes.

:class:`SetupState` is what one member sees: it reads the facts and the member's
:class:`~membership.models.PermissionSetup` row, opens the first step still to do, and finishes
setup (the badge) the first time every required step is done.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from django.urls import reverse

from membership.names import user_display_name

if TYPE_CHECKING:
    from membership.models import LeadershipBadge, Member, PermissionSetup


@dataclass(frozen=True)
class SetupStep:
    """One step of a permission's Get Set Up card.

    ``got_it`` marks a reading step, done when the member presses Got It; every other step is done
    by doing it, which the spec's ``facts`` reads. An ``optional`` step can be skipped and never
    holds up finishing.
    """

    key: str
    title: str
    #: The muted line under a step still to do, while it is closed.
    note: str
    #: The opening line once the step is the open one.
    why: str
    #: The step in the grant email's list, in sentence case.
    email_line: str
    action_label: str = ""
    action_url_name: str = ""
    #: The bullets inside the open step; reading steps show them in the body, others in a disclosure.
    how: tuple[str, ...] = ()
    how_title: str = "How It Works"
    #: A disclosure under a reading step's bullets, for what happens less often.
    more_title: str = ""
    more: tuple[str, ...] = ()
    #: The Guide page section that covers the step, for the "Read ... in the Guide" link.
    guide_section: str = ""
    #: Why the step cannot be done here, shown while ``facts`` says it is unavailable.
    unavailable_note: str = ""
    got_it: bool = False
    optional: bool = False

    @property
    def action_url(self) -> str:
        """Where the step's button and its closed row go, or "" for a step with no page."""
        return reverse(self.action_url_name) if self.action_url_name else ""


@dataclass(frozen=True)
class SetupStat:
    """One figure on the card once setup is done, e.g. 3 Tours Given."""

    value: int
    label: str


@dataclass(frozen=True)
class SetupFacts:
    """What a member has done towards a permission's steps, read fresh on every load."""

    #: Steps done by doing them (never the reading steps, which the row records).
    done: frozenset[str]
    #: Steps that cannot be done on this site right now; they are skipped and never hold up finishing.
    unavailable: frozenset[str] = frozenset()
    #: A done step's own line ("rjames@pastlives.space"), shown after "Done ·".
    notes: dict[str, str] = field(default_factory=dict)
    stats: tuple[SetupStat, ...] = ()


@dataclass(frozen=True)
class PermissionSetupSpec:
    """Everything one permission's Get Set Up card says and checks."""

    permission: str
    title: str
    lead: str
    steps: tuple[SetupStep, ...]
    facts: Callable[[Member], SetupFacts]
    #: The page the card lives on, which the grant email's Get Set Up button opens.
    page_url_name: str
    #: That page's name in the grant email, e.g. "the Tours page".
    page_name: str
    #: The tab that shows the permission's wiki page and brings a hidden card back.
    guide_url_name: str
    done_title: str
    done_line: str
    #: The one rule the folded card states, e.g. how a guide is paid. Never a tracked figure.
    rule_line: str
    #: The toast the member sees once, when setup finishes.
    celebration: str
    #: ``(step key, title, lead)``: once that step is done the card's title and lead change to these.
    milestone: tuple[str, str, str] | None = None

    @property
    def label(self) -> str:
        """The permission's name, e.g. Tour Guide."""
        from membership.models import AdminCapability

        return AdminCapability.Capability(self.permission).label

    def step(self, key: str) -> SetupStep:
        """The step called ``key``.

        Raises:
            KeyError: The spec has no such step.
        """
        for step in self.steps:
            if step.key == key:
                return step
        raise KeyError(key)


def specs() -> dict[str, PermissionSetupSpec]:
    """Every permission that has a Get Set Up card, by permission."""
    from tours.setup import TOUR_GUIDE_SETUP

    return {TOUR_GUIDE_SETUP.permission: TOUR_GUIDE_SETUP}


def spec_for(permission: str) -> PermissionSetupSpec | None:
    """The permission's spec, or None for one that has no card yet."""
    return specs().get(permission)


@dataclass(frozen=True)
class StepView:
    """One step as the card draws it."""

    step: SetupStep
    number: int
    done: bool
    skipped: bool
    unavailable: bool
    is_open: bool
    note: str


@dataclass(frozen=True)
class SetupState:
    """One member's card for one permission, read and brought up to date in one call (:meth:`load`)."""

    spec: PermissionSetupSpec
    row: PermissionSetup
    steps: tuple[StepView, ...]
    facts: SetupFacts
    granter_name: str
    #: True on the load that finished setup or first saw it finished: the caller shows the toast.
    just_finished: bool

    @classmethod
    def load(cls, member: Member, permission: str) -> SetupState | None:
        """The member's card for ``permission``, or None when they were not granted it or it has no card.

        Finishes setup (the badge, :meth:`PermissionSetup.finish`) the first time every required
        step is done, and celebrates it once (:meth:`PermissionSetup.celebrate`).
        """
        from membership.models import AdminCapability, PermissionSetup

        spec = spec_for(permission)
        if spec is None:
            return None
        grant = (
            AdminCapability.objects.filter(member=member, capability=permission)
            .select_related("granted_by__member")
            .first()
        )
        if grant is None:
            return None
        row = PermissionSetup.for_member(member, permission)
        facts = spec.facts(member)
        steps = _step_views(spec, row, facts)
        if row.completed_at is None and _all_required_done(steps):
            row.finish()
        granter = grant.granted_by
        granter_name = user_display_name(granter) if granter is not None and granter.pk != member.user_id else ""
        return cls(
            spec=spec,
            row=row,
            steps=steps,
            facts=facts,
            granter_name=granter_name or "an admin",
            just_finished=row.celebrate(),
        )

    @property
    def finished(self) -> bool:
        """Setup is done and the badge given; the card folds to its summary."""
        return self.row.completed_at is not None

    @property
    def badges(self) -> list[LeadershipBadge]:
        """The badge setup earned, as the pill list the directory draws (empty before it is earned)."""
        from membership.models import LeadershipBadge

        if not self.finished:
            return []
        return list(LeadershipBadge.objects.filter(permission=self.spec.permission))

    @property
    def hidden(self) -> bool:
        """The member hid the card; the Guide tab offers it back."""
        return self.row.hidden_at is not None

    @property
    def counted(self) -> tuple[StepView, ...]:
        """The steps the progress counts: every one this site can do."""
        return tuple(view for view in self.steps if not view.unavailable)

    @property
    def done_count(self) -> int:
        """Steps done or skipped, of :attr:`counted`."""
        return sum(1 for view in self.counted if view.done or view.skipped)

    @property
    def open_step(self) -> StepView | None:
        """The one step the card shows opened: the first neither done, skipped nor unavailable."""
        return next((view for view in self.steps if view.is_open), None)

    @property
    def title(self) -> str:
        """The card's title, the milestone's once its step is done."""
        return self._milestone()[0]

    @property
    def lead(self) -> str:
        """The line under the title, the milestone's once its step is done."""
        return self._milestone()[1]

    def _milestone(self) -> tuple[str, str]:
        milestone = self.spec.milestone
        if milestone is not None and any(view.done and view.step.key == milestone[0] for view in self.steps):
            return milestone[1], milestone[2]
        return self.spec.title, self.spec.lead


def _step_views(spec: PermissionSetupSpec, row: PermissionSetup, facts: SetupFacts) -> tuple[StepView, ...]:
    views: list[StepView] = []
    opened = False
    for number, step in enumerate(spec.steps, start=1):
        unavailable = step.key in facts.unavailable
        done = not unavailable and (step.key in row.read_steps if step.got_it else step.key in facts.done)
        skipped = not done and not unavailable and step.optional and step.key in row.skipped_steps
        is_open = not opened and not (done or skipped or unavailable)
        opened = opened or is_open
        if done:
            note = f"Done · {facts.notes[step.key]}" if step.key in facts.notes else "Done"
        elif skipped:
            note = "Skipped"
        elif unavailable:
            note = step.unavailable_note
        else:
            note = step.note
        views.append(
            StepView(
                step=step,
                number=number,
                done=done,
                skipped=skipped,
                unavailable=unavailable,
                is_open=is_open,
                note=note,
            )
        )
    return tuple(views)


def _all_required_done(steps: tuple[StepView, ...]) -> bool:
    return all(view.done for view in steps if not view.step.optional and not view.unavailable)
