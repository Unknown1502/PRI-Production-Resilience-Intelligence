"""The workbook specification — the single source of truth for the import format.

Every column in the import workbook is declared **exactly once**, here. The
template generator, the parser, the exporter and the user-facing README sheet
all read this module. Adding a column is one line; there is no second place to
keep in sync, and therefore no way for them to drift apart.

That constraint is the whole design of this module. A format defined in two
places is a format that will disagree with itself the first time somebody is in
a hurry, and the person who discovers the disagreement is a 1st AD at 19:00 with
a file that will not import.

Read alongside:
    :mod:`pri.importer.template`  — turns this into an .xlsx, and back
    :mod:`pri.importer.parser`    — turns an .xlsx into a ``ProductionState``
    :mod:`pri.importer.errors`    — the codes a violation of this spec produces
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

__all__ = [
    "AVAILABILITY_COMBINATIONS",
    "SHEET_AVAILABILITY",
    "SHEET_EQUIPMENT",
    "SHEET_LOCATIONS",
    "SHEET_PEOPLE",
    "SHEET_PRODUCTION",
    "SHEET_SCENES",
    "SHEET_SCHEDULE",
    "TEMPLATE_VERSION",
    "WORKBOOK_SPEC",
    "ColumnSpec",
    "FieldKind",
    "SheetSpec",
    "sheet_by_name",
]

#: Bumped when the workbook shape changes in a way an older file cannot satisfy.
#: A mismatch is a warning (W007), never an error — a file written against
#: version 1 must still import when the template reaches version 2.
TEMPLATE_VERSION = "1"

SHEET_PRODUCTION = "production"
SHEET_SCENES = "scenes"
SHEET_PEOPLE = "people"
SHEET_LOCATIONS = "locations"
SHEET_EQUIPMENT = "equipment"
SHEET_AVAILABILITY = "availability"
SHEET_SCHEDULE = "schedule"


class FieldKind(StrEnum):
    """The declared type of a column.

    The parser dispatches coercion on this, the template dispatches number
    format and data validation on it, and the exporter dispatches serialisation
    on it. One enum, three consumers, no per-column special cases.
    """

    STRING = "STRING"
    INT = "INT"
    DECIMAL = "DECIMAL"
    DATE = "DATE"
    TIME = "TIME"
    BOOL = "BOOL"
    ENUM = "ENUM"
    ID_LIST = "ID_LIST"
    DATE_LIST = "DATE_LIST"
    TIMEZONE = "TIMEZONE"


#: How each kind is described to a human in the README sheet.
KIND_FORMAT: dict[FieldKind, str] = {
    FieldKind.STRING: "text",
    FieldKind.INT: "whole number",
    FieldKind.DECIMAL: "number, 2 decimals",
    FieldKind.DATE: "YYYY-MM-DD",
    FieldKind.TIME: "HH:MM (24-hour)",
    FieldKind.BOOL: "TRUE or FALSE",
    FieldKind.ENUM: "pick from the dropdown",
    FieldKind.ID_LIST: "ids separated by ;",
    FieldKind.DATE_LIST: "dates separated by ;",
    FieldKind.TIMEZONE: "IANA name, e.g. Asia/Kolkata",
}


@dataclass(frozen=True, slots=True)
class ColumnSpec:
    """One column, in one sheet.

    Inputs:
        name:     Exact header text. Matched case-sensitively after whitespace
                  stripping, because a header that differs only in case is
                  almost always a typo rather than an intent.
        kind:     Declared type; drives coercion, formatting and validation.
        required: Whether a blank cell is an error (E003) or the default.
        enum:     Permitted values for ``ENUM``; rendered as an Excel dropdown.
        examples: Two example values shown in the template's example rows.
        help:     One line, shown in the README sheet. Written for a 1st AD.
        width:    Template column width, in Excel's character units.
        default:  Value used when an optional cell is blank.
    """

    name: str
    kind: FieldKind
    required: bool
    examples: tuple[str, str]
    help: str
    width: int = 18
    enum: tuple[str, ...] | None = None
    default: object | None = None

    def __post_init__(self) -> None:
        if self.kind is FieldKind.ENUM and not self.enum:
            raise ValueError(f"ENUM column {self.name!r} declares no values")
        if self.kind is not FieldKind.ENUM and self.enum:
            raise ValueError(f"Non-ENUM column {self.name!r} declares enum values")

    @property
    def format_hint(self) -> str:
        """How the README sheet describes this column's format."""
        if self.kind is FieldKind.ENUM and self.enum:
            return " | ".join(self.enum)
        return KIND_FORMAT[self.kind]


