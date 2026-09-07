"""Turning what Excel actually gives you into what the domain model needs.

This is where the importer meets reality. A spreadsheet cell that a human sees
as ``07:00`` can arrive as a ``datetime.time``, a ``datetime.datetime`` on an
arbitrary date, the float ``0.2916666...``, or the string ``"7:00 AM"`` — and a
scene id typed as ``17`` arrives as the float ``17.0``, which stringifies to
``"17.0"`` and breaks every foreign key in the workbook.

Every function here is pure and raises :class:`CoercionError` on failure, which
the parser turns into an E003 or E004 with the sheet, row and column attached.
Nothing here knows about sheets or rows.
"""

from __future__ import annotations

import math
import re
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dateutil import parser as dateutil_parser

from pri.importer.spec import ColumnSpec, FieldKind

__all__ = [
    "CoercionError",
    "coerce_cell",
    "excel_serial_to_date",
    "excel_serial_to_time",
    "normalise_id",
    "normalise_text",
    "prefers_day_first",
    "to_bool",
    "to_date",
    "to_decimal",
    "to_time",
]


class CoercionError(ValueError):
    """A cell could not be read as its declared type.

    Inputs:
        message:   What went wrong, in a sentence.
        hint:      What the user should do about it.
        ambiguous: ``True`` when a value *was* read but only by making an
                   assumption the user should be told about (W011).
    """

    def __init__(self, message: str, hint: str) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint


#: Excel's day zero. 1899-12-30 rather than 1900-01-01, because Excel believes
#: 1900 was a leap year and everybody has agreed to keep pretending.
_EXCEL_EPOCH = date(1899, 12, 30)

#: Whitespace Excel and Word insert that ``str.strip()`` does not remove:
#: no-break space, narrow no-break space, figure space, zero-width space.
_INVISIBLE = "   ​﻿"  # noqa: RUF001 - the lookalikes are the point

#: Everything that is not a digit, a sign, a dot or a comma. Currency symbols,
#: spaces, stray letters.
_NON_NUMERIC = re.compile(r"[^0-9,.\-+]")

_TRUE_WORDS = frozenset({"true", "yes", "y", "1", "t", "x", "✓"})
_FALSE_WORDS = frozenset({"false", "no", "n", "0", "f", ""})

#: IANA prefixes for countries that write dates month-first. Everywhere else in
#: the world is day-first, so the list is short and the default is day-first.
#:
#: This is a heuristic, and it is only ever consulted for a value that is
#: genuinely ambiguous (both halves ≤ 12). Whenever it fires, the parser emits
#: W011 naming the assumption, so a user who shoots in Manila with a US-format
#: sheet is told what PRI decided rather than silently getting March instead of
#: the fifth of the month.
_MONTH_FIRST_ZONES = (
    "America/New_York",
    "America/Chicago",
    "America/Denver",
    "America/Los_Angeles",
    "America/Phoenix",
    "America/Anchorage",
    "America/Detroit",
    "America/Indiana",
    "America/Kentucky",
    "America/Boise",
    "America/Juneau",
    "America/Sitka",
    "America/Nome",
    "America/Adak",
    "America/Honolulu",
    "Pacific/Honolulu",
    "Asia/Manila",
    "US/",
)


def prefers_day_first(timezone_name: str | None) -> bool:
    """Whether an ambiguous ``05/03`` should read as the fifth of March.

    Inputs:
        timezone_name: The production's IANA timezone, if it parsed.

    Outputs:
        ``True`` for day-first (most of the world), ``False`` for month-first.

    Failure modes:
        Does not raise. An unknown or missing timezone falls back to day-first,
        because that is the majority convention and the caller warns either way.
    """
    if not timezone_name:
        return True
    return not any(timezone_name.startswith(prefix) for prefix in _MONTH_FIRST_ZONES)


# ---------------------------------------------------------------------------
# Text
# ---------------------------------------------------------------------------


def normalise_text(value: Any) -> str | None:
    """Strip a cell to a clean string, or ``None`` if it is empty.

    Both ``""`` and ``None`` normalise to ``None`` — Excel is inconsistent about
    which one a cleared cell produces, and the rest of the module should not
    have to care.
    """
    if value is None:
        return None
    text = str(value)
    for character in _INVISIBLE:
        text = text.replace(character, " ")
    text = text.strip()
    return text or None


