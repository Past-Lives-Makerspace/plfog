"""The real Past Lives floor plans, transcribed as room rectangles.

This is *data*, kept in the repo on purpose: the map is drawn by the app, so these
rectangles are the map. They were traced off the two studio floor-plan drawings (the
"2nd Floor Map" and "Past Lives Floor 1" sheets), which is why every box is written in
the **source drawing's pixel coordinates** — that keeps a row auditable against the
picture it came from. :class:`SeedFloor` converts them to the percentage coordinates
:class:`~membership.models.MapHotspot` stores, so the drawing's own pixel grid never
leaks into the database.

**What is deliberately NOT here.** Those drawings are working documents, and the map is
the *space layout*, not a reproduction of the sheet. So: no title block or revision
metadata, no "Frame" outlines, no hand-drawn colour key (the app renders a live one from
real :class:`~membership.models.Space` status, and a static one could contradict it), no
working annotations or dimension scribbles, no spreadsheet gridlines behind the drawing,
and no occupant names — occupancy is read from ``Space.current_occupants`` at render time
and never baked into geometry. The test: if someone walking the building would not
recognise it as a place, it is not a room here.

Re-seed with ``manage.py seed_floor_geometry`` (``--dry-run`` to look first).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

#: Rooms are traced by hand off a drawing, so a rectangle is only ever this accurate.
_QUANT = Decimal("0.01")


@dataclass(frozen=True)
class SeedRoom:
    """One rectangle on a floor drawing, in that drawing's pixel coordinates.

    ``label`` is what the drawing prints in the box. For a leasable room that is also its
    :attr:`~membership.models.Space.space_id`; where the two differ (two halves of one
    leased space, say) ``space_id`` names the real record.
    """

    label: str
    left: int
    top: int
    right: int
    bottom: int
    #: ``"studio"`` / ``"cubby"`` bind to a Space; the rest are facility/info markers.
    kind: str = "studio"
    space_id: str = ""

    @property
    def space_code(self) -> str:
        """The ``Space.space_id`` this room should bind to (``""`` for facilities)."""
        if self.kind not in ("studio", "cubby"):
            return ""
        return self.space_id or self.label


@dataclass(frozen=True)
class SeedFloor:
    """One floor: the drawing's crop box, plus every room traced inside it."""

    name: str
    sort_order: int
    #: The crop of the source drawing that becomes the whole canvas, in its pixels.
    origin_x: int
    origin_y: int
    width: int
    height: int
    caption: str
    rooms: tuple[SeedRoom, ...] = field(default_factory=tuple)

    @property
    def aspect_ratio(self) -> Decimal:
        """Canvas shape (width ÷ height), so the drawn floor keeps the drawing's proportions."""
        return (Decimal(self.width) / Decimal(self.height)).quantize(_QUANT, rounding=ROUND_HALF_UP)

    def percent_box(self, room: SeedRoom) -> tuple[Decimal, Decimal, Decimal, Decimal]:
        """``(x, y, w, h)`` for ``room`` as percentages of this floor's canvas.

        Clamped into the canvas so a rectangle traced a pixel past the crop can never be
        stored running off the edge — the same bound the placement editor enforces.
        """
        x = self._pct(room.left - self.origin_x, self.width)
        y = self._pct(room.top - self.origin_y, self.height)
        w = self._pct(room.right - room.left, self.width)
        h = self._pct(room.bottom - room.top, self.height)
        x = min(x, Decimal(100))
        y = min(y, Decimal(100))
        w = max(min(w, Decimal(100) - x), _QUANT)
        h = max(min(h, Decimal(100) - y), _QUANT)
        return x, y, w, h

    @staticmethod
    def _pct(value: int, span: int) -> Decimal:
        """A pixel measurement as a clamped 0–100 percentage of ``span``."""
        raw = (Decimal(max(value, 0)) / Decimal(span)) * Decimal(100)
        return min(raw.quantize(_QUANT, rounding=ROUND_HALF_UP), Decimal(100))