@dataclass(frozen=True, slots=True)
class SheetSpec:
    """One sheet in the workbook.

    Inputs:
        name:        Exact sheet name, lower-case.
        required:    Whether its absence is E001.
        primary_key: Column whose values must be unique (E005), or ``None``.
        columns:     Every column, in template order.
        help:        What PRI does with this sheet, for the README.
        max_rows:    Hard cap enforced at parse time (E017). Not a performance
                     limit — a defence against a file that would exhaust memory
                     before validation ever runs.
        exactly_one: The sheet must hold exactly one data row (E012).
    """

    name: str
    required: bool
    primary_key: str | None
    columns: tuple[ColumnSpec, ...]
    help: str
    max_rows: int
    exactly_one: bool = False

    @property
    def column_names(self) -> tuple[str, ...]:
        return tuple(column.name for column in self.columns)

    @property
    def required_columns(self) -> tuple[str, ...]:
        return tuple(column.name for column in self.columns if column.required)

    def column(self, name: str) -> ColumnSpec | None:
        return next((c for c in self.columns if c.name == name), None)


# ---------------------------------------------------------------------------
# production
# ---------------------------------------------------------------------------

_PRODUCTION = SheetSpec(
    name=SHEET_PRODUCTION,
    required=True,
    primary_key=None,
    exactly_one=True,
    max_rows=1,
    help=(
        "One row describing the whole production. The timezone matters more than "
        "it looks: every call and wrap time in the schedule sheet is read as local "
        "wall-clock time in this zone, and the crew-turnaround maths depends on it."
    ),
    columns=(
        ColumnSpec(
            name="production_id",
            kind=FieldKind.STRING,
            required=True,
            examples=("film-042", "film-042"),
            help="A short stable id with no spaces. Used in URLs and audit records.",
            width=16,
        ),
        ColumnSpec(
            name="title",
            kind=FieldKind.STRING,
            required=True,
            examples=("The Long Monsoon", "The Long Monsoon"),
            help="The production's title, as you would write it on a call sheet.",
            width=26,
        ),
        ColumnSpec(
            name="currency",
            kind=FieldKind.ENUM,
            required=True,
            enum=("USD", "EUR", "GBP", "INR"),
            examples=("INR", "INR"),
            help="Currency for every rate in this workbook.",
            width=10,
        ),
        ColumnSpec(
            name="timezone",
            kind=FieldKind.TIMEZONE,
            required=True,
            examples=("Asia/Kolkata", "Asia/Kolkata"),
            help="IANA timezone where you shoot. All times are local to this zone.",
            width=20,
        ),
        ColumnSpec(
            name="shoot_start",
            kind=FieldKind.DATE,
            required=True,
            examples=("2026-11-02", "2026-11-02"),
            help="First day of principal photography.",
            width=14,
        ),
        ColumnSpec(
            name="shoot_end",
            kind=FieldKind.DATE,
            required=True,
            examples=("2026-11-30", "2026-11-30"),
            help="Last day. Every schedule date must fall inside this span.",
            width=14,
        ),
        ColumnSpec(
            name="reserve_days",
            kind=FieldKind.DATE_LIST,
            required=False,
            examples=("2026-11-14;2026-11-28", "2026-11-14;2026-11-28"),
            help="Contingency days held empty, separated by ; — PRI defers work onto these.",
            width=26,
        ),
        ColumnSpec(
            name="template_version",
            kind=FieldKind.STRING,
            required=True,
            examples=(TEMPLATE_VERSION, TEMPLATE_VERSION),
            help=f"Leave as {TEMPLATE_VERSION}. Tells PRI which template you used.",
            width=16,
        ),
    ),
)

# ---------------------------------------------------------------------------
# scenes
# ---------------------------------------------------------------------------

