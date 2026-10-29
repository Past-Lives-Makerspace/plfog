"""Vendor Tables' importer: Kate's sheet (or the page's own export) read into table requests.

Two steps, so the CSV upload and the daily sheet pull share one reader and differ only in their rule:

1. :func:`parse_rows` takes the rows of cells (the header first) and returns a :class:`ParsedSheet`: per data row
   its sheet row number, its match key (the name stripped, inner spaces collapsed, casefolded), its raw cells by
   column key, the values its non-blank cells read as, and the problems of the cells that could not be read. It
   touches the database only through the guilds it is handed. :func:`read_csv` turns an uploaded file's bytes into
   those rows, or refuses the file whole (:class:`ImportRefused`).
2. :func:`apply_upload` is the CSV upload's rule: a row whose name matches a table on the hub is updated from its
   non-blank cells only (a blank cell never clears a field); an unmatched row is added with ``created_by`` the
   importer; nothing is deleted; a name seen twice in one file keeps the first and reports the rest. One
   transaction.

Columns are read by header name, case and spacing ignored, in any order; a column the importer does not know is
left alone (the export's Added by and Added). The old export's Artist or group and Setup day, and Notes for
Source, are read too. Values are read loosely: a guild by its name with or without "Guild" and a plural s, then by
a unique start of its name; a size or status by its label or its value; a setup window by its label or its day; a
date as 2026-10-21 or 10/21/2026.
"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import TYPE_CHECKING, Any

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import models, transaction

from hq.holiday_bizarre import VENDOR_SETUP_WINDOWS
from hq.models import VendorSetup, VendorTable, VendorTableSize, VendorTableStatus

if TYPE_CHECKING:
    from hq.models import EventHub
    from membership.models import Guild, Member

# The largest file and the most data rows an upload takes; past either the file is refused whole.
MAX_IMPORT_BYTES = 1024 * 1024
MAX_IMPORT_ROWS = 1000
TOO_LARGE = "That file is over 1 MB. Nothing was imported."

# The columns, keyed by the VendorTable field each fills, in the sheet's order; the header is the sheet's.
COLUMNS: tuple[tuple[str, str], ...] = (
    ("name", "Name"),
    ("guild", "Guild"),
    ("size", "Table size"),
    ("status", "Status"),
    ("sells", "What they sell"),
    ("setup", "Setup time"),
    ("table_no", "Table #"),
    ("email", "Email"),
    ("phone", "Phone"),
    ("note", "Source"),
    ("requested_on", "Requested on"),
    ("request_source", "Request source"),
    ("discord_handle", "Discord handle"),
    ("needs", "Needs"),
)
HEADERS: dict[str, str] = dict(COLUMNS)

# Other headers for a column: the old export's, and the sheet's earlier name for Source.
HEADER_ALIASES: dict[str, str] = {
    "artist or group": "name",
    "setup day": "setup",
    "notes": "note",
}

# Text columns: each is read as typed (trimmed) and refused past its field's length.
TEXT_FIELDS = ("name", "sells", "table_no", "phone", "note", "request_source", "discord_handle", "needs")

# What the export writes for an empty guild and setup, read back as those empty values.
NO_GUILD = "not in a guild"

# Words for a size or status beyond its label and value (spacing and case are ignored before these are looked up).
SIZE_ALIASES: dict[str, str] = {
    "halftable(shared)": VendorTableSize.HALF,
    "halftable": VendorTableSize.HALF,
    "bringingown": VendorTableSize.OWN,
    "bringingtheirown": VendorTableSize.OWN,
}
STATUS_ALIASES: dict[str, str] = {"canceled": VendorTableStatus.CANCELLED}

# A shop name that means a guild found by the start of its name: Woodshop is the woodworking guild.
GUILD_NICKNAMES: dict[str, str] = {"woodshop": "wood"}

# What the export puts before a cell a spreadsheet would run as a formula (hq.exports._neutralise_formula).
_FORMULA_LEADS = ("=", "+", "-", "@", "\t", "\r")

_DMY = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")


class ImportRefused(Exception):
    """The file cannot be read at all, so nothing in it is written. The message says why, in the vendor team's
    words."""


def normalise(text: str) -> str:
    """``text`` stripped, its inner whitespace collapsed to one space, casefolded: how names and headers match."""
    return " ".join(text.split()).casefold()


def _squash(text: str) -> str:
    """``text`` casefolded with every space removed: how a size or a status label matches ("6ft" is "6 ft")."""
    return "".join(text.split()).casefold()


@dataclass(frozen=True)
class ImportProblem:
    """One cell (or row) that could not be read: its sheet row number, the column's header, and what went wrong."""

    line: int
    column: str
    message: str

    def __str__(self) -> str:
        return f"Row {self.line}: {self.message}"


