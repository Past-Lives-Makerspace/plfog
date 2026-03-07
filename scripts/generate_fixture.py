#!/usr/bin/env python3
"""Generate Django fixture JSON from the plfog CSV export.

Usage:
    python scripts/generate_fixture.py ../../plfog.csv > membership/fixtures/initial_data.json

This script is pure Python (no Django ORM required). It reads the CSV and outputs
a JSON fixture suitable for `python manage.py loaddata initial_data`.
"""

from __future__ import annotations

import csv
import json
import re
import sys
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

STANDARD_PLAN_PK = 1
STANDARD_PLAN_PRICE = "130.00"

# Placeholder timestamp for auto_now_add fields (fixtures require explicit values)
CREATED_AT = "2025-01-01T00:00:00Z"

# Guild space mappings: (space_id) -> guild_name
GUILD_SPACE_MAP: dict[str, str] = {
    "A2b": "Glass Guild",
    "A22b": "Textiles Guild",
    "A28": "Tech Guild",
    "A33": "Art Framing Guild",
    "A34": "Art Framing Guild",
    "A51": "Prison Outreach Guild",
    "A52": "Glass Guild",
    "B21": "Art Framing Guild",
    "B32a": "Gallery & Retail Guild",
    "B32c": "Gallery & Retail Guild",
    "C28": "Leatherwork Guild",
    "C54": "Ceramics Guild",
}

# Sub-units of A2b (Glass Guild) - Space only, no Lease
PLM_SUB_UNITS: set[str] = {"A2c", "A2d", "A2e", "A2f", "A2g"}

# Facility spaces - Space only, no Lease, status=maintenance, is_rentable=False
FACILITY_SPACES: set[str] = {
    "A21b",
    "A24",
    "B5",
    "B9b",
    "B32b",
    "C29",
    "C29b",
    "C30",
    "C60",
}

# Battery storage - Space only, no Lease
BATTERY_STORAGE_SPACE_ID = "C12"

# Member name normalization: short form -> canonical full_legal_name
MEMBER_NAME_ALIASES: dict[str, str] = {
    "Ochen": "Ochen Kaylan",
    "Ha'Ne": "Ha'ne",
}

# Special lease overrides keyed by (space_id, member_name)
LEASE_OVERRIDES: dict[tuple[str, str], dict[str, Any]] = {
    ("A14a", "Elle McGillagreen"): {
        "prepaid_through": "2025-12-20",
        "discount_reason": ("Paid for one year. $150/month minus $270 for prepayment. Expires December 20, 2025."),
    },
    ("B16", "Sy Baskent"): {
        "prepaid_through": "2026-12-01",
        "monthly_rent_override": Decimal("0.00"),
        "discount_reason": ("Purchased planer for $3600, covers rent through December 2026"),
    },
    ("B1", "Francisco Salgado"): {
        "prepaid_through": "2026-07-01",
        "lease_type": "annual",
        "discount_reason": "One year term prepaid",
    },
    ("B2", "Brian Boring"): {
        "lease_type": "annual",
        "discount_reason": "One year term (10% discount)",
    },
    ("B11", "Sloan Coffin"): {
        "lease_type": "annual",
        "discount_reason": "10% annual discount",
    },
    ("B12", "Sloan Coffin"): {
        "lease_type": "annual",
        "discount_reason": "10% annual discount",
    },
    ("A26", "Kira Hosler"): {
        "prepaid_through": "2025-12-31",
        "discount_reason": "Paid $3500 for Jan-Dec 2025",
    },
    ("A44", "Allyson Barlow"): {
        "is_split_override": True,
    },
}


# ---------------------------------------------------------------------------
# Parsed row dataclass
# ---------------------------------------------------------------------------


@dataclass
class ParsedRow:
    """All parsed/cleaned values from a single CSV row."""

    space_code: str
    label: str
    member_raw: str
    full_price: Decimal | None
    open_val: Decimal | None
    actual_paid: Decimal | None
    notes: str
    space_id: str
    space_type: str
    sqft: Decimal | None
    width: Decimal | None
    depth: Decimal | None
    rate: Decimal | None
    deposit: Decimal | None


# ---------------------------------------------------------------------------
# Accumulator for collecting fixture data
# ---------------------------------------------------------------------------