_SCENES = SheetSpec(
    name=SHEET_SCENES,
    required=True,
    primary_key="scene_id",
    max_rows=2000,
    help=(
        "Every scene in the shooting script. PRI uses estimated_minutes to work out "
        "whether a day fits, and prerequisite_scene_ids to work out what a delay "
        "drags with it."
    ),
    columns=(
        ColumnSpec(
            name="scene_id",
            kind=FieldKind.STRING,
            required=True,
            examples=("SC-001", "SC-002"),
            help="Unique id for the scene. Must not repeat.",
            width=12,
        ),
        ColumnSpec(
            name="number",
            kind=FieldKind.STRING,
            required=True,
            examples=("12", "12A"),
            help="Scene number exactly as written on the script — 12A and 12 are different.",
            width=10,
        ),
        ColumnSpec(
            name="slug",
            kind=FieldKind.STRING,
            required=True,
            examples=("INT. TRAIN COMPARTMENT - DAY", "EXT. STATION PLATFORM - NIGHT"),
            help="The slug line from the script.",
            width=36,
        ),
        ColumnSpec(
            name="description",
            kind=FieldKind.STRING,
            required=False,
            examples=("Arun boards the train", "Meera misses him by seconds"),
            help="One line, for the call sheet.",
            width=32,
        ),
        ColumnSpec(
            name="int_ext",
            kind=FieldKind.ENUM,
            required=True,
            enum=("INT", "EXT"),
            examples=("INT", "EXT"),
            help="Interior or exterior. Must be supported by the scene's location.",
            width=9,
        ),
        ColumnSpec(
            name="time_of_day",
            kind=FieldKind.ENUM,
            required=True,
            enum=("DAY", "NIGHT", "DAWN", "DUSK"),
            examples=("DAY", "NIGHT"),
            help="Must be supported by the scene's location.",
            width=13,
        ),
        ColumnSpec(
            name="estimated_minutes",
            kind=FieldKind.INT,
            required=True,
            examples=("120", "95"),
            help="How long the scene takes to shoot, in minutes. Must be above zero.",
            width=18,
        ),
        ColumnSpec(
            name="location_id",
            kind=FieldKind.STRING,
            required=True,
            examples=("LOC-A", "LOC-B"),
            help="Where it shoots. Must appear in the locations sheet.",
            width=13,
        ),
        ColumnSpec(
            name="cast_ids",
            kind=FieldKind.ID_LIST,
            required=False,
            examples=("P01;P02", "P02"),
            help="Cast in the scene, separated by ; — must be people with role CAST.",
            width=18,
        ),
        ColumnSpec(
            name="crew_ids",
            kind=FieldKind.ID_LIST,
            required=False,
            examples=("", "P90"),
            help="Specific crew the scene needs, separated by ; — must have role CREW.",
            width=18,
        ),
        ColumnSpec(
            name="equipment_ids",
            kind=FieldKind.ID_LIST,
            required=False,
            examples=("CAM-01", "CAM-01;CRANE-01"),
            help="Equipment the scene needs, separated by ;",
            width=20,
        ),
        ColumnSpec(
            name="vfx_plate",
            kind=FieldKind.BOOL,
            required=False,
            default=False,
            examples=("FALSE", "TRUE"),
            help="TRUE if the scene captures a VFX plate. Blank means FALSE.",
            width=11,
        ),
        ColumnSpec(
            name="prerequisite_scene_ids",
            kind=FieldKind.ID_LIST,
            required=False,
            examples=("", "SC-001"),
            help="Scenes that must shoot on an earlier date, separated by ;",
            width=24,
        ),
    ),
)

# ---------------------------------------------------------------------------
# people
# ---------------------------------------------------------------------------