def _shelf_rooms() -> tuple[SeedRoom, ...]:
    """The 27 storage shelves, drawn on Floor 1 as a 9-wide grid of three rows.

    The drawing prints them ``S1-S1``…``S1-S22`` (the last five are unlabelled); the
    records they bind to are ``S1-1``…``S1-27``.
    """
    left, right = 1159, 1557
    rows = ((196, 237), (239, 283), (285, 320))
    columns = 9
    step = (right - left) / columns
    rooms: list[SeedRoom] = []
    for index in range(27):
        row_top, row_bottom = rows[index // columns]
        column = index % columns
        rooms.append(
            SeedRoom(
                label=f"S1-{index + 1}",
                left=int(left + column * step),
                top=row_top,
                right=int(left + (column + 1) * step) - 1,
                bottom=row_bottom,
                kind="cubby",
            )
        )
    return tuple(rooms)


# ── 2nd Floor — the A-series studios ────────────────────────────────────────────────
_SECOND_FLOOR = SeedFloor(
    name="2nd Floor",
    sort_order=2,
    origin_x=140,
    origin_y=195,
    width=830,
    height=905,
    caption="The A-series studios, traced from the 2nd-floor plan.",
    rooms=(
        # North wall, west to east
        SeedRoom("A2b", 179, 209, 339, 431),
        SeedRoom("A3", 343, 209, 434, 290),
        SeedRoom("A4", 435, 209, 528, 290),
        SeedRoom("A5", 529, 209, 594, 290),
        SeedRoom("A6", 595, 209, 678, 290),
        SeedRoom("A7", 679, 209, 764, 290),
        SeedRoom("A8", 765, 209, 818, 290),
        SeedRoom("A9", 819, 209, 866, 274),
        # East wall, north to south
        SeedRoom("A10", 870, 210, 953, 262),
        SeedRoom("A11", 887, 265, 953, 333),
        SeedRoom("A12", 887, 337, 953, 380),
        SeedRoom("A13", 887, 383, 953, 432),
        SeedRoom("A14a", 887, 435, 953, 476),
        SeedRoom("A14b", 887, 479, 953, 520),
        SeedRoom("A15", 887, 523, 953, 600),
        SeedRoom("A16", 887, 604, 953, 680),
        SeedRoom("A17", 887, 683, 953, 759),
        SeedRoom("A18", 921, 761, 954, 803),
        SeedRoom("A19", 921, 805, 954, 866),
        SeedRoom("A20", 921, 868, 954, 931),
        SeedRoom("A21", 921, 933, 954, 981),
        # Second bank
        SeedRoom("A2", 389, 368, 468, 431),
        SeedRoom("A1", 470, 368, 528, 431),
        SeedRoom("A51", 530, 368, 571, 431),
        SeedRoom("A50", 645, 371, 688, 428),
        SeedRoom("A49", 690, 368, 758, 431),
        SeedRoom("A48", 759, 368, 813, 431),
        SeedRoom("A47", 814, 368, 845, 431),
        SeedRoom("A46", 812, 435, 845, 483),
        SeedRoom("A45", 812, 505, 845, 570),
        # Central bank
        SeedRoom("A41", 611, 599, 673, 665),
        SeedRoom("A42", 674, 599, 737, 665),
        SeedRoom("A43", 739, 599, 764, 665),
        SeedRoom("A44", 765, 599, 861, 665),
        SeedRoom("A37", 686, 690, 750, 751),
        SeedRoom("A38", 751, 690, 795, 751),
        SeedRoom("A39", 797, 690, 861, 751),
        SeedRoom("A40", 769, 752, 855, 817),
        SeedRoom("A36", 603, 703, 665, 772),
        SeedRoom("A35", 539, 703, 601, 772),
        SeedRoom("A34", 475, 703, 538, 772),
        SeedRoom("A33", 407, 703, 474, 772),
        # South-west corner
        SeedRoom("A27", 151, 826, 259, 942),
        SeedRoom("A26", 262, 801, 400, 862),
        SeedRoom("A25a", 400, 801, 510, 940),
        SeedRoom("A25b", 262, 863, 394, 940),
        # Textile corner
        SeedRoom("A22b", 748, 869, 800, 922),
        SeedRoom("A22a", 801, 869, 854, 922),
        # Drawn off on its own at the foot of the sheet
        SeedRoom("A23", 537, 1003, 637, 1097),
        # Facilities
        SeedRoom("Bathroom 3", 151, 642, 320, 722, kind="restroom"),
        SeedRoom("Bathroom 4", 151, 726, 320, 813, kind="restroom"),
        SeedRoom("Electrical Room", 349, 622, 372, 801, kind="facility"),
        SeedRoom("Stairs", 291, 505, 322, 578, kind="facility"),
        SeedRoom("Admin Office", 513, 801, 658, 938, kind="facility"),
        SeedRoom("Tufting Studio", 745, 822, 855, 867, kind="facility"),
        SeedRoom("Closet", 465, 721, 491, 770, kind="facility"),
    ),
)


# ── Floor 1 — B- and C-series studios, the shops, and the shelves ───────────────────
_FIRST_FLOOR = SeedFloor(
    name="Floor 1",
    sort_order=1,
    origin_x=30,
    origin_y=25,
    width=1535,
    height=775,
    caption="Floor 1, traced from the Past Lives Floor 1 sheet — studios, shops and shelves.",
    rooms=(
        # B studios — north wall
        SeedRoom("B1", 283, 200, 330, 243),
        SeedRoom("B1a", 283, 244, 330, 290),
        SeedRoom("B2", 331, 200, 380, 268),
        SeedRoom("B3a", 381, 200, 407, 262),
        SeedRoom("B3b", 408, 200, 441, 262),
        SeedRoom("B4a", 442, 200, 470, 262),
        SeedRoom("B4b", 471, 200, 525, 262),
        SeedRoom("B6a", 552, 200, 579, 262),
        SeedRoom("B6b", 580, 200, 607, 262),
        # B studios — second bank
        SeedRoom("B29", 266, 320, 339, 377),
        SeedRoom("B28", 266, 377, 339, 430),
        SeedRoom("B27", 266, 431, 339, 470),
        SeedRoom("B26", 340, 431, 374, 470),
        SeedRoom("B22", 375, 321, 401, 352),
        SeedRoom("B21", 402, 318, 425, 352),
        SeedRoom("B23a", 375, 352, 388, 416),
        SeedRoom("B23b", 388, 352, 401, 416),
        SeedRoom("B19", 402, 352, 425, 416),
        SeedRoom("B18", 375, 416, 425, 471),
        SeedRoom("B13", 453, 321, 486, 353),
        SeedRoom("B14", 453, 354, 486, 398),
        SeedRoom("B15", 453, 399, 486, 430),
        SeedRoom("B16", 453, 431, 486, 470),
        SeedRoom("B11", 487, 319, 508, 416),
        SeedRoom("B12", 509, 319, 530, 416),
        SeedRoom("B10", 487, 416, 556, 470),
        SeedRoom("B9b", 556, 442, 583, 468),
        SeedRoom("B9a", 584, 442, 612, 468),
        SeedRoom("B7", 573, 319, 592, 412),
        SeedRoom("B8", 593, 319, 611, 412),
        # C studios — the north-east block
        SeedRoom("C1", 639, 388, 691, 441),
        SeedRoom("C5", 692, 470, 727, 497),
        SeedRoom("C10", 727, 470, 757, 497),
        SeedRoom("C2", 770, 369, 812, 497),
        SeedRoom("C15a (North)", 812, 369, 880, 421, space_id="C15"),
        SeedRoom("C15b (South)", 812, 421, 880, 481, space_id="C15"),
        SeedRoom("C18b", 881, 444, 891, 470),
        # C studios — the ceramics/glaze block
        SeedRoom("C37", 663, 546, 678, 577),
        SeedRoom("C38", 663, 578, 678, 609),
        SeedRoom("C36", 679, 542, 724, 580),
        SeedRoom("C35", 679, 581, 724, 609),
        SeedRoom("C43", 598, 592, 627, 628),
        SeedRoom("C44", 598, 629, 627, 666),
        SeedRoom("C45", 598, 667, 627, 705),
        SeedRoom("C52", 539, 606, 568, 628),
        SeedRoom("C51", 539, 643, 568, 666),
        SeedRoom("C50", 539, 675, 568, 705),
        SeedRoom("C41", 631, 610, 652, 706),
        SeedRoom("C39", 653, 610, 695, 666),
        SeedRoom("C40", 653, 667, 695, 706),
        SeedRoom("C34", 697, 610, 730, 634),
        SeedRoom("C33", 697, 635, 730, 655),
        SeedRoom("C32", 697, 656, 730, 684),
        SeedRoom("C31", 697, 685, 730, 706),
        # C studios — the south-east rows
        SeedRoom("C53", 785, 542, 874, 581),
        SeedRoom("C55", 875, 542, 941, 581),
        SeedRoom("C24", 767, 594, 791, 630),
        SeedRoom("C25", 792, 594, 816, 630),
        SeedRoom("C26", 817, 594, 846, 630),
        SeedRoom("C27", 847, 594, 874, 630),
        SeedRoom("C28", 875, 594, 894, 630),
        SeedRoom("C22", 758, 666, 780, 705),
        SeedRoom("C23", 781, 666, 808, 705),
        SeedRoom("C21", 809, 666, 837, 705),
        SeedRoom("C20", 838, 666, 866, 705),
        SeedRoom("C19d", 867, 666, 894, 705),
        SeedRoom("C19c", 906, 666, 935, 712),
        SeedRoom("C19b", 936, 666, 975, 712),
        SeedRoom("C19a", 975, 687, 1006, 712),
        # The mezzanines — only the biggest is a leasable record today
        SeedRoom("Mezzanine 1", 1072, 749, 1126, 796, space_id="Mezzanine"),
        SeedRoom("Mezzanine 2", 980, 749, 1034, 796, kind="facility"),
        SeedRoom("Mezzanine 3", 895, 749, 979, 796, kind="facility"),
        # Shops and shared areas
        SeedRoom("Garden Guild", 36, 30, 600, 160, kind="facility"),
        SeedRoom("Bus #1", 710, 40, 860, 96, kind="facility"),
        SeedRoom("Trailer", 870, 40, 940, 96, kind="facility"),
        SeedRoom("PL Truck", 783, 110, 940, 155, kind="facility"),
        SeedRoom("Bus #2", 955, 33, 1112, 92, kind="facility"),
        SeedRoom("Driveway", 955, 100, 1155, 157, kind="facility"),
        SeedRoom("Sanding Area", 958, 215, 1065, 248, kind="facility"),
        SeedRoom("Loading Bay", 955, 255, 1115, 305, kind="facility"),
        SeedRoom("CNC", 958, 308, 1070, 348, kind="facility"),
        SeedRoom("Wood Shop", 700, 230, 880, 330, kind="facility"),
        SeedRoom("Metal Shop", 960, 470, 1110, 570, kind="facility"),
        SeedRoom("Gallery", 100, 360, 260, 420, kind="facility"),
        SeedRoom("Ceramics", 456, 478, 612, 603, kind="facility"),
        SeedRoom("Electrical Access", 526, 200, 551, 262, kind="facility"),
        SeedRoom("Leather", 895, 585, 941, 630, kind="facility"),
        *_shelf_rooms(),
    ),
)


#: Both floors, in the order they appear on the members' floor switcher.
FLOORS: tuple[SeedFloor, ...] = (_FIRST_FLOOR, _SECOND_FLOOR)