def normalise_id(value: Any) -> str | None:
    """Read an identifier, defeating Excel's habit of making it a number.

    A user types ``17`` into a scene id column. openpyxl hands back the int
    ``17`` or, if the column was ever formatted as a number, the float ``17.0``.
    ``str()`` on the latter gives ``"17.0"``, which matches nothing.

    Inputs:
        value: Whatever the cell contained.

    Outputs:
        The id as text, or ``None`` when the cell is empty.

    Failure modes:
        Does not raise. A float with a real fractional part keeps it — ``17.5``
        stays ``"17.5"`` — because silently truncating an id is worse than
        letting the reference check fail loudly.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if math.isfinite(value) and value == int(value):
            return str(int(value))
        return normalise_text(repr(value))
    return normalise_text(value)


def split_list(value: Any) -> tuple[str, ...]:
    """Split a semicolon-separated cell into ids, dropping blanks.

    Commas are accepted too. A 1st AD typing a list into a spreadsheet will use
    whichever separator their muscle memory offers, and rejecting a comma over a
    semicolon would be pedantry with no upside.
    """
    text = normalise_id(value)
    if text is None:
        return ()
    parts = re.split(r"[;,]", text)
    return tuple(item for item in (normalise_text(p) for p in parts) if item)


# ---------------------------------------------------------------------------
# Numbers
# ---------------------------------------------------------------------------


def to_int(value: Any) -> int | None:
    """Read a whole number.

    Failure modes:
        Raises :class:`CoercionError` for text that is not a number, or for a
        float with a fractional part — ``estimated_minutes = 90.5`` is a typo,
        not a request to round.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        raise CoercionError(
            "expected a whole number but found TRUE or FALSE",
            "Type a number of minutes, for example 90.",
        )
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value) or value != int(value):
            raise CoercionError(
                f"{value} is not a whole number",
                "Round it to a whole number of minutes.",
            )
        return int(value)
    text = normalise_text(value)
    if text is None:
        return None
    cleaned = _NON_NUMERIC.sub("", text).replace(",", "")
    try:
        return int(Decimal(cleaned))
    except (InvalidOperation, ValueError) as exc:
        raise CoercionError(
            f"{text!r} is not a whole number",
            "Type a plain number with no letters or symbols, for example 90.",
        ) from exc


