"""The Excel coercion layer — where this module meets reality.

Every case here is one PRI has to survive because a spreadsheet produced it,
not because the format allows it. A scene id typed as ``17`` arriving as the
float ``17.0`` is not an edge case; it is what happens the first time somebody
formats a column as Number.
"""

from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal

import pytest

from pri.importer import parse_workbook
from pri.importer.coercion import (
    CoercionError,
    excel_serial_to_date,
    excel_serial_to_time,
    normalise_id,
    normalise_text,
    prefers_day_first,
    split_list,
    to_bool,
    to_date,
    to_decimal,
    to_int,
    to_time,
)
from tests.importer.conftest import SheetRows, build_xlsx


class TestIdentifiers:
    """Excel turning an id into a number is the single most damaging coercion."""

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (17, "17"),
            (17.0, "17"),
            ("17", "17"),
            ("17A", "17A"),
            ("  S-04  ", "S-04"),
            ("S\xa004", "S 04"),
            (None, None),
            ("", None),
        ],
    )
    def test_ids_never_gain_a_decimal_point(self, value: object, expected: str | None) -> None:
        assert normalise_id(value) == expected

    def test_a_genuinely_fractional_id_keeps_its_fraction(self) -> None:
        """Truncating silently would be worse than a loud reference failure."""
        assert normalise_id(17.5) == "17.5"

    def test_invisible_whitespace_is_stripped(self) -> None:
        assert normalise_text(" S1​") == "S1"  # noqa: RUF001 - invisible by design

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("P1;P2", ("P1", "P2")),
            ("P1, P2", ("P1", "P2")),
            ("P1 ; ; P2", ("P1", "P2")),
            (17, ("17",)),
            ("", ()),
            (None, ()),
        ],
    )
    def test_lists_split_on_either_separator(
        self, value: object, expected: tuple[str, ...]
    ) -> None:
        assert split_list(value) == expected


class TestTimes:
    def test_an_excel_serial_becomes_a_clock_time(self) -> None:
        """0.291666… of a day is 07:00, and must not come back as 06:59."""
        assert excel_serial_to_time(0.2916666666666667) == time(7, 0)

    @pytest.mark.parametrize(
        ("serial", "expected"),
        [
            (0.0, time(0, 0)),
            (0.5, time(12, 0)),
            (0.75, time(18, 0)),
            (0.8125, time(19, 30)),
            (45000.2916666, time(7, 0)),
        ],
    )
    def test_serial_times_round_to_the_nearest_minute(self, serial: float, expected: time) -> None:
        assert excel_serial_to_time(serial) == expected

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (time(7, 0), time(7, 0)),
            (datetime(2027, 1, 1, 19, 30), time(19, 30)),
            ("07:00", time(7, 0)),
            ("7:00 AM", time(7, 0)),
            ("19:30", time(19, 30)),
            (0.2916666666666667, time(7, 0)),
            (None, None),
        ],
    )
    def test_times_arrive_in_four_shapes(self, value: object, expected: time | None) -> None:
        assert to_time(value) == expected

    def test_seconds_are_discarded(self) -> None:
        assert to_time(time(7, 0, 42)) == time(7, 0)

    def test_nonsense_raises_with_a_hint(self) -> None:
        with pytest.raises(CoercionError) as caught:
            to_time("after lunch")
        assert "24-hour" in caught.value.hint


class TestDates:
    def test_an_excel_serial_becomes_a_date(self) -> None:
        assert excel_serial_to_date(45000) == date(2023, 3, 15)

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("2027-06-01", date(2027, 6, 1)),
            (date(2027, 6, 1), date(2027, 6, 1)),
            (datetime(2027, 6, 1, 9, 0), date(2027, 6, 1)),
            ("1 June 2027", date(2027, 6, 1)),
        ],
    )
    def test_unambiguous_dates(self, value: object, expected: date) -> None:
        parsed, ambiguous = to_date(value)
        assert parsed == expected
        assert ambiguous is False

    def test_day_first_reading(self) -> None:
        parsed, ambiguous = to_date("05/03/2027", day_first=True)
        assert parsed == date(2027, 3, 5)
        assert ambiguous is True, "both halves are ≤ 12, so the reading is a choice"

    def test_month_first_reading(self) -> None:
        parsed, ambiguous = to_date("05/03/2027", day_first=False)
        assert parsed == date(2027, 5, 3)
        assert ambiguous is True

    def test_a_day_above_twelve_is_not_ambiguous(self) -> None:
        parsed, ambiguous = to_date("25/03/2027", day_first=True)
        assert parsed == date(2027, 3, 25)
        assert ambiguous is False

    def test_iso_is_never_ambiguous(self) -> None:
        _, ambiguous = to_date("2027-05-03", day_first=False)
        assert ambiguous is False

    @pytest.mark.parametrize(
        ("zone", "day_first"),
        [
            ("Asia/Kolkata", True),
            ("Europe/London", True),
            ("America/New_York", False),
            ("America/Los_Angeles", False),
            ("Asia/Manila", False),
            (None, True),
        ],
    )
    def test_the_timezone_picks_the_convention(self, zone: str | None, day_first: bool) -> None:
        assert prefers_day_first(zone) is day_first


