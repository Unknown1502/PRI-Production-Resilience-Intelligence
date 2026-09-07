"""Production data import: spreadsheet in, versioned state out.

A 1st AD keeps their board in Excel. This package accepts that reality — it
parses the workbook, validates it in ordered phases, shows the user what is
already broken in their own schedule, and stages the result for confirmation
before anything is committed.

Nothing here calls an LLM. Import is deterministic parsing.

**Merge is not supported.** If ``production_id`` already exists that is E013,
and the user changes the id or deletes the existing production. Reconciling two
versions of a board is a different product with a different UI, and guessing at
it would silently lose somebody's schedule.
"""

from pri.importer.errors import ImportIssue, Severity
from pri.importer.parser import ImportSummary, ParseResult, parse_workbook
from pri.importer.spec import TEMPLATE_VERSION, WORKBOOK_SPEC
from pri.importer.template import build_template, export_state

__all__ = [
    "TEMPLATE_VERSION",
    "WORKBOOK_SPEC",
    "ImportIssue",
    "ImportSummary",
    "ParseResult",
    "Severity",
    "build_template",
    "export_state",
    "parse_workbook",
]
