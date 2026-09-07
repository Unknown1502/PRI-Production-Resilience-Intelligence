"""One test per blocking error code, E001 to E018.

Each starts from the valid baseline and breaks exactly one thing, then asserts
the code, the sheet and — where the defect lives in a row — the row number as
Excel displays it. The row number is asserted deliberately: a user reading the
error scrolls to that number, and an off-by-one wastes their afternoon.
"""

from __future__ import annotations

import io
import zipfile
from datetime import date, time
from typing import TYPE_CHECKING

import pytest

from pri.importer import parse_workbook
from tests.importer.conftest import SheetRows, build_xlsx

if TYPE_CHECKING:
    from pri.importer.errors import ImportIssue


def codes(issues: tuple[ImportIssue, ...]) -> list[str]:
    return sorted({issue.code for issue in issues})


def only(issues: tuple[ImportIssue, ...], code: str) -> ImportIssue:
    """The single issue with this code, asserting there is exactly one."""
    matching = [issue for issue in issues if issue.code == code]
    assert len(matching) == 1, f"expected one {code}, got {[i.code for i in issues]}"
    return matching[0]


def test_the_baseline_itself_is_clean(baseline: dict[str, SheetRows]) -> None:
    """Everything below is a mutation of this, so it has to start valid."""
    result = parse_workbook(build_xlsx(baseline), "baseline.xlsx")
    assert result.errors == ()
    assert result.state is not None
    assert result.state.production.id == "film-test"


class TestStructurePhase:
    def test_e001_missing_required_sheet(self, baseline: dict[str, SheetRows]) -> None:
        result = parse_workbook(
            build_xlsx(baseline, omit_sheets=frozenset({"locations"})), "x.xlsx"
        )
        issue = only(result.errors, "E001")
        assert issue.sheet == "locations"
        assert "locations" in issue.fix_hint

    def test_e002_missing_required_column(self, baseline: dict[str, SheetRows]) -> None:
        result = parse_workbook(
            build_xlsx(baseline, omit_columns={"scenes": frozenset({"int_ext"})}),
            "x.xlsx",
        )
        issue = only(result.errors, "E002")
        assert issue.sheet == "scenes"
        assert issue.column == "int_ext"
        assert issue.row == 1

    def test_e003_unparseable_value(self, baseline: dict[str, SheetRows]) -> None:
        baseline["scenes"][1]["estimated_minutes"] = "about an hour"
        result = parse_workbook(build_xlsx(baseline), "x.xlsx")
        issue = only(result.errors, "E003")
        assert issue.sheet == "scenes"
        assert issue.row == 3, "row 3 is the second data row, header included"
        assert issue.column == "estimated_minutes"
        assert issue.offending_value == "about an hour"

    def test_e003_required_cell_left_blank(self, baseline: dict[str, SheetRows]) -> None:
        baseline["people"][0]["name"] = None
        result = parse_workbook(build_xlsx(baseline), "x.xlsx")
        issue = only(result.errors, "E003")
        assert (issue.sheet, issue.row, issue.column) == ("people", 2, "name")

    def test_e004_value_outside_the_enum(self, baseline: dict[str, SheetRows]) -> None:
        baseline["scenes"][0]["time_of_day"] = "AFTERNOON"
        result = parse_workbook(build_xlsx(baseline), "x.xlsx")
        issue = only(result.errors, "E004")
        assert (issue.sheet, issue.row, issue.column) == ("scenes", 2, "time_of_day")
        assert "DAY" in issue.fix_hint

    def test_e012_production_sheet_with_two_rows(self, baseline: dict[str, SheetRows]) -> None:
        baseline["production"].append(dict(baseline["production"][0]))
        result = parse_workbook(build_xlsx(baseline), "x.xlsx")
        issue = only(result.errors, "E012")
        assert issue.sheet == "production"
        assert "exactly one" in issue.message

    def test_e012_workbook_with_no_known_sheets(self) -> None:
        result = parse_workbook(
            build_xlsx({}, extra_sheets={"budget": [["a", "b"], [1, 2]]}), "x.xlsx"
        )
        assert "E012" in codes(result.errors)

    def test_e016_unsupported_file_type(self) -> None:
        result = parse_workbook(b"\x89PNG\r\n\x1a\n" + bytes(64), "logo.png")
        issue = only(result.errors, "E016")
        assert ".xlsx" in issue.fix_hint

    def test_e016_a_lone_csv_is_refused(self) -> None:
        result = parse_workbook(b"scene_id,number\nS1,1\n", "scenes.csv")
        issue = only(result.errors, "E016")
        assert "single CSV" in issue.message

    def test_e017_sheet_over_its_row_cap(self, baseline: dict[str, SheetRows]) -> None:
        template = baseline["schedule"][0]
        baseline["schedule"] = [
            {**template, "date": date(2027, 6, 1), "scene_ids": ""} for _ in range(501)
        ]
        result = parse_workbook(build_xlsx(baseline), "x.xlsx")
        issue = only(result.errors, "E017")
        assert issue.sheet == "schedule"
        assert "500" in issue.message


