"""Turning an uploaded workbook into a ``ProductionState``, or into good errors.

Validation runs in five ordered phases and **short-circuits between them**:

    1 STRUCTURE   sheets, columns, types, enums, row caps
    2 IDENTITY    duplicate primary keys
    3 REFERENCE   foreign keys and role mismatches
    4 SEMANTIC    cycles, scheduling, dates, timezone arithmetic
    5 WARNINGS    advisory only, never blocking

If a phase produces errors the later phases do not run. A missing ``locations``
sheet would otherwise generate one dangling-reference error per scene, and
forty errors caused by one omission is worse than one error, not better.

No LLM is involved. Import is deterministic parsing.

**Merge is not supported.** If ``production_id`` already exists in the database
that is E013, and the user changes the id or deletes the existing production.
Reconciling two versions of a board is a different product with a different UI,
and guessing at it would silently lose somebody's schedule.
"""

from __future__ import annotations

import time as time_module
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

import networkx as nx
from pydantic import BaseModel, ValidationError

from pri.domain.models import (
    Equipment,
    Location,
    Person,
    Production,
    ProductionState,
    Scene,
    Schedule,
    ShootingDay,
    TimeWindow,
)
from pri.importer.coercion import CoercionError, coerce_cell, prefers_day_first
from pri.importer.errors import ImportIssue, error, warning
from pri.importer.limits import (
    MAX_PARSE_SECONDS,
    LimitExceeded,
    check_size,
    detect_kind,
    sanitise_filename,
)
from pri.importer.reader import RawSheet, RawWorkbook, read_workbook
from pri.importer.spec import (
    AVAILABILITY_COMBINATIONS,
    SHEET_AVAILABILITY,
    SHEET_EQUIPMENT,
    SHEET_LOCATIONS,
    SHEET_PEOPLE,
    SHEET_PRODUCTION,
    SHEET_SCENES,
    SHEET_SCHEDULE,
    TEMPLATE_VERSION,
    WORKBOOK_SPEC,
    FieldKind,
    SheetSpec,
)
from pri.importer.template import EXAMPLE_MARKER

if TYPE_CHECKING:
    from collections.abc import Iterable

__all__ = ["ImportSummary", "ParseResult", "money", "parse_workbook"]

#: Money is stored to two places. Fixing the scale here means an exported rate
#: re-imports to the same Decimal, which is what makes the round-trip test an
#: equality rather than an approximation.
_MONEY_SCALE = Decimal("0.01")

_KNOWN_SHEETS = frozenset(sheet.name for sheet in WORKBOOK_SPEC)


def money(value: Decimal | int | float | None) -> Decimal:
    """Quantise an amount to two decimal places, half-up."""
    if value is None:
        return Decimal("0.00")
    return Decimal(str(value)).quantize(_MONEY_SCALE, rounding=ROUND_HALF_UP)


class ImportSummary(BaseModel, frozen=True):
    """What was in the file, for the review screen's header."""

    scene_count: int = 0
    person_count: int = 0
    location_count: int = 0
    equipment_count: int = 0
    shooting_day_count: int = 0
    shoot_span_days: int = 0
    unit_names: tuple[str, ...] = ()
    first_shoot_date: date | None = None
    last_shoot_date: date | None = None


class ParseResult(BaseModel, frozen=True):
    """Everything one parse produced.

    ``state`` is ``None`` whenever any blocking error was found — a partially
    built state is a trap, because it looks usable.
    """

    state: ProductionState | None
    errors: tuple[ImportIssue, ...] = ()
    warnings: tuple[ImportIssue, ...] = ()
    summary: ImportSummary = ImportSummary()

    @property
    def ok(self) -> bool:
        return not self.errors


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def parse_workbook(file_bytes: bytes, filename: str) -> ParseResult:
    """Parse and validate an uploaded workbook.

    Inputs:
        file_bytes: The raw upload.
        filename:   Original name, used only to disambiguate zip from xlsx and
                    to write better error messages. Sanitised before use.

    Outputs:
        A :class:`ParseResult`. ``state`` is populated only when there are no
        blocking errors.

    Failure modes:
        Never raises for bad input — every problem becomes a typed issue. Only
        a genuine bug in this module can raise.
    """
    started = time_module.monotonic()
    safe_name = sanitise_filename(filename)
    parser = _Parser(started)

    try:
        check_size(file_bytes)
        kind = detect_kind(file_bytes, safe_name)
        raw = read_workbook(file_bytes, kind, _KNOWN_SHEETS)
    except LimitExceeded as exc:
        return ParseResult(
            state=None,
            errors=(error(exc.code, exc.message, exc.hint),),
        )

    return parser.run(raw)