@dataclass
class FixtureAccumulator:
    """Collects all fixture data during row processing."""

    guilds: dict[str, int] = field(default_factory=dict)
    members: dict[str, int] = field(default_factory=dict)
    member_preferred: dict[str, str] = field(default_factory=dict)
    spaces: list[dict[str, Any]] = field(default_factory=list)
    space_id_to_pk: dict[str, int] = field(default_factory=dict)
    leases: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    _guild_pk_counter: int = 0
    _member_pk_counter: int = 0
    _lease_pk_counter: int = 0

    def get_or_create_guild(self, name: str) -> int:
        if name not in self.guilds:
            self._guild_pk_counter += 1
            self.guilds[name] = self._guild_pk_counter
        return self.guilds[name]

    def get_or_create_member(self, raw_name: str) -> int:
        name = clean_member_name(raw_name)
        if name not in self.members:
            self._member_pk_counter += 1
            self.members[name] = self._member_pk_counter
            if name == "Ochen Kaylan":
                self.member_preferred[name] = "Ochen"
        return self.members[name]

    def next_lease_pk(self) -> int:
        self._lease_pk_counter += 1
        return self._lease_pk_counter


# ---------------------------------------------------------------------------
# Data cleaning helpers
# ---------------------------------------------------------------------------


def extract_space_id(space_code: str) -> str:
    """Extract canonical space_id from the raw Space Code column."""
    code = space_code.strip()

    # Storage: "S1 Storage - Space N" -> "S1-N"
    m = re.match(r"S(\d+)\s+Storage\s*-\s*Space\s+(\d+)", code)
    if m:
        return f"S{m.group(1)}-{m.group(2)}"

    # Wood storage: "WN - Wood Storage" -> "WN"
    m = re.match(r"(W\d+)\s*-", code)
    if m:
        return m.group(1)

    # Parking: "Parking Space" -> "P1", "Parking Space #2" -> "P2"
    m = re.match(r"Parking Space(?:\s*#(\d+))?", code)
    if m:
        num = m.group(1) or "1"
        return f"P{num}"

    # Mezzanine
    if code.lower().startswith("mezzanine"):
        return "Mezzanine"

    # C30 special: "C30 (a,b,c,d)" -> "C30"
    m = re.match(r"(C30)\s*\(", code)
    if m:
        return m.group(1)

    # General: extract alphanumeric prefix (e.g., "A1 Studio" -> "A1")
    m = re.match(r"([A-Z]\d+[a-z]?)", code)
    if m:
        return m.group(1)

    # Fallback: E1, etc.
    m = re.match(r"([A-Z]+\d+[a-z]?)", code)
    if m:
        return m.group(1)

    # Last resort: use the whole code truncated to 20 chars
    return code[:20]


def classify_space_type(space_code: str, space_id: str) -> str:
    """Determine space type from the space code."""
    if space_id.startswith("S") or space_id.startswith("W"):
        return "storage"
    if "Parking" in space_code:
        return "parking"
    if space_id == "Mezzanine":
        return "other"
    return "studio"


def parse_currency(value: str) -> Decimal | None:
    """Parse a currency string like '$255.00' or '$2,175.00' into Decimal."""
    if not value or not value.strip():
        return None
    cleaned = value.strip().replace("$", "").replace(",", "")
    if not cleaned:
        return None
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def parse_sqft(value: str) -> Decimal | None:
    """Parse square footage, handling *, ~, text annotations."""
    if not value or not value.strip():
        return None
    cleaned = value.strip().lstrip("*").lstrip("~").strip()
    m = re.match(r"([\d.]+)", cleaned)
    if m:
        try:
            return Decimal(m.group(1))
        except InvalidOperation:
            return None
    return None


def parse_dimensions(value: str) -> tuple[Decimal | None, Decimal | None]:
    """Parse dimensions like '8.5 x 8' -> (width, depth)."""
    if not value or not value.strip():
        return None, None
    cleaned = value.strip().lstrip("*").lstrip("~").strip()
    if cleaned.upper() in ("X", "SEE NOTES", ""):
        return None, None
    m = re.match(r"([\d.]+)\s*[xX]\s*([\d.]+)", cleaned)
    if m:
        try:
            return Decimal(m.group(1)), Decimal(m.group(2))
        except InvalidOperation:
            return None, None
    return None, None


def parse_rate_per_sqft(value: str) -> Decimal | None:
    """Parse $/sq ft column."""
    if not value or not value.strip():
        return None
    try:
        return Decimal(value.strip())
    except InvalidOperation:
        return None