@dataclass(frozen=True)
class ParsedRow:
    """One data row of the sheet.

    ``line`` is its row number in the sheet (the header is row 1). ``key`` is the name to match on, ``cells`` every
    known column's raw text, ``values`` what each non-blank cell read as (a field name to the value to store; a cell
    that could not be read is left out and named in ``problems``).
    """

    line: int
    key: str
    cells: dict[str, str]
    values: dict[str, Any]
    problems: tuple[ImportProblem, ...]


@dataclass(frozen=True)
class ParsedSheet:
    """The rows that carry anything, in sheet order, with the columns the header named."""

    columns: tuple[str, ...]
    rows: tuple[ParsedRow, ...]


@dataclass
class ImportResult:
    """What an upload did: tables added, updated and left as they were, and every problem in row order."""

    added: int = 0
    updated: int = 0
    unchanged: int = 0
    problems: list[ImportProblem] = field(default_factory=list)


# ---- Reading the file ----


def read_csv(data: bytes) -> list[list[str]]:
    """The rows of an uploaded CSV, or :class:`ImportRefused` saying why the file cannot be read.

    Raises:
        ImportRefused: over :data:`MAX_IMPORT_BYTES`, not UTF-8 (a byte order mark is fine), not CSV, or over
            :data:`MAX_IMPORT_ROWS` rows after the header.
    """
    if len(data) > MAX_IMPORT_BYTES:
        raise ImportRefused(TOO_LARGE)
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ImportRefused("That file is not a CSV saved as UTF-8. Nothing was imported.") from exc
    if "\x00" in text:
        raise ImportRefused("That file is not a CSV. Nothing was imported.")
    try:
        rows = list(csv.reader(io.StringIO(text, newline=""), strict=True))
    except csv.Error as exc:
        raise ImportRefused("That file could not be read as a CSV. Nothing was imported.") from exc
    if sum(1 for row in rows[1:] if any(cell.strip() for cell in row)) > MAX_IMPORT_ROWS:
        raise ImportRefused(f"That file has more than {MAX_IMPORT_ROWS} rows. Nothing was imported.")
    return rows


def header_columns(header: Sequence[str]) -> dict[int, str]:
    """The known columns of ``header``, by position: each cell's column key, read by name with case and spacing
    ignored. A column named twice is read from its first place; an unknown one is left out."""
    by_header = {normalise(label): key for key, label in COLUMNS} | HEADER_ALIASES
    columns: dict[int, str] = {}
    for position, cell in enumerate(header):
        key = by_header.get(normalise(cell))
        if key is not None and key not in columns.values():
            columns[position] = key
    return columns


def parse_rows(rows: Sequence[Sequence[str]], *, guilds: Sequence[Guild]) -> ParsedSheet:
    """Read ``rows`` (the header first) into a :class:`ParsedSheet`, matching guilds among ``guilds``.

    A row with no cell filled is skipped; a row with cells and no name is kept with a problem and no key.

    Raises:
        ImportRefused: the header has no Name column.
    """
    if not rows:
        raise ImportRefused("That file is empty. Nothing was imported.")
    columns = header_columns(rows[0])
    if "name" not in columns.values():
        raise ImportRefused("That file has no Name column. Nothing was imported.")
    reader = _CellReader(guilds)
    parsed = []
    for index, row in enumerate(rows[1:], start=2):
        cells = {key: row[position] if position < len(row) else "" for position, key in columns.items()}
        if any(cell.strip() for cell in cells.values()):
            parsed.append(reader.row(index, cells))
    return ParsedSheet(columns=tuple(columns.values()), rows=tuple(parsed))


