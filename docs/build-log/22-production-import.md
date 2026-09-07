# Production Data Import

**Not an IBM Bob session.** Recorded here, deliberately outside
[`ibm/bob/artifacts/`](../../ibm/bob/artifacts/), so that directory contains
Bob's work and only Bob's work.
**Specified:** in-session, after the twenty-one-prompt Bob pack was complete.
**Outcome:** complete — 318 tests, 7 sheets, 18 error codes, 13 warning codes

---

## Plan

Until this session PRI could only ever hold one production: the one in
`data/fixtures/night_train.json`. Everything downstream of that fixture — the
graph, the validator, the candidate generator, the call sheet — was general,
and the only thing standing between PRI and a stranger's film was the absence
of a way to get their schedule into it.

So: a 1st AD's Excel board goes in, gets parsed, gets validated in ordered
phases, gets shown back with everything wrong in it, and only then — on an
explicit confirmation — becomes state version 1 of a new production.

The hard part is not reading a spreadsheet. It is being trustworthy about a
file you did not write, in a format that lies: a scene number that is secretly
a float, a date that is 46,752, a time that does not exist because the clocks
moved, a cell that starts with `=`, a `.zip` that is a decompression bomb.

## Files created

| File | Lines | Contents |
|---|---|---|
| [`importer/spec.py`](../../src/pri/importer/spec.py) | 755 | `WORKBOOK_SPEC` — the single definition of every sheet and column |
| [`importer/errors.py`](../../src/pri/importer/errors.py) | 214 | `ImportIssue`, `Severity`, `Phase`, `ERROR_CATALOG`, `error()`, `warning()` |
| [`importer/coercion.py`](../../src/pri/importer/coercion.py) | 560 | Every "what does this cell actually mean" decision |
| [`importer/limits.py`](../../src/pri/importer/limits.py) | 323 | Size caps, magic bytes, zip-slip and zip-bomb defence, `assert_safe_xml()` |
| [`importer/reader.py`](../../src/pri/importer/reader.py) | 407 | `read_workbook` — xlsx, xlsm, csv, zip-of-csv → `RawWorkbook` |
| [`importer/parser.py`](../../src/pri/importer/parser.py) | 1247 | The five validation phases and the `ProductionState` build |
| [`importer/template.py`](../../src/pri/importer/template.py) | 583 | `build_template()`, `export_state()`, `guard_formula()` |
| [`importer/service.py`](../../src/pri/importer/service.py) | 520 | Stage → review → commit / reject, and the 24-hour expiry |
| [`importer/samples.py`](../../src/pri/importer/samples.py) | 515 | Harbour Lights — a second production that is not the fixture |
| [`api/import_routes.py`](../../src/pri/api/import_routes.py) | 310 | Ten routes under `/api` |
| `persistence/migrations/002_import_staging.sql` | 38 | `import_staging`, expiry index, pending-duplicate index |
| [`web/components/DropZone.tsx`](../../web/components/DropZone.tsx) | 575 | The upload control |
| `web/app/import/page.tsx` | 183 | Upload, template, samples, the sheet contract |
| `web/app/import/[stagingId]/page.tsx` | 586 | The review screen |

**Modified:** `domain/models.py` (`Scene.number` is a string, `Scene.crew_ids`,
`quantise_money`), `engine/graph/dependency.py` (crew participate in the graph),
`persistence/bootstrap.py` (`split_sql_statements`), `data/fixtures/night_train.json`,
`web/components/Shell.tsx`, `web/app/page.tsx`, `web/app/layout.tsx`, `web/lib/api.ts`,
`web/lib/types.ts`.

## The seven sheets, as built

Defined once, in `WORKBOOK_SPEC`. The template, the exporter, the parser, the
error messages and the frontend's contract panel are all generated from it —
there is no second list of columns anywhere in the repository. **Bold** is
required.

| Sheet | | Key | Cap | Columns |
|---|---|---|---|---|
| `production` | required | — | 1 row | **production_id**, **title**, **currency**, **timezone**, **shoot_start**, **shoot_end**, reserve_days, **template_version** |
| `scenes` | required | `scene_id` | 2000 | **scene_id**, **number**, **slug**, description, **int_ext**, **time_of_day**, **estimated_minutes**, **location_id**, cast_ids, crew_ids, equipment_ids, vfx_plate, prerequisite_scene_ids |
| `people` | required | `person_id` | 1000 | **person_id**, **name**, **role**, character, daily_rate |
| `locations` | required | `location_id` | 1000 | **location_id**, **name**, kind, day_rate, permit_start_time, permit_end_time, **supports_int_ext**, **supports_time_of_day** |
| `equipment` | required | `equipment_id` | 1000 | **equipment_id**, **name**, kind, daily_rate |
| `availability` | optional | — | 5000 | **subject_type**, **subject_id**, **window_kind**, **from_date**, **to_date**, note |
| `schedule` | required | — | 500 | **date**, **call_time**, **wrap_time**, wrap_next_day, **location_id**, scene_ids, unit |

