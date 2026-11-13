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

from collections.abc import Callable, Iterable
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
    #: A query string for the action's page, e.g. "?status=needs_review" for a filter chip.
    action_query: str = ""
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
        return f"{reverse(self.action_url_name)}{self.action_query}" if self.action_url_name else ""


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
    done_title: str
    #: The folded card's line while the member holds the badge.
    done_line: str
    #: The line instead, once an admin has taken the badge away on the Badges page.
    done_line_without_badge: str
    #: The toast the member sees once, when setup finishes.
    celebration: str
    #: The one rule the folded card states, e.g. how a guide is paid. Never a tracked figure.
    rule_line: str = ""
    #: ``(step key, title, lead)``: once that step is done the card's title and lead change to these.
    milestone: tuple[str, str, str] | None = None
    #: The page's own tab showing the permission's wiki page, when it has one (Tours); else the
    #: shared Guide page (``hub_permission_guide``) shows it.
    guide_url_name: str = ""
    #: The badge's name when it differs from the permission's (Can create events earns Event Creator).
    badge_label: str = ""
    #: A query string for the card's page, e.g. "?tab=notifications".
    page_query: str = ""
    #: For a member's own page (a Class Reviewer's guild): where their card lives. Else ``page_url``.
    page_url_for: Callable[[Member], str] | None = None
    #: For a grant that is not an :class:`AdminCapability` (Instructor, Admin, Class Reviewer): who
    #: granted it, "" when nothing records who, or None when the member does not hold it. A
    #: capability is looked up on its row instead.
    grant_of: Callable[[Member], str | None] | None = None

    @property
    def label(self) -> str:
        """The permission's name, e.g. Tour Guide."""
        from membership.models import SetupPermission

        return SetupPermission(self.permission).label

    @property
    def badge_name(self) -> str:
        """What the badge setup earns is called, e.g. Event Creator."""
        return self.badge_label or self.label

    @property
    def hidden_message(self) -> str:
        """The toast after Hide: where this card comes back from."""
        if self.guide_url_name:
            return "Get Set Up is hidden. Bring it back from the Guide tab."
        return f"Get Set Up is hidden. Bring it back with Show Get Set Up on {self.page_name}."

    @property
    def page_url(self) -> str:
        """The page the card lives on, for anyone (a member's own page is :meth:`page_url_of`)."""
        return f"{reverse(self.page_url_name)}{self.page_query}"

    def page_url_of(self, member: Member) -> str:
        """The page ``member``'s card lives on: their own (a Class Reviewer's guild) when the spec has one."""
        return self.page_url_for(member) if self.page_url_for is not None else self.page_url

    def granter(self, member: Member) -> str | None:
        """Who granted ``member`` this permission ("" when nothing records who), or None when they do not hold it."""
        from membership.models import AdminCapability

        if self.grant_of is not None:
            return self.grant_of(member)
        grant = (
            AdminCapability.objects.filter(member=member, capability=self.permission)
            .select_related("granted_by__member")
            .first()
        )
        if grant is None:
            return None
        granted_by = grant.granted_by
        return user_display_name(granted_by) if granted_by is not None and granted_by.pk != member.user_id else ""

    @property
    def guide_url(self) -> str:
        """Where Open the Guide goes: the page's own Guide tab, else the shared Guide page."""
        if self.guide_url_name:
            return reverse(self.guide_url_name)
        return reverse("hub_permission_guide", args=[self.permission])

    def email_steps(self, member: Member) -> tuple[SetupStep, ...]:
        """The steps the grant email lists: those this site can do now, by the same check the card uses."""
        unavailable = self.facts(member).unavailable
        return tuple(step for step in self.steps if step.key not in unavailable)

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
    from membership.capability_setup import CAPABILITY_SETUPS
    from membership.grant_setup import GRANT_SETUPS
    from tours.setup import TOUR_GUIDE_SETUP

    return {spec.permission: spec for spec in (*CAPABILITY_SETUPS, *GRANT_SETUPS, TOUR_GUIDE_SETUP)}


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
    member: Member
    row: PermissionSetup
    steps: tuple[StepView, ...]
    facts: SetupFacts
    granter_name: str
    #: True on the load that finished setup or first saw it finished: the caller shows the toast.
    just_finished: bool

    @classmethod
    def evaluate(cls, member: Member, permission: str) -> None:
        """Finish setup if every required step is now done, without using up the toast.

        For a save path that may finish setup for someone else (an admin marking a guide's tour
        Attended): the badge lands at once, and the guide still sees the toast on their next visit.
        """
        cls.load(member, permission, celebrate=False)

    @classmethod
    def for_page(cls, member: Member | None, permissions: Iterable[str]) -> list[SetupState]:
        """The member's cards for the permissions a page hosts, in that order: only those they hold.

        One query finds which they hold, so a page costs a single query for everyone else.
        """
        from membership.models import AdminCapability

        if member is None:
            return []
        wanted = [permission for permission in permissions if spec_for(permission) is not None]
        capabilities = [permission for permission in wanted if permission in AdminCapability.Capability.values]
        held = (
            set(
                AdminCapability.objects.filter(member=member, capability__in=capabilities).values_list(
                    "capability", flat=True
                )
            )
            if capabilities
            else set()
        )
        states = (
            cls.load(member, permission)
            for permission in wanted
            if permission in held or permission not in capabilities
        )
        return [state for state in states if state is not None]

    @classmethod
    def load(cls, member: Member, permission: str, *, celebrate: bool = True) -> SetupState | None:
        """The member's card for ``permission``, or None when they were not granted it or it has no card.

        Finishes setup (the badge, :meth:`PermissionSetup.finish`) the first time every required
        step is done, and, unless ``celebrate`` is False, celebrates it once (:meth:`PermissionSetup.celebrate`).
        """
        from membership.models import PermissionSetup

        spec = spec_for(permission)
        if spec is None:
            return None
        granter_name = spec.granter(member)
        if granter_name is None:
            return None
        row = PermissionSetup.for_member(member, permission)
        facts = spec.facts(member)
        steps = _step_views(spec, row, facts)
        if row.completed_at is None and _all_required_done(steps):
            row.finish()
        return cls(
            spec=spec,
            member=member,
            row=row,
            steps=steps,
            facts=facts,
            granter_name=granter_name or "an admin",
            just_finished=row.celebrate() if celebrate else False,
        )

    @property
    def page_url(self) -> str:
        """The page this member's card lives on."""
        return self.spec.page_url_of(self.member)

    @property
    def finished(self) -> bool:
        """Setup is done and the badge given; the card folds to its summary."""
        return self.row.completed_at is not None

    @property
    def badges(self) -> list[LeadershipBadge]:
        """The badge setup earned, as the pill list the directory draws: only while the member holds it.

        Empty before setup finishes, and after an admin takes the badge away on the Badges page.
        """
        from membership.models import LeadershipBadge

        if not self.finished:
            return []
        return list(LeadershipBadge.objects.filter(permission=self.spec.permission, members=self.row.member_id))

    @property
    def done_line(self) -> str:
        """The folded card's line, true to whether the member still holds the badge."""
        return self.spec.done_line if self.badges else self.spec.done_line_without_badge

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
