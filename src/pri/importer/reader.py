"""Getting rows out of a file, before anything is validated.

Three input shapes reach this module — an .xlsx/.xlsm workbook, a .zip of CSVs
named after the sheets, and a lone .csv — and all three leave it as the same
:class:`RawWorkbook`: sheets of rows, each row keyed by header text and carrying
the row number **exactly as Excel displays it**.

Nothing here validates. A cell that says ``"banana"`` in an integer column
arrives at the parser as the string ``"banana"`` with its row number attached,
and the parser decides what to call it.

Two facts openpyxl's read-only mode cannot give us are read from the sheet XML
directly: which ranges are merged, and which rows and sheets are hidden. The
alternative is a full non-read-only load, which for a 10 MB workbook costs
several hundred megabytes of DOM to learn two things.
"""

from __future__ import annotations

import csv
import io
import re
import zipfile
from dataclasses import dataclass, field
from typing import Any

from defusedxml.ElementTree import fromstring as safe_fromstring
from openpyxl import load_workbook

from pri.importer.limits import FileKind, LimitExceeded, safe_zip_entries

__all__ = [
    "RawRow",
    "RawSheet",
    "RawWorkbook",
    "read_workbook",
]

_SPREADSHEET_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_REL_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_PACKAGE_REL_NS = "{http://schemas.openxmlformats.org/package/2006/relationships}"

_CELL_REF = re.compile(r"([A-Z]+)(\d+)")


@dataclass(frozen=True, slots=True)
class RawRow:
    """One data row, before any coercion.

    Inputs:
        excel_row: 1-based row number as Excel shows it, header included. A
                   user reading an error scrolls to this number.
        values:    Cell values keyed by header text.
        hidden:    Whether the row was hidden in the source.
    """

    excel_row: int
    values: dict[str, Any]
    hidden: bool = False

    def get(self, column: str) -> Any:
        return self.values.get(column)

    @property
    def is_blank(self) -> bool:
        """Whether every cell is empty — a spacer row, not data."""
        return all(
            v is None or (isinstance(v, str) and not v.strip()) for v in self.values.values()
        )


@dataclass(slots=True)
class RawSheet:
    """One sheet's rows plus the facts the parser needs to warn about.

    Inputs:
        name:             Sheet name as found in the file.
        headers:          Header texts in column order, stripped.
        rows:             Data rows, in file order.
        unknown_columns:  Headers PRI does not know (W006).
        hidden:           The sheet itself was hidden (W013).
        had_hidden_rows:  At least one row was hidden (W013).
        merged_ranges:    Count of merged ranges expanded (W012).
        formula_columns:  Columns whose cells were formulas openpyxl could not
                          evaluate, and which therefore read as blank (W010).
    """

    name: str
    headers: list[str]
    rows: list[RawRow] = field(default_factory=list)
    unknown_columns: list[str] = field(default_factory=list)
    hidden: bool = False
    had_hidden_rows: bool = False
    merged_ranges: int = 0
    formula_columns: list[str] = field(default_factory=list)


@dataclass(slots=True)
class RawWorkbook:
    """Every sheet PRI could read, plus how the file arrived."""

    kind: FileKind
    sheets: dict[str, RawSheet] = field(default_factory=dict)
    extra_sheet_names: list[str] = field(default_factory=list)

    def sheet(self, name: str) -> RawSheet | None:
        return self.sheets.get(name)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def read_workbook(data: bytes, kind: FileKind, known_sheets: frozenset[str]) -> RawWorkbook:
    """Read an upload into sheets of rows.

    Inputs:
        data:         The upload.
        kind:         Result of :func:`~pri.importer.limits.detect_kind`.
        known_sheets: Sheet names PRI understands. Anything else is recorded in
                      ``extra_sheet_names`` and otherwise ignored — a user's own
                      "budget" tab must not stop their import.

    Outputs:
        A :class:`RawWorkbook`.

    Failure modes:
        Raises :class:`~pri.importer.limits.LimitExceeded` with E016 for a lone
        CSV, or for an archive or workbook that cannot be opened at all.
    """
    if kind is FileKind.XLSX:
        return _read_xlsx(data, known_sheets)
    if kind is FileKind.CSV_ZIP:
        return _read_csv_zip(data, known_sheets)
    raise LimitExceeded(
        "E016",
        "A single CSV file cannot describe a production.",
        "PRI needs the production, scenes, people, locations, equipment and "
        "schedule sheets. Upload the .xlsx template, or a .zip containing one "
        "CSV per sheet named after it — scenes.csv, schedule.csv and so on.",
    )


