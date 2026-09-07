"""Local time, overnight shoots, and the two days a year the clocks move.

Every time in the workbook is naive local wall-clock. Getting the localisation
wrong does not produce an error — it produces a turnaround calculation that is
quietly an hour out, on a system whose entire purpose is to be trusted about
turnaround. So the edges are handled explicitly and tested here.
"""

from __future__ import annotations

from datetime import date, time, timedelta

from pri.importer import parse_workbook
from tests.importer.conftest import SheetRows, build_xlsx


def _london(baseline: dict[str, SheetRows], start: date, end: date) -> None:
    baseline["production"][0]["timezone"] = "Europe/London"
    baseline["production"][0]["shoot_start"] = start
    baseline["production"][0]["shoot_end"] = end
    baseline["production"][0]["reserve_days"] = ""


class TestOvernight:
    def test_a_wrap_before_the_call_is_inferred_as_the_next_morning(
        self, baseline: dict[str, SheetRows]
    ) -> None:
        baseline["schedule"][0]["call_time"] = time(18, 0)
        baseline["schedule"][0]["wrap_time"] = time(2, 0)
        baseline["schedule"][0]["wrap_next_day"] = None

        result = parse_workbook(build_xlsx(baseline), "overnight.xlsx")
        assert result.errors == ()
        assert result.state is not None

        day = result.state.schedule.days[0]
        assert day.call_time.date() == date(2027, 6, 1)
        assert day.wrap_time.date() == date(2027, 6, 2)
        assert day.wrap_time - day.call_time == timedelta(hours=8)

    def test_the_inference_is_reported_so_the_user_can_confirm_it(
        self, baseline: dict[str, SheetRows]
    ) -> None:
        baseline["schedule"][0]["call_time"] = time(18, 0)
        baseline["schedule"][0]["wrap_time"] = time(2, 0)
        baseline["schedule"][0]["wrap_next_day"] = None

        result = parse_workbook(build_xlsx(baseline), "overnight.xlsx")
        issue = next(w for w in result.warnings if w.code == "W008")
        assert issue.row == 2
        assert issue.column == "wrap_time"
        assert "wrap_next_day" in issue.fix_hint

    def test_an_explicit_wrap_next_day_needs_no_warning(
        self, baseline: dict[str, SheetRows]
    ) -> None:
        baseline["schedule"][0]["call_time"] = time(18, 0)
        baseline["schedule"][0]["wrap_time"] = time(2, 0)
        baseline["schedule"][0]["wrap_next_day"] = "TRUE"

        result = parse_workbook(build_xlsx(baseline), "overnight.xlsx")
        assert result.errors == ()
        assert not [w for w in result.warnings if w.code == "W008"]
        assert result.state is not None
        assert result.state.schedule.days[0].wrap_time.date() == date(2027, 6, 2)

    def test_wrap_next_day_true_also_works_when_the_wrap_looks_normal(
        self, baseline: dict[str, SheetRows]
    ) -> None:
        """A day that calls at 08:00 and genuinely wraps at 09:00 the next morning."""
        baseline["schedule"][0]["call_time"] = time(8, 0)
        baseline["schedule"][0]["wrap_time"] = time(9, 0)
        baseline["schedule"][0]["wrap_next_day"] = "TRUE"

        result = parse_workbook(build_xlsx(baseline), "long.xlsx")
        assert result.errors == ()
        assert result.state is not None
        day = result.state.schedule.days[0]
        assert day.wrap_time - day.call_time == timedelta(hours=25)


