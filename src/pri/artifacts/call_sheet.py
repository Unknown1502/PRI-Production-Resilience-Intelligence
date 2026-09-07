"""Call-sheet PDF generation.

A call sheet is how a schedule reaches the ninety people who have to act on it.
Regenerating one is therefore not a nice-to-have after a plan executes — it is
the point of executing the plan, and verification check V6 refuses to pass
without it.

This module renders something a first AD would recognise: header block with the
day number and the weather, the location and its hospital, the scene strip, the
cast table with pickup and on-set times, and the equipment list.  The footer
carries the state version and the first twelve characters of its digest, so a
sheet found on a table can be traced back to exactly the state that produced it.

Layout data that is not part of ``ProductionState`` — addresses, parking,
hospitals, sunrise — comes from ``config/logistics.yaml``.  None of it affects a
constraint, so versioning it would add churn to the state chain for nothing.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from io import BytesIO
from typing import TYPE_CHECKING, Any

import yaml
from pydantic import BaseModel
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from pri.paths import config_path, project_root
from pri.persistence.serialization import state_to_snapshot

if TYPE_CHECKING:
    from pathlib import Path

    from pri.domain.models import Person, ProductionState, Scene, ShootingDay

__all__ = [
    "ARTIFACT_KIND",
    "CallSheetError",
    "GeneratedArtifact",
    "artifact_path",
    "generate_call_sheet",
    "generate_call_sheet_delta",
    "regenerate_all",
]

#: The artifact kind verification check V6 looks for.
ARTIFACT_KIND = "call_sheet"

_DEFAULT_LOGISTICS_PATH = config_path("logistics.yaml")
_DEFAULT_OUTPUT_ROOT = project_root() / "artifacts" / "call_sheets"

_INK = colors.HexColor("#111318")
_MUTED = colors.HexColor("#5b6472")
_RULE = colors.HexColor("#c9ced8")
_BAND = colors.HexColor("#eef1f6")
_CHANGED = colors.HexColor("#fde2c4")


class CallSheetError(ValueError):
    """Raised when a call sheet is requested for a day that is not scheduled."""


class GeneratedArtifact(BaseModel, frozen=True):
    """A rendered artifact and where it was written.

    Inputs:
        kind:    Artifact type; ``"call_sheet"`` here.
        day:     The shooting day the sheet covers.
        version: The state version it was generated from.
        path:    Absolute path on disk.
        digest:  Snapshot digest of the state, as printed in the footer.
    """

    kind: str
    day: date
    version: int
    path: str
    digest: str


# ---------------------------------------------------------------------------
# Reference data
# ---------------------------------------------------------------------------


def _load_logistics(path: Path | None = None) -> dict[str, Any]:
    """Load ``config/logistics.yaml``, or return empty defaults if absent.

    A missing logistics file degrades the sheet to em-dashes rather than
    failing the execution pipeline: a call sheet without a parking note is
    still a call sheet, and V6 only asks that one exists.
    """
    resolved = path if path is not None else _DEFAULT_LOGISTICS_PATH
    if not resolved.exists():
        return {"defaults": {}, "locations": {}, "cast_timings": {}}
    with resolved.open(encoding="utf-8") as fh:
        loaded: dict[str, Any] = yaml.safe_load(fh) or {}
    return loaded


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_call_sheet(
    state: ProductionState,
    day_date: date,
    *,
    logistics_path: Path | None = None,
    changed_scene_ids: frozenset[str] = frozenset(),
) -> bytes:
    """Render one day's call sheet as a PDF.

    Inputs:
        state:             The state to render from.
        day_date:          Which shooting day.
        logistics_path:    Override ``config/logistics.yaml``.
        changed_scene_ids: Scenes to flag ``CHANGED`` — used by
                           :func:`generate_call_sheet_delta` to show a producer
                           what moved since the last version.

    Outputs:
        The PDF as bytes.

    Failure modes:
        Raises :class:`CallSheetError` if ``day_date`` is not in the schedule.
    """
    day = next((d for d in state.schedule.days if d.date == day_date), None)
    if day is None:
        raise CallSheetError(f"{day_date} is not a shooting day for {state.production.id}")

    logistics = _load_logistics(logistics_path)
    _, digest = state_to_snapshot(state)

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=16 * mm,
        rightMargin=16 * mm,
        topMargin=14 * mm,
        bottomMargin=16 * mm,
        title=f"{state.production.title} - Call Sheet {day_date.isoformat()}",
        author="PRI",
    )

    story: list[Any] = []
    story.extend(_header_block(state, day, logistics))
    story.append(Spacer(1, 5 * mm))
    story.extend(_times_block(state, day))
    story.append(Spacer(1, 5 * mm))
    story.extend(_location_block(state, day, logistics))
    story.append(Spacer(1, 5 * mm))
    story.extend(_scenes_block(state, day, changed_scene_ids))
    story.append(Spacer(1, 5 * mm))
    story.extend(_cast_block(state, day, logistics))
    story.append(Spacer(1, 5 * mm))
    story.extend(_equipment_block(state, day))

    doc.build(
        story,
        onFirstPage=lambda canvas, _doc: _footer(canvas, state.version, digest),
        onLaterPages=lambda canvas, _doc: _footer(canvas, state.version, digest),
    )
    return buffer.getvalue()


def generate_call_sheet_delta(
    old_state: ProductionState,
    new_state: ProductionState,
    day_date: date,
    *,
    logistics_path: Path | None = None,
) -> bytes:
    """Render a day's call sheet with every changed row flagged.

    "Changed" means the scene was not on this day before, or it was and its
    position moved.  That is what a crew member scanning the sheet needs: not a
    diff of the whole production, just "these lines are new to your day".

    Inputs:
        old_state:      The previous version.
        new_state:      The version being published.
        day_date:       Which day.
        logistics_path: Override ``config/logistics.yaml``.

    Outputs:
        The PDF as bytes.

    Failure modes:
        Raises :class:`CallSheetError` if the day is not in ``new_state``.
    """
    old_day = next((d for d in old_state.schedule.days if d.date == day_date), None)
    new_day = next((d for d in new_state.schedule.days if d.date == day_date), None)
    if new_day is None:
        raise CallSheetError(f"{day_date} is not a shooting day in the new state")

    before = list(old_day.scene_ids) if old_day else []
    changed = {
        sid
        for index, sid in enumerate(new_day.scene_ids)
        if index >= len(before) or before[index] != sid
    }
    return generate_call_sheet(
        new_state,
        day_date,
        logistics_path=logistics_path,
        changed_scene_ids=frozenset(changed),
    )


def artifact_path(state: ProductionState, day_date: date, root: Path | None = None) -> Path:
    """Return the on-disk path a day's call sheet is written to."""
    base = root if root is not None else _DEFAULT_OUTPUT_ROOT
    return base / state.production.id / f"v{state.version}" / f"{day_date.isoformat()}.pdf"


