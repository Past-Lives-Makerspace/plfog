"""Kiln tickets for the Ceramics Guild (#691).

A maker files one ticket per piece (or set of matching pieces) waiting on the shelf. The
ticket carries photos, the guild's questions and the flags its answers raise; the crew loads
it into a :class:`KilnFiring` (part 2) and unloads it (part 3). A reply thread on the ticket
runs between the maker and the crew. Clay and studio glaze choices are guild-edited lists
that are archived, never deleted, so an old ticket always shows what was really used.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Any, TypeVar

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import IntegrityError, models, transaction
from django.utils import timezone

from core.images import normalize_image

if TYPE_CHECKING:
    from django.core.files.storage import Storage
    from django.core.files.uploadedfile import UploadedFile

    from membership.models import Member

# The longest edge of the small copy a photo tile, a list row and the crew's loading grid
# show. The full photo keeps the gallery ceiling (settings.IMAGE_MAX_LONG_EDGE_GALLERY).
TILE_LONG_EDGE = 480

_OptionT = TypeVar("_OptionT", bound="ListOption")


def member_name(member: Member) -> str:
    """What the kiln pages call a person: their name, or their email when no name is on file.

    A class guest's account can carry no name, and a blank name in "Loaded by" or on a tile
    reads as a bug. The email is looked up only when the name is blank.
    """
    return member.display_name or member.primary_email


class ListOptionNameTaken(Exception):
    """An archived option cannot come back while an active one already carries its name."""


class ListOptionQuerySet(models.QuerySet[_OptionT]):
    """The active and archived halves of a clay or glaze list."""

    def active(self) -> ListOptionQuerySet[_OptionT]:
        """Options a maker can pick on a new ticket."""
        return self.filter(archived_at__isnull=True)

    def archived(self) -> ListOptionQuerySet[_OptionT]:
        """Options hidden from new tickets that old tickets still show."""
        return self.filter(archived_at__isnull=False)

    def append(self, name: str) -> _OptionT:
        """Add an option at the end of the list."""
        last = self.order_by("-sort_order").first()
        return self.create(name=name, sort_order=(last.sort_order + 1) if last else 0)


class ListOption(models.Model):
    """One choice on a guild-edited list. Archived, never deleted."""

    name = models.CharField(max_length=80, help_text="The name makers pick on the ticket form.")
    sort_order = models.PositiveIntegerField(default=0, help_text="Ascending order on the form and the list.")
    archived_at = models.DateTimeField(
        null=True, blank=True, help_text="When the crew took this off new tickets; blank while it is offered."
    )
    archived_by = models.ForeignKey(
        "membership.Member",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text="Who archived it.",
    )
    created_at = models.DateTimeField(auto_now_add=True, help_text="When it was added to the list.")

    class Meta:
        abstract = True
        ordering = ["sort_order", "name"]

    def __str__(self) -> str:
        return self.name

    @property
    def is_archived(self) -> bool:
        return self.archived_at is not None

    def archive(self, by: Member) -> None:
        """Take this option off new tickets; tickets that used it keep it."""
        self.archived_at = timezone.now()
        self.archived_by = by
        self.save(update_fields=["archived_at", "archived_by"])

    def restore(self) -> None:
        """Offer this option on new tickets again.

        Raises:
            ListOptionNameTaken: an active option already has this name.
        """
        if type(self)._default_manager.filter(archived_at__isnull=True, name__iexact=self.name).exists():
            raise ListOptionNameTaken(self.name)
        self.archived_at = None
        self.archived_by = None
        self.save(update_fields=["archived_at", "archived_by"])


class ClayOption(ListOption):
    """A clay body on the studio list."""

    objects = ListOptionQuerySet.as_manager()

    class Meta(ListOption.Meta):
        constraints = [
            models.UniqueConstraint(
                fields=["name"], condition=models.Q(archived_at__isnull=True), name="uq_kiln_clay_active_name"
            ),
        ]


class GlazeOption(ListOption):
    """A glaze on the studio shelf."""

    objects = ListOptionQuerySet.as_manager()

    class Meta(ListOption.Meta):
        constraints = [
            models.UniqueConstraint(
                fields=["name"], condition=models.Q(archived_at__isnull=True), name="uq_kiln_glaze_active_name"
            ),
        ]


@dataclass(frozen=True)
class MissingAnswer:
    """One thing a ticket still needs before it can go in the queue.

    Attributes:
        field: The form field the answer belongs to.
        short: A few words for the draft row's "Still needs" line.
        message: The sentence shown under the field when Submit is refused.
    """

    field: str
    short: str
    message: str


class KilnTicketQuerySet(models.QuerySet["KilnTicket"]):
    """The queue the crew loads from, and which tickets a viewer may open."""

    def waiting(self) -> KilnTicketQuerySet:
        """Tickets in the queue, the longest wait first."""
        return self.filter(status=KilnTicket.Status.SUBMITTED).order_by("submitted_at", "pk")

    def visible_to(self, member: Member, *, crew: bool) -> KilnTicketQuerySet:
        """Every ticket for the crew; a maker's own tickets for anyone else."""
        return self if crew else self.filter(maker=member)

    def newest_fired_first(self) -> KilnTicketQuerySet:
        """Fired tickets by when they came out, newest first; every other ticket after, newest first."""
        return self.order_by(models.F("fired_at").desc(nulls_last=True), "-created_at", "-pk")


