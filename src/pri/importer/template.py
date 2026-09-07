"""Writing the workbook: the blank template, and a live production exported.

Both directions share :data:`~pri.importer.spec.WORKBOOK_SPEC` and share
:func:`_write_row`. A column is never formatted in two places, so the template a
user downloads and the export of their committed state are the same shape by
construction rather than by discipline.

The export is the inverse of the parser, and the round-trip test asserts it:
seed → export → parse → identical content digest.
"""

from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from io import BytesIO
from typing import TYPE_CHECKING, Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

from pri.importer.spec import (
    SHEET_AVAILABILITY,
    SHEET_EQUIPMENT,
    SHEET_LOCATIONS,
    SHEET_PEOPLE,
    SHEET_PRODUCTION,
    SHEET_SCENES,
    SHEET_SCHEDULE,
    TEMPLATE_VERSION,
    WORKBOOK_SPEC,
    ColumnSpec,
    FieldKind,
    SheetSpec,
)

if TYPE_CHECKING:
    from pri.domain.models import ProductionState

__all__ = [
    "EXAMPLE_MARKER",
    "build_template",
    "export_state",
    "guard_formula",
]

#: Written into the first cell of every example row. The parser drops any row
#: whose first cell starts with this, so a user who forgets to delete the
#: examples still gets a clean import rather than two phantom scenes.
EXAMPLE_MARKER = "EXAMPLE — delete this row"

#: Characters Excel and Google Sheets treat as the start of a formula. A cell
#: beginning with one of these, sourced from user data, is a CSV-injection
#: vector: the recipient opens the export and the "cell" executes.
_FORMULA_LEAD = ("=", "+", "-", "@", "\t", "\r")

_HEADER_FILL = PatternFill("solid", fgColor="1F2937")
_HEADER_FONT = Font(bold=True, color="F9FAFB", size=10)
_EXAMPLE_FILL = PatternFill("solid", fgColor="E5E7EB")
_EXAMPLE_FONT = Font(italic=True, color="6B7280", size=9)
_TITLE_FONT = Font(bold=True, size=13)
_SECTION_FONT = Font(bold=True, size=11)
_MUTED_FONT = Font(color="6B7280", size=9)

_NUMBER_FORMATS: dict[FieldKind, str] = {
    FieldKind.DATE: "YYYY-MM-DD",
    FieldKind.TIME: "HH:MM",
    FieldKind.DECIMAL: "0.00",
}


def _drop_default_sheet(workbook: Workbook) -> None:
    """Remove the empty sheet openpyxl creates, if it is still there."""
    default = workbook.active
    if default is not None:
        workbook.remove(default)


def guard_formula(text: str) -> str:
    """Neutralise a string that a spreadsheet would execute as a formula.

    Prefixes an apostrophe, which Excel strips on display and treats as "this
    is text". Applied to every string written to xlsx or csv, on both the
    template and the export path.

    Inputs:
        text: The value about to be written.

    Outputs:
        The value, prefixed if it began with ``=``, ``+``, ``-``, ``@``, a tab
        or a carriage return.
    """
    if text and text[0] in _FORMULA_LEAD:
        return "'" + text
    return text


# ---------------------------------------------------------------------------
# The one place a cell is written
# ---------------------------------------------------------------------------