class _Parser:
    """One parse. Holds the issue lists and the partially built tables.

    A class rather than a pile of functions because every phase needs the
    previous phase's output and the shared error list, and threading eight
    dictionaries through eight functions reads worse than this does.
    """

    def __init__(self, started: float) -> None:
        self._started = started
        self.errors: list[ImportIssue] = []
        self.warnings: list[ImportIssue] = []

        self.timezone_name: str = "UTC"
        self.zone: ZoneInfo = ZoneInfo("UTC")
        self.day_first: bool = True

        self.production_row: dict[str, Any] = {}
        self.tables: dict[str, list[tuple[int, dict[str, Any]]]] = {}

    # -- helpers ---------------------------------------------------------

    def _budget_exceeded(self) -> bool:
        return (time_module.monotonic() - self._started) > MAX_PARSE_SECONDS

    def _timeout(self) -> ParseResult:
        return ParseResult(
            state=None,
            errors=(
                error(
                    "E016",
                    f"Reading the file took longer than {MAX_PARSE_SECONDS:.0f} seconds.",
                    "The workbook is unusually large or complex. Remove unused sheets "
                    "and embedded objects, then upload again.",
                ),
            ),
            warnings=tuple(self.warnings),
        )

    def _result(self, state: ProductionState | None, summary: ImportSummary) -> ParseResult:
        return ParseResult(
            state=state,
            errors=tuple(self.errors),
            warnings=tuple(self.warnings),
            summary=summary,
        )

    # -- the run ---------------------------------------------------------

    def run(self, raw: RawWorkbook) -> ParseResult:
        """Execute the five phases, stopping at the first that fails."""
        self._phase_structure(raw)
        if self.errors:
            return self._result(None, ImportSummary())
        if self._budget_exceeded():
            return self._timeout()

        self._phase_identity()
        if self.errors:
            return self._result(None, ImportSummary())

        self._phase_reference()
        if self.errors:
            return self._result(None, ImportSummary())

        state = self._phase_semantic()
        if self.errors or state is None:
            return self._result(None, ImportSummary())

        self._phase_warnings(raw, state)
        return self._result(state, _summarise(state))

    # ------------------------------------------------------------------
    # Phase 1 — structure
    # ------------------------------------------------------------------

    def _phase_structure(self, raw: RawWorkbook) -> None:
        if not raw.sheets:
            self.errors.append(
                error(
                    "E012",
                    "The workbook has none of the sheets PRI needs.",
                    "Download the template and copy your data into it, keeping the "
                    "sheet names as they are.",
                )
            )
            return

        for sheet_spec in WORKBOOK_SPEC:
            sheet = raw.sheet(sheet_spec.name)
            if sheet is None:
                if sheet_spec.required:
                    self.errors.append(
                        error(
                            "E001",
                            f"The {sheet_spec.name!r} sheet is missing.",
                            f"Add a sheet named exactly {sheet_spec.name!r}. The "
                            f"template has it — {sheet_spec.help.split('.')[0]}.",
                            sheet=sheet_spec.name,
                        )
                    )
                continue
            self._check_columns(sheet_spec, sheet)

        if self.errors:
            return

        # The production sheet drives the timezone, which every later date and
        # time depends on, so it is read first and on its own.
        self._read_production(raw)
        if self.errors:
            return

        for sheet_spec in WORKBOOK_SPEC:
            if sheet_spec.name == SHEET_PRODUCTION:
                continue
            sheet = raw.sheet(sheet_spec.name)
            self.tables[sheet_spec.name] = (
                self._read_rows(sheet_spec, sheet) if sheet is not None else []
            )

    def _check_columns(self, sheet_spec: SheetSpec, sheet: RawSheet) -> None:
        present = set(sheet.headers)
        for column in sheet_spec.columns:
            if column.required and column.name not in present:
                self.errors.append(
                    error(
                        "E002",
                        f"The {sheet_spec.name!r} sheet has no {column.name!r} column.",
                        f"Add a column headed exactly {column.name!r}. {column.help}",
                        sheet=sheet_spec.name,
                        row=1,
                        column=column.name,
                    )
                )
        sheet.unknown_columns = [h for h in sheet.headers if sheet_spec.column(h) is None]

        # A sheet that must hold exactly one row reports E012, which says what
        # is actually wrong ("the production sheet needs one row"), rather than
        # E017's generic "over the row cap".
        data_rows = [r for r in sheet.rows if not r.is_blank]
        if not sheet_spec.exactly_one and len(data_rows) > sheet_spec.max_rows:
            self.errors.append(
                error(
                    "E017",
                    f"The {sheet_spec.name!r} sheet has {len(data_rows)} rows, over the "
                    f"limit of {sheet_spec.max_rows}.",
                    f"PRI handles up to {sheet_spec.max_rows} rows on this sheet. Split "
                    f"the production, or remove rows you are not shooting.",
                    sheet=sheet_spec.name,
                )
            )

    def _read_production(self, raw: RawWorkbook) -> None:
        sheet = raw.sheet(SHEET_PRODUCTION)
        spec = next(s for s in WORKBOOK_SPEC if s.name == SHEET_PRODUCTION)
        rows = [r for r in (sheet.rows if sheet else []) if not r.is_blank]
        rows = [r for r in rows if not _is_example(r.values)]

        if len(rows) != 1:
            self.errors.append(
                error(
                    "E012",
                    f"The production sheet has {len(rows)} rows; it needs exactly one.",
                    "Keep a single row describing the production and delete the rest.",
                    sheet=SHEET_PRODUCTION,
                )
            )
            return

        row = rows[0]

        # Read the timezone before anything else: it decides how an ambiguous
        # date like 05/03 is interpreted for every remaining cell.
        tz_column = spec.column("timezone")
        assert tz_column is not None
        try:
            zone_name, _ = coerce_cell(row.get("timezone"), tz_column)
        except CoercionError as exc:
            self.errors.append(
                error(
                    "E003",
                    exc.message,
                    exc.hint,
                    sheet=SHEET_PRODUCTION,
                    row=row.excel_row,
                    column="timezone",
                    value=row.get("timezone"),
                )
            )
            return
        if zone_name:
            self.timezone_name = str(zone_name)
            self.zone = ZoneInfo(self.timezone_name)
        self.day_first = prefers_day_first(self.timezone_name)

        values = self._coerce_row(spec, row.excel_row, row.values)
        if values is not None:
            self.production_row = values

    def _read_rows(
        self, sheet_spec: SheetSpec, sheet: RawSheet
    ) -> list[tuple[int, dict[str, Any]]]:
        out: list[tuple[int, dict[str, Any]]] = []
        for row in sheet.rows:
            if row.is_blank or _is_example(row.values):
                continue
            values = self._coerce_row(sheet_spec, row.excel_row, row.values)
            if values is not None:
                out.append((row.excel_row, values))
        return out

    def _coerce_row(
        self, sheet_spec: SheetSpec, excel_row: int, raw_values: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Coerce one row; returns ``None`` if any cell failed."""
        result: dict[str, Any] = {}
        clean = True
        for column in sheet_spec.columns:
            raw = raw_values.get(column.name)
            try:
                value, ambiguous = coerce_cell(raw, column, day_first=self.day_first)
            except CoercionError as exc:
                code = "E004" if column.kind is FieldKind.ENUM else "E003"
                self.errors.append(
                    error(
                        code,
                        f"{column.name}: {exc.message}",
                        exc.hint,
                        sheet=sheet_spec.name,
                        row=excel_row,
                        column=column.name,
                        value=raw,
                    )
                )
                clean = False
                continue

            if column.required and _is_empty(value):
                self.errors.append(
                    error(
                        "E003",
                        f"{column.name} is required but the cell is empty.",
                        f"Fill in {column.name}. {column.help}",
                        sheet=sheet_spec.name,
                        row=excel_row,
                        column=column.name,
                    )
                )
                clean = False
                continue

            if ambiguous:
                self.warnings.append(
                    warning(
                        "W011",
                        f"{raw!r} could be read as day-first or month-first.",
                        f"PRI read it as {'day' if self.day_first else 'month'}-first, "
                        f"based on the timezone {self.timezone_name}. Write dates as "
                        f"YYYY-MM-DD to remove the doubt.",
                        sheet=sheet_spec.name,
                        row=excel_row,
                        column=column.name,
                        value=raw,
                    )
                )
            result[column.name] = value
        return result if clean else None

    # ------------------------------------------------------------------
    # Phase 2 — identity
    # ------------------------------------------------------------------

    def _phase_identity(self) -> None:
        for sheet_spec in WORKBOOK_SPEC:
            key = sheet_spec.primary_key
            if key is None:
                continue
            seen: dict[str, int] = {}
            for excel_row, values in self.tables.get(sheet_spec.name, []):
                identifier = values.get(key)
                if not identifier:
                    continue
                if identifier in seen:
                    self.errors.append(
                        error(
                            "E005",
                            f"{identifier!r} appears twice in {sheet_spec.name} — "
                            f"rows {seen[identifier]} and {excel_row}.",
                            f"Every {key} must be unique. Renumber one of them, or "
                            f"delete the duplicate row.",
                            sheet=sheet_spec.name,
                            row=excel_row,
                            column=key,
                            value=identifier,
                        )
                    )
                else:
                    seen[identifier] = excel_row

    # ------------------------------------------------------------------
    # Phase 3 — reference
    # ------------------------------------------------------------------

    def _phase_reference(self) -> None:
        scene_ids = self._ids(SHEET_SCENES, "scene_id")
        person_roles = {
            values["person_id"]: values.get("role")
            for _, values in self.tables.get(SHEET_PEOPLE, [])
            if values.get("person_id")
        }
        location_ids = self._ids(SHEET_LOCATIONS, "location_id")
        equipment_ids = self._ids(SHEET_EQUIPMENT, "equipment_id")

        for excel_row, values in self.tables.get(SHEET_SCENES, []):
            scene_id = values.get("scene_id", "?")
            self._check_ref(
                values.get("location_id"),
                location_ids,
                SHEET_SCENES,
                excel_row,
                "location_id",
                f"Scene {scene_id}",
                SHEET_LOCATIONS,
            )
            for pid in values.get("cast_ids", ()):
                if not self._check_ref(
                    pid,
                    set(person_roles),
                    SHEET_SCENES,
                    excel_row,
                    "cast_ids",
                    f"Scene {scene_id}",
                    SHEET_PEOPLE,
                ):
                    continue
                if person_roles.get(pid) != "CAST":
                    self.errors.append(
                        error(
                            "E008",
                            f"Scene {scene_id} lists {pid} as cast, but {pid} is CREW.",
                            f"Move {pid} to the crew_ids column, or change their role to "
                            f"CAST on the people sheet.",
                            sheet=SHEET_SCENES,
                            row=excel_row,
                            column="cast_ids",
                            value=pid,
                        )
                    )
            for pid in values.get("crew_ids", ()):
                if not self._check_ref(
                    pid,
                    set(person_roles),
                    SHEET_SCENES,
                    excel_row,
                    "crew_ids",
                    f"Scene {scene_id}",
                    SHEET_PEOPLE,
                ):
                    continue
                if person_roles.get(pid) != "CREW":
                    self.errors.append(
                        error(
                            "E008",
                            f"Scene {scene_id} lists {pid} as crew, but {pid} is CAST.",
                            f"Move {pid} to the cast_ids column, or change their role to "
                            f"CREW on the people sheet.",
                            sheet=SHEET_SCENES,
                            row=excel_row,
                            column="crew_ids",
                            value=pid,
                        )
                    )
            for eid in values.get("equipment_ids", ()):
                self._check_ref(
                    eid,
                    equipment_ids,
                    SHEET_SCENES,
                    excel_row,
                    "equipment_ids",
                    f"Scene {scene_id}",
                    SHEET_EQUIPMENT,
                )
            for pre in values.get("prerequisite_scene_ids", ()):
                self._check_ref(
                    pre,
                    scene_ids,
                    SHEET_SCENES,
                    excel_row,
                    "prerequisite_scene_ids",
                    f"Scene {scene_id}",
                    SHEET_SCENES,
                )

        for excel_row, values in self.tables.get(SHEET_SCHEDULE, []):
            self._check_ref(
                values.get("location_id"),
                location_ids,
                SHEET_SCHEDULE,
                excel_row,
                "location_id",
                f"The day on {values.get('date')}",
                SHEET_LOCATIONS,
            )
            for sid in values.get("scene_ids", ()):
                self._check_ref(
                    sid,
                    scene_ids,
                    SHEET_SCHEDULE,
                    excel_row,
                    "scene_ids",
                    f"The day on {values.get('date')}",
                    SHEET_SCENES,
                )

        by_type = {
            "PERSON": (set(person_roles), SHEET_PEOPLE),
            "EQUIPMENT": (equipment_ids, SHEET_EQUIPMENT),
            "LOCATION": (location_ids, SHEET_LOCATIONS),
        }
        for excel_row, values in self.tables.get(SHEET_AVAILABILITY, []):
            subject_type = values.get("subject_type")
            window_kind = values.get("window_kind")
            expected = AVAILABILITY_COMBINATIONS.get(str(subject_type))
            if expected and window_kind != expected:
                self.errors.append(
                    error(
                        "E014",
                        f"A {subject_type} row cannot have window_kind {window_kind}.",
                        f"For {subject_type} rows use {expected}. People get UNAVAILABLE "
                        f"days, equipment gets AVAILABLE rental windows, locations get "
                        f"CLOSED days.",
                        sheet=SHEET_AVAILABILITY,
                        row=excel_row,
                        column="window_kind",
                        value=window_kind,
                    )
                )
                continue
            known, source_sheet = by_type.get(str(subject_type), (set(), SHEET_PEOPLE))
            self._check_ref(
                values.get("subject_id"),
                known,
                SHEET_AVAILABILITY,
                excel_row,
                "subject_id",
                f"This {str(subject_type).lower()} window",
                source_sheet,
            )

    def _ids(self, sheet_name: str, key: str) -> set[str]:
        return {values[key] for _, values in self.tables.get(sheet_name, []) if values.get(key)}

    def _check_ref(
        self,
        value: Any,
        known: set[str],
        sheet: str,
        excel_row: int,
        column: str,
        subject: str,
        target_sheet: str,
    ) -> bool:
        """Record E006 if ``value`` names something that does not exist."""
        if not value or value in known:
            return True
        self.errors.append(
            error(
                "E006",
                f"{subject} refers to {value!r}, which is not in the {target_sheet} sheet.",
                f"Add {value!r} to the {target_sheet} sheet, or correct the {column} on "
                f"{sheet} row {excel_row}.",
                sheet=sheet,
                row=excel_row,
                column=column,
                value=value,
            )
        )
        return False

    # ------------------------------------------------------------------
    # Phase 4 — semantic
    # ------------------------------------------------------------------

    def _phase_semantic(self) -> ProductionState | None:
        production = self._build_production()
        if production is None:
            return None

        self._check_prerequisite_cycles()
        self._check_schedule_semantics(production)
        self._check_availability_windows()
        if self.errors:
            return None

        try:
            return self._build_state(production)
        except ValidationError as exc:
            # A domain invariant the sheet-level checks did not cover. Reported
            # rather than raised, because the user can still act on it.
            for detail in exc.errors()[:10]:
                location = ".".join(str(p) for p in detail["loc"])
                self.errors.append(
                    error(
                        "E003",
                        f"{location}: {detail['msg']}",
                        "Check this value against the README sheet in the template.",
                        value=detail.get("input"),
                    )
                )
            return None

    def _build_production(self) -> Production | None:
        values = self.production_row
        if not values:
            return None
        try:
            return Production(
                id=str(values["production_id"]),
                title=str(values["title"]),
                currency=str(values["currency"]),
                shoot_start=values["shoot_start"],
                shoot_end=values["shoot_end"],
                reserve_days=tuple(values.get("reserve_days") or ()),
            )
        except (ValidationError, KeyError) as exc:
            self.errors.append(
                error(
                    "E003",
                    f"The production row could not be read: {exc}",
                    "Check shoot_start is on or before shoot_end, and that every "
                    "required cell is filled in.",
                    sheet=SHEET_PRODUCTION,
                    row=2,
                )
            )
            return None

    def _check_prerequisite_cycles(self) -> None:
        graph = nx.DiGraph()
        rows = {
            values["scene_id"]: excel_row
            for excel_row, values in self.tables.get(SHEET_SCENES, [])
            if values.get("scene_id")
        }
        for _, values in self.tables.get(SHEET_SCENES, []):
            scene_id = values.get("scene_id")
            if not scene_id:
                continue
            graph.add_node(scene_id)
            for pre in values.get("prerequisite_scene_ids", ()):
                graph.add_edge(pre, scene_id)

        for cycle in nx.simple_cycles(graph):
            path = " → ".join([*cycle, cycle[0]])
            first = cycle[0]
            self.errors.append(
                error(
                    "E007",
                    f"These scenes depend on each other in a loop: {path}.",
                    "One of these prerequisites is wrong — a scene cannot need itself, "
                    "however indirectly. Remove one link in the loop.",
                    sheet=SHEET_SCENES,
                    row=rows.get(first),
                    column="prerequisite_scene_ids",
                    value=path,
                )
            )

    def _check_schedule_semantics(self, production: Production) -> None:
        seen_scene_slots: dict[str, tuple[date, str, int]] = {}

        for excel_row, values in self.tables.get(SHEET_SCHEDULE, []):
            day_date: date | None = values.get("date")
            if day_date is None:
                continue

            if not (production.shoot_start <= day_date <= production.shoot_end):
                self.errors.append(
                    error(
                        "E010",
                        f"{day_date.isoformat()} is outside the shoot window "
                        f"{production.shoot_start.isoformat()} to "
                        f"{production.shoot_end.isoformat()}.",
                        "Either correct the date, or widen shoot_start / shoot_end on "
                        "the production sheet.",
                        sheet=SHEET_SCHEDULE,
                        row=excel_row,
                        column="date",
                        value=day_date,
                    )
                )

            unit = str(values.get("unit") or "MAIN")
            for sid in values.get("scene_ids", ()):
                previous = seen_scene_slots.get(sid)
                if previous is not None and (previous[0], previous[1]) != (day_date, unit):
                    self.errors.append(
                        error(
                            "E009",
                            f"Scene {sid} is scheduled twice — {previous[0].isoformat()} "
                            f"({previous[1]}) and {day_date.isoformat()} ({unit}).",
                            f"A scene shoots once. Remove {sid} from one of the two days, "
                            f"or split it into two scenes with different ids.",
                            sheet=SHEET_SCHEDULE,
                            row=excel_row,
                            column="scene_ids",
                            value=sid,
                        )
                    )
                else:
                    seen_scene_slots[sid] = (day_date, unit, excel_row)

            self._resolve_day_times(excel_row, values, day_date)

    def _resolve_day_times(self, excel_row: int, values: dict[str, Any], day_date: date) -> None:
        """Work out the aware call and wrap datetimes, recording E011/E015/W008/W009."""
        call: time | None = values.get("call_time")
        wrap: time | None = values.get("wrap_time")
        if call is None or wrap is None:
            return

        explicit = values.get("wrap_next_day")
        overnight = False
        if explicit is True:
            overnight = True
        elif wrap <= call:
            if explicit is False:
                self.errors.append(
                    error(
                        "E011",
                        f"Wrap {wrap.strftime('%H:%M')} is not after call "
                        f"{call.strftime('%H:%M')}, but wrap_next_day says FALSE.",
                        "For an overnight shoot set wrap_next_day to TRUE, or leave it "
                        "blank and PRI will work it out. Otherwise correct the times.",
                        sheet=SHEET_SCHEDULE,
                        row=excel_row,
                        column="wrap_time",
                        value=wrap.strftime("%H:%M"),
                    )
                )
                return
            overnight = True
            self.warnings.append(
                warning(
                    "W008",
                    f"Wrap {wrap.strftime('%H:%M')} is earlier than call "
                    f"{call.strftime('%H:%M')}, so PRI read it as the next morning.",
                    "That is almost certainly what you meant. Set wrap_next_day to TRUE "
                    "to say so explicitly.",
                    sheet=SHEET_SCHEDULE,
                    row=excel_row,
                    column="wrap_time",
                    value=wrap.strftime("%H:%M"),
                )
            )

        call_dt = self._localise(day_date, call, excel_row, "call_time")
        wrap_date = day_date + timedelta(days=1) if overnight else day_date
        wrap_dt = self._localise(wrap_date, wrap, excel_row, "wrap_time")

        if call_dt is not None and wrap_dt is not None:
            values["_call_dt"] = call_dt
            values["_wrap_dt"] = wrap_dt
            values["_overnight"] = overnight

    def _localise(self, day: date, clock: time, excel_row: int, column: str) -> datetime | None:
        """Attach the production timezone to a wall-clock time.

        Two edge cases are handled rather than left to chance, because the
        alternative is a silently wrong turnaround calculation — the exact
        failure class this system exists to prevent.

        A time that does not exist (the hour skipped on a spring-forward date)
        is E015. A time that happens twice (a fall-back date) resolves to the
        first occurrence with W009.
        """
        naive = datetime.combine(day, clock)
        first = naive.replace(tzinfo=self.zone, fold=0)
        second = naive.replace(tzinfo=self.zone, fold=1)

        # A nonexistent local time does not round-trip through UTC.
        if first.astimezone(UTC).astimezone(self.zone).replace(tzinfo=None) != naive:
            self.errors.append(
                error(
                    "E015",
                    f"{clock.strftime('%H:%M')} does not exist on "
                    f"{day.isoformat()} in {self.timezone_name} — the clocks go "
                    f"forward and that hour is skipped.",
                    "Move the time outside the skipped hour, for example to 03:00.",
                    sheet=SHEET_SCHEDULE,
                    row=excel_row,
                    column=column,
                    value=clock.strftime("%H:%M"),
                )
            )
            return None

        if first.utcoffset() != second.utcoffset():
            self.warnings.append(
                warning(
                    "W009",
                    f"{clock.strftime('%H:%M')} happens twice on {day.isoformat()} in "
                    f"{self.timezone_name} — the clocks go back that day.",
                    "PRI used the first occurrence, before the clocks change. If you "
                    "meant the second, move the time by an hour.",
                    sheet=SHEET_SCHEDULE,
                    row=excel_row,
                    column=column,
                    value=clock.strftime("%H:%M"),
                )
            )
        return first

    def _check_availability_windows(self) -> None:
        for excel_row, values in self.tables.get(SHEET_AVAILABILITY, []):
            start: date | None = values.get("from_date")
            end: date | None = values.get("to_date")
            if start is None or end is None:
                continue
            if end < start:
                self.errors.append(
                    error(
                        "E018",
                        f"This window ends on {end.isoformat()}, before it starts on "
                        f"{start.isoformat()}.",
                        "Swap the two dates. to_date is the last day of the window and "
                        "is included.",
                        sheet=SHEET_AVAILABILITY,
                        row=excel_row,
                        column="to_date",
                        value=end,
                    )
                )

    # ------------------------------------------------------------------
    # Building the state
    # ------------------------------------------------------------------

    def _midnight(self, day: date) -> datetime:
        return datetime.combine(day, time.min, tzinfo=self.zone)

    def _window(self, start: date, end_inclusive: date) -> TimeWindow:
        """A half-open window covering whole days, ``end`` inclusive."""
        return TimeWindow(
            start=self._midnight(start),
            end=self._midnight(end_inclusive + timedelta(days=1)),
        )

    def _build_state(self, production: Production) -> ProductionState:
        availability = self.tables.get(SHEET_AVAILABILITY, [])

        person_windows: dict[str, list[TimeWindow]] = {}
        equipment_windows: dict[str, list[TimeWindow]] = {}
        closed_days: dict[str, set[date]] = {}
        for _, values in availability:
            subject = str(values.get("subject_id") or "")
            start, end = values.get("from_date"), values.get("to_date")
            if not subject or start is None or end is None:
                continue
            kind = values.get("window_kind")
            if kind == "UNAVAILABLE":
                person_windows.setdefault(subject, []).append(self._window(start, end))
            elif kind == "AVAILABLE":
                equipment_windows.setdefault(subject, []).append(self._window(start, end))
            elif kind == "CLOSED":
                closed = closed_days.setdefault(subject, set())
                cursor = start
                while cursor <= end:
                    closed.add(cursor)
                    cursor += timedelta(days=1)

        scenes = tuple(
            Scene(
                id=values["scene_id"],
                number=values["number"],
                slug=values["slug"],
                description=str(values.get("description") or ""),
                int_ext=values["int_ext"],
                time_of_day=values["time_of_day"],
                estimated_minutes=values["estimated_minutes"],
                location_id=values["location_id"],
                cast_ids=tuple(values.get("cast_ids") or ()),
                crew_ids=tuple(values.get("crew_ids") or ()),
                equipment_ids=tuple(values.get("equipment_ids") or ()),
                vfx_plate=bool(values.get("vfx_plate")),
                prerequisite_scene_ids=tuple(values.get("prerequisite_scene_ids") or ()),
            )
            for _, values in self.tables.get(SHEET_SCENES, [])
        )

        people = tuple(
            Person(
                id=values["person_id"],
                name=values["name"],
                role=values["role"],
                character=values.get("character"),
                daily_rate=money(values.get("daily_rate")),
                unavailable_windows=tuple(person_windows.get(values["person_id"], ())),
            )
            for _, values in self.tables.get(SHEET_PEOPLE, [])
        )

        locations = tuple(
            Location(
                id=values["location_id"],
                name=values["name"],
                kind=str(values.get("kind") or ""),
                day_rate=money(values.get("day_rate")),
                permit_windows=self._permit_windows(
                    production,
                    values.get("permit_start_time"),
                    values.get("permit_end_time"),
                    closed_days.get(values["location_id"], set()),
                ),
                supports_int_ext=tuple(values.get("supports_int_ext") or ()),
                supports_time_of_day=tuple(values.get("supports_time_of_day") or ()),
                address=str(values.get("address") or ""),
                parking_note=str(values.get("parking_note") or ""),
                nearest_hospital=str(values.get("nearest_hospital") or ""),
            )
            for _, values in self.tables.get(SHEET_LOCATIONS, [])
        )

        equipment = tuple(
            Equipment(
                id=values["equipment_id"],
                name=values["name"],
                kind=str(values.get("kind") or ""),
                daily_rate=money(values.get("daily_rate")),
                available_windows=tuple(equipment_windows.get(values["equipment_id"], ())),
            )
            for _, values in self.tables.get(SHEET_EQUIPMENT, [])
        )

        days: list[ShootingDay] = []
        for _, values in self.tables.get(SHEET_SCHEDULE, []):
            call_dt = values.get("_call_dt")
            wrap_dt = values.get("_wrap_dt")
            if call_dt is None or wrap_dt is None:
                continue
            scene_ids = tuple(values.get("scene_ids") or ())
            days.append(
                ShootingDay(
                    date=values["date"],
                    call_time=call_dt,
                    wrap_time=wrap_dt,
                    location_id=values["location_id"],
                    scene_ids=scene_ids,
                    unit=str(values.get("unit") or "MAIN"),
                    # Blank infers from the scenes: a day with work on it is a
                    # SHOOT day, a day without is held in RESERVE. That is what
                    # every board written before this column existed meant.
                    day_kind=str(values.get("day_kind") or "")
                    or ("SHOOT" if scene_ids else "RESERVE"),
                )
            )

        return ProductionState(
            production=production,
            scenes=scenes,
            people=people,
            locations=locations,
            equipment=equipment,
            schedule=Schedule(days=tuple(sorted(days, key=lambda d: (d.date, d.unit)))),
            version=1,
            parent_version=None,
            event_id=None,
            created_at=datetime.now(UTC),
        )

    def _permit_windows(
        self,
        production: Production,
        start: time | None,
        end: time | None,
        closed: set[date],
    ) -> tuple[TimeWindow, ...]:
        """Expand a daily permit into one window per shoot day, minus closures.

        A location with no permit times and no closures is unrestricted, and is
        given no windows at all — which is how the validator reads "no limit".
        A location with *only* closures still needs windows, otherwise the
        closure would have nothing to carve out of, so it gets a full day on
        every open date.
        """
        if start is None and end is None and not closed:
            return ()

        windows: list[TimeWindow] = []
        cursor = production.shoot_start
        while cursor <= production.shoot_end:
            if cursor in closed:
                cursor += timedelta(days=1)
                continue
            if start is None or end is None:
                windows.append(
                    TimeWindow(
                        start=self._midnight(cursor),
                        end=self._midnight(cursor + timedelta(days=1)),
                    )
                )
            else:
                opens = datetime.combine(cursor, start, tzinfo=self.zone)
                closes = datetime.combine(cursor, end, tzinfo=self.zone)
                if closes <= opens:
                    closes = datetime.combine(cursor + timedelta(days=1), end, tzinfo=self.zone)
                windows.append(TimeWindow(start=opens, end=closes))
            cursor += timedelta(days=1)
        return tuple(windows)

    # ------------------------------------------------------------------
    # Phase 5 — warnings
    # ------------------------------------------------------------------

    def _phase_warnings(self, raw: RawWorkbook, state: ProductionState) -> None:
        scheduled = {sid for day in state.schedule.days for sid in day.scene_ids}
        for scene in state.scenes:
            if scene.id not in scheduled:
                self.warnings.append(
                    warning(
                        "W001",
                        f"Scene {scene.id} ({scene.slug}) is never scheduled.",
                        "Add it to a day on the schedule sheet, or leave it — PRI will "
                        "treat it as not yet boarded.",
                        sheet=SHEET_SCENES,
                        value=scene.id,
                    )
                )

        used_people = {pid for scene in state.scenes for pid in (*scene.cast_ids, *scene.crew_ids)}
        for person in state.people:
            if person.id not in used_people:
                self.warnings.append(
                    warning(
                        "W002",
                        f"{person.name} ({person.id}) is not in any scene.",
                        "Add them to a scene's cast_ids or crew_ids, or leave them on "
                        "the sheet as a contact.",
                        sheet=SHEET_PEOPLE,
                        value=person.id,
                    )
                )

        used_equipment = {eid for scene in state.scenes for eid in scene.equipment_ids}
        for item in state.equipment:
            if item.id not in used_equipment:
                self.warnings.append(
                    warning(
                        "W003",
                        f"{item.name} ({item.id}) is not used by any scene.",
                        "Add it to a scene's equipment_ids, or remove it if you are not hiring it.",
                        sheet=SHEET_EQUIPMENT,
                        value=item.id,
                    )
                )

        reserve = set(state.production.reserve_days)
        for day in state.schedule.days:
            if day.scene_ids and not day.is_shoot:
                self.warnings.append(
                    warning(
                        "W014",
                        f"{day.date.isoformat()} is marked {day.day_kind} but has "
                        f"{len(day.scene_ids)} scenes on it.",
                        "PRI will not move work onto a day that is not SHOOT, and will "
                        "not check its shooting hours. Set day_kind to SHOOT if the "
                        "unit is filming.",
                        sheet=SHEET_SCHEDULE,
                        column="day_kind",
                        value=day.day_kind,
                    )
                )

            if not day.scene_ids:
                self.warnings.append(
                    warning(
                        "W004",
                        f"{day.date.isoformat()} has no scenes.",
                        "That is fine for a reserve or travel day. Add scene ids if it "
                        "should be shooting.",
                        sheet=SHEET_SCHEDULE,
                        value=day.date,
                    )
                )
            elif day.date in reserve:
                self.warnings.append(
                    warning(
                        "W005",
                        f"{day.date.isoformat()} is listed as a reserve day but already "
                        f"has {len(day.scene_ids)} scenes on it.",
                        "PRI defers work onto reserve days. Either clear this one, or "
                        "remove it from reserve_days on the production sheet.",
                        sheet=SHEET_SCHEDULE,
                        value=day.date,
                    )
                )

        for sheet in raw.sheets.values():
            if sheet.unknown_columns:
                self.warnings.append(
                    warning(
                        "W006",
                        f"{sheet.name}: PRI ignored these columns — "
                        f"{', '.join(sheet.unknown_columns)}.",
                        "That is fine. Keep your own columns; PRI only reads the ones it "
                        "knows and leaves the rest alone.",
                        sheet=sheet.name,
                        row=1,
                    )
                )
            if sheet.merged_ranges:
                self.warnings.append(
                    warning(
                        "W012",
                        f"{sheet.name}: {sheet.merged_ranges} merged cell "
                        f"{'range' if sheet.merged_ranges == 1 else 'ranges'} were "
                        f"expanded so every row has its own value.",
                        "Check the preview tab to confirm PRI split them the way you meant.",
                        sheet=sheet.name,
                    )
                )
            if sheet.hidden or sheet.had_hidden_rows:
                what = "sheet" if sheet.hidden else "rows"
                self.warnings.append(
                    warning(
                        "W013",
                        f"{sheet.name}: hidden {what} were read as data.",
                        "PRI does not skip hidden rows. Delete them rather than hiding "
                        "them if they should not be imported.",
                        sheet=sheet.name,
                    )
                )
            for column in sheet.formula_columns:
                self.warnings.append(
                    warning(
                        "W010",
                        f"{sheet.name}: the {column!r} column holds formulas that were "
                        f"never calculated, so PRI read it as blank.",
                        "Open the file in Excel, press F9 to recalculate, save, and "
                        "upload again — or paste the values over the formulas.",
                        sheet=sheet.name,
                        column=column,
                    )
                )

        declared = str(self.production_row.get("template_version") or "")
        if declared and declared != TEMPLATE_VERSION:
            self.warnings.append(
                warning(
                    "W007",
                    f"The file says template version {declared}; PRI is on {TEMPLATE_VERSION}.",
                    "The import went ahead. Download the current template if you want "
                    "the newest columns.",
                    sheet=SHEET_PRODUCTION,
                    row=2,
                    column="template_version",
                    value=declared,
                )
            )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, tuple | list):
        return len(value) == 0
    return False


def _is_example(values: dict[str, Any]) -> bool:
    """Whether this is one of the template's own grey example rows.

    A user who forgets to delete them should still get a clean import rather
    than two phantom scenes and a dangling reference.
    """
    for value in values.values():
        if isinstance(value, str) and value.startswith(EXAMPLE_MARKER):
            return True
    return False


def _summarise(state: ProductionState) -> ImportSummary:
    days = state.schedule.days
    dates = sorted(day.date for day in days)
    span = (
        (state.production.shoot_end - state.production.shoot_start).days + 1
        if state.production.shoot_end >= state.production.shoot_start
        else 0
    )
    return ImportSummary(
        scene_count=len(state.scenes),
        person_count=len(state.people),
        location_count=len(state.locations),
        equipment_count=len(state.equipment),
        shooting_day_count=len(days),
        shoot_span_days=span,
        unit_names=tuple(sorted({day.unit for day in days})),
        first_shoot_date=dates[0] if dates else None,
        last_shoot_date=dates[-1] if dates else None,
    )


def _iter_issue_codes(issues: Iterable[ImportIssue]) -> list[str]:
    return [issue.code for issue in issues]