class TestDaylightSaving:
    def test_a_local_time_that_does_not_exist_is_refused(
        self, baseline: dict[str, SheetRows]
    ) -> None:
        """UK clocks go forward at 01:00 on 2027-03-28, so 01:30 never happens."""
        _london(baseline, date(2027, 3, 1), date(2027, 4, 30))
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
                "date": date(2027, 3, 30),
                "call_time": time(9, 0),
                "wrap_time": time(17, 0),
                "wrap_next_day": "FALSE",
                "location_id": "L2",
                "scene_ids": "S3",
                "unit": "MAIN",
            },
        ]
        result = parse_workbook(build_xlsx(baseline), "dst.xlsx")
        issue = next(e for e in result.errors if e.code == "E015")
        assert "2027-03-28" in issue.message
        assert "clocks go forward" in issue.message
        assert result.state is None

    def test_an_ambiguous_local_time_resolves_to_the_first_occurrence(
        self, baseline: dict[str, SheetRows]
    ) -> None:
        """UK clocks go back at 02:00 on 2027-10-31, so 01:30 happens twice."""
        _london(baseline, date(2027, 10, 1), date(2027, 11, 30))
        baseline["schedule"] = [
            {
                "date": date(2027, 10, 30),
                "call_time": time(18, 0),
                "wrap_time": time(1, 30),
                "wrap_next_day": "TRUE",
                "location_id": "L1",
                "scene_ids": "S1;S2",
                "unit": "MAIN",
            },
            {
                "date": date(2027, 11, 2),
                "call_time": time(9, 0),
                "wrap_time": time(17, 0),
                "wrap_next_day": "FALSE",
                "location_id": "L2",
                "scene_ids": "S3",
                "unit": "MAIN",
            },
        ]
        result = parse_workbook(build_xlsx(baseline), "dst.xlsx")
        assert result.errors == ()
        assert result.state is not None

        issue = next(w for w in result.warnings if w.code == "W009")
        assert "2027-10-31" in issue.message
        assert "clocks go back" in issue.message

        wrap = result.state.schedule.days[0].wrap_time
        assert wrap.utcoffset() == timedelta(hours=1), "BST — the first occurrence"

    def test_a_normal_date_in_the_same_zone_is_silent(self, baseline: dict[str, SheetRows]) -> None:
        _london(baseline, date(2027, 10, 1), date(2027, 11, 30))
        baseline["schedule"] = [
            {
                "date": date(2027, 10, 20),
                "call_time": time(8, 0),
                "wrap_time": time(18, 0),
                "wrap_next_day": "FALSE",
                "location_id": "L1",
                "scene_ids": "S1;S2",
                "unit": "MAIN",
            },
            {
                "date": date(2027, 10, 21),
                "call_time": time(9, 0),
                "wrap_time": time(17, 0),
                "wrap_next_day": "FALSE",
                "location_id": "L2",
                "scene_ids": "S3",
                "unit": "MAIN",
            },
        ]
        result = parse_workbook(build_xlsx(baseline), "normal.xlsx")
        assert result.errors == ()
        assert not [w for w in result.warnings if w.code in ("W008", "W009")]


class TestLocalisation:
    def test_times_come_out_aware_and_in_the_declared_zone(
        self, baseline: dict[str, SheetRows]
    ) -> None:
        baseline["production"][0]["timezone"] = "Asia/Kolkata"
        result = parse_workbook(build_xlsx(baseline), "tz.xlsx")
        assert result.state is not None
        for day in result.state.schedule.days:
            assert day.call_time.tzinfo is not None
            assert day.call_time.utcoffset() == timedelta(hours=5, minutes=30)

    def test_a_short_timezone_abbreviation_is_refused(self, baseline: dict[str, SheetRows]) -> None:
        """IST is India, Ireland and Israel. PRI will not guess."""
        baseline["production"][0]["timezone"] = "IST"
        result = parse_workbook(build_xlsx(baseline), "tz.xlsx")
        issue = next(e for e in result.errors if e.code == "E003")
        assert issue.column == "timezone"
        assert "Asia/Kolkata" in issue.fix_hint

    def test_the_timezone_decides_how_an_ambiguous_date_is_read(
        self, baseline: dict[str, SheetRows]
    ) -> None:
        """05/06 is the fifth of June in London and the sixth of May in LA."""

        def day_of(result: object) -> date:
            # Days come back sorted by date, so the row under test is found by
            # the scenes on it rather than by position.
            state = result.state  # type: ignore[attr-defined]
            assert state is not None
            return next(d for d in state.schedule.days if "S1" in d.scene_ids).date

        baseline["production"][0]["timezone"] = "Europe/London"
        baseline["schedule"][0]["date"] = "05/06/2027"
        london = parse_workbook(build_xlsx(baseline), "tz.xlsx")
        assert london.errors == ()
        assert day_of(london) == date(2027, 6, 5)
        assert any(w.code == "W011" for w in london.warnings)

        baseline["production"][0]["timezone"] = "America/Los_Angeles"
        baseline["production"][0]["shoot_start"] = date(2027, 5, 1)
        la = parse_workbook(build_xlsx(baseline), "tz.xlsx")
        assert la.errors == ()
        assert day_of(la) == date(2027, 5, 6)
        assert any(w.code == "W011" for w in la.warnings)