def regenerate_all(
    state: ProductionState,
    *,
    output_root: Path | None = None,
    logistics_path: Path | None = None,
    only_days: frozenset[date] | None = None,
) -> list[GeneratedArtifact]:
    """Render and write a call sheet for every shooting day with work on it.

    Called by ``TransitionService`` step 6.  Days with no scenes are skipped —
    a reserve day nobody is called to does not need a sheet.

    Inputs:
        state:          The committed state.
        output_root:    Override ``artifacts/call_sheets/``.
        logistics_path: Override ``config/logistics.yaml``.
        only_days:      Restrict to these dates; ``None`` renders all of them.

    Outputs:
        One :class:`GeneratedArtifact` per written file, in date order.

    Failure modes:
        Raises ``OSError`` if the output directory cannot be created or written.
    """
    _, digest = state_to_snapshot(state)
    written: list[GeneratedArtifact] = []

    for day in sorted(state.schedule.days, key=lambda d: d.date):
        if not day.scene_ids:
            continue
        if only_days is not None and day.date not in only_days:
            continue
        pdf = generate_call_sheet(state, day.date, logistics_path=logistics_path)
        target = artifact_path(state, day.date, output_root)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(pdf)
        written.append(
            GeneratedArtifact(
                kind=ARTIFACT_KIND,
                day=day.date,
                version=state.version,
                path=str(target),
                digest=digest,
            )
        )
    return written