# ---------------------------------------------------------------------------
# xlsx
# ---------------------------------------------------------------------------


def _read_xlsx(data: bytes, known_sheets: frozenset[str]) -> RawWorkbook:
    metadata = _sheet_metadata(data)

    try:
        workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except (zipfile.BadZipFile, KeyError, ValueError) as exc:
        raise LimitExceeded(
            "E016",
            "The workbook could not be opened.",
            "Open it in Excel, use File → Save As → Excel Workbook (.xlsx), and "
            "upload the new file.",
        ) from exc

    result = RawWorkbook(kind=FileKind.XLSX)
    try:
        for worksheet in workbook.worksheets:
            name = str(worksheet.title).strip()
            if name not in known_sheets:
                result.extra_sheet_names.append(name)
                continue
            meta = metadata.get(name, _SheetMeta())
            result.sheets[name] = _read_worksheet(worksheet, name, meta)
    finally:
        workbook.close()

    return result


def _read_worksheet(worksheet: Any, name: str, meta: _SheetMeta) -> RawSheet:
    """Pull one worksheet into rows, expanding merged cells as we go."""
    rows_iter = worksheet.iter_rows(values_only=True)
    try:
        header_values = next(rows_iter)
    except StopIteration:
        return RawSheet(name=name, headers=[], hidden=meta.hidden)

    headers = [_header_text(value) for value in header_values]
    sheet = RawSheet(
        name=name,
        headers=[h for h in headers if h],
        hidden=meta.hidden,
        merged_ranges=len(meta.merged),
    )

    # A merged range holds its value only in the top-left cell; every other
    # cell in the range reads as None. Fill them so a merged "Sep 10" spanning
    # three schedule rows is read as three rows all dated Sep 10, which is what
    # the person who merged them meant.
    fill: dict[tuple[int, int], Any] = {}

    for excel_row, values in enumerate(rows_iter, start=2):
        record: dict[str, Any] = {}
        for index, header in enumerate(headers):
            if not header:
                continue
            value = values[index] if index < len(values) else None
            if value is None:
                value = fill.get((excel_row, index))
            record[header] = value

        for top, left, bottom, right in meta.merged:
            if top == excel_row and left < len(headers):
                source = record.get(headers[left])
                if source is None:
                    continue
                for r in range(top, bottom + 1):
                    for c in range(left, right + 1):
                        if (r, c) != (top, left):
                            fill[(r, c)] = source

        hidden_row = excel_row in meta.hidden_rows
        sheet.had_hidden_rows = sheet.had_hidden_rows or hidden_row
        sheet.rows.append(RawRow(excel_row=excel_row, values=record, hidden=hidden_row))

    sheet.formula_columns = [
        headers[index]
        for index in sorted(meta.formula_columns)
        if index < len(headers) and headers[index]
    ]
    return sheet


def _header_text(value: Any) -> str:
    """Normalise a header cell. Empty headers become '' and are skipped."""
    if value is None:
        return ""
    return str(value).replace("\xa0", " ").strip()


# ---------------------------------------------------------------------------
# Sheet metadata, read from the XML
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _SheetMeta:
    """What the sheet XML knows that read-only openpyxl does not."""

    hidden: bool = False
    hidden_rows: set[int] = field(default_factory=set)
    merged: list[tuple[int, int, int, int]] = field(default_factory=list)
    formula_columns: set[int] = field(default_factory=set)


def _sheet_metadata(data: bytes) -> dict[str, _SheetMeta]:
    """Collect merged ranges, hidden rows and formula columns, per sheet.

    Parsed with ``defusedxml`` because this is untrusted XML: the standard
    library parser will happily expand a billion-laughs entity bomb.

    Failure modes:
        Returns whatever it managed to read. A file whose XML this cannot
        follow still imports — it just does not get W012 or W013, which are
        advisory. Losing a warning is a better outcome than refusing a valid
        workbook because its internals are laid out unusually.
    """
    result: dict[str, _SheetMeta] = {}
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = set(archive.namelist())
            if "xl/workbook.xml" not in names:
                return result

            book = safe_fromstring(archive.read("xl/workbook.xml"))
            rels = _relationships(archive, names)

            for element in book.iter(f"{_SPREADSHEET_NS}sheet"):
                title = (element.get("name") or "").strip()
                rel_id = element.get(f"{_REL_NS}id")
                target = rels.get(rel_id or "")
                meta = _SheetMeta(hidden=element.get("state") in ("hidden", "veryHidden"))
                if target and target in names:
                    _read_sheet_xml(archive.read(target), meta)
                result[title] = meta
    except (zipfile.BadZipFile, KeyError, ValueError, SyntaxError):
        return result
    return result