_PEOPLE = SheetSpec(
    name=SHEET_PEOPLE,
    required=True,
    primary_key="person_id",
    max_rows=1000,
    help=(
        "Cast and crew. Put day-out-of-days blocks in the availability sheet, not "
        "here — one row per person, however many windows they have."
    ),
    columns=(
        ColumnSpec(
            name="person_id",
            kind=FieldKind.STRING,
            required=True,
            examples=("P01", "P02"),
            help="Unique id. Must not repeat.",
            width=11,
        ),
        ColumnSpec(
            name="name",
            kind=FieldKind.STRING,
            required=True,
            examples=("Arun Menon", "Meera Nair"),
            help="Their name.",
            width=22,
        ),
        ColumnSpec(
            name="role",
            kind=FieldKind.ENUM,
            required=True,
            enum=("CAST", "CREW"),
            examples=("CAST", "CAST"),
            help="CAST or CREW. A scene's cast_ids may only name CAST.",
            width=9,
        ),
        ColumnSpec(
            name="character",
            kind=FieldKind.STRING,
            required=False,
            examples=("Arun", "Meera"),
            help="Character name. Leave blank for crew.",
            width=18,
        ),
        ColumnSpec(
            name="daily_rate",
            kind=FieldKind.DECIMAL,
            required=False,
            default="0",
            examples=("2400.00", "2600.00"),
            help="Day rate in the production's currency. Blank means zero.",
            width=13,
        ),
    ),
)

# ---------------------------------------------------------------------------
# locations
# ---------------------------------------------------------------------------

_LOCATIONS = SheetSpec(
    name=SHEET_LOCATIONS,
    required=True,
    primary_key="location_id",
    max_rows=1000,
    help=(
        "Where you shoot. permit_start_time and permit_end_time describe a window "
        "that repeats every day — leave both blank for a location with no "
        "restriction. One-off closures go in the availability sheet."
    ),
    columns=(
        ColumnSpec(
            name="location_id",
            kind=FieldKind.STRING,
            required=True,
            examples=("LOC-A", "LOC-B"),
            help="Unique id. Must not repeat.",
            width=13,
        ),
        ColumnSpec(
            name="name",
            kind=FieldKind.STRING,
            required=True,
            examples=("Studio Stage 4", "Marine Drive"),
            help="What the crew calls it.",
            width=24,
        ),
        ColumnSpec(
            name="kind",
            kind=FieldKind.STRING,
            required=False,
            examples=("studio", "street"),
            help="Free text — studio, street, practical, standing set.",
            width=14,
        ),
        ColumnSpec(
            name="day_rate",
            kind=FieldKind.DECIMAL,
            required=False,
            default="0",
            examples=("3000.00", "4200.00"),
            help="Hire cost per shooting day. Blank means zero.",
            width=13,
        ),
        ColumnSpec(
            name="permit_start_time",
            kind=FieldKind.TIME,
            required=False,
            examples=("", "06:00"),
            help="Earliest you may shoot, every day. Blank with the end time means no limit.",
            width=18,
        ),
        ColumnSpec(
            name="permit_end_time",
            kind=FieldKind.TIME,
            required=False,
            examples=("", "20:00"),
            help="Latest you may shoot. Before the start time means the window crosses midnight.",
            width=17,
        ),
        ColumnSpec(
            name="supports_int_ext",
            kind=FieldKind.ID_LIST,
            required=True,
            examples=("INT", "EXT"),
            help="Which of INT / EXT this location can play, separated by ;",
            width=18,
        ),
        ColumnSpec(
            name="supports_time_of_day",
            kind=FieldKind.ID_LIST,
            required=True,
            examples=("DAY;NIGHT", "DAY;DUSK"),
            help="Which of DAY / NIGHT / DAWN / DUSK it can play, separated by ;",
            width=22,
        ),
        # Call-sheet reference data. Optional, because a 1st AD's board rarely
        # carries it — but without it an imported production renders a call
        # sheet with em-dashes where the address and the hospital should be,
        # and the call sheet is the artifact that proves state propagated.
        # config/logistics.yaml is keyed by the demo fixture's location ids and
        # can never know about a stranger's locations, so the workbook has to
        # be able to say.
        ColumnSpec(
            name="address",
            kind=FieldKind.STRING,
            required=False,
            examples=("", "Princess Street, Fort Kochi, Ernakulam 682001"),
            help="Street address, printed on the call sheet.",
            width=34,
        ),
        ColumnSpec(
            name="parking_note",
            kind=FieldKind.STRING,
            required=False,
            examples=("", "Basecamp at Parade Ground; no unit vehicles past the barricade"),
            help="Where the unit parks. Free text.",
            width=34,
        ),
        ColumnSpec(
            name="nearest_hospital",
            kind=FieldKind.STRING,
            required=False,
            examples=("", "Fort Kochi Government Hospital - 0484 2215055"),
            help="Nearest A&E, with a telephone number. Printed on every call sheet.",
            width=34,
        ),
    ),
)