class TestMoney:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("1200.50", "1200.50"),
            ("₹1,200.50", "1200.50"),
            ("$ 1200.5", "1200.5"),
            ("£1,200", "1200"),
            ("1 200,50", "1200.50"),
            ("1.200,50", "1200.50"),
            ("1,200.50", "1200.50"),
            (1200.5, "1200.5"),
            (1200, "1200"),
            (Decimal("1200.50"), "1200.50"),
        ],
    )
    def test_currency_symbols_and_separators_are_stripped(
        self, value: object, expected: str
    ) -> None:
        assert to_decimal(value) == Decimal(expected)

    def test_money_never_routes_through_float(self) -> None:
        """Decimal(float) would give the binary expansion; Decimal(str) does not."""
        assert to_decimal(0.1) == Decimal("0.1")
        assert str(to_decimal(0.1)) == "0.1"

    def test_a_word_is_refused_with_a_hint(self) -> None:
        with pytest.raises(CoercionError) as caught:
            to_decimal("about a grand")
        assert "1200.00" in caught.value.hint


class TestWholeNumbers:
    def test_a_fractional_value_is_refused_rather_than_rounded(self) -> None:
        with pytest.raises(CoercionError) as caught:
            to_int(90.5)
        assert "whole number" in caught.value.message

    @pytest.mark.parametrize(
        ("value", "expected"), [(90, 90), (90.0, 90), ("90", 90), ("1,200", 1200)]
    )
    def test_whole_numbers(self, value: object, expected: int) -> None:
        assert to_int(value) == expected


class TestBooleans:
    @pytest.mark.parametrize(
        "value", ["TRUE", "True", "true", "1", "YES", "Y", "yes", "T", 1, True]
    )
    def test_the_true_spellings(self, value: object) -> None:
        assert to_bool(value) is True

    @pytest.mark.parametrize(
        "value", ["FALSE", "False", "false", "0", "NO", "N", "no", "F", 0, False]
    )
    def test_the_false_spellings(self, value: object) -> None:
        assert to_bool(value) is False

    def test_blank_takes_the_default(self) -> None:
        assert to_bool(None, default=False) is False
        assert to_bool("", default=True) is True

    def test_a_word_that_is_neither_is_refused(self) -> None:
        with pytest.raises(CoercionError):
            to_bool("maybe")