class TestIdentityPhase:
    def test_e005_duplicate_primary_key(self, baseline: dict[str, SheetRows]) -> None:
        baseline["scenes"][1]["scene_id"] = "S1"
        result = parse_workbook(build_xlsx(baseline), "x.xlsx")
        issue = only(result.errors, "E005")
        assert issue.sheet == "scenes"
        assert issue.row == 3
        assert "rows 2 and 3" in issue.message


class TestReferencePhase:
    def test_e006_dangling_location_reference(self, baseline: dict[str, SheetRows]) -> None:
        baseline["scenes"][0]["location_id"] = "L-99"
        result = parse_workbook(build_xlsx(baseline), "x.xlsx")
        issue = only(result.errors, "E006")
        assert (issue.sheet, issue.row, issue.column) == ("scenes", 2, "location_id")
        assert "L-99" in issue.message
        assert "locations" in issue.fix_hint

    def test_e006_dangling_cast_id_names_the_row_and_the_fix(
        self, baseline: dict[str, SheetRows]
    ) -> None:
        """Acceptance criterion 3: a non-developer must be able to act on this."""
        baseline["scenes"][2]["cast_ids"] = "P1;P-404"
        result = parse_workbook(build_xlsx(baseline), "x.xlsx")
        issue = only(result.errors, "E006")

        assert issue.sheet == "scenes"
        assert issue.row == 4
        assert issue.column == "cast_ids"
        assert issue.offending_value == "P-404"
        assert "P-404" in issue.message
        assert "people" in issue.fix_hint
        assert "row 4" in issue.fix_hint

    def test_e008_cast_column_names_a_crew_member(self, baseline: dict[str, SheetRows]) -> None:
        baseline["scenes"][0]["cast_ids"] = "P2"
        result = parse_workbook(build_xlsx(baseline), "x.xlsx")
        issue = only(result.errors, "E008")
        assert issue.column == "cast_ids"
        assert "crew_ids" in issue.fix_hint

    def test_e008_crew_column_names_a_cast_member(self, baseline: dict[str, SheetRows]) -> None:
        baseline["scenes"][1]["crew_ids"] = "P1"
        result = parse_workbook(build_xlsx(baseline), "x.xlsx")
        issue = only(result.errors, "E008")
        assert issue.column == "crew_ids"
        assert "cast_ids" in issue.fix_hint

    def test_e014_invalid_availability_combination(self, baseline: dict[str, SheetRows]) -> None:
        baseline["availability"] = [
            {
                "subject_type": "PERSON",
                "subject_id": "P1",
                "window_kind": "AVAILABLE",
                "from_date": date(2027, 6, 5),
                "to_date": date(2027, 6, 6),
                "note": "wrong way round",
            }
        ]
        result = parse_workbook(build_xlsx(baseline), "x.xlsx")
        issue = only(result.errors, "E014")
        assert issue.row == 2
        assert "UNAVAILABLE" in issue.fix_hint

    def test_e006_availability_names_an_unknown_subject(
        self, baseline: dict[str, SheetRows]
    ) -> None:
        baseline["availability"] = [
            {
                "subject_type": "EQUIPMENT",
                "subject_id": "E-NOPE",
                "window_kind": "AVAILABLE",
                "from_date": date(2027, 6, 5),
                "to_date": date(2027, 6, 6),
                "note": "",
            }
        ]
        result = parse_workbook(build_xlsx(baseline), "x.xlsx")
        issue = only(result.errors, "E006")
        assert issue.sheet == "availability"
        assert "E-NOPE" in issue.message