# ---------------------------------------------------------------------------
# equipment
# ---------------------------------------------------------------------------

_EQUIPMENT = SheetSpec(
    name=SHEET_EQUIPMENT,
    required=True,
    primary_key="equipment_id",
    max_rows=1000,
    help=(
        "Cameras, cranes, anything with a rental window. Put the rental dates in "
        "the availability sheet as AVAILABLE rows; equipment with no AVAILABLE row "
        "is treated as yours for the whole shoot."
    ),
    columns=(
        ColumnSpec(
            name="equipment_id",
            kind=FieldKind.STRING,
            required=True,
            examples=("CAM-01", "CRANE-01"),
            help="Unique id. Must not repeat.",
            width=14,
        ),
        ColumnSpec(
            name="name",
            kind=FieldKind.STRING,
            required=True,
            examples=("Alexa 35", "Technocrane 22"),
            help="What it is.",
            width=22,
        ),
        ColumnSpec(
            name="kind",
            kind=FieldKind.STRING,
            required=False,
            examples=("camera", "crane"),
            help="Free text — camera, crane, lighting.",
            width=14,
        ),
        ColumnSpec(
            name="daily_rate",
            kind=FieldKind.DECIMAL,
            required=False,
            default="0",
            examples=("950.00", "1800.00"),
            help="Hire cost per day. Blank means zero.",
            width=13,
        ),
    ),
)

# ---------------------------------------------------------------------------
# availability
# ---------------------------------------------------------------------------

#: The only meaningful (subject_type, window_kind) pairs. Anything else is E014.
#:
#: The asymmetry is deliberate and is the thing people get backwards: a person's
#: rows say when they *cannot* work, equipment's rows say when it *can*, and a
#: location's rows are one-off closures on top of its daily permit.
AVAILABILITY_COMBINATIONS: dict[str, str] = {
    "PERSON": "UNAVAILABLE",
    "EQUIPMENT": "AVAILABLE",
    "LOCATION": "CLOSED",
}

_AVAILABILITY = SheetSpec(
    name=SHEET_AVAILABILITY,
    required=False,
    primary_key=None,
    max_rows=5000,
    help=(
        "The day-out-of-days, the rental calendar and one-off location closures, in "
        "one place. One row per window. PERSON rows say when somebody CANNOT work. "
        "EQUIPMENT rows say when a rental IS available — outside those dates the "
        "item does not exist. LOCATION rows are CLOSED days that override the "
        "daily permit."
    ),
    columns=(
        ColumnSpec(
            name="subject_type",
            kind=FieldKind.ENUM,
            required=True,
            enum=("PERSON", "EQUIPMENT", "LOCATION"),
            examples=("PERSON", "EQUIPMENT"),
            help="What the window is about.",
            width=14,
        ),
        ColumnSpec(
            name="subject_id",
            kind=FieldKind.STRING,
            required=True,
            examples=("P02", "CRANE-01"),
            help="The id, from the matching sheet.",
            width=13,
        ),
        ColumnSpec(
            name="window_kind",
            kind=FieldKind.ENUM,
            required=True,
            enum=("UNAVAILABLE", "AVAILABLE", "CLOSED"),
            examples=("UNAVAILABLE", "AVAILABLE"),
            help="UNAVAILABLE for people, AVAILABLE for equipment, CLOSED for locations.",
            width=14,
        ),
        ColumnSpec(
            name="from_date",
            kind=FieldKind.DATE,
            required=True,
            examples=("2026-11-08", "2026-11-03"),
            help="First day of the window.",
            width=13,
        ),
        ColumnSpec(
            name="to_date",
            kind=FieldKind.DATE,
            required=True,
            examples=("2026-11-10", "2026-11-06"),
            help="Last day of the window, inclusive.",
            width=13,
        ),
        ColumnSpec(
            name="note",
            kind=FieldKind.STRING,
            required=False,
            examples=("Second unit on another film", "Rental returns Friday"),
            help="Why. Shown back to you in the review screen.",
            width=30,
        ),
    ),
)

