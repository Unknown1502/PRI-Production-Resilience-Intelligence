"""Typed import issues, and the codes they carry.

Every problem the importer finds is one of these records. A bare
"validation failed" is a failure of this module: the person reading it is a 1st
AD looking at a 400-row spreadsheet, and they need the sheet, the row number as
Excel displays it, the column, the offending value, and a sentence telling them
what to change.

``fix_hint`` is the field that does the work. It is written for somebody who
has never seen this codebase.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel

__all__ = [
    "BLOCKING_CODES",
    "ERROR_CATALOG",
    "WARNING_CODES",
    "ImportIssue",
    "Phase",
    "Severity",
    "error",
    "warning",
]


class Severity(StrEnum):
    """Whether an issue stops the import."""

    ERROR = "ERROR"
    WARNING = "WARNING"


class Phase(StrEnum):
    """Validation runs in ordered phases and short-circuits between them.

    A missing sheet makes every downstream check meaningless noise, so if a
    phase produces errors the later phases do not run at all. Reporting forty
    dangling-reference errors caused by one absent sheet helps nobody.
    """

    STRUCTURE = "STRUCTURE"
    IDENTITY = "IDENTITY"
    REFERENCE = "REFERENCE"
    SEMANTIC = "SEMANTIC"
    WARNINGS = "WARNINGS"


class ImportIssue(BaseModel, frozen=True):
    """One problem found in an uploaded workbook.

    Inputs:
        code:            ``E001`` to ``E018``, or ``W001`` to ``W013``.
        severity:        ERROR blocks the import; WARNING never does.
        sheet:           Sheet name, or ``None`` for a whole-file problem.
        row:             1-based row number **exactly as Excel displays it**,
                         header included. A user reading this will scroll to
                         that number; an off-by-one here wastes their time.
        column:          Column header, or ``None``.
        message:         What is wrong, in one sentence.
        offending_value: What was actually in the cell, stringified.
        fix_hint:        What to do about it. Written for a 1st AD.
    """

    code: str
    severity: Severity
    message: str
    fix_hint: str
    sheet: str | None = None
    row: int | None = None
    column: str | None = None
    offending_value: str | None = None

    @property
    def blocking(self) -> bool:
        return self.severity is Severity.ERROR

    def location(self) -> str:
        """``scenes row 12, column location_id`` — for log lines and copy-paste."""
        parts: list[str] = []
        if self.sheet:
            parts.append(self.sheet)
        if self.row is not None:
            parts.append(f"row {self.row}")
        if self.column:
            parts.append(f"column {self.column}")
        return ", ".join(parts) if parts else "workbook"

    def as_line(self) -> str:
        """One line, for the review screen's copy-all button."""
        return f"[{self.code}] {self.location()}: {self.message} — {self.fix_hint}"


#: Short titles, used to group the review screen and to keep messages honest
#: about what each code actually means.
ERROR_CATALOG: dict[str, str] = {
    # Phase 1 — structure
    "E001": "Missing required sheet",
    "E002": "Missing required column",
    "E003": "Value does not match the column's type",
    "E004": "Value is not one of the permitted options",
    "E016": "Unsupported file",
    "E017": "Sheet is too large",
    # Phase 2 — identity
    "E005": "Duplicate id",
    # Phase 3 — reference
    "E006": "Reference to something that does not exist",
    "E008": "Person used in the wrong role",
    "E014": "Invalid availability combination",
    # Phase 4 — semantic
    "E007": "Prerequisite cycle",
    "E009": "Scene scheduled more than once",
    "E010": "Schedule date outside the shoot window",
    "E011": "Wrap time is not after the call time",
    "E012": "Empty workbook or wrong number of production rows",
    "E013": "Production already exists",
    "E015": "That local time does not exist on that date",
    "E018": "Availability window ends before it starts",
    # Warnings
    "W001": "Scene never scheduled",
    "W002": "Person never used",
    "W003": "Equipment never used",
    "W004": "Shooting day with no scenes",
    "W005": "Reserve day already has scenes",
    "W006": "Unknown columns ignored",
    "W007": "Template version mismatch",
    "W008": "Overnight wrap inferred",
    "W009": "Ambiguous local time during a clock change",
    "W010": "Column looks like uncomputed formulas",
    "W011": "Ambiguous date format",
    "W012": "Merged cells expanded",
    "W013": "Hidden sheets or rows were read",
    "W014": "Day has scenes but is not marked SHOOT",
}

BLOCKING_CODES: frozenset[str] = frozenset(c for c in ERROR_CATALOG if c.startswith("E"))
WARNING_CODES: frozenset[str] = frozenset(c for c in ERROR_CATALOG if c.startswith("W"))


def error(
    code: str,
    message: str,
    fix_hint: str,
    *,
    sheet: str | None = None,
    row: int | None = None,
    column: str | None = None,
    value: Any = None,
) -> ImportIssue:
    """Build a blocking issue.

    Failure modes:
        Raises ``KeyError`` if ``code`` is not in :data:`ERROR_CATALOG` — a typo
        in a code is a bug in this module, and it should surface here rather
        than reaching a user as an unrecognised label.
    """
    if code not in BLOCKING_CODES:
        raise KeyError(f"{code!r} is not a known blocking error code")
    return ImportIssue(
        code=code,
        severity=Severity.ERROR,
        message=message,
        fix_hint=fix_hint,
        sheet=sheet,
        row=row,
        column=column,
        offending_value=_render(value),
    )


def warning(
    code: str,
    message: str,
    fix_hint: str,
    *,
    sheet: str | None = None,
    row: int | None = None,
    column: str | None = None,
    value: Any = None,
) -> ImportIssue:
    """Build a non-blocking issue.

    Failure modes:
        Raises ``KeyError`` for an unknown warning code.
    """
    if code not in WARNING_CODES:
        raise KeyError(f"{code!r} is not a known warning code")
    return ImportIssue(
        code=code,
        severity=Severity.WARNING,
        message=message,
        fix_hint=fix_hint,
        sheet=sheet,
        row=row,
        column=column,
        offending_value=_render(value),
    )


def _render(value: Any) -> str | None:
    """Stringify an offending value without letting it grow unbounded.

    A cell can contain a paragraph. The review screen shows this inline, and a
    2 KB "offending value" makes the table unreadable — and is exactly the sort
    of thing a hostile file would contain.
    """
    if value is None:
        return None
    text = str(value)
    return text if len(text) <= 120 else text[:117] + "..."