class KilnTicket(models.Model):
    """One piece, or a set of identical pieces, waiting for a firing."""

    class Status(models.TextChoices):
        # Submit moves DRAFT to SUBMITTED, loading SUBMITTED to LOADED, and unloading LOADED
        # to FIRED, or back to SUBMITTED for a piece the crew sends back to the queue.
        DRAFT = "draft", "Draft"
        SUBMITTED = "submitted", "In the queue"
        LOADED = "loaded", "In the kiln"
        FIRED = "fired", "Ready for pickup"

    class FiringType(models.TextChoices):
        BISQUE = "bisque", "Bisque"
        GLAZE = "glaze", "Glaze"

    class MakerType(models.TextChoices):
        MEMBER = "member", "Member"
        STUDENT = "student", "Student or guest"
        STAFF = "staff", "Guild staff"

    maker = models.ForeignKey(
        "membership.Member",
        on_delete=models.PROTECT,
        related_name="kiln_tickets",
        help_text="Who filed the ticket. Their name and contact are read from here, never typed.",
    )
    maker_type = models.CharField(
        max_length=20,
        choices=MakerType.choices,
        default=MakerType.MEMBER,
        help_text="What the maker was when they last saved the ticket (a snapshot).",
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.DRAFT, help_text="Where the ticket is."
    )
    firing_type = models.CharField(
        max_length=10, choices=FiringType.choices, blank=True, help_text="Bisque or glaze; blank on an early draft."
    )
    height_in = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0.01"))],
        help_text="Height in inches.",
    )
    width_in = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0.01"))],
        help_text="Width in inches.",
    )
    length_in = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0.01"))],
        help_text="Length in inches.",
    )
    quantity = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(500)],
        help_text="Identical pieces on this ticket. Blank means one.",
    )
    clay = models.ForeignKey(
        ClayOption,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="tickets",
        help_text="The studio clay, when the maker picked one from the list.",
    )
    clay_other = models.BooleanField(default=False, help_text="The maker chose Other (specify) for the clay.")
    clay_other_name = models.CharField(max_length=120, blank=True, help_text="The clay named under Other.")
    clay_other_cone6 = models.BooleanField(
        default=False, help_text="The maker confirmed the Other clay can be fired to at least Cone 6."
    )
    walls_under_inch = models.BooleanField(
        null=True, blank=True, help_text="Bisque: every wall is under 1 inch thick. Blank when unanswered."
    )
    glaze_studio = models.BooleanField(default=False, help_text="Glaze: a studio glaze is on the piece.")
    studio_glazes = models.ManyToManyField(
        GlazeOption, blank=True, related_name="tickets", help_text="Which studio glazes are on the piece."
    )
    glaze_commercial = models.BooleanField(default=False, help_text="Glaze: a commercial glaze is on the piece.")
    commercial_glaze_name = models.CharField(
        max_length=200, blank=True, help_text="The commercial glaze's brand and name."
    )
    glaze_self_made = models.BooleanField(default=False, help_text="Glaze: a self made or other glaze is on the piece.")
    self_made_glaze_description = models.CharField(
        max_length=300, blank=True, help_text="What the self made or other glaze is."
    )
    bottom_free_of_glaze = models.BooleanField(
        null=True, blank=True, help_text="Glaze: the bottom quarter inch is free of glaze. Blank when unanswered."
    )
    stilts_added = models.BooleanField(
        null=True, blank=True, help_text="Glaze with a glazed bottom: stilts or cookies protect the shelf."
    )
    glaze_cone6 = models.BooleanField(
        default=False, help_text="The maker confirmed the clay and every glaze can be fired to at least Cone 6."
    )
    created_at = models.DateTimeField(auto_now_add=True, help_text="When the ticket was started.")
    updated_at = models.DateTimeField(auto_now=True, help_text="When the ticket was last saved.")
    submitted_at = models.DateTimeField(null=True, blank=True, help_text="When the ticket first went in the queue.")
    fired_at = models.DateTimeField(
        null=True, blank=True, help_text="When the crew unloaded it and marked it Ready for pickup."
    )
    firing = models.ForeignKey(
        "KilnFiring",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="tickets",
        help_text="The firing the crew loaded this piece into; blank until it is loaded.",
    )

    objects = KilnTicketQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["maker", "status"], name="kiln_ticket_maker_status")]

    def __str__(self) -> str:
        return f"Ticket {self.pk}"

    # ---- display ---------------------------------------------------------------------

    @property
    def piece_count(self) -> int:
        """Identical pieces on the ticket; a blank quantity means one."""
        return self.quantity or 1

    @property
    def pieces_label(self) -> str:
        """ "1 piece" / "3 pieces"."""
        count = self.piece_count
        return f"{count} piece" if count == 1 else f"{count} pieces"

    @property
    def summary(self) -> str:
        """The firing and the count, e.g. "Glaze · 3 pieces"."""
        if self.firing_type:
            return f"{self.get_firing_type_display()} · {self.pieces_label}"
        return self.pieces_label

    @property
    def size_label(self) -> str:
        """ "4 x 6 x 6 in", with " each" for a set; blank until all three are given."""
        dims = (self.height_in, self.width_in, self.length_in)
        if any(d is None for d in dims):
            return ""
        text = " x ".join(f"{d.normalize():f}" for d in dims if d is not None) + " in"
        return f"{text} each" if self.piece_count > 1 else text

    @property
    def clay_label(self) -> str:
        """The clay as the maker answered it."""
        if self.clay_other:
            return f"Other: {self.clay_other_name}" if self.clay_other_name else "Other"
        return self.clay.name if self.clay is not None else ""

    def glaze_lines(self) -> list[str]:
        """One line per kind of glaze on a glaze ticket, for the answers list."""
        if self.firing_type != self.FiringType.GLAZE:
            return []
        lines: list[str] = []
        if self.glaze_studio:
            names = ", ".join(g.name for g in self.studio_glazes.all())
            lines.append(f"Studio: {names}" if names else "Studio")
        if self.glaze_commercial:
            lines.append(f"Commercial: {self.commercial_glaze_name}")
        if self.glaze_self_made:
            lines.append(f"Self made or other: {self.self_made_glaze_description}")
        return lines

    @property
    def is_editable(self) -> bool:
        """The maker can change answers until the crew loads the piece."""
        return self.status in (self.Status.DRAFT, self.Status.SUBMITTED)

    def cover_photo(self) -> KilnTicketPhoto | None:
        """The photo the crew finds the piece by (reads the prefetched photos when present)."""
        for photo in self.photos.all():
            if photo.is_cover:
                return photo
        return None

    def open_flags(self) -> list[KilnFlag]:
        """Flags the crew has not cleared, loudest first (the crew's view: hand-added ones too)."""
        flags = [f for f in self.flags.all() if f.cleared_at is None]
        return sorted(flags, key=lambda f: (not f.is_loud, f.pk))

    def maker_flags(self) -> list[KilnFlag]:
        """The open flags the maker sees: automatic ones only. A crew flag and its note stay with the crew."""
        return [f for f in self.open_flags() if f.kind != KilnFlag.Kind.MANUAL]

    def crew_flags(self) -> list[KilnFlag]:
        """Every flag for the crew's ticket view: open ones loudest first, then cleared ones."""
        cleared = [f for f in self.flags.all() if f.cleared_at is not None]
        return self.open_flags() + cleared

    def returned_to_queue(self) -> list[KilnReply]:
        """The crew's notes that sent this piece back to the queue at an unload, oldest first.

        Reads the prefetched replies, so the timeline costs no query of its own.
        """
        return [r for r in self.replies.all() if r.outcome == KilnReply.Outcome.BACK_TO_QUEUE]

    def add_flag(self, note: str, by: Member) -> KilnFlag:
        """A flag the crew adds by hand, with a note only the crew sees."""
        return KilnFlag.objects.create(ticket=self, kind=KilnFlag.Kind.MANUAL, note=note, added_by=by)

    @property
    def glaze_names(self) -> str:
        """The glazes by name alone, for a loading tile: "Ritual Clear, Amaco Blue Rutile"."""
        if self.firing_type != self.FiringType.GLAZE:
            return ""
        names: list[str] = []
        if self.glaze_studio:
            names.extend(g.name for g in self.studio_glazes.all())
        if self.glaze_commercial:
            names.append(self.commercial_glaze_name)
        if self.glaze_self_made:
            names.append(self.self_made_glaze_description)
        return ", ".join(name for name in names if name)

    @property
    def waiting_label(self) -> str:
        """How long the piece has been in the queue, in whole days: "Today", "1 day", "6 days"."""
        if self.submitted_at is None:
            return ""
        days = (timezone.localdate() - timezone.localdate(self.submitted_at)).days
        if days < 1:
            return "Today"
        return "1 day" if days == 1 else f"{days} days"

    @property
    def maker_name(self) -> str:
        return member_name(self.maker)

    @property
    def member_url(self) -> str:
        """Absolute link to the ticket's messages, for a notification."""
        from django.urls import reverse

        return f"{settings.MEMBER_BASE_URL}{reverse('kiln:detail', args=[self.pk])}#kiln-messages"

    # ---- answers ---------------------------------------------------------------------

    def missing_for_submit(self, *, photo_count: int, studio_glaze_count: int) -> list[MissingAnswer]:
        """What Submit still refuses for, in form order. Empty when the ticket can go in.

        Only these block a ticket: a photo, a firing type, a clay and the Cone 6 confirmation
        that applies (the follow up text an Other answer asks for counts as part of it). Every
        other answer only raises a flag.

        Args:
            photo_count: Photos the ticket will have once the pending changes are saved.
            studio_glaze_count: Studio glazes the maker has ticked.
        """
        missing: list[MissingAnswer] = []
        if photo_count < 1:
            missing.append(MissingAnswer("photos", "a photo", "Add at least one photo of the piece."))
        if not self.firing_type:
            missing.append(MissingAnswer("firing_type", "the firing", "Choose bisque or glaze."))
        if self.clay_other:
            if not self.clay_other_name.strip():
                missing.append(MissingAnswer("clay_other_name", "the clay name", "Say which clay this is."))
            if not self.clay_other_cone6:
                missing.append(
                    MissingAnswer(
                        "clay_other_cone6",
                        "the clay Cone 6 check",
                        "Confirm this clay can be safely fired to Cone 6.",
                    )
                )
        elif self.clay_id is None:
            missing.append(MissingAnswer("clay_choice", "the clay", "Choose the clay."))
        if self.firing_type == self.FiringType.GLAZE:
            missing.extend(self._missing_glaze_answers(studio_glaze_count))
        return missing

    def _missing_glaze_answers(self, studio_glaze_count: int) -> list[MissingAnswer]:
        """The glaze follow ups a ticked box asks for, and the glaze Cone 6 confirmation."""
        missing: list[MissingAnswer] = []
        if self.glaze_studio and studio_glaze_count < 1:
            missing.append(MissingAnswer("studio_glazes", "the studio glazes", "Tick the studio glazes you used."))
        if self.glaze_commercial and not self.commercial_glaze_name.strip():
            missing.append(
                MissingAnswer("commercial_glaze_name", "the commercial glaze", "Say which commercial glaze.")
            )
        if self.glaze_self_made and not self.self_made_glaze_description.strip():
            missing.append(
                MissingAnswer("self_made_glaze_description", "the other glaze", "Say what the other glaze is.")
            )
        if not self.glaze_cone6:
            missing.append(
                MissingAnswer(
                    "glaze_cone6",
                    "the glaze Cone 6 check",
                    "Confirm the clay and every glaze can be safely fired to Cone 6.",
                )
            )
        return missing

    def still_needs(self) -> list[str]:
        """The draft row's "Still needs" phrases, from the saved answers and photos."""
        missing = self.missing_for_submit(
            photo_count=len(self.photos.all()), studio_glaze_count=len(self.studio_glazes.all())
        )
        return [m.short for m in missing]

    def drop_inapplicable_answers(self) -> None:
        """Blank the answers a branch the maker left no longer asks.

        A maker who answers the glaze questions and then switches to bisque leaves glaze
        answers behind; they would otherwise raise glaze flags on a bisque piece. Studio
        glazes are many to many, so the caller clears them after the save
        (:meth:`keeps_studio_glazes`).
        """
        if not self.clay_other:
            self.clay_other_name = ""
            self.clay_other_cone6 = False
        else:
            self.clay = None
        if self.firing_type != self.FiringType.BISQUE:
            self.walls_under_inch = None
        if self.firing_type != self.FiringType.GLAZE:
            self.glaze_studio = self.glaze_commercial = self.glaze_self_made = False
            self.bottom_free_of_glaze = None
            self.glaze_cone6 = False
        if not self.glaze_commercial:
            self.commercial_glaze_name = ""
        if not self.glaze_self_made:
            self.self_made_glaze_description = ""
        if self.bottom_free_of_glaze is not False:
            self.stilts_added = None

    @property
    def keeps_studio_glazes(self) -> bool:
        """Whether the studio glaze ticks still apply after :meth:`drop_inapplicable_answers`."""
        return self.firing_type == self.FiringType.GLAZE and self.glaze_studio

    def copy_initial(self) -> dict[str, Any]:
        """Form answers for "Make another like this": everything but the photos.

        The Cone 6 confirmations are left off on purpose: the guild asks for them on every
        ticket, so a copy asks again.
        """
        clay_choice = "other" if self.clay_other else (str(self.clay_id) if self.clay_id else "")
        return {
            "firing_type": self.firing_type,
            "height_in": self.height_in,
            "width_in": self.width_in,
            "length_in": self.length_in,
            "quantity": self.quantity,
            "clay_choice": clay_choice,
            "clay_other_name": self.clay_other_name,
            "walls_under_inch": _yes_no(self.walls_under_inch),
            "glaze_studio": self.glaze_studio,
            "studio_glazes": [g.pk for g in self.studio_glazes.all() if not g.is_archived],
            "glaze_commercial": self.glaze_commercial,
            "commercial_glaze_name": self.commercial_glaze_name,
            "glaze_self_made": self.glaze_self_made,
            "self_made_glaze_description": self.self_made_glaze_description,
            "bottom_free_of_glaze": _yes_no(self.bottom_free_of_glaze),
            "stilts_added": _yes_no(self.stilts_added),
        }

    # ---- flags -----------------------------------------------------------------------

    def automatic_flag_kinds(self) -> list[KilnFlag.Kind]:
        """The flags these answers raise, in question order. Flags never block; they ask the crew to look."""
        return list(self.automatic_flags())

    def automatic_flags(self) -> dict[KilnFlag.Kind, str]:
        """Each flag these answers raise, with the answer that raised it.

        The answer is what the crew checked when they cleared the flag, so a change to it
        opens the flag again (:meth:`sync_automatic_flags`).
        """
        flags: dict[KilnFlag.Kind, str] = {}
        if self.clay_other:
            flags[KilnFlag.Kind.OTHER_CLAY] = self.clay_other_name.strip()
        if self.firing_type == self.FiringType.BISQUE and self.walls_under_inch is False:
            flags[KilnFlag.Kind.THICK_WALLS] = "walls 1 inch or thicker"
        if self.firing_type == self.FiringType.GLAZE:
            if self.glaze_commercial:
                flags[KilnFlag.Kind.COMMERCIAL_GLAZE] = self.commercial_glaze_name.strip()
            if self.glaze_self_made:
                flags[KilnFlag.Kind.SELF_MADE_GLAZE] = self.self_made_glaze_description.strip()
            if self.bottom_free_of_glaze is False:
                # No stilts, or no answer about stilts, is the loud one.
                if self.stilts_added:
                    flags[KilnFlag.Kind.GLAZE_ON_BOTTOM] = "glazed bottom, stilts added"
                else:
                    no_answer = self.stilts_added is None
                    flags[KilnFlag.Kind.GLAZE_ON_BOTTOM_NO_STILTS] = (
                        "glazed bottom, stilts not answered" if no_answer else "glazed bottom, no stilts"
                    )
        return flags

    def sync_automatic_flags(self) -> None:
        """Make the automatic flags match the answers.

        New flags are added in question order and stale ones dropped. A flag the answers still
        raise keeps the crew's clear while the answer behind it is unchanged; a changed answer
        (another Other clay, a different commercial glaze) opens it again for a fresh look.
        Flags the crew added by hand are never touched.
        """
        wanted = self.automatic_flags()
        automatic = self.flags.exclude(kind=KilnFlag.Kind.MANUAL)
        automatic.exclude(kind__in=list(wanted)).delete()
        existing = {flag.kind: flag for flag in automatic}
        new: list[KilnFlag] = []
        for kind, answer in wanted.items():
            flag = existing.get(kind)
            if flag is None:
                new.append(KilnFlag(ticket=self, kind=kind, answer=answer))
            elif flag.answer != answer:
                flag.answer = answer
                flag.cleared_at = None
                flag.cleared_by = None
                flag.save(update_fields=["answer", "cleared_at", "cleared_by"])
        KilnFlag.objects.bulk_create(new)

    def submit(self) -> None:
        """Put the ticket in the queue (first time) and refresh its flags (every time)."""
        if self.status == self.Status.DRAFT:
            self.status = self.Status.SUBMITTED
            self.submitted_at = timezone.now()
            self.save(update_fields=["status", "submitted_at", "updated_at"])
        self.sync_automatic_flags()

    # ---- photos ----------------------------------------------------------------------

    def add_photo(self, upload: UploadedFile) -> KilnTicketPhoto:
        """Store an upload as a photo; the first photo becomes the cover.

        The ticket row is locked while the cover is chosen, so two first uploads arriving
        together cannot both take it. Where the database cannot lock (SQLite), the one-cover
        constraint still holds: the loser of the race is saved as an ordinary photo.
        """
        photo = KilnTicketPhoto(ticket=self)
        photo.image.save(
            "photo.jpg",
            normalize_image(upload, max_long_edge=settings.IMAGE_MAX_LONG_EDGE_GALLERY),
            save=False,
        )
        photo.tile.save("tile.jpg", normalize_image(upload, max_long_edge=TILE_LONG_EDGE), save=False)
        with transaction.atomic():
            list(KilnTicket.objects.select_for_update().filter(pk=self.pk).values_list("pk", flat=True))
            photo.is_cover = not self._has_cover()
            photo.sort_order = self.photos.count()
            try:
                with transaction.atomic():
                    photo.save()
            except IntegrityError:
                photo.pk = None
                photo.is_cover = False
                photo.save()
        return photo

    def _has_cover(self) -> bool:
        return self.photos.filter(is_cover=True).exists()

    def set_cover(self, photo_pk: int) -> None:
        """Make one of this ticket's photos the cover, and only that one.

        Raises:
            KilnTicketPhoto.DoesNotExist: The photo is not on this ticket.
        """
        photo = self.photos.get(pk=photo_pk)
        with transaction.atomic():
            self.photos.filter(is_cover=True).exclude(pk=photo.pk).update(is_cover=False)
            if not photo.is_cover:
                photo.is_cover = True
                photo.save(update_fields=["is_cover"])

    def remove_photo(self, photo_pk: int) -> None:
        """Delete one photo; when it was the cover, the earliest remaining photo takes over.

        Raises:
            KilnTicketPhoto.DoesNotExist: The photo is not on this ticket.
        """
        photo = self.photos.get(pk=photo_pk)
        was_cover = photo.is_cover
        storage, names = photo.image.storage, [name for name in (photo.image.name, photo.tile.name) if name]
        photo.delete()
        # The files go only once the row is gone for good: a rolled back save keeps both.
        transaction.on_commit(lambda: _delete_files(storage, names), robust=True)
        if was_cover:
            successor = self.photos.order_by("sort_order", "pk").first()
            if successor is not None:
                self.set_cover(successor.pk)