def _cell_value(column: ColumnSpec, value: Any) -> Any:
    """Convert a domain value into what openpyxl should store.

    Dates, times and numbers are written as real typed cells so Excel formats
    and sorts them properly. Everything else becomes a guarded string.
    """
    if value is None:
        return None

    kind = column.kind

    if kind is FieldKind.DATE:
        return value if isinstance(value, date | datetime) else guard_formula(str(value))
    if kind is FieldKind.TIME:
        return value if isinstance(value, time) else guard_formula(str(value))
    if kind is FieldKind.DECIMAL:
        # Written as a float so the cell is numeric and formats as 0.00. The
        # parser reads it back through Decimal(str(...)) and quantises, so the
        # exact scale survives the round-trip; the float only ever lives in the
        # spreadsheet, never in a calculation.
        return float(value) if isinstance(value, Decimal | int | float) else value
    if kind is FieldKind.INT:
        return int(value)
    if kind is FieldKind.BOOL:
        return "TRUE" if value else "FALSE"
    if kind in (FieldKind.ID_LIST, FieldKind.DATE_LIST):
        items = value if isinstance(value, list | tuple) else [value]
        rendered = ";".join(
            item.isoformat() if isinstance(item, date | datetime) else str(item) for item in items
        )
        return guard_formula(rendered) if rendered else None

    return guard_formula(str(value))


def _write_row(
    worksheet: Any,
    row_index: int,
    sheet: SheetSpec,
    values: dict[str, Any],
    *,
    example: bool = False,
) -> None:
    """Write one data row, formatting every cell from its column spec.

    This is the only function in the module that touches a data cell. The
    template's example rows and a live export both come through here, which is
    what stops the two drifting apart.
    """
    for index, column in enumerate(sheet.columns, start=1):
        cell = worksheet.cell(row=row_index, column=index)
        cell.value = _cell_value(column, values.get(column.name))
        number_format = _NUMBER_FORMATS.get(column.kind)
        if number_format:
            cell.number_format = number_format
        if example:
            cell.fill = _EXAMPLE_FILL
            cell.font = _EXAMPLE_FONT


def _write_sheet_frame(workbook: Workbook, sheet: SheetSpec) -> Any:
    """Create a sheet with its header row, widths, freeze pane and dropdowns."""
    worksheet: Any = workbook.create_sheet(sheet.name)

    for index, column in enumerate(sheet.columns, start=1):
        cell = worksheet.cell(row=1, column=index, value=column.name)
        cell.font = _HEADER_FONT
        cell.fill = _HEADER_FILL
        cell.alignment = Alignment(vertical="center")
        worksheet.column_dimensions[get_column_letter(index)].width = column.width

    worksheet.row_dimensions[1].height = 20
    worksheet.freeze_panes = "A2"

    # Dropdowns for every ENUM column, applied down the whole usable column so
    # the validation is there when the user adds row 300.
    for index, column in enumerate(sheet.columns, start=1):
        if column.kind is not FieldKind.ENUM or not column.enum:
            continue
        letter = get_column_letter(index)
        validation = DataValidation(
            type="list",
            formula1='"' + ",".join(column.enum) + '"',
            allow_blank=not column.required,
            showDropDown=False,
        )
        validation.error = f"Pick one of: {', '.join(column.enum)}"
        validation.errorTitle = f"{column.name}"
        worksheet.add_data_validation(validation)
        validation.add(f"{letter}2:{letter}{max(sheet.max_rows, 2) + 1}")

    return worksheet


# ---------------------------------------------------------------------------
# The README sheet
# ---------------------------------------------------------------------------