`scene_ids` on the schedule sheet is **not** required, against the first draft
of the spec. A reserve day, a travel day and a company move are all real rows on
a real board with no scenes on them, and W004 already exists to say "this day
has nothing on it" as a warning. Making the column required would have made a
legal board illegal.

## Every code, implemented

Eighteen blocking errors and thirteen warnings, each with a test that triggers
exactly it.

| | | | |
|---|---|---|---|
| E001 | Missing required sheet | E010 | Schedule date outside the shoot window |
| E002 | Missing required column | E011 | Wrap time is not after the call time |
| E003 | Value does not match the column's type | E012 | Empty workbook or wrong number of production rows |
| E004 | Value is not one of the permitted options | E013 | Production already exists |
| E005 | Duplicate id | E014 | Invalid availability combination |
| E006 | Reference to something that does not exist | E015 | That local time does not exist on that date |
| E007 | Prerequisite cycle | E016 | Unsupported file |
| E008 | Person used in the wrong role | E017 | Sheet is too large |
| E009 | Scene scheduled more than once | E018 | Availability window ends before it starts |

| | | | |
|---|---|---|---|
| W001 | Scene never scheduled | W008 | Overnight wrap inferred |
| W002 | Person never used | W009 | Ambiguous local time during a clock change |
| W003 | Equipment never used | W010 | Column looks like uncomputed formulas |
| W004 | Shooting day with no scenes | W011 | Ambiguous date format |
| W005 | Reserve day already has scenes | W012 | Merged cells expanded |
| W006 | Unknown columns ignored | W013 | Hidden sheets or rows were read |
| W007 | Template version mismatch | | |

Every issue carries the sheet, the row **as Excel numbers it**, the column, the
offending value and a fix hint. There is no bare "validation failed" anywhere
in the module.

## Decisions

**Errors and violations are different things, and the module never confuses
them.** An error is PRI's problem with the *file* — a missing sheet, an id that
points at nothing. It blocks. A constraint violation is the production's problem
with *itself* — a nine-hour turnaround already on the board. It does not block,
it is never repaired on the way in, and it is counted and shown. A board that
already breaks a rule is precisely the board PRI exists to help with; refusing
it would be refusing the job. `can_commit` is a function of errors only.

**Five phases, short-circuiting.** STRUCTURE → IDENTITY → REFERENCE → SEMANTIC →
WARNINGS. A workbook missing the `scenes` sheet produces one error about the
missing sheet, not four hundred about unresolvable scene ids. Each phase runs
only if the one before it found nothing.

**Nothing is written before the user says so.** The upload lands in
`import_staging` — parsed payload and report as JSONB, never the raw file — and
`state_versions` is not touched until `POST /api/import/{id}/commit`. Staged
rows expire after 24 hours.

**Re-import is not merge, and says so.** If `production_id` already exists that
is E013 and the fix is to change the id or delete the existing production. A
merge would have to guess which side of a conflict is authoritative, and
guessing wrong silently rewrites a schedule. Stated in the module docstring.

**Decimal everywhere, floats nowhere.** Money is `Decimal` quantised to two
places in the domain model, which is what made the round-trip digest-exact —
`Decimal("2400")` and `Decimal("2400.00")` are the same money and different
JSON.

**The exporter is the inverse of the parser, and that is tested.** `export_state`
writes a workbook that `parse_workbook` reads back to a state with an identical
`content_digest` — for the seeded fixture, for the second-unit sample, and for
100 randomly shaped productions. This is the strongest guarantee in the module:
it proves the spec covers its own output rather than just the file we happened
to write down.

**No LLM. Anywhere.** Not for column matching, not for the fix hints, not for
guessing a date format. An import is a legal record of what a production
committed to.

## Security