def _delete_files(storage: Storage, names: list[str]) -> None:
    """Remove stored files once the rows that named them are gone for good."""
    for name in names:
        storage.delete(name)


def _yes_no(value: bool | None) -> str:
    """A nullable answer as the form's "yes" / "no" / "" value."""
    if value is None:
        return ""
    return "yes" if value else "no"


class KilnTicketPhoto(models.Model):
    """One photo of the piece. Exactly one per ticket is the cover the crew finds it by."""

    ticket = models.ForeignKey(
        KilnTicket, on_delete=models.CASCADE, related_name="photos", help_text="The ticket this photo shows."
    )
    image = models.ImageField(upload_to="kiln/photos/", help_text="The photo, resized for the detail page.")
    tile = models.ImageField(upload_to="kiln/tiles/", help_text="A small copy for rows and tiles.")
    is_cover = models.BooleanField(default=False, help_text="The photo the crew finds the piece by.")
    sort_order = models.PositiveIntegerField(default=0, help_text="Order in the gallery.")
    created_at = models.DateTimeField(auto_now_add=True, help_text="When the photo was added.")

    class Meta:
        ordering = ["sort_order", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["ticket"], condition=models.Q(is_cover=True), name="uq_kiln_photo_one_cover"
            ),
        ]

    def __str__(self) -> str:
        return f"Photo {self.pk} of ticket {self.ticket_id}"