# ---------------------------------------------------------------------------
# schedule
# ---------------------------------------------------------------------------

_SCHEDULE = SheetSpec(
    name=SHEET_SCHEDULE,
    required=True,
    primary_key=None,
    max_rows=500,
    help=(
        "Your board: one row per shooting day. Times are local wall-clock. For an "
        "overnight, either set wrap_next_day to TRUE or just put a wrap earlier "
        "than the call and PRI will infer it and tell you it did."
    ),
    columns=(
        ColumnSpec(
            name="date",
            kind=FieldKind.DATE,
            required=True,
            examples=("2026-11-02", "2026-11-03"),
            help="The shooting date. Must be inside the shoot window.",
            width=13,
        ),
        ColumnSpec(
            name="call_time",
            kind=FieldKind.TIME,
            required=True,
            examples=("08:00", "18:00"),
            help="Crew call, local time.",
            width=11,
        ),
        ColumnSpec(
            name="wrap_time",
            kind=FieldKind.TIME,
            required=True,
            examples=("19:00", "04:00"),
            help="Expected wrap, local time.",
            width=11,
        ),
        ColumnSpec(
            name="wrap_next_day",
            kind=FieldKind.BOOL,
            required=False,
            examples=("FALSE", "TRUE"),
            help="TRUE when the wrap falls after midnight. Blank lets PRI infer it.",
            width=15,
        ),
        ColumnSpec(
            name="location_id",
            kind=FieldKind.STRING,
            required=True,
            examples=("LOC-A", "LOC-B"),
            help="Where the unit is based that day.",
            width=13,
        ),
        ColumnSpec(
            # Not required: a reserve day, a travel day or a company move is a
            # real row on a real board with no scenes on it. An empty cell is
            # W004, a warning, not an error — see the warning list.
            name="scene_ids",
            kind=FieldKind.ID_LIST,
            required=False,
            examples=("SC-001", "SC-002"),
            help=(
                "Scenes shooting that day, in shooting order, separated by ; "
                "— blank for a reserve day."
            ),
            width=26,
        ),
        ColumnSpec(
            name="unit",
            kind=FieldKind.STRING,
            required=False,
            default="MAIN",
            examples=("MAIN", "MAIN"),
            help="Unit name. Blank means MAIN. Use SECOND for a second unit.",
            width=10,
        ),
        # Optional with an inferred default, so every existing board still
        # imports and TEMPLATE_VERSION does not move.
        #
        # Making scene_ids optional was right — travel days and company moves
        # are real rows — but it left the generators unable to tell "empty
        # because nothing is booked" from "empty because the unit is on a bus".
        # Without this column PRI will happily propose shooting on a travel day.
        ColumnSpec(
            name="day_kind",
            kind=FieldKind.ENUM,
            required=False,
            enum=("SHOOT", "TRAVEL", "COMPANY_MOVE", "HOLD", "RESERVE"),
            examples=("SHOOT", "SHOOT"),
            help=(
                "What the day is for. Blank means SHOOT when the day has scenes "
                "and RESERVE when it does not. Only RESERVE days may be filled "
                "by a recovery plan."
            ),
            width=14,
        ),
    ),
)


#: Every sheet, in template order. The order is the order a 1st AD fills them
#: in: describe the production, then what you are shooting, then who and where
#: and with what, then when things are unavailable, then the board itself.
WORKBOOK_SPEC: tuple[SheetSpec, ...] = (
    _PRODUCTION,
    _SCENES,
    _PEOPLE,
    _LOCATIONS,
    _EQUIPMENT,
    _AVAILABILITY,
    _SCHEDULE,
)


def sheet_by_name(name: str) -> SheetSpec | None:
    """Return the spec for a sheet, or ``None`` if PRI does not know it.

    Inputs:
        name: Sheet name as it appears in the workbook.

    Outputs:
        The :class:`SheetSpec`, or ``None`` for a sheet the user added
        themselves — which is ignored rather than rejected.
    """
    return next((sheet for sheet in WORKBOOK_SPEC if sheet.name == name), None)
