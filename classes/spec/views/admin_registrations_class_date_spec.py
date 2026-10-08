"""The admin Registrations table's Class Date column, beside the Class title."""

from __future__ import annotations

import re
from datetime import datetime
from zoneinfo import ZoneInfo

from django.urls import reverse

PACIFIC = ZoneInfo("America/Los_Angeles")


def _noon(year: int, month: int, day: int) -> datetime:
    # Midday local, so no timezone conversion can move the printed day.
    return datetime(year, month, day, 12, tzinfo=PACIFIC)


def _cells(content: bytes, email: str) -> list[str]:
    """The text of each cell in the row that carries ``email``."""
    html = content.decode()
    row = next(r for r in re.findall(r"<tr>(.*?)</tr>", html, re.S) if email in r)
    return [re.sub(r"<[^>]+>", "", c).strip() for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]


def describe_class_date_column():
    def it_heads_the_column_right_after_class(admin_user, client, db):
        from classes.factories import RegistrationFactory

        client.force_login(admin_user)
        RegistrationFactory()
        html = client.get(reverse("classes:admin_registrations")).content.decode()
        headers = [re.sub(r"<[^>]+>", "", h).strip() for h in re.findall(r"<th[^>]*>(.*?)</th>", html, re.S)]
        # Each header carries a sort glyph after its label, so match on the label.
        class_at = next(i for i, h in enumerate(headers) if h.startswith("Class") and not h.startswith("Class Date"))
        assert headers[class_at + 1].startswith("Class Date")

    def it_shows_a_one_session_class_date_beside_its_title(admin_user, client, db):
        from classes.factories import ClassSessionFactory, RegistrationFactory

        client.force_login(admin_user)
        session = ClassSessionFactory(class_offering__title="Lost Wax Casting", starts_at=_noon(2026, 11, 14))
        RegistrationFactory(class_offering=session.class_offering, email="one@example.com")
        cells = _cells(client.get(reverse("classes:admin_registrations")).content, "one@example.com")
        assert cells[cells.index("Lost Wax Casting") + 1] == "Nov 14, 2026"

    def it_shows_the_span_of_a_multi_session_class(admin_user, client, db):
        from classes.factories import ClassSessionFactory, RegistrationFactory

        client.force_login(admin_user)
        first = ClassSessionFactory(starts_at=_noon(2026, 11, 14))
        ClassSessionFactory(class_offering=first.class_offering, starts_at=_noon(2026, 11, 21))
        RegistrationFactory(class_offering=first.class_offering, email="span@example.com")
        cells = _cells(client.get(reverse("classes:admin_registrations")).content, "span@example.com")
        assert "Nov 14 – Nov 21, 2026" in cells

    def it_shows_a_dash_for_a_class_with_no_sessions(admin_user, client, db):
        from classes.factories import RegistrationFactory

        client.force_login(admin_user)
        reg = RegistrationFactory(class_offering__title="Undated", email="none@example.com")
        cells = _cells(client.get(reverse("classes:admin_registrations")).content, "none@example.com")
        assert cells[cells.index(reg.class_offering.title) + 1] == "—"

    def it_sorts_by_class_date(admin_user, client, db):
        from classes.factories import ClassSessionFactory, RegistrationFactory

        client.force_login(admin_user)
        later = ClassSessionFactory(starts_at=_noon(2026, 12, 5))
        sooner = ClassSessionFactory(starts_at=_noon(2026, 11, 2))
        RegistrationFactory(class_offering=later.class_offering, email="later@example.com")
        RegistrationFactory(class_offering=sooner.class_offering, email="sooner@example.com")
        html = client.get(reverse("classes:admin_registrations"), {"sort": "class_first_session", "dir": "asc"})
        body = html.content.decode()
        assert body.index("sooner@example.com") < body.index("later@example.com")