class KilnFlag(models.Model):
    """A request for the crew to double check a ticket, and maybe reach out.

    Automatic flags come from the answers (``added_by`` is blank) and the maker sees each as a
    kind note. The crew add their own (``MANUAL``, with a ``note`` and ``added_by``) that only
    the crew see, and clear any flag (``cleared_at`` and ``cleared_by``).
    """

    class Kind(models.TextChoices):
        OTHER_CLAY = "other_clay", "Other clay"
        COMMERCIAL_GLAZE = "commercial_glaze", "Commercial glaze"
        SELF_MADE_GLAZE = "self_made_glaze", "Self made or other glaze"
        THICK_WALLS = "thick_walls", "Thick walls"
        GLAZE_ON_BOTTOM = "glaze_on_bottom", "Glaze near the bottom"
        GLAZE_ON_BOTTOM_NO_STILTS = "glaze_no_stilts", "Glaze on the bottom, no stilts"
        MANUAL = "manual", "Added by the crew"

    # What the maker reads: kind, never a scolding, and what happens next is said once
    # around the list ("the crew will take a look"). A crew flag has no maker note: the
    # maker never sees it (KilnTicket.maker_flags).
    MAKER_NOTES: dict[str, str] = {
        Kind.OTHER_CLAY: "Your clay is not on the studio list.",
        Kind.COMMERCIAL_GLAZE: "You used a commercial glaze.",
        Kind.SELF_MADE_GLAZE: "You used a self made or other glaze.",
        Kind.THICK_WALLS: "Some walls are 1 inch thick or more. Thick walls can trap air and crack in the kiln.",
        Kind.GLAZE_ON_BOTTOM: "There is glaze near the bottom, and you added stilts. Thank you!",
        Kind.GLAZE_ON_BOTTOM_NO_STILTS: "There is glaze near the bottom and no stilts yet, so the crew will check "
        "before it goes on a shelf.",
    }

    ticket = models.ForeignKey(
        KilnTicket, on_delete=models.CASCADE, related_name="flags", help_text="The flagged ticket."
    )
    kind = models.CharField(max_length=30, choices=Kind.choices, help_text="Why the ticket is flagged.")
    note = models.TextField(blank=True, help_text="The crew's note on a flag they added by hand.")
    answer = models.CharField(
        max_length=300,
        blank=True,
        help_text="The answer that raised an automatic flag. When the maker changes it, a cleared flag opens again.",
    )
    added_by = models.ForeignKey(
        "membership.Member",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text="The crew member who added the flag; blank for an automatic flag.",
    )
    created_at = models.DateTimeField(auto_now_add=True, help_text="When the flag was raised.")
    cleared_at = models.DateTimeField(null=True, blank=True, help_text="When the crew cleared it.")
    cleared_by = models.ForeignKey(
        "membership.Member",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text="Who cleared it.",
    )

    class Meta:
        ordering = ["pk"]
        constraints = [
            # One automatic flag of a kind per ticket; hand-added flags may repeat.
            models.UniqueConstraint(
                fields=["ticket", "kind"], condition=~models.Q(kind="manual"), name="uq_kiln_flag_auto_kind"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.get_kind_display()} on ticket {self.ticket_id}"

    @property
    def is_loud(self) -> bool:
        """Glaze on the bottom without stilts is the one the crew must not miss."""
        return self.kind == self.Kind.GLAZE_ON_BOTTOM_NO_STILTS

    @property
    def maker_note(self) -> str:
        """The kind sentence the maker sees for this flag."""
        return self.MAKER_NOTES[self.kind]

    @property
    def short_label(self) -> str:
        """Lowercase label for a row chip: "The crew will take a look: thick walls"."""
        return str(self.get_kind_display()).lower()

    @property
    def crew_label(self) -> str:
        """What the crew reads on a loading tile: the kind, or a hand flag's own note."""
        if self.kind == self.Kind.MANUAL:
            return f"Crew note: {self.note}"
        return str(self.get_kind_display())

    def clear(self, by: Member) -> None:
        """The crew checked it. The first clear is the one kept; clearing again changes nothing.

        An automatic flag opens again only when the maker changes the answer that raised it
        (:meth:`KilnTicket.sync_automatic_flags`).
        """
        if self.cleared_at is not None:
            return
        self.cleared_at = timezone.now()
        self.cleared_by = by
        self.save(update_fields=["cleared_at", "cleared_by"])


class KilnFiringQuerySet(models.QuerySet["KilnFiring"]):
    """Firings still in the kiln, and the ones the crew has unloaded."""

    def in_kiln(self) -> KilnFiringQuerySet:
        """Loaded and not unloaded yet, the oldest load first (the one to unload next)."""
        return self.filter(unloaded_at__isnull=True).order_by("loaded_at", "pk")


class KilnFiring(models.Model):
    """One load of the kiln: a bisque or glaze firing and the tickets that went in.

    Numbered in one sequence across both types ("Glaze firing 88"), so the crew can name
    a load out loud. Unloading (``kiln.services.unload_kiln``) sets ``unloaded_by`` and
    ``unloaded_at`` once; a firing is never unloaded twice.
    """

    firing_type = models.CharField(
        max_length=10, choices=KilnTicket.FiringType.choices, help_text="Bisque or glaze; every ticket in it matches."
    )
    number = models.PositiveIntegerField(
        unique=True, help_text="The firing's number, one sequence across bisque and glaze."
    )
    loaded_by = models.ForeignKey(
        "membership.Member",
        on_delete=models.PROTECT,
        related_name="+",
        help_text="The crew member who confirmed the load.",
    )
    loaded_at = models.DateTimeField(default=timezone.now, help_text="When the load was confirmed.")
    unloaded_by = models.ForeignKey(
        "membership.Member",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
        help_text="The crew member who unloaded it and told the makers; blank while it is in the kiln.",
    )
    unloaded_at = models.DateTimeField(
        null=True, blank=True, help_text="When it was unloaded; blank while it is in the kiln."
    )

    objects = KilnFiringQuerySet.as_manager()

    class Meta:
        ordering = ["-number"]

    def __str__(self) -> str:
        return self.name

    @property
    def loaded_by_name(self) -> str:
        return member_name(self.loaded_by)

    @property
    def is_unloaded(self) -> bool:
        return self.unloaded_at is not None

    @property
    def unloaded_by_name(self) -> str:
        """Who unloaded it, or blank while it is in the kiln."""
        return member_name(self.unloaded_by) if self.unloaded_by is not None else ""

    def exception_notes(self) -> list[KilnReply]:
        """The crew's notes on pieces that did not come out right (reads the prefetched ``exceptions``)."""
        return list(self.exceptions.all())

    @property
    def exceptions_label(self) -> str:
        """The log's note: "1 exception: glaze ran", "2 exceptions: cracked, stuck to the shelf"; blank when none."""
        notes = self.exception_notes()
        if not notes:
            return ""
        noun = "exception" if len(notes) == 1 else "exceptions"
        kinds = list(dict.fromkeys(str(n.get_what_happened_display()).lower() for n in notes))
        return f"{len(notes)} {noun}: {', '.join(kinds)}"

    @property
    def name(self) -> str:
        """ "Glaze firing 88"."""
        return f"{self.get_firing_type_display()} firing {self.number}"

    @classmethod
    def next_number(cls) -> int:
        """The number the next firing will carry."""
        last = cls.objects.aggregate(last=models.Max("number"))["last"]
        return (last or 0) + 1

    @classmethod
    def start(cls, firing_type: str, by: Member) -> KilnFiring:
        """Create the next firing. Two crews confirming at once each get their own number.

        The unique number decides a tie: the loser of the race takes the next one.
        """
        for _attempt in range(5):
            try:
                with transaction.atomic():
                    return cls.objects.create(firing_type=firing_type, number=cls.next_number(), loaded_by=by)
            except IntegrityError:
                continue
        raise IntegrityError("Could not number a new firing.")


class KilnReply(models.Model):
    """One message on a ticket's thread between the maker and the crew.

    A note the crew writes while unloading (a piece that cracked, or did not get fired) is a
    reply too, with ``firing``, ``what_happened`` and ``outcome`` set: the exception record
    the timeline and the kiln log read. It reaches the maker in the ready for pickup notice,
    never as a separate message notification.
    """

    class WhatHappened(models.TextChoices):
        CRACKED = "cracked", "Cracked"
        GLAZE_RAN = "glaze_ran", "Glaze ran"
        STUCK = "stuck", "Stuck to the shelf"
        OTHER = "other", "Something else"

    class Outcome(models.TextChoices):
        FIRED = "fired", "Fired, with this note"
        BACK_TO_QUEUE = "back_to_queue", "Back to the queue"

    ticket = models.ForeignKey(
        KilnTicket, on_delete=models.CASCADE, related_name="replies", help_text="The ticket this message is on."
    )
    author = models.ForeignKey(
        "membership.Member",
        on_delete=models.PROTECT,
        related_name="kiln_replies",
        help_text="Who wrote it: the maker or a crew member.",
    )
    body = models.TextField(help_text="The message.")
    created_at = models.DateTimeField(auto_now_add=True, help_text="When it was sent.")
    firing = models.ForeignKey(
        KilnFiring,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="exceptions",
        help_text="The firing being unloaded when the crew wrote this note; blank for an ordinary message.",
    )
    what_happened = models.CharField(
        max_length=20,
        choices=WhatHappened.choices,
        blank=True,
        help_text="What went wrong with the piece at the unload; blank for an ordinary message.",
    )
    outcome = models.CharField(
        max_length=20,
        choices=Outcome.choices,
        blank=True,
        help_text="Whether the piece was fired anyway or went back to the queue; blank for an ordinary message.",
    )

    class Meta:
        ordering = ["created_at", "pk"]
        constraints = [
            # An unload note carries its firing, what happened and the outcome; a message carries none.
            models.CheckConstraint(
                condition=(
                    models.Q(firing__isnull=True, what_happened="", outcome="")
                    | (models.Q(firing__isnull=False) & ~models.Q(what_happened="") & ~models.Q(outcome=""))
                ),
                name="ck_kiln_reply_unload_note",
            ),
        ]

    def __str__(self) -> str:
        return f"Message {self.pk} on ticket {self.ticket_id}"

    @property
    def author_name(self) -> str:
        return member_name(self.author)

    @property
    def is_unload_note(self) -> bool:
        return self.firing_id is not None

    @property
    def from_crew(self) -> bool:
        """Anyone but the maker writing on a ticket is the crew (only the crew can open another's ticket)."""
        return self.author_id != self.ticket.maker_id