def clean_member_name(raw: str) -> str:
    """Clean member name from CSV: strip Airtable suffix, trailing dash."""
    name = raw.strip()
    name = re.sub(r"\s+-\s+\d+$", "", name)
    name = re.sub(r"\s+-$", "", name)
    if name in MEMBER_NAME_ALIASES:
        name = MEMBER_NAME_ALIASES[name]
    return name


def decimal_to_str(d: Decimal | None) -> str | None:
    """Convert Decimal to string for JSON output, or None."""
    if d is None:
        return None
    return str(d)


def _make_space_obj(
    row: ParsedRow,
    *,
    is_rentable: bool,
    status: str,
    notes: str,
    manual_price: Decimal | None = None,
) -> dict[str, Any]:
    """Build a space dict from a parsed row and overrides."""
    return {
        "space_id": row.space_id,
        "name": row.space_code,
        "space_type": row.space_type,
        "size_sqft": decimal_to_str(row.sqft),
        "width": decimal_to_str(row.width),
        "depth": decimal_to_str(row.depth),
        "rate_per_sqft": decimal_to_str(row.rate),
        "is_rentable": is_rentable,
        "manual_price": decimal_to_str(manual_price),
        "status": status,
        "notes": notes,
    }


# ---------------------------------------------------------------------------
# Row reading with multiline CSV handling
# ---------------------------------------------------------------------------