class _CellReader:
    """Reads one row's cells into field values, with the guilds' lookups built once per file."""

    def __init__(self, guilds: Sequence[Guild]) -> None:
        self.guilds = list(guilds)
        self.max_lengths = {name: _max_length(name) for name in (*TEXT_FIELDS, "email")}

    def row(self, line: int, cells: dict[str, str]) -> ParsedRow:
        values: dict[str, Any] = {}
        problems: list[ImportProblem] = []
        for key, raw in cells.items():
            text = _unneutralise(raw.strip())
            if not text:
                continue
            try:
                values[key] = self.value(key, text)
            except ValueError as exc:
                problems.append(ImportProblem(line, HEADERS[key], str(exc)))
        key = normalise(values.get("name", ""))
        if not cells["name"].strip():
            problems.insert(0, ImportProblem(line, HEADERS["name"], "No Name, so the row was skipped."))
        return ParsedRow(line=line, key=key, cells=cells, values=values, problems=tuple(problems))

    def value(self, key: str, text: str) -> Any:
        """The value ``text`` (non-blank, trimmed) stores in ``key``'s field.

        Raises:
            ValueError: the text cannot be read; the message names the column and the text.
        """
        if key in TEXT_FIELDS:
            return self._text(key, text)
        reader = {
            "guild": self.guild,
            "size": _size,
            "status": _status,
            "setup": _setup,
            "email": self.email,
            "requested_on": _date,
        }[key]
        return reader(text)

    def _text(self, key: str, text: str) -> str:
        limit = self.max_lengths[key]
        if len(text) > limit:
            raise ValueError(f"{HEADERS[key]} is longer than {limit} characters.")
        return text

    def email(self, text: str) -> str:
        email = text.lower()
        try:
            validate_email(email)
        except ValidationError as exc:
            raise ValueError(f'Email "{text}" is not an email address.') from exc
        if len(email) > self.max_lengths["email"]:
            raise ValueError(f"Email is longer than {self.max_lengths['email']} characters.")
        return email

    def guild(self, text: str) -> Guild | None:
        """The active guild ``text`` names, or None for Not in a guild.

        By name with or without "Guild" and a plural s ("Ceramics" is the Ceramics Guild), then the one guild whose
        name starts with it ("Wood" is the Woodworking Guild when no other guild starts with Wood). Woodshop is
        read as Wood.
        """
        wanted = normalise(text)
        if wanted == NO_GUILD:
            return None
        stem = _guild_stem(wanted)
        exact = [guild for guild in self.guilds if _guild_stem(normalise(guild.name)) == stem]
        if len(exact) == 1:
            return exact[0]
        start = GUILD_NICKNAMES.get(stem, stem)
        starts = [guild for guild in self.guilds if _guild_stem(normalise(guild.name)).startswith(start)]
        if len(exact) > 1 or len(starts) > 1:
            raise ValueError(f'Guild "{text}" matches more than one guild.')
        if not starts:
            raise ValueError(f'Guild "{text}" is not one of the active guilds.')
        return starts[0]


def _max_length(name: str) -> int:
    """The longest text ``name``'s field holds."""
    field = VendorTable._meta.get_field(name)
    assert isinstance(field, models.CharField) and field.max_length is not None, name
    return field.max_length


def _guild_stem(name: str) -> str:
    """A normalised guild name without a last word "guild" and a plural s: "ceramics guild" is "ceramic"."""
    words = name.split()
    if len(words) > 1 and words[-1] == "guild":
        words = words[:-1]
    stem = " ".join(words)
    return stem[:-1] if stem.endswith("s") and len(stem) > 1 else stem


