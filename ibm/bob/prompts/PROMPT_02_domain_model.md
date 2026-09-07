# PROMPT 02 — Typed Domain Model

## Task

Build the typed domain model in `src/pri/domain/models.py`.

## Models Required

All Pydantic v2 models, all frozen (immutable), all with explicit types:

```
Production(id, title, currency, shoot_start, shoot_end, reserve_days: list[date])

Scene(id, number, slug, description, int_ext: Literal["INT","EXT"],
      time_of_day: Literal["DAY","NIGHT","DAWN","DUSK"],
      estimated_minutes: int, location_id, cast_ids: list[str],
      equipment_ids: list[str], vfx_plate: bool, prerequisite_scene_ids: list[str])

Person(id, name, role: Literal["CAST","CREW"], character: str | None,
       daily_rate: Decimal, unavailable_windows: list[TimeWindow])

Location(id, name, kind, day_rate: Decimal, permit_windows: list[TimeWindow],
         supports_int_ext: list[str], supports_time_of_day: list[str])

Equipment(id, name, kind, daily_rate: Decimal, available_windows: list[TimeWindow])

TimeWindow(start: datetime, end: datetime)  # half-open [start, end)

ShootingDay(date, call_time: datetime, wrap_time: datetime, location_id,
            scene_ids: list[str], unit: str = "MAIN")

Schedule(days: list[ShootingDay])

ProductionState(production, scenes, people, locations, equipment, schedule,
                version: int, parent_version: int | None, event_id: str | None,
                created_at: datetime)

DisruptionEvent(event_id, production_id, event_type, occurred_at, source,
                severity, payload: dict)
  event_type is a Literal of: "location.blocked", "actor.unavailable",
  "equipment.failed", "weather.changed", "crew.unavailable"
```

## Move — Discriminated Union

A discriminated union (field `kind`) of:
- `MoveSceneToDay(scene_id, target_date)`
- `SwapDays(date_a, date_b)`
- `ShiftCallTime(date, new_call_time)`
- `RelocateScene(scene_id, target_location_id)`

## Additional Models

```
CandidatePlan(id, label, base_version: int, moves: list[Move],
              rationale_hint: str | None)

ConstraintViolation(code, severity: Literal["HARD","SOFT"], message,
                    subject_ids: list[str], observed: str, required: str)

PlanScore(schedule_delay_days: float, incremental_cost: Decimal,
          operational_risk: float, affected_scene_count: int,
          crew_disruption_hours: float, downstream_dependency_impact: int)

EvaluatedPlan(plan, valid: bool, violations: list[ConstraintViolation],
              score: PlanScore | None, resulting_state_digest: str | None,
              pareto_optimal: bool = False)
```

## Helper Required

`ProductionState.apply(moves) -> ProductionState` — returns a NEW state with
`version+1` and `parent_version` set. Applies moves mechanically. Does NOT
validate — validation is a separate engine concern.

## Tests Required

- Immutability is enforced
- `apply()` never mutates the input
- Version chain is correct
- Each Move kind applies correctly

## Acceptance Criteria

- mypy strict passes on `src/pri/domain`
- All tests green

## Constraints

- DO NOT put any database, IO, or LLM code in this module