def read_csv_rows(filepath: str) -> list[dict[str, str]]:
    """Read the CSV and return list of row dicts.

    The CSV has multiline fields (quoted), which Python's csv module handles.
    We filter out continuation rows that don't have meaningful first-column data.
    """
    col_names = [
        "space_code",
        "label",
        "member",
        "full_price",
        "open",
        "actual_amount_paid",
        "dollar_loss",
        "dimensions",
        "sqft",
        "deviation",
        "earn_money",
        "paid_deposit",
        "notes",
        "accurate_complete",
        "rate_per_sqft",
    ]

    with open(filepath, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        next(reader)  # Skip header row

        rows = []
        for raw_row in reader:
            while len(raw_row) < len(col_names):
                raw_row.append("")

            row = {col: raw_row[i] if i < len(raw_row) else "" for i, col in enumerate(col_names)}

            if not row["space_code"].strip():
                continue
            if re.match(r"^\s*[\d.]+\s*$", row["space_code"]):
                continue

            rows.append(row)

    return rows


def parse_row(row: dict[str, str]) -> ParsedRow:
    """Parse and clean a single CSV row into a ParsedRow."""
    space_code = row["space_code"].strip()
    space_id = extract_space_id(space_code)
    dimensions_raw = row["dimensions"].strip()
    width, depth = parse_dimensions(dimensions_raw)

    return ParsedRow(
        space_code=space_code,
        label=row["label"].strip(),
        member_raw=row["member"].strip(),
        full_price=parse_currency(row["full_price"]),
        open_val=parse_currency(row["open"]),
        actual_paid=parse_currency(row["actual_amount_paid"]),
        notes=row["notes"].strip(),
        space_id=space_id,
        space_type=classify_space_type(space_code, space_id),
        sqft=parse_sqft(row["sqft"].strip()),
        width=width,
        depth=depth,
        rate=parse_rate_per_sqft(row["rate_per_sqft"].strip()),
        deposit=parse_currency(row["paid_deposit"].strip()),
    )


# ---------------------------------------------------------------------------
# Row classification and processing
# ---------------------------------------------------------------------------


def _handle_non_tenant_row(row: ParsedRow, acc: FixtureAccumulator) -> bool:
    """Handle PLM shelf, battery, facility, sub-unit, and vacancy rows.

    Returns True if the row was handled, False if it should continue
    to guild/tenant processing.
    """
    if row.member_raw == "PLM Shelf":
        acc.spaces.append(
            _make_space_obj(row, is_rentable=False, status="maintenance", notes="PLM shelf - not for rent")
        )
        return True

    if row.space_id == BATTERY_STORAGE_SPACE_ID:
        acc.spaces.append(
            _make_space_obj(
                row,
                is_rentable=False,
                status="maintenance",
                notes=f"Battery storage. {row.notes}".strip(),
            )
        )
        return True

    if row.space_id in FACILITY_SPACES:
        acc.spaces.append(
            _make_space_obj(
                row,
                is_rentable=False,
                status="maintenance",
                notes=f"{row.label}. {row.notes}".strip() if row.notes else row.label,
                manual_price=row.full_price,
            )
        )
        return True

    if row.space_id in PLM_SUB_UNITS:
        acc.spaces.append(
            _make_space_obj(
                row,
                is_rentable=False,
                status="occupied",
                notes=f"Sub-unit of A2b (Glass Guild). {row.notes}".strip(),
            )
        )
        return True

    # Vacant spaces (Label="Open", Member="X")
    if row.label == "Open" and row.member_raw == "X":
        open_note = f"Half-rent amount: ${row.open_val}" if row.open_val is not None else ""
        notes = f"{open_note}. {row.notes}".strip(". ") if open_note else row.notes
        acc.spaces.append(
            _make_space_obj(row, is_rentable=True, status="available", notes=notes, manual_price=row.full_price)
        )
        return True

    # Open storage/wood storage (Member="Open")
    if row.member_raw == "Open":
        open_note = f"Available at: ${row.open_val}" if row.open_val is not None else ""
        notes = f"{open_note}. {row.notes}".strip(". ") if open_note else row.notes
        acc.spaces.append(
            _make_space_obj(row, is_rentable=True, status="available", notes=notes, manual_price=row.full_price)
        )
        return True

    # Remaining PLM rows (not in guild map -- guild spaces are handled separately)
    member_name = clean_member_name(row.member_raw)
    if member_name == "PLM" and row.space_id not in GUILD_SPACE_MAP:
        acc.warnings.append(f"Unclassified PLM row: {row.space_id} ({row.label}) - creating as facility space")
        acc.spaces.append(
            _make_space_obj(
                row,
                is_rentable=False,
                status="maintenance",
                notes=f"{row.label}. {row.notes}".strip() if row.notes else row.label,
                manual_price=row.full_price,
            )
        )
        return True

    if member_name == "Battery storage":
        acc.spaces.append(
            _make_space_obj(
                row,
                is_rentable=False,
                status="maintenance",
                notes=f"Battery storage. {row.notes}".strip(),
            )
        )
        return True

    return False


def _handle_guild_row(row: ParsedRow, acc: FixtureAccumulator) -> bool:
    """Handle guild space rows. Returns True if handled."""
    if row.space_id not in GUILD_SPACE_MAP:
        return False

    guild_name = GUILD_SPACE_MAP[row.space_id]
    guild_pk = acc.get_or_create_guild(guild_name)

    acc.spaces.append(
        _make_space_obj(row, is_rentable=True, status="occupied", notes=row.notes, manual_price=row.full_price)
    )

    if row.actual_paid is not None or row.full_price is not None:
        base = row.full_price if row.full_price is not None else Decimal("0.00")
        rent = row.actual_paid if row.actual_paid is not None else Decimal("0.00")
        acc.leases.append(
            _make_lease(
                acc.next_lease_pk(),
                content_type=["membership", "guild"],
                object_id=guild_pk,
                space_id=row.space_id,
                base_price=base,
                monthly_rent=rent,
                deposit=row.deposit,
                notes=row.notes,
            )
        )

    return True


def _handle_tenant_row(row: ParsedRow, acc: FixtureAccumulator) -> None:
    """Handle an occupied space with a named tenant member."""
    member_pk = acc.get_or_create_member(row.member_raw)
    member_name = clean_member_name(row.member_raw)

    acc.spaces.append(
        _make_space_obj(row, is_rentable=True, status="occupied", notes=row.notes, manual_price=row.full_price)
    )

    is_split = row.open_val is not None and row.actual_paid is not None and row.actual_paid > Decimal("0")
    monthly_rent = row.actual_paid if row.actual_paid is not None else Decimal("0.00")
    base = row.full_price if row.full_price is not None else Decimal("0.00")

    # Apply special-case overrides
    lease_type = "month_to_month"
    discount_reason = ""
    prepaid_through = None

    overrides = LEASE_OVERRIDES.get((row.space_id, member_name), {})
    if overrides:
        lease_type = overrides.get("lease_type", lease_type)
        discount_reason = overrides.get("discount_reason", discount_reason)
        prepaid_through = overrides.get("prepaid_through", prepaid_through)
        if "monthly_rent_override" in overrides:
            monthly_rent = overrides["monthly_rent_override"]
        if overrides.get("is_split_override"):
            is_split = True

    # Warn about $0 rent (skip known cases)
    if monthly_rent == Decimal("0.00") and "monthly_rent_override" not in overrides:
        if row.actual_paid is not None and row.actual_paid == Decimal("0.00"):
            acc.warnings.append(f"$0 rent occupied space: {row.space_id} ({member_name})")

    acc.leases.append(
        _make_lease(
            acc.next_lease_pk(),
            content_type=["membership", "member"],
            object_id=member_pk,
            space_id=row.space_id,
            base_price=base,
            monthly_rent=monthly_rent,
            deposit=row.deposit,
            notes=row.notes,
            lease_type=lease_type,
            discount_reason=discount_reason,
            is_split=is_split,
            prepaid_through=prepaid_through,
        )
    )


def _make_lease(
    pk: int,
    *,
    content_type: list[str],
    object_id: int,
    space_id: str,
    base_price: Decimal,
    monthly_rent: Decimal,
    deposit: Decimal | None,
    notes: str,
    lease_type: str = "month_to_month",
    discount_reason: str = "",
    is_split: bool = False,
    prepaid_through: str | None = None,
) -> dict[str, Any]:
    """Build a lease dict."""
    return {
        "pk": pk,
        "content_type": content_type,
        "object_id": object_id,
        "space": space_id,
        "lease_type": lease_type,
        "base_price": decimal_to_str(base_price),
        "monthly_rent": decimal_to_str(monthly_rent),
        "start_date": "2025-01-01",
        "end_date": None,
        "committed_until": None,
        "deposit_required": decimal_to_str(deposit),
        "deposit_paid_date": None,
        "deposit_paid_amount": decimal_to_str(deposit),
        "discount_reason": discount_reason,
        "is_split": is_split,
        "prepaid_through": prepaid_through,
        "notes": notes,
    }


# ---------------------------------------------------------------------------
# Fixture assembly
# ---------------------------------------------------------------------------


def _build_fixture_json(acc: FixtureAccumulator) -> list[dict[str, Any]]:
    """Assemble the final fixture list from accumulated data."""
    fixture: list[dict[str, Any]] = []

    # 1. MembershipPlan
    fixture.append(
        {
            "model": "membership.membershipplan",
            "pk": STANDARD_PLAN_PK,
            "fields": {
                "name": "Standard",
                "monthly_price": STANDARD_PLAN_PRICE,
                "deposit_required": None,
                "notes": "Standard membership plan for all members.",
                "created_at": CREATED_AT,
            },
        }
    )

    # 2. Guilds
    for guild_name, guild_pk in sorted(acc.guilds.items(), key=lambda x: x[1]):
        fixture.append(
            {
                "model": "membership.guild",
                "pk": guild_pk,
                "fields": {
                    "name": guild_name,
                    "guild_lead": None,
                    "notes": "",
                    "created_at": CREATED_AT,
                },
            }
        )

    # 3. Members
    for member_name, member_pk in sorted(acc.members.items(), key=lambda x: x[1]):
        fixture.append(
            {
                "model": "membership.member",
                "pk": member_pk,
                "fields": {
                    "user": None,
                    "full_legal_name": member_name,
                    "preferred_name": acc.member_preferred.get(member_name, ""),
                    "email": "",
                    "phone": "",
                    "billing_name": "",
                    "emergency_contact_name": "",
                    "emergency_contact_phone": "",
                    "emergency_contact_relationship": "",
                    "membership_plan": STANDARD_PLAN_PK,
                    "status": "active",
                    "role": "standard",
                    "join_date": None,
                    "cancellation_date": None,
                    "committed_until": None,
                    "notes": "",
                    "created_at": CREATED_AT,
                },
            }
        )

    # 4. Spaces -- assign explicit PKs and build mapping
    for i, space in enumerate(acc.spaces, start=1):
        acc.space_id_to_pk[space["space_id"]] = i
        fields: dict[str, Any] = {
            "space_id": space["space_id"],
            "name": space["name"],
            "space_type": space["space_type"],
            "size_sqft": space["size_sqft"],
            "width": space["width"],
            "depth": space["depth"],
            "rate_per_sqft": space["rate_per_sqft"],
            "is_rentable": space["is_rentable"],
            "manual_price": space.get("manual_price"),
            "status": space["status"],
            "floorplan_ref": "",
            "notes": space.get("notes", ""),
            "created_at": CREATED_AT,
        }
        fixture.append({"model": "membership.space", "pk": i, "fields": fields})

    # 5. Leases -- resolve space FK to integer PK
    for lease in acc.leases:
        space_pk = acc.space_id_to_pk[lease["space"]]
        fixture.append(
            {
                "model": "membership.lease",
                "pk": lease["pk"],
                "fields": {
                    "content_type": lease["content_type"],
                    "object_id": lease["object_id"],
                    "space": space_pk,
                    "lease_type": lease["lease_type"],
                    "base_price": lease["base_price"],
                    "monthly_rent": lease["monthly_rent"],
                    "start_date": lease["start_date"],
                    "end_date": lease["end_date"],
                    "committed_until": lease["committed_until"],
                    "deposit_required": lease["deposit_required"],
                    "deposit_paid_date": lease["deposit_paid_date"],
                    "deposit_paid_amount": lease["deposit_paid_amount"],
                    "discount_reason": lease["discount_reason"],
                    "is_split": lease["is_split"],
                    "prepaid_through": lease["prepaid_through"],
                    "notes": lease["notes"],
                    "created_at": CREATED_AT,
                },
            }
        )

    return fixture


# ---------------------------------------------------------------------------
# Stderr report
# ---------------------------------------------------------------------------


def _print_report(acc: FixtureAccumulator) -> None:
    """Print the fixture generation report to stderr."""
    print("=" * 60, file=sys.stderr)
    print("FIXTURE GENERATION REPORT", file=sys.stderr)
    print("=" * 60, file=sys.stderr)

    print("\nModel counts:", file=sys.stderr)
    print("  MembershipPlan: 1", file=sys.stderr)
    print(f"  Guild:          {len(acc.guilds)}", file=sys.stderr)
    print(f"  Member:         {len(acc.members)}", file=sys.stderr)
    print(f"  Space:          {len(acc.spaces)}", file=sys.stderr)
    print(f"  Lease:          {len(acc.leases)}", file=sys.stderr)
    total = 1 + len(acc.guilds) + len(acc.members) + len(acc.spaces) + len(acc.leases)
    print(f"  TOTAL:          {total}", file=sys.stderr)

    print("\nGuilds created:", file=sys.stderr)
    for guild_name, guild_pk in sorted(acc.guilds.items(), key=lambda x: x[1]):
        print(f"  [{guild_pk}] {guild_name}", file=sys.stderr)

    print("\nMembers created:", file=sys.stderr)
    for member_name, member_pk in sorted(acc.members.items(), key=lambda x: x[1]):
        pref = acc.member_preferred.get(member_name, "")
        suffix = f' (preferred: "{pref}")' if pref else ""
        print(f"  [{member_pk:3d}] {member_name}{suffix}", file=sys.stderr)

    split_leases = [le for le in acc.leases if le["is_split"]]
    if split_leases:
        print(f"\nSplit spaces ({len(split_leases)}):", file=sys.stderr)
        for le in split_leases:
            print(f"  {le['space']}: rent={le['monthly_rent']}", file=sys.stderr)

    zero_rent = [le for le in acc.leases if le["monthly_rent"] in ("0.00", "0")]
    if zero_rent:
        print(f"\n$0 rent leases ({len(zero_rent)}):", file=sys.stderr)
        for le in zero_rent:
            ct = le["content_type"][1]
            print(f"  {le['space']}: {ct} #{le['object_id']}", file=sys.stderr)

    if acc.warnings:
        print(f"\nWarnings ({len(acc.warnings)}):", file=sys.stderr)
        for w in acc.warnings:
            print(f"  WARNING: {w}", file=sys.stderr)

    print(f"\n{'=' * 60}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Main fixture generation
# ---------------------------------------------------------------------------


def generate_fixture(csv_path: str) -> str:
    """Read CSV, classify rows, generate Django fixture JSON."""
    rows = read_csv_rows(csv_path)
    acc = FixtureAccumulator()

    for raw_row in rows:
        row = parse_row(raw_row)

        if _handle_non_tenant_row(row, acc):
            continue
        if _handle_guild_row(row, acc):
            continue
        _handle_tenant_row(row, acc)

    fixture = _build_fixture_json(acc)
    _print_report(acc)
    return json.dumps(fixture, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <csv_path>", file=sys.stderr)
        sys.exit(1)

    csv_path = sys.argv[1]
    output = generate_fixture(csv_path)
    print(output)
