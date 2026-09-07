# PROMPT 05 — Seed Fixture: The Demo Production

## Task

Create the demo production fixture and loader.

Create `data/fixtures/night_train.json` containing the production described in
[APPENDIX_A_demo_fixture.md](APPENDIX_A_demo_fixture.md), and
`src/pri/persistence/seed.py` with:

```python
seed(production_id="film-001", reset: bool = True) -> ProductionState
```

which wipes and re-inserts the fixture as **version 1**, and is wired to `make seed`.

> **Paste the full text of [APPENDIX_A_demo_fixture.md](APPENDIX_A_demo_fixture.md) here
> when running this prompt in a fresh Bob session.**

## Non-negotiable

Every number in the fixture is **load-bearing** for the demo — reproduce it exactly.

Validate on load: the seeded state must pass the constraint validator from
[PROMPT 06](PROMPT_06_constraint_validator.md) with **zero HARD violations**.
Add a test asserting exactly that.

## Acceptance Criteria

- `make seed` produces a valid version-1 state
- The validity test passes

## Constraints

- DO NOT invent extra scenes
- DO NOT round the times
- DO NOT "improve" the numbers
