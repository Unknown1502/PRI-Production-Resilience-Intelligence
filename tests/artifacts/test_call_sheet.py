"""Call sheets for productions the deployment has never heard of.

``config/logistics.yaml`` is keyed by location id and holds the Night Train
fixture's addresses, parking notes and hospitals. An imported production's
locations are not in it and never will be — the 1st AD's workbook has no column
for a hospital telephone number.

So the coupling has to degrade, not fail: the execution pipeline generates a
call sheet as its final step, and a production imported from a stranger's board
must reach the end of that pipeline. These tests pin that.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
import yaml

from pri.artifacts.call_sheet import CallSheetError, generate_call_sheet
from pri.importer.samples import build_second_unit_state

if TYPE_CHECKING:
    from pri.domain.models import ProductionState

_LOGISTICS = Path(__file__).parents[2] / "config" / "logistics.yaml"


@pytest.fixture()
def imported_state() -> ProductionState:
    """Harbour Lights — HL-DOCK and friends, none of them in the config."""
    return build_second_unit_state()


def _configured_location_ids() -> set[str]:
    with _LOGISTICS.open(encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle) or {}
    return set(loaded.get("locations", {}))


class TestUnknownLocations:
    def test_a_call_sheet_renders_for_a_location_the_config_never_heard_of(
        self, imported_state: ProductionState
    ) -> None:
        day = imported_state.schedule.days[0]
        assert day.location_id not in _configured_location_ids(), (
            "the sample production must not be in the demo logistics config, "
            "or this test proves nothing"
        )

        pdf = generate_call_sheet(imported_state, day.date)
        assert pdf.startswith(b"%PDF"), "the pipeline's last step must still produce a PDF"
        assert len(pdf) > 2000, "a stub rendering nothing would also start with %PDF"

    def test_every_day_of_an_imported_production_renders(
        self, imported_state: ProductionState
    ) -> None:
        """Including the overnight and the second-unit days."""
        for day in imported_state.schedule.days:
            assert generate_call_sheet(imported_state, day.date).startswith(b"%PDF")

    def test_a_missing_logistics_file_is_not_an_error(
        self, imported_state: ProductionState
    ) -> None:
        pdf = generate_call_sheet(
            imported_state,
            imported_state.schedule.days[0].date,
            logistics_path=Path("does-not-exist.yaml"),
        )
        assert pdf.startswith(b"%PDF")

    def test_a_day_that_is_not_in_the_schedule_still_raises(
        self, imported_state: ProductionState
    ) -> None:
        """Degrading on missing reference data must not degrade on a real bug."""
        with pytest.raises(CallSheetError):
            generate_call_sheet(imported_state, date(1999, 1, 1))