# ---------------------------------------------------------------------------
# Blocks
# ---------------------------------------------------------------------------


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "title",
            parent=base["Title"],
            fontName="Helvetica-Bold",
            fontSize=16,
            leading=19,
            textColor=_INK,
            alignment=TA_CENTER,
            spaceAfter=0,
        ),
        "subtitle": ParagraphStyle(
            "subtitle",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=9,
            leading=12,
            textColor=_MUTED,
            alignment=TA_CENTER,
        ),
        "section": ParagraphStyle(
            "section",
            parent=base["Normal"],
            fontName="Helvetica-Bold",
            fontSize=9,
            leading=11,
            textColor=_INK,
            spaceAfter=2,
        ),
        "cell": ParagraphStyle(
            "cell",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=8,
            leading=10,
            textColor=_INK,
        ),
    }


def _hhmm(value: datetime) -> str:
    """Render a datetime as a bare 24-hour clock time."""
    return value.strftime("%H:%M")


def _day_number(state: ProductionState, day: ShootingDay) -> tuple[int, int]:
    """Return ``(day N, of M)`` counting only days that carry scenes."""
    working = [d for d in sorted(state.schedule.days, key=lambda d: d.date) if d.scene_ids]
    total = len(working)
    for index, candidate in enumerate(working, start=1):
        if candidate.date == day.date:
            return index, total
    return 0, total


def _header_block(state: ProductionState, day: ShootingDay, logistics: dict[str, Any]) -> list[Any]:
    styles = _styles()
    defaults = logistics.get("defaults", {})
    number, total = _day_number(state, day)

    weather = defaults.get("weather", "-")
    sunrise = defaults.get("sunrise", "-")
    sunset = defaults.get("sunset", "-")

    return [
        Paragraph(state.production.title.upper(), styles["title"]),
        Paragraph(
            f"CALL SHEET &nbsp;&middot;&nbsp; Day {number} of {total} "
            f"&nbsp;&middot;&nbsp; {day.date.strftime('%A %d %B %Y')} "
            f"&nbsp;&middot;&nbsp; Unit {day.unit}",
            styles["subtitle"],
        ),
        Spacer(1, 2 * mm),
        Paragraph(
            f"Weather: {weather} &nbsp;&middot;&nbsp; "
            f"Sunrise {sunrise} &nbsp;&middot;&nbsp; Sunset {sunset}",
            styles["subtitle"],
        ),
    ]


def _times_block(state: ProductionState, day: ShootingDay) -> list[Any]:
    """Crew call, first shot and estimated wrap, as three big numbers."""
    styles = _styles()
    setup = _setup_allowance(state)
    first_shot = day.call_time + timedelta(minutes=setup)

    data = [
        [
            Paragraph("<b>CREW CALL</b>", styles["cell"]),
            Paragraph("<b>FIRST SHOT</b>", styles["cell"]),
            Paragraph("<b>EST. WRAP</b>", styles["cell"]),
        ],
        [
            Paragraph(f"<font size=13><b>{_hhmm(day.call_time)}</b></font>", styles["cell"]),
            Paragraph(f"<font size=13><b>{_hhmm(first_shot)}</b></font>", styles["cell"]),
            Paragraph(f"<font size=13><b>{_hhmm(day.wrap_time)}</b></font>", styles["cell"]),
        ],
    ]
    table = Table(data, colWidths=[59 * mm, 59 * mm, 60 * mm])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), _BAND),
                ("BOX", (0, 0), (-1, -1), 0.6, _RULE),
                ("INNERGRID", (0, 0), (-1, -1), 0.4, _RULE),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return [table]