def to_decimal(value: Any) -> Decimal | None:
    """Read money, exactly, from whatever a spreadsheet made of it.

    Handles ``"₹1,200.50"``, ``"$ 1200.5"``, ``"1 200,50"`` and a bare float.
    Never routes through ``float`` — a rate that arrives as ``1200.499999`` and
    is later multiplied by 34 crew is a bug nobody finds until the budget
    reconciliation.

    Thousands-versus-decimal separators are resolved by position: whichever of
    ``.`` or ``,`` appears **last** is treated as the decimal point, which is
    correct for both ``1,200.50`` and ``1.200,50``. A lone comma followed by
    exactly three digits is read as a thousands separator.

    Failure modes:
        Raises :class:`CoercionError` if nothing numeric survives cleaning.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        raise CoercionError(
            "expected an amount but found TRUE or FALSE",
            "Type an amount, for example 1200.00, or leave the cell blank for zero.",
        )
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        # str() of a float gives the shortest round-tripping representation,
        # which is what a human typed. Decimal(float) would give the full
        # binary expansion.
        return Decimal(str(value))

    text = normalise_text(value)
    if text is None:
        return None

    cleaned = _NON_NUMERIC.sub("", text)
    if not cleaned or cleaned in {"-", "+"}:
        raise CoercionError(
            f"{text!r} is not an amount",
            "Type a number, for example 1200.00, or leave the cell blank for zero.",
        )

    cleaned = _resolve_separators(cleaned)

    try:
        return Decimal(cleaned)
    except InvalidOperation as exc:
        raise CoercionError(
            f"{text!r} is not an amount",
            "Type a number, for example 1200.00, or leave the cell blank for zero.",
        ) from exc


def _resolve_separators(cleaned: str) -> str:
    """Decide which of ``.`` and ``,`` is the decimal point.

    Three cases, in the order they are distinguishable:

    Both present — whichever comes **last** is the decimal point. Correct for
    ``1,200.50`` and for ``1.200,50``.

    Only commas — thousands separators if every group after the first is
    exactly three digits (``1,200`` and ``1,234,567``), otherwise a decimal
    point (``1200,50``). Getting this backwards turns £1,200 into £1.20, which
    is the kind of error that reaches a budget before anyone notices.

    Only dots — the decimal point, as written.
    """
    last_dot = cleaned.rfind(".")
    last_comma = cleaned.rfind(",")

    if last_dot >= 0 and last_comma >= 0:
        if last_comma > last_dot:
            return cleaned.replace(".", "").replace(",", ".")
        return cleaned.replace(",", "")

    if last_comma >= 0:
        groups = cleaned.lstrip("+-").split(",")
        if len(groups) > 1 and all(len(g) == 3 and g.isdigit() for g in groups[1:]):
            return cleaned.replace(",", "")
        return cleaned.replace(",", ".")

    return cleaned


def to_bool(value: Any, default: bool | None = None) -> bool | None:
    """Read a yes-or-no cell in any of the spellings people actually use.

    ``TRUE`` / ``True`` / ``true`` / ``1`` / ``YES`` / ``Y`` / ``T`` / ``X`` /
    a tick are all true; the corresponding negatives are false; a blank cell
    takes ``default``.

    Failure modes:
        Raises :class:`CoercionError` for a word that is neither.
    """
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float) and not isinstance(value, bool):
        return bool(value)
    text = normalise_text(value)
    if text is None:
        return default
    lowered = text.lower()
    if lowered in _TRUE_WORDS:
        return True
    if lowered in _FALSE_WORDS:
        return False
    raise CoercionError(
        f"{text!r} is not TRUE or FALSE",
        "Type TRUE or FALSE, or leave the cell blank.",
    )


# ---------------------------------------------------------------------------
# Dates and times
# ---------------------------------------------------------------------------


def excel_serial_to_date(serial: float) -> date:
    """Convert an Excel date serial to a calendar date."""
    return _EXCEL_EPOCH + timedelta(days=int(serial))


def excel_serial_to_time(serial: float) -> time:
    """Convert an Excel time serial — a fraction of a day — to a clock time.

    ``0.2916666...`` is 07:00. Rounded to the nearest minute, because the
    fraction is a binary approximation of a time a human typed and 06:59:59.99
    is not a time anybody meant.
    """
    fraction = serial - math.floor(serial)
    total_minutes = round(fraction * 24 * 60)
    total_minutes %= 24 * 60
    return time(hour=total_minutes // 60, minute=total_minutes % 60)


def to_date(value: Any, *, day_first: bool = True) -> tuple[date | None, bool]:
    """Read a calendar date from a datetime, a date, a serial or a string.

    Inputs:
        value:     Whatever the cell contained.
        day_first: How to read an ambiguous ``05/03``.

    Outputs:
        ``(date, ambiguous)``. ``ambiguous`` is ``True`` when the value was only
        readable by applying ``day_first`` — both halves were 12 or less — so
        the caller can raise W011 and name the assumption.

    Failure modes:
        Raises :class:`CoercionError` for anything unparseable.
    """
    if value is None:
        return None, False
    if isinstance(value, datetime):
        return value.date(), False
    if isinstance(value, date):
        return value, False
    if isinstance(value, int | float) and not isinstance(value, bool):
        return excel_serial_to_date(float(value)), False

    text = normalise_text(value)
    if text is None:
        return None, False

    # ISO is unambiguous and by far the most common; try it before dateutil so
    # a well-formed file never touches the heuristic path.
    iso = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", text)
    if iso:
        try:
            return date(int(iso[1]), int(iso[2]), int(iso[3])), False
        except ValueError as exc:
            raise CoercionError(
                f"{text!r} is not a real date",
                "Check the day and month — for example 2026-11-02.",
            ) from exc

    slashed = re.fullmatch(r"(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2,4})", text)
    ambiguous = slashed is not None and int(slashed[1]) <= 12 and int(slashed[2]) <= 12

    try:
        parsed = dateutil_parser.parse(text, dayfirst=day_first, yearfirst=False)
    except (ValueError, OverflowError) as exc:
        raise CoercionError(
            f"{text!r} is not a date",
            "Write dates as YYYY-MM-DD, for example 2026-11-02.",
        ) from exc
    return parsed.date(), ambiguous


def to_time(value: Any) -> time | None:
    """Read a clock time from a time, a datetime, a serial float or a string.

    Failure modes:
        Raises :class:`CoercionError` for anything unparseable.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.time().replace(second=0, microsecond=0)
    if isinstance(value, time):
        return value.replace(second=0, microsecond=0)
    if isinstance(value, int | float) and not isinstance(value, bool):
        return excel_serial_to_time(float(value))

    text = normalise_text(value)
    if text is None:
        return None
    try:
        parsed = dateutil_parser.parse(text)
    except (ValueError, OverflowError) as exc:
        raise CoercionError(
            f"{text!r} is not a time",
            "Write times as HH:MM on a 24-hour clock, for example 07:00 or 19:30.",
        ) from exc
    return parsed.time().replace(second=0, microsecond=0)