| Attack | Defence |
|---|---|
| Oversized upload | 10 MB cap, enforced in 1 MB chunks *while reading*, before the body is buffered |
| Renamed executable | Magic bytes, not the extension; OLE2 gets "Save As .xlsx" rather than a stack trace |
| XXE / billion laughs | `defusedxml` asserted present at import time — the module refuses to load without it |
| Zip slip | Any entry with an absolute path or `..` is rejected |
| Zip bomb | 100:1 ratio cap, 50 MB total uncompressed, 32 entries |
| Slow-parse DoS | 20-second wall-clock budget, and per-sheet row caps → E017 |
| Formula injection | Every string written to xlsx or csv is guarded; `=`, `+`, `-`, `@`, tab and CR are prefixed |
| Header injection | Every `Content-Disposition` filename is sanitised |
| Unauthenticated writes | Upload, commit, reject and purge carry the existing API key |
| Data at rest | The raw file is never stored — only the parsed payload and the report |

## The frontend

`DropZone` tracks drag state with a **depth counter**, incremented on
`dragenter` and decremented on `dragleave`, treating the zone as inactive only
at zero. `dragleave` fires every time the pointer crosses into a child element,
so the obvious implementation flickers continuously while a file is held over
it, and `relatedTarget` does not fix it — it is null on some cross-document
transitions.

`dragover` and `drop` are cancelled at the *window* level whatever the state,
including while the zone is disabled: a file dropped anywhere the page does not
handle makes the browser navigate to it and destroy whatever was on screen. The
whole page is the drop target, because a 1st AD is aiming at a window, not at a
dashed rectangle.

The state machine is written down — `idle → dragActive → validating → uploading
→ analyzing → done | rejected | failed` — with an `aria-live` region that
announces each transition, XHR progress, `AbortController` cancel, retry, and
one specific message per rejection reason: more than one file, a folder, zero
bytes, wrong extension, over the limit, already uploaded.

The review screen is four tabs — Errors, Schedule health, Preview, Warnings —
opening on whichever one the user actually has to deal with, a "copy all"
button so a 1st AD can fix forty rows in Excel with the list beside them, and a
pinned footer carrying **Import as version 1** and **Discard**.

## How to run it

```bash
make migrate                                   # applies 002_import_staging.sql
python -m pri.importer.samples                 # writes data/samples/*.xlsx
pytest tests/importer -q                       # 314
pytest tests/artifacts/test_call_sheet.py -q   # 4
```

Then `http://localhost:3000/import` — drop `data/samples/second_unit.xlsx`.

## Acceptance criteria

- ✅ Seed → export → re-import → identical `content_digest`
- ✅ 100 randomly generated productions all round-trip
- ✅ A production imported from a workbook runs a full disruption/recovery cycle:
  impact `('HL-S3','HL-S4')`, downstream `('HL-S7',)`, blast radius 0.333,
  4 candidates, Pareto `['B2']`
- ✅ A board with a 9.5-hour turnaround imports with `can_commit=True` and
  exactly one C001 reporting `observed="9.5h"`
- ✅ One test per error code, E001 to E018
- ✅ Zip slip, zip bomb, entry count and XXE each refused by a test
- ✅ ruff, `ruff format`, `mypy --strict`, `tsc --noEmit`, eslint, `next build`
- ✅ No LLM call anywhere in the module

## Not covered

**Merge on re-import does not exist.** E013 and a manual delete. Deliberate,
documented in the module docstring, and the single largest gap.

**There is no partial import.** One blocking error means nothing is staged for
commit, however good the other six sheets are. For a 500-row board with one bad
cell that is the right call; for a 2,000-scene board it will be irritating.

**Column names are matched exactly** (after case and whitespace normalisation).
There is no fuzzy matching of `Scene #` to `number`, and by design — a
mis-matched column silently imports the wrong data, and the template exists to
make matching unnecessary.

**`config/logistics.yaml` is still keyed by the fixture's location ids.** An
imported production's call sheet renders with em-dashes where the address,
parking note and hospital would be. It degrades rather than failing —
`tests/artifacts/test_call_sheet.py` pins that — but a real deployment needs
this data to come from somewhere per-production, and the workbook has no column
for it.

**The exporter refuses irregular permit windows.** A location whose permits
follow more than one daily pattern (weekdays 07:00–19:00, Sundays 09:00–14:00)
cannot be written to the workbook format at all, and `export_state` raises
rather than silently dropping half of it. The parser can only produce the
regular case, so a state that came in through an import always exports; a state
built by hand may not.

**The review screen's Preview tab shows counts, not rows.** The parsed payload
is in the database and is not served, so a user cannot see the schedule they are
about to import day by day — only how many days there are.