def _setup_allowance(state: ProductionState) -> int:
    """Minutes between crew call and first shot, from the scoring config."""
    from pri.engine.simulation.candidates import load_scoring_config

    try:
        config = load_scoring_config()
    except (FileNotFoundError, yaml.YAMLError):
        return 0
    return int(config.get("capacity", {}).get("setup_minutes_per_scene", 0))


def _location_block(
    state: ProductionState, day: ShootingDay, logistics: dict[str, Any]
) -> list[Any]:
    styles = _styles()
    location = next((loc for loc in state.locations if loc.id == day.location_id), None)
    entry = logistics.get("locations", {}).get(day.location_id, {})
    defaults = logistics.get("defaults", {})

    name = location.name if location else day.location_id

    def resolve(own: str, config_key: str, fallback: str = "") -> str:
        """The production's own data first, then the deployment's, then a dash.

        ``config/logistics.yaml`` is keyed by the demo fixture's location ids,
        so for any imported production it has nothing to say. The workbook's
        own address / parking_note / nearest_hospital columns win when present,
        which is what stops a stranger's call sheet from being half blank.
        """
        return own.strip() or str(entry.get(config_key) or fallback or "-")

    rows = [
        ["LOCATION", Paragraph(f"<b>{name}</b> ({day.location_id})", styles["cell"])],
        [
            "ADDRESS",
            Paragraph(resolve(location.address if location else "", "address"), styles["cell"]),
        ],
        [
            "PARKING",
            Paragraph(
                resolve(location.parking_note if location else "", "parking"), styles["cell"]
            ),
        ],
        [
            "NEAREST HOSPITAL",
            Paragraph(
                resolve(
                    location.nearest_hospital if location else "",
                    "hospital",
                    str(defaults.get("hospital") or ""),
                ),
                styles["cell"],
            ),
        ],
    ]
    table = Table(rows, colWidths=[38 * mm, 140 * mm])
    table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (0, -1), 7),
                ("TEXTCOLOR", (0, 0), (0, -1), _MUTED),
                ("BOX", (0, 0), (-1, -1), 0.6, _RULE),
                ("INNERGRID", (0, 0), (-1, -1), 0.3, _RULE),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    return [table]


def _pages(scene: Scene) -> str:
    """Approximate script pages in eighths, the way a call sheet writes them.

    One page is taken as one minute per eighth — the standard rough
    equivalence — so a 210-minute scene reads as 26 2/8 pages.
    """
    eighths = max(1, round(scene.estimated_minutes / 1))
    whole, remainder = divmod(eighths, 8)
    if whole and remainder:
        return f"{whole} {remainder}/8"
    if whole:
        return str(whole)
    return f"{remainder}/8"


def _scenes_block(state: ProductionState, day: ShootingDay, changed: frozenset[str]) -> list[Any]:
    styles = _styles()
    scenes_by_id = {s.id: s for s in state.scenes}

    header = ["SC.", "I/E", "D/N", "DESCRIPTION", "PAGES", "CAST", "MINS"]
    rows: list[list[Any]] = [[Paragraph(f"<b>{h}</b>", styles["cell"]) for h in header]]
    changed_rows: list[int] = []

    for index, sid in enumerate(day.scene_ids, start=1):
        scene = scenes_by_id.get(sid)
        if scene is None:
            continue
        if sid in changed:
            changed_rows.append(index)
        description = scene.description
        if sid in changed:
            description = f"{description} <b>[CHANGED]</b>"
        rows.append(
            [
                Paragraph(str(scene.number), styles["cell"]),
                Paragraph(scene.int_ext, styles["cell"]),
                Paragraph(scene.time_of_day[0], styles["cell"]),
                Paragraph(description, styles["cell"]),
                Paragraph(_pages(scene), styles["cell"]),
                Paragraph(", ".join(scene.cast_ids), styles["cell"]),
                Paragraph(str(scene.estimated_minutes), styles["cell"]),
            ]
        )

    if len(rows) == 1:
        rows.append(
            [Paragraph("<i>No scenes scheduled.</i>", styles["cell"]), "", "", "", "", "", ""]
        )

    table = Table(
        rows,
        colWidths=[11 * mm, 11 * mm, 10 * mm, 76 * mm, 17 * mm, 34 * mm, 19 * mm],
        repeatRows=1,
    )
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), _BAND),
        ("BOX", (0, 0), (-1, -1), 0.6, _RULE),
        ("INNERGRID", (0, 0), (-1, -1), 0.3, _RULE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]
    style.extend(("BACKGROUND", (0, r), (-1, r), _CHANGED) for r in changed_rows)
    table.setStyle(TableStyle(style))

    return [Paragraph("SCENES", styles["section"]), table]