class TestSemanticPhase:
    def test_e007_prerequisite_cycle_reports_the_path(self, baseline: dict[str, SheetRows]) -> None:
        baseline["scenes"][0]["prerequisite_scene_ids"] = "S3"
        result = parse_workbook(build_xlsx(baseline), "x.xlsx")
        issue = only(result.errors, "E007")
        assert issue.sheet == "scenes"
        assert "S1" in str(issue.offending_value)
        assert "S3" in str(issue.offending_value)
        assert "→" in str(issue.offending_value)

    def test_e009_scene_scheduled_on_two_days(self, baseline: dict[str, SheetRows]) -> None:
        baseline["schedule"][1]["scene_ids"] = "S3;S1"
        result = parse_workbook(build_xlsx(baseline), "x.xlsx")
        issue = only(result.errors, "E009")
        assert issue.sheet == "schedule"
        assert "S1" in issue.message

    def test_e010_schedule_date_outside_the_shoot_window(
        self, baseline: dict[str, SheetRows]
    ) -> None:
        baseline["schedule"][1]["date"] = date(2027, 7, 15)
        result = parse_workbook(build_xlsx(baseline), "x.xlsx")
        issue = only(result.errors, "E010")
        assert issue.row == 3
        assert "2027-07-15" in issue.message

    def test_e011_wrap_before_call_with_next_day_explicitly_false(
        self, baseline: dict[str, SheetRows]
    ) -> None:
        baseline["schedule"][0]["call_time"] = time(18, 0)
        baseline["schedule"][0]["wrap_time"] = time(4, 0)
        baseline["schedule"][0]["wrap_next_day"] = "FALSE"
        result = parse_workbook(build_xlsx(baseline), "x.xlsx")
        issue = only(result.errors, "E011")
        assert issue.row == 2
        assert "wrap_next_day" in issue.fix_hint

    def test_e015_local_time_that_does_not_exist(self, baseline: dict[str, SheetRows]) -> None:
        """01:30 on the UK spring-forward date is skipped by the clocks."""
        baseline["production"][0]["shoot_start"] = date(2027, 3, 1)
        baseline["production"][0]["shoot_end"] = date(2027, 4, 30)
        baseline["production"][0]["reserve_days"] = ""
        baseline["schedule"] = [
            {
                "date": date(2027, 3, 28),
                "call_time": time(1, 30),
                "wrap_time": time(12, 0),
                "wrap_next_day": "FALSE",
                "location_id": "L1",
                "scene_ids": "S1;S2",
                "unit": "MAIN",
            },
            {
                "date": date(2027, 3, 29),
                "call_time": time(9, 0),
                "wrap_time": time(17, 0),
                "wrap_next_day": "FALSE",
                "location_id": "L2",
                "scene_ids": "S3",
                "unit": "MAIN",
            },
        ]
        result = parse_workbook(build_xlsx(baseline), "x.xlsx")
        issue = only(result.errors, "E015")
        assert "2027-03-28" in issue.message
        assert "Europe/London" in issue.message

    def test_e018_availability_window_ends_before_it_starts(
        self, baseline: dict[str, SheetRows]
    ) -> None:
        baseline["availability"] = [
            {
                "subject_type": "PERSON",
                "subject_id": "P1",
                "window_kind": "UNAVAILABLE",
                "from_date": date(2027, 6, 12),
                "to_date": date(2027, 6, 8),
                "note": "",
            }
        ]
        result = parse_workbook(build_xlsx(baseline), "x.xlsx")
        issue = only(result.errors, "E018")
        assert issue.row == 2
        assert "Swap" in issue.fix_hint


class TestPhasesShortCircuit:
    def test_a_missing_sheet_does_not_produce_dangling_reference_noise(
        self, baseline: dict[str, SheetRows]
    ) -> None:
        """One omission must not turn into one error per row that referenced it."""
        result = parse_workbook(
            build_xlsx(baseline, omit_sheets=frozenset({"locations"})), "x.xlsx"
        )
        assert codes(result.errors) == ["E001"]
        assert len(result.errors) == 1

    def test_a_type_error_stops_before_the_reference_phase(
        self, baseline: dict[str, SheetRows]
    ) -> None:
        baseline["scenes"][0]["estimated_minutes"] = "nope"
        baseline["scenes"][1]["location_id"] = "L-99"
        result = parse_workbook(build_xlsx(baseline), "x.xlsx")
        assert codes(result.errors) == ["E003"]


class TestZipSecurity:
    def test_a_zip_slip_archive_is_refused(self) -> None:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("../../../etc/passwd", "root:x:0:0\n")
            archive.writestr("scenes.csv", "scene_id\nS1\n")
        result = parse_workbook(buffer.getvalue(), "sheets.zip")
        issue = only(result.errors, "E016")
        assert "unsafe path" in issue.message

    def test_a_zip_bomb_is_refused_before_decompression(self) -> None:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("scenes.csv", "A" * 20_000_000)
        result = parse_workbook(buffer.getvalue(), "sheets.zip")
        issue = only(result.errors, "E016")
        assert "decompress" in issue.message.lower()

    def test_an_oversized_upload_is_refused(self) -> None:
        result = parse_workbook(b"PK\x03\x04" + b"\x00" * (11 * 1024 * 1024), "big.xlsx")
        issue = only(result.errors, "E016")
        assert "MB limit" in issue.message


class TestEveryCodeIsReachable:
    """The catalogue and the implementation must not drift apart."""

    @pytest.mark.parametrize(
        "code",
        [f"E{n:03d}" for n in range(1, 19)],
    )
    def test_code_is_in_the_catalogue(self, code: str) -> None:
        from pri.importer.errors import ERROR_CATALOG

        assert code in ERROR_CATALOG, f"{code} has no catalogue entry"

    def test_every_issue_carries_a_fix_hint(self, baseline: dict[str, SheetRows]) -> None:
        baseline["scenes"][0]["location_id"] = "L-99"
        result = parse_workbook(build_xlsx(baseline), "x.xlsx")
        for issue in (*result.errors, *result.warnings):
            assert issue.fix_hint.strip(), f"{issue.code} has an empty fix hint"
            assert not issue.fix_hint.lower().startswith("error"), (
                f"{issue.code}'s hint reads like a log line, not advice"
            )


def test_e013_is_the_services_job_not_the_parsers() -> None:
    """E013 needs the database, so the parser cannot check it and does not try.

    A workbook whose production already exists parses cleanly here; the clash is
    only visible to something that can query ``productions``. The behaviour
    itself is covered in ``test_service.py``.
    """
    from pri.importer.errors import ERROR_CATALOG

    assert ERROR_CATALOG["E013"] == "Production already exists"