def _unneutralise(text: str) -> str:
    """``text`` without the apostrophe the export puts before a cell a spreadsheet would run as a formula, so an
    exported "'=SUM(1)" reads back as the "=SUM(1)" it was."""
    return text[1:] if text.startswith("'") and text[1:].startswith(_FORMULA_LEADS) else text


def _choice(text: str, column: str, choices: Any, aliases: Mapping[str, str]) -> str:
    lookup = {_squash(str(label)): value for value, label in choices.choices} | {
        _squash(value): value for value in choices.values
    }
    lookup |= aliases
    found = lookup.get(_squash(text))
    if found is None:
        labels = ", ".join(str(label) for label in choices.labels)
        raise ValueError(f'{column} "{text}" is not one of {labels}.')
    return str(found)


def _size(text: str) -> str:
    return _choice(text, HEADERS["size"], VendorTableSize, SIZE_ALIASES)


def _status(text: str) -> str:
    return _choice(text, HEADERS["status"], VendorTableStatus, STATUS_ALIASES)


def _setup(text: str) -> str:
    """The setup window ``text`` names: its label, its value, or anything starting with its day ("Thursday");
    "Not chosen" is none."""
    wanted = normalise(text)
    if wanted == normalise(str(VendorSetup.NOT_CHOSEN.label)):
        return VendorSetup.NOT_CHOSEN
    for value, label in VENDOR_SETUP_WINDOWS.items():
        if wanted == normalise(label) or wanted.startswith(value):
            return value
    windows = ", ".join(VENDOR_SETUP_WINDOWS.values())
    raise ValueError(f'{HEADERS["setup"]} "{text}" is not one of {windows}.')


def _date(text: str) -> date:
    """A date written 2026-10-21 or 10/21/2026."""
    try:
        return date.fromisoformat(text) if "-" in text else _month_day_year(text)
    except ValueError as exc:
        raise ValueError(f'{HEADERS["requested_on"]} "{text}" is not a date like 2026-10-21 or 10/21/2026.') from exc


def _month_day_year(text: str) -> date:
    match = _DMY.match(text)
    if match is None:
        raise ValueError(text)
    month, day, year = (int(part) for part in match.groups())
    return datetime(year, month, day).date()


# ---- The CSV upload's rule ----


def apply_upload(hub: EventHub, sheet: ParsedSheet, *, member: Member | None) -> ImportResult:
    """Write ``sheet`` to ``hub``'s table requests by the CSV upload's rule, in one transaction.

    A row matches the hub's table of the same name (case and spacing ignored; the earliest when two share a name)
    and sets only the fields its non-blank cells read; a row that changes nothing is unchanged. An unmatched row is
    added, made by ``member``, requested unless its Status says otherwise. A later row with a name seen earlier in
    the file is skipped and reported. Nothing is deleted.
    """
    result = ImportResult()
    with transaction.atomic():
        existing: dict[str, VendorTable] = {}
        for table in hub.vendor_tables.select_related("guild").select_for_update(of=("self",)).order_by("pk"):
            existing.setdefault(normalise(table.name), table)
        first_line: dict[str, int] = {}
        for row in sheet.rows:
            result.problems.extend(row.problems)
            if not row.key:
                continue
            if row.key in first_line:
                result.problems.append(
                    ImportProblem(
                        row.line,
                        HEADERS["name"],
                        f'"{row.values["name"]}" is also on row {first_line[row.key]}, so this row was skipped.',
                    )
                )
                continue
            first_line[row.key] = row.line
            match = existing.get(row.key)
            if match is None:
                VendorTable.objects.create(hub=hub, created_by=member, **row.values)
                result.added += 1
            elif _update(match, row.values):
                result.updated += 1
            else:
                result.unchanged += 1
    result.problems.sort(key=lambda problem: problem.line)
    return result


def _update(table: VendorTable, values: Mapping[str, Any]) -> bool:
    """Set ``values`` on ``table`` and save it when any differs; whether it did."""
    changed = [name for name, value in values.items() if getattr(table, name) != value]
    if not changed:
        return False
    for name in changed:
        setattr(table, name, values[name])
    table.save()
    return True