class TestThroughTheWholeParser:
    """The same coercions, exercised end to end from a real workbook."""

    def test_numeric_ids_and_serial_times_survive_a_full_parse(
        self, baseline: dict[str, SheetRows]
    ) -> None:
        baseline["scenes"][0]["number"] = 12.0
        baseline["scenes"][0]["estimated_minutes"] = 120.0
        baseline["people"][0]["daily_rate"] = "£1,200.50"
        baseline["schedule"][0]["call_time"] = 0.2916666666666667

        result = parse_workbook(build_xlsx(baseline), "coerced.xlsx")
        assert result.errors == ()
        assert result.state is not None

        assert result.state.scenes[0].number == "12"
        assert result.state.people[0].daily_rate == Decimal("1200.50")
        assert result.state.schedule.days[0].call_time.strftime("%H:%M") == "07:00"

    def test_merged_cells_are_expanded_and_warned_about(
        self, baseline: dict[str, SheetRows]
    ) -> None:
        """A 1st AD merges a date down three rows; every row means that date."""
        baseline["schedule"][1]["date"] = None
        data = build_xlsx(baseline, merges={"schedule": ["A2:A3"]})

        result = parse_workbook(data, "merged.xlsx")
        assert result.errors == ()
        assert result.state is not None
        assert {w.code for w in result.warnings} >= {"W012"}
        assert all(day.date == date(2027, 6, 1) for day in result.state.schedule.days)

    def test_a_hidden_sheet_is_read_and_warned_about(self, baseline: dict[str, SheetRows]) -> None:
        data = build_xlsx(baseline, hidden_sheets=frozenset({"equipment"}))
        result = parse_workbook(data, "hidden.xlsx")
        assert result.errors == ()
        assert any(w.code == "W013" for w in result.warnings)
        assert result.state is not None
        assert len(result.state.equipment) == 1

    def test_hidden_rows_are_read_and_warned_about(self, baseline: dict[str, SheetRows]) -> None:
        data = build_xlsx(baseline, hidden_rows={"scenes": frozenset({3})})
        result = parse_workbook(data, "hidden.xlsx")
        assert result.errors == ()
        assert any(w.code == "W013" for w in result.warnings)
        assert result.state is not None
        assert len(result.state.scenes) == 3, "a hidden row is still data"

    def test_unknown_columns_are_ignored_with_a_warning(
        self, baseline: dict[str, SheetRows]
    ) -> None:
        """Real users add their own columns. Their file must still import."""
        data = build_xlsx(
            baseline,
            extra_columns={"scenes": {"my_notes": "call the agent", "page_count": 3}},
        )
        result = parse_workbook(data, "extra.xlsx")
        assert result.errors == ()
        issue = next(w for w in result.warnings if w.code == "W006")
        assert "my_notes" in issue.message
        assert "page_count" in issue.message

    def test_an_unknown_sheet_is_ignored_silently(self, baseline: dict[str, SheetRows]) -> None:
        data = build_xlsx(baseline, extra_sheets={"budget": [["line", "amount"], ["camera", 5000]]})
        result = parse_workbook(data, "extra.xlsx")
        assert result.errors == ()
        assert not [w for w in result.warnings if "budget" in (w.message or "")]

    def test_a_template_version_mismatch_warns_and_continues(
        self, baseline: dict[str, SheetRows]
    ) -> None:
        baseline["production"][0]["template_version"] = "0"
        result = parse_workbook(build_xlsx(baseline), "old.xlsx")
        assert result.errors == ()
        assert result.state is not None
        issue = next(w for w in result.warnings if w.code == "W007")
        assert "0" in issue.message

    def test_the_templates_own_example_rows_are_skipped(self) -> None:
        """A user who forgets to delete them still gets a clean import."""
        from pri.importer.template import build_template

        result = parse_workbook(build_template(), "template.xlsx")
        # The blank template has no real data, so it fails on the production
        # row count — but never because the example rows were read as scenes.
        assert not [e for e in result.errors if e.sheet == "scenes"]


class TestCsvZip:
    def test_a_zip_of_csvs_imports(self, baseline: dict[str, SheetRows]) -> None:
        import csv
        import io
        import zipfile

        from pri.importer.spec import WORKBOOK_SPEC

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            for sheet in WORKBOOK_SPEC:
                rows = baseline.get(sheet.name, [])
                text = io.StringIO(newline="")
                writer = csv.writer(text)
                writer.writerow(sheet.column_names)
                for row in rows:
                    writer.writerow([_csv(row.get(name)) for name in sheet.column_names])
                archive.writestr(f"{sheet.name}.csv", text.getvalue())

        result = parse_workbook(buffer.getvalue(), "sheets.zip")
        assert result.errors == ()
        assert result.state is not None
        assert len(result.state.scenes) == 3

    def test_a_utf8_bom_csv_decodes_without_corrupting_the_first_header(self) -> None:
        from pri.importer.reader import _read_csv_sheet

        payload = "﻿person_id,name\nP1,Alex\n".encode()
        sheet = _read_csv_sheet("people", payload)
        assert sheet.headers[0] == "person_id", "the BOM must not glue onto the header"
        assert sheet.rows[0].values["person_id"] == "P1"

    def test_a_windows_1252_csv_decodes(self) -> None:
        from pri.importer.reader import _read_csv_sheet

        payload = "person_id,name\nP1,Renée Café\n".encode("cp1252")
        sheet = _read_csv_sheet("people", payload)
        assert sheet.rows[0].values["name"] == "Renée Café"


def _csv(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, date | datetime):
        return value.isoformat()
    if isinstance(value, time):
        return value.strftime("%H:%M")
    return str(value)