def _cast_block(state: ProductionState, day: ShootingDay, logistics: dict[str, Any]) -> list[Any]:
    styles = _styles()
    timings = logistics.get("cast_timings", {})
    pickup_offset = int(timings.get("pickup_minutes_before_call", 0))
    on_set_offset = int(timings.get("on_set_minutes_after_call", 0))

    scenes_by_id = {s.id: s for s in state.scenes}
    people_by_id: dict[str, Person] = {p.id: p for p in state.people}

    called: list[str] = []
    for sid in day.scene_ids:
        scene = scenes_by_id.get(sid)
        if scene is None:
            continue
        for pid in scene.cast_ids:
            if pid not in called:
                called.append(pid)

    header = ["ID", "CHARACTER", "PICKUP", "CALL", "ON SET"]
    rows: list[list[Any]] = [[Paragraph(f"<b>{h}</b>", styles["cell"]) for h in header]]
    for pid in called:
        person = people_by_id.get(pid)
        rows.append(
            [
                Paragraph(pid, styles["cell"]),
                Paragraph((person.character or person.name) if person else "-", styles["cell"]),
                Paragraph(_hhmm(day.call_time - timedelta(minutes=pickup_offset)), styles["cell"]),
                Paragraph(_hhmm(day.call_time), styles["cell"]),
                Paragraph(_hhmm(day.call_time + timedelta(minutes=on_set_offset)), styles["cell"]),
            ]
        )
    if len(rows) == 1:
        rows.append([Paragraph("<i>No cast called.</i>", styles["cell"]), "", "", "", ""])

    table = Table(rows, colWidths=[18 * mm, 76 * mm, 28 * mm, 28 * mm, 28 * mm], repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), _BAND),
                ("BOX", (0, 0), (-1, -1), 0.6, _RULE),
                ("INNERGRID", (0, 0), (-1, -1), 0.3, _RULE),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    return [KeepTogether([Paragraph("CAST", styles["section"]), table])]


def _equipment_block(state: ProductionState, day: ShootingDay) -> list[Any]:
    styles = _styles()
    scenes_by_id = {s.id: s for s in state.scenes}
    equipment_by_id = {e.id: e for e in state.equipment}

    needed: list[str] = []
    for sid in day.scene_ids:
        scene = scenes_by_id.get(sid)
        if scene is None:
            continue
        for eid in scene.equipment_ids:
            if eid not in needed:
                needed.append(eid)

    if not needed:
        listing = "<i>None called.</i>"
    else:
        listing = " &nbsp;&middot;&nbsp; ".join(
            f"{eid} {equipment_by_id[eid].name}" if eid in equipment_by_id else eid
            for eid in needed
        )
    return [
        KeepTogether(
            [Paragraph("EQUIPMENT", styles["section"]), Paragraph(listing, styles["cell"])]
        )
    ]


def _footer(canvas: Any, version: int, digest: str) -> None:
    """Stamp the provenance line every sheet is traced back by."""
    canvas.saveState()
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(_MUTED)
    canvas.drawCentredString(
        A4[0] / 2.0,
        10 * mm,
        f"Generated by PRI - state version {version} - {digest[:12]}",
    )
    canvas.restoreState()