def to_timezone(value: Any) -> str | None:
    """Validate an IANA timezone name.

    Failure modes:
        Raises :class:`CoercionError` for a name ``zoneinfo`` does not know —
        which includes ``IST``, ``EST`` and every other abbreviation, because
        they are ambiguous across countries.
    """
    text = normalise_text(value)
    if text is None:
        return None
    try:
        ZoneInfo(text)
    except (ZoneInfoNotFoundError, ValueError, KeyError) as exc:
        raise CoercionError(
            f"{text!r} is not a timezone PRI recognises",
            "Use a full IANA name such as Asia/Kolkata, Europe/London or "
            "America/Los_Angeles. Short forms like IST or EST are ambiguous.",
        ) from exc
    return text


# ---------------------------------------------------------------------------
# The dispatcher
# ---------------------------------------------------------------------------


def coerce_cell(
    value: Any,
    column: ColumnSpec,
    *,
    day_first: bool = True,
) -> tuple[Any, bool]:
    """Read one cell as its declared type.

    Inputs:
        value:     Raw cell value from openpyxl or csv.
        column:    The column's spec, which carries the kind, enum and default.
        day_first: How to read an ambiguous slashed date.

    Outputs:
        ``(coerced value, ambiguous)``. ``None`` means the cell was empty and
        the column has no default.

    Failure modes:
        Raises :class:`CoercionError`, which the parser converts into E003 (bad
        type) or E004 (outside the enum) with the location attached.
    """
    kind = column.kind

    if kind is FieldKind.STRING:
        text = normalise_id(value)
        return (text if text is not None else column.default), False

    if kind is FieldKind.INT:
        return to_int(value), False

    if kind is FieldKind.DECIMAL:
        parsed = to_decimal(value)
        if parsed is None and column.default is not None:
            return Decimal(str(column.default)), False
        return parsed, False

    if kind is FieldKind.DATE:
        return to_date(value, day_first=day_first)

    if kind is FieldKind.TIME:
        return to_time(value), False

    if kind is FieldKind.BOOL:
        default = column.default if isinstance(column.default, bool) else None
        return to_bool(value, default), False

    if kind is FieldKind.TIMEZONE:
        return to_timezone(value), False

    if kind is FieldKind.ENUM:
        text = normalise_text(value)
        if text is None:
            return column.default, False
        upper = text.upper()
        permitted = column.enum or ()
        if upper not in permitted:
            raise CoercionError(
                f"{text!r} is not one of {', '.join(permitted)}",
                f"Pick one of: {', '.join(permitted)}. The template has a dropdown on this column.",
            )
        return upper, False

    if kind is FieldKind.ID_LIST:
        return split_list(value), False

    if kind is FieldKind.DATE_LIST:
        collected: list[date] = []
        any_ambiguous = False
        for item in split_list(value):
            parsed_date, was_ambiguous = to_date(item, day_first=day_first)
            any_ambiguous = any_ambiguous or was_ambiguous
            if parsed_date is not None:
                collected.append(parsed_date)
        return tuple(collected), any_ambiguous

    raise CoercionError(  # pragma: no cover - exhaustive over FieldKind
        f"unsupported column kind {kind}",
        "This is a bug in PRI, not in your file.",
    )


def utc_now() -> datetime:
    """Timezone-aware now, for staging timestamps."""
    return datetime.now(UTC)