def _write_readme(workbook: Workbook) -> None:
    """The first sheet a user sees: what each sheet is for, then every column."""
    worksheet: Any = workbook.create_sheet("README")
    worksheet.column_dimensions["A"].width = 22
    worksheet.column_dimensions["B"].width = 24
    worksheet.column_dimensions["C"].width = 26
    worksheet.column_dimensions["D"].width = 10
    worksheet.column_dimensions["E"].width = 72

    row = 1
    worksheet.cell(row=row, column=1, value="PRI — production import template").font = _TITLE_FONT
    row += 1
    worksheet.cell(
        row=row,
        column=1,
        value=f"Template version {TEMPLATE_VERSION}. Fill in the sheets, then upload the file.",
    ).font = _MUTED_FONT
    row += 2

    intro = [
        "Delete the grey EXAMPLE rows before you upload — or leave them, PRI skips them.",
        "Every time is local wall-clock time in the timezone you set on the production sheet.",
        "For an overnight, put the wrap earlier than the call and PRI works it out.",
        "PRI will import a schedule that already breaks your own rules, and then tell you which.",
        "Only the production, scenes, people, locations, equipment and schedule "
        "sheets are required.",
    ]
    for line in intro:
        worksheet.cell(row=row, column=1, value=line)
        row += 1
    row += 1

    worksheet.cell(row=row, column=1, value="The sheets").font = _SECTION_FONT
    row += 1
    for sheet in WORKBOOK_SPEC:
        worksheet.cell(
            row=row, column=1, value=f"{sheet.name}{'' if sheet.required else '  (optional)'}"
        ).font = Font(bold=True, size=10)
        cell = worksheet.cell(row=row, column=2, value=sheet.help)
        cell.alignment = Alignment(wrap_text=True, vertical="top")
        worksheet.merge_cells(start_row=row, start_column=2, end_row=row, end_column=5)
        worksheet.row_dimensions[row].height = 30
        row += 1
    row += 1

    worksheet.cell(row=row, column=1, value="Every column").font = _SECTION_FONT
    row += 1
    for index, heading in enumerate(("sheet", "column", "format", "required", "what it means"), 1):
        cell = worksheet.cell(row=row, column=index, value=heading)
        cell.font = _HEADER_FONT
        cell.fill = _HEADER_FILL
    row += 1

    for sheet in WORKBOOK_SPEC:
        for column in sheet.columns:
            worksheet.cell(row=row, column=1, value=sheet.name)
            worksheet.cell(row=row, column=2, value=column.name)
            worksheet.cell(row=row, column=3, value=column.format_hint)
            worksheet.cell(row=row, column=4, value="yes" if column.required else "")
            cell = worksheet.cell(row=row, column=5, value=column.help)
            cell.alignment = Alignment(wrap_text=True, vertical="top")
            row += 1

    worksheet.freeze_panes = "A2"


# ---------------------------------------------------------------------------
# build_template
# ---------------------------------------------------------------------------


def build_template() -> bytes:
    """Build the blank import template as .xlsx bytes.

    Driven entirely by :data:`WORKBOOK_SPEC`: a README sheet, then the seven
    sheets with bold frozen headers, spec widths, ENUM dropdowns, and two
    example rows forming a coherent mini-production whose ids reference each
    other correctly.

    Outputs:
        The workbook as bytes, ready to stream.
    """
    workbook = Workbook()
    _drop_default_sheet(workbook)

    _write_readme(workbook)

    for sheet in WORKBOOK_SPEC:
        worksheet = _write_sheet_frame(workbook, sheet)
        example_rows = 1 if sheet.exactly_one else 2
        for offset in range(example_rows):
            values = {column.name: _example_cell(column, offset) for column in sheet.columns}
            _write_row(worksheet, 2 + offset, sheet, values, example=True)

        # The marker goes in the first column of *every* example row, after the
        # rows are written. The parser drops any row carrying it, so a user who
        # forgets to delete them still gets a clean import — and marking only
        # the first row would let the second one through as real data.
        for offset in range(example_rows):
            note = worksheet.cell(row=2 + offset, column=1)
            note.value = f"{EXAMPLE_MARKER} — {note.value}"

    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _example_cell(column: ColumnSpec, offset: int) -> Any:
    """Turn a spec example string into a properly typed cell value.

    The examples are written as text in the spec because that is how a human
    reads them, but the template must contain real dates and real numbers — a
    template whose date column is text teaches the user the wrong habit and
    then fails their file.
    """
    from pri.importer.coercion import CoercionError, coerce_cell

    raw = column.examples[min(offset, len(column.examples) - 1)]
    if raw == "":
        return None
    try:
        value, _ = coerce_cell(raw, column, day_first=False)
    except CoercionError:  # pragma: no cover - a bad example is a spec bug
        return raw
    return value


# ---------------------------------------------------------------------------
# export_state — the exact inverse
# ---------------------------------------------------------------------------


