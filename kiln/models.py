"""Kiln tickets for the Ceramics Guild (#691).

A maker files one ticket per piece (or set of matching pieces) waiting on the shelf. The
ticket carries photos, the guild's questions and the flags its answers raise; the crew loads
and unloads from it (parts 2 and 3). Clay and studio glaze choices are guild-edited lists
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


class KilnTicket(models.Model):
    """One piece, or a set of identical pieces, waiting for a firing."""

    class Status(models.TextChoices):
        # The whole lifecycle is declared now so loading (part 2) and unloading (part 3)
        # need no status migration. Part 1 moves tickets from DRAFT to SUBMITTED only.
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
        """Flags the crew has not cleared, loudest first."""
        flags = [f for f in self.flags.all() if f.cleared_at is None]
        return sorted(flags, key=lambda f: (not f.is_loud, f.pk))

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

    Automatic flags come from the answers (``added_by`` is blank). Part 2 adds the crew's own
    flags (``MANUAL`` with a ``note`` and ``added_by``) and clearing (``cleared_at`` and
    ``cleared_by``), so the fields are here from the start.
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
    # around the list ("the crew will take a look").
    MAKER_NOTES: dict[str, str] = {
        Kind.OTHER_CLAY: "Your clay is not on the studio list.",
        Kind.COMMERCIAL_GLAZE: "You used a commercial glaze.",
        Kind.SELF_MADE_GLAZE: "You used a self made or other glaze.",
        Kind.THICK_WALLS: "Some walls are 1 inch thick or more. Thick walls can trap air and crack in the kiln.",
        Kind.GLAZE_ON_BOTTOM: "There is glaze near the bottom, and you added stilts. Thank you!",
        Kind.GLAZE_ON_BOTTOM_NO_STILTS: "There is glaze near the bottom and no stilts yet, so the crew will check "
        "before it goes on a shelf.",
        Kind.MANUAL: "The crew wants a closer look.",
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
