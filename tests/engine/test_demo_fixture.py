"""The demo fixture is a contract, not test data.

Appendix A of the Bob prompt pack fixes every number in *Night Train to Kochi*
because the demo narration reads them aloud.  These tests fail the moment the
fixture drifts from the story the video tells.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

from pri.domain.models import ProductionState, SwapDays
from pri.engine.constraints.validator import validate

IST = timezone(timedelta(hours=5, minutes=30))

SEP_10 = date(2026, 9, 10)
SEP_11 = date(2026, 9, 11)


def _hard(state: ProductionState) -> list[str]:
    """Return the codes of every HARD violation in ``state``."""
    return [v.code for v in validate(state) if v.severity == "HARD"]


# ---------------------------------------------------------------------------
# The baseline
# ---------------------------------------------------------------------------


def test_seeded_state_has_no_hard_violations(night_train_state: ProductionState) -> None:
    """PROMPT 05 acceptance: version 1 must be a legal schedule."""
    assert _hard(night_train_state) == []


def test_baseline_shape_matches_appendix_a(night_train_state: ProductionState) -> None:
    state = night_train_state
    assert state.version == 1
    assert state.parent_version is None
    assert state.production.id == "film-001"
    assert state.production.title == "Night Train to Kochi"
    assert state.production.currency == "USD"
    assert state.production.shoot_start == date(2026, 9, 8)
    assert state.production.shoot_end == date(2026, 10, 3)
    assert state.production.reserve_days == (date(2026, 9, 16), date(2026, 9, 30))


def test_sep_10_is_the_courtyard_day(night_train_state: ProductionState) -> None:
    day = next(d for d in night_train_state.schedule.days if d.date == SEP_10)
    assert day.location_id == "LOC-04"
    assert day.scene_ids == ("S17", "S18", "S21")
    assert day.call_time == datetime.combine(SEP_10, time(7, 0), tzinfo=IST)
    assert day.wrap_time == datetime.combine(SEP_10, time(19, 0), tzinfo=IST)


def test_baseline_turnaround_into_sep_11_is_17_hours(
    night_train_state: ProductionState,
) -> None:
    days = {d.date: d for d in night_train_state.schedule.days}
    gap = days[SEP_11].call_time - days[SEP_10].wrap_time
    assert gap == timedelta(hours=17)


def test_crane_rental_window_covers_only_sep_9_to_11(
    night_train_state: ProductionState,
) -> None:
    crane = next(e for e in night_train_state.equipment if e.id == "CRANE-01")
    (window,) = crane.available_windows
    assert window.start == datetime.combine(date(2026, 9, 9), time.min, tzinfo=IST)
    assert window.end == datetime.combine(date(2026, 9, 12), time.min, tzinfo=IST)


def test_meera_is_unavailable_sep_12_to_14(night_train_state: ProductionState) -> None:
    meera = next(p for p in night_train_state.people if p.id == "P02")
    (window,) = meera.unavailable_windows
    assert window.start == datetime.combine(date(2026, 9, 12), time.min, tzinfo=IST)
    assert window.end == datetime.combine(date(2026, 9, 15), time.min, tzinfo=IST)


# ---------------------------------------------------------------------------
# The moment the demo exists for
# ---------------------------------------------------------------------------


def test_swap_sep_10_and_11_produces_exactly_one_c001_at_9_hours(
    night_train_state: ProductionState,
) -> None:
    """PROMPT 06 acceptance: Plan B's failure, verbatim as the narrator reads it.

    After the swap Sep 10 runs the warehouse day and wraps at 22:00, while
    Sep 11 inherits the courtyard's 07:00 call — nine hours of turnaround
    against a ten-hour minimum.
    """
    swapped = night_train_state.apply([SwapDays(date_a=SEP_10, date_b=SEP_11)])

    violations = validate(swapped)
    hard = [v for v in violations if v.severity == "HARD"]

    assert len(hard) == 1
    violation = hard[0]
    assert violation.code == "C001"
    assert violation.observed == "9.0h"
    assert violation.required == ">= 10.0h"
    assert violation.subject_ids == ("2026-09-10", "2026-09-11")


def test_swap_moves_the_whole_day_not_just_its_scenes(
    night_train_state: ProductionState,
) -> None:
    swapped = night_train_state.apply([SwapDays(date_a=SEP_10, date_b=SEP_11)])
    days = {d.date: d for d in swapped.schedule.days}

    sep_10 = days[SEP_10]
    assert sep_10.location_id == "LOC-03"
    assert sep_10.scene_ids == ("S24", "S25")
    assert sep_10.call_time == datetime.combine(SEP_10, time(12, 0), tzinfo=IST)
    assert sep_10.wrap_time == datetime.combine(SEP_10, time(22, 0), tzinfo=IST)

    sep_11 = days[SEP_11]
    assert sep_11.location_id == "LOC-04"
    assert sep_11.scene_ids == ("S17", "S18", "S21")
    assert sep_11.call_time == datetime.combine(SEP_11, time(7, 0), tzinfo=IST)
    assert sep_11.wrap_time == datetime.combine(SEP_11, time(19, 0), tzinfo=IST)


def test_apply_never_mutates_the_base_state(night_train_state: ProductionState) -> None:
    before = {d.date: d.scene_ids for d in night_train_state.schedule.days}
    night_train_state.apply([SwapDays(date_a=SEP_10, date_b=SEP_11)])
    after = {d.date: d.scene_ids for d in night_train_state.schedule.days}
    assert before == after