def export_state(state: ProductionState, *, timezone_name: str | None = None) -> bytes:
    """Serialise a live ``ProductionState`` into the import workbook format.

    Inputs:
        state:         The state to export.
        timezone_name: IANA zone to declare on the production sheet. Inferred
                       from the schedule's own offsets when omitted.

    Outputs:
        The workbook as bytes.

    Failure modes:
        Raises ``ValueError`` if the state's location permit windows cannot be
        expressed as a single daily recurrence — the workbook format has no way
        to say "open 07:00 to 19:00 on weekdays and 09:00 to 14:00 on Sundays", and
        silently dropping half of it would be worse than refusing.
    """
    workbook = Workbook()
    _drop_default_sheet(workbook)

    zone = timezone_name or _infer_timezone(state)
    rows_by_sheet = {
        SHEET_PRODUCTION: _production_rows(state, zone),
        SHEET_SCENES: _scene_rows(state),
        SHEET_PEOPLE: _people_rows(state),
        SHEET_LOCATIONS: _location_rows(state),
        SHEET_EQUIPMENT: _equipment_rows(state),
        SHEET_AVAILABILITY: _availability_rows(state),
        SHEET_SCHEDULE: _schedule_rows(state),
    }

    _write_readme(workbook)
    for sheet in WORKBOOK_SPEC:
        worksheet = _write_sheet_frame(workbook, sheet)
        for offset, values in enumerate(rows_by_sheet[sheet.name]):
            _write_row(worksheet, 2 + offset, sheet, values)

    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _infer_timezone(state: ProductionState) -> str:
    """Best-effort IANA name for a state whose datetimes carry only an offset.

    A state built from the seeded fixture uses a fixed-offset tzinfo, which has
    no name. The export has to declare *something* the parser can localise
    against, and the offset is what actually matters for the arithmetic, so a
    zone with the right constant offset is a faithful choice.
    """
    for day in state.schedule.days:
        offset = day.call_time.utcoffset()
        if offset is None:
            continue
        known = {
            timedelta(hours=5, minutes=30): "Asia/Kolkata",
            timedelta(0): "UTC",
            timedelta(hours=-8): "Etc/GMT+8",
            timedelta(hours=-5): "Etc/GMT+5",
            timedelta(hours=1): "Etc/GMT-1",
        }
        if offset in known:
            return known[offset]
        total_minutes = int(offset.total_seconds() // 60)
        if total_minutes % 60 == 0:
            hours = total_minutes // 60
            return f"Etc/GMT{-hours:+d}"
        break
    return "UTC"


def _production_rows(state: ProductionState, zone: str) -> list[dict[str, Any]]:
    production = state.production
    return [
        {
            "production_id": production.id,
            "title": production.title,
            "currency": production.currency,
            "timezone": zone,
            "shoot_start": production.shoot_start,
            "shoot_end": production.shoot_end,
            "reserve_days": production.reserve_days,
            "template_version": TEMPLATE_VERSION,
        }
    ]


def _scene_rows(state: ProductionState) -> list[dict[str, Any]]:
    return [
        {
            "scene_id": scene.id,
            "number": scene.number,
            "slug": scene.slug,
            "description": scene.description,
            "int_ext": scene.int_ext,
            "time_of_day": scene.time_of_day,
            "estimated_minutes": scene.estimated_minutes,
            "location_id": scene.location_id,
            "cast_ids": scene.cast_ids,
            "crew_ids": scene.crew_ids,
            "equipment_ids": scene.equipment_ids,
            "vfx_plate": scene.vfx_plate,
            "prerequisite_scene_ids": scene.prerequisite_scene_ids,
        }
        for scene in state.scenes
    ]


def _people_rows(state: ProductionState) -> list[dict[str, Any]]:
    return [
        {
            "person_id": person.id,
            "name": person.name,
            "role": person.role,
            "character": person.character,
            "daily_rate": person.daily_rate,
        }
        for person in state.people
    ]


def _location_rows(state: ProductionState) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for location in state.locations:
        start, end = _daily_permit(location.permit_windows, location.id)
        rows.append(
            {
                "location_id": location.id,
                "name": location.name,
                "kind": location.kind,
                "day_rate": location.day_rate,
                "permit_start_time": start,
                "permit_end_time": end,
                "supports_int_ext": location.supports_int_ext,
                "supports_time_of_day": location.supports_time_of_day,
                "address": location.address,
                "parking_note": location.parking_note,
                "nearest_hospital": location.nearest_hospital,
            }
        )
    return rows


def _daily_permit(windows: tuple[Any, ...], location_id: str) -> tuple[time | None, time | None]:
    """Collapse per-day permit windows back to the daily recurrence that made them.

    Failure modes:
        Raises ``ValueError`` when the windows do not share one pair of local
        clock times — the workbook format cannot express that, and writing only
        the first pair would silently discard the rest.
    """
    if not windows:
        return None, None
    clock_pairs = {
        (window.start.timetz().replace(tzinfo=None), window.end.timetz().replace(tzinfo=None))
        for window in windows
    }
    if len(clock_pairs) != 1:
        raise ValueError(
            f"Location {location_id!r} has permit windows with more than one daily "
            f"pattern; the workbook format can only express a single recurring window."
        )
    start, end = next(iter(clock_pairs))
    return start, end


def _equipment_rows(state: ProductionState) -> list[dict[str, Any]]:
    return [
        {
            "equipment_id": item.id,
            "name": item.name,
            "kind": item.kind,
            "daily_rate": item.daily_rate,
        }
        for item in state.equipment
    ]


def _availability_rows(state: ProductionState) -> list[dict[str, Any]]:
    """Flatten every person and equipment window into availability rows.

    Location closures are not reconstructed: the domain model folds a one-off
    closure into the same ``permit_windows`` tuple as the daily recurrence, so
    an exported state cannot tell them apart. A production imported with CLOSED
    rows and then re-exported comes back with the closure already applied to the
    permit pattern — which is why ``_daily_permit`` raises rather than guessing.
    """
    rows: list[dict[str, Any]] = []
    for person in state.people:
        for window in person.unavailable_windows:
            rows.append(
                {
                    "subject_type": "PERSON",
                    "subject_id": person.id,
                    "window_kind": "UNAVAILABLE",
                    "from_date": window.start.date(),
                    "to_date": (window.end - timedelta(days=1)).date(),
                    "note": None,
                }
            )
    for item in state.equipment:
        for window in item.available_windows:
            rows.append(
                {
                    "subject_type": "EQUIPMENT",
                    "subject_id": item.id,
                    "window_kind": "AVAILABLE",
                    "from_date": window.start.date(),
                    "to_date": (window.end - timedelta(days=1)).date(),
                    "note": None,
                }
            )
    return rows


def _schedule_rows(state: ProductionState) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for day in sorted(state.schedule.days, key=lambda d: d.date):
        overnight = day.wrap_time.date() > day.call_time.date()
        rows.append(
            {
                "date": day.date,
                "call_time": day.call_time.timetz().replace(tzinfo=None),
                "wrap_time": day.wrap_time.timetz().replace(tzinfo=None),
                "wrap_next_day": overnight,
                "location_id": day.location_id,
                "scene_ids": day.scene_ids,
                "unit": day.unit,
                "day_kind": day.day_kind,
            }
        )
    return rows


# ---------------------------------------------------------------------------
# CSV export, sharing the same guard
# ---------------------------------------------------------------------------


_CSV_SAFE = re.compile(r"^[^=+\-@\t\r]")


def csv_cell(value: Any) -> str:
    """Render a value for a CSV cell, guarded against formula injection."""
    if value is None:
        return ""
    if isinstance(value, date | datetime):
        return value.isoformat()
    if isinstance(value, time):
        return value.strftime("%H:%M")
    return guard_formula(str(value))