def _relationships(archive: zipfile.ZipFile, names: set[str]) -> dict[str, str]:
    """Map each sheet's relationship id to its part name inside the archive."""
    rels: dict[str, str] = {}
    if "xl/_rels/workbook.xml.rels" not in names:
        return rels
    root = safe_fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    for element in root.iter(f"{_PACKAGE_REL_NS}Relationship"):
        rel_id = element.get("Id")
        target = element.get("Target") or ""
        if not rel_id or not target:
            continue
        normalised = target.lstrip("/")
        if not normalised.startswith("xl/"):
            normalised = "xl/" + normalised
        rels[rel_id] = normalised
    return rels


def _read_sheet_xml(payload: bytes, meta: _SheetMeta) -> None:
    """Extract hidden rows, merged ranges and formula columns from one sheet."""
    root = safe_fromstring(payload)

    for row in root.iter(f"{_SPREADSHEET_NS}row"):
        if row.get("hidden") in ("1", "true"):
            number = row.get("r")
            if number and number.isdigit():
                meta.hidden_rows.add(int(number))
        for cell in row:
            if cell.find(f"{_SPREADSHEET_NS}f") is None:
                continue
            reference = cell.get("r") or ""
            match = _CELL_REF.match(reference)
            if match:
                meta.formula_columns.add(_column_index(match.group(1)))

    for merge in root.iter(f"{_SPREADSHEET_NS}mergeCell"):
        span = _parse_range(merge.get("ref") or "")
        if span:
            meta.merged.append(span)


def _column_index(letters: str) -> int:
    """``A`` → 0, ``B`` → 1, ``AA`` → 26."""
    index = 0
    for character in letters:
        index = index * 26 + (ord(character) - ord("A") + 1)
    return index - 1


def _parse_range(reference: str) -> tuple[int, int, int, int] | None:
    """``A2:C4`` → ``(top_row, left_col, bottom_row, right_col)``, zero-based cols."""
    parts = reference.split(":")
    if len(parts) != 2:
        return None
    start = _CELL_REF.match(parts[0])
    end = _CELL_REF.match(parts[1])
    if not start or not end:
        return None
    return (
        int(start.group(2)),
        _column_index(start.group(1)),
        int(end.group(2)),
        _column_index(end.group(1)),
    )


# ---------------------------------------------------------------------------
# csv
# ---------------------------------------------------------------------------

#: Tried in order. UTF-8-BOM first because Excel's "CSV UTF-8" export writes
#: one, and decoding it as plain UTF-8 leaves a zero-width character glued to
#: the first header — which then fails to match ``production_id`` for reasons
#: invisible on screen.
_CSV_ENCODINGS = ("utf-8-sig", "utf-8", "cp1252")


def _decode_csv(payload: bytes) -> str:
    """Decode CSV bytes, trying the encodings a spreadsheet actually produces."""
    for encoding in _CSV_ENCODINGS:
        try:
            return payload.decode(encoding)
        except UnicodeDecodeError:
            continue
    return payload.decode("utf-8", errors="replace")


def _read_csv_zip(data: bytes, known_sheets: frozenset[str]) -> RawWorkbook:
    result = RawWorkbook(kind=FileKind.CSV_ZIP)
    for entry in safe_zip_entries(data):
        stem = entry.name.replace("\\", "/").rsplit("/", 1)[-1]
        name = stem.rsplit(".", 1)[0].strip().lower()
        if name not in known_sheets:
            result.extra_sheet_names.append(stem)
            continue
        result.sheets[name] = _read_csv_sheet(name, entry.data)
    return result


def _read_csv_sheet(name: str, payload: bytes) -> RawSheet:
    text = _decode_csv(payload)
    reader = csv.reader(io.StringIO(text, newline=""))
    try:
        header_values = next(reader)
    except StopIteration:
        return RawSheet(name=name, headers=[])

    headers = [_header_text(value) for value in header_values]
    sheet = RawSheet(name=name, headers=[h for h in headers if h])

    for excel_row, values in enumerate(reader, start=2):
        record: dict[str, Any] = {}
        for index, header in enumerate(headers):
            if not header:
                continue
            raw = values[index] if index < len(values) else None
            record[header] = raw if raw not in ("", None) else None
        sheet.rows.append(RawRow(excel_row=excel_row, values=record))
    return sheet
