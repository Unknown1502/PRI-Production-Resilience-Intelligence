# PRI — Bob Session Artifacts

One file per Bob session, matching the prompt of the same number in
[`../prompts/`](../prompts/). Each records what the session planned, the files
it created and modified, the decisions it had to make, how to run the result,
and — the part that matters most — what it did **not** cover.

The format is the one [Appendix B](../prompts/APPENDIX_B_guardrails.md) demands
at the end of every task:

> End with: files created, files modified, how to run it, what is not covered.

## Index

| Artifact | Prompt | Built | Tests |
|---|---|---|---|
| [00-system-rules](00-system-rules.md) | [00](../prompts/PROMPT_00_system_rules.md) | Contract acknowledged | — |
| [01-repo-skeleton](01-repo-skeleton.md) | [01](../prompts/PROMPT_01_repo_skeleton.md) | Package layout, `config.py`, Makefile | — |
| [02-domain-model](02-domain-model.md) | [02](../prompts/PROMPT_02_domain_model.md) | `domain/models.py` | 56 |
| [03-persistence](03-persistence.md) | [03](../prompts/PROMPT_03_persistence.md) | `persistence/` | 24 |
| [04-graph-impact](04-graph-impact.md) 🎥 | [04](../prompts/PROMPT_04_graph_impact.md) | `engine/graph/dependency.py` | 41 |
| [05-seed-fixture](05-seed-fixture.md) | [05](../prompts/PROMPT_05_seed_fixture.md) | `night_train.json`, `seed.py` | 9 |
| [06-constraint-validator](06-constraint-validator.md) 🎥 | [06](../prompts/PROMPT_06_constraint_validator.md) | `engine/constraints/validator.py` | 44 |
| [07-candidates-scoring](07-candidates-scoring.md) | [07](../prompts/PROMPT_07_candidates_scoring.md) | `engine/simulation/`, `scoring.yaml` | 25 |
| [08-verification-transition](08-verification-transition.md) | [08](../prompts/PROMPT_08_verification_transition.md) | `verify.py`, `transition.py` | 27 |
| [09-fastapi](09-fastapi.md) | [09](../prompts/PROMPT_09_fastapi.md) | `api/` | 26 |
| [10-confluent-events](10-confluent-events.md) | [10](../prompts/PROMPT_10_confluent_events.md) | `events/` | 7 |
| [11-call-sheet](11-call-sheet.md) | [11](../prompts/PROMPT_11_call_sheet.md) | `artifacts/call_sheet.py` | — |
| [12-adk-agent](12-adk-agent.md) 🎥 | [12](../prompts/PROMPT_12_adk_agent.md) | `agent/` | 24 |
| [13-frontend-scaffold](13-frontend-scaffold.md) | [13](../prompts/PROMPT_13_frontend_scaffold.md) | `web/` shell + overview | — |
| [14-impact-recovery-screens](14-impact-recovery-screens.md) | [14](../prompts/PROMPT_14_impact_recovery_screens.md) | Disruption, Recovery | — |
| [15-governance-screens](15-governance-screens.md) | [15](../prompts/PROMPT_15_governance_screens.md) | Governance, Verification, Audit | — |
| [16-cloud-run-deploy](16-cloud-run-deploy.md) | [16](../prompts/PROMPT_16_cloud_run_deploy.md) | Dockerfiles, `infra/deploy.sh` | — |
| [17-demo-runner](17-demo-runner.md) | [17](../prompts/PROMPT_17_demo_runner.md) | `run_demo.py`, reset, fallback | — |
| [19-critical-path-tests](19-critical-path-tests.md) | [19](../prompts/PROMPT_19_critical_path_tests.md) | E2E suite, CI | 26 |
| [20-readme-evidence](20-readme-evidence.md) | [20](../prompts/PROMPT_20_readme_evidence.md) | README, evidence, compliance | — |
| [21-devpost-copy](21-devpost-copy.md) | [21](../prompts/PROMPT_21_devpost_copy.md) | `docs/hackathon/devpost.md` | — |

Prompt 18 (watsonx Orchestrate A2A) was not started — it is marked `OPTIONAL`
and is first on [Appendix C](../prompts/APPENDIX_C_cut_ladder.md)'s cut ladder.

🎥 = session recorded in full for the demo video.

## Totals

**46 source modules**, ~10,700 lines of Python, plus 16 files of TypeScript/TSX.
**283 tests**, of which 66 run against a real PostgreSQL.

```
ruff check          All checks passed
ruff format         77 files already formatted
mypy --strict       Success: no issues found in 46 source files
pytest              283 passed
web: tsc --noEmit   clean
web: eslint         No warnings or errors
web: next build     9 routes, standalone output
```

These totals are Bob's sessions only. Later work on this repository was done
with other tools and is logged in [`docs/build-log/`](../../../docs/build-log/);
it is deliberately not counted here.

## Where the sessions were wrong

Collected in one place because the corrections are better evidence of a real
process than the successes. Full write-ups in
[`docs/evidence/ibm-bob/development-log.md`](../../../docs/evidence/ibm-bob/development-log.md).

| Session | What was wrong | Why it mattered |
|---|---|---|
| 02 | `SwapDays` exchanged only the scene lists | A day swap moves the location and the clock too. This is what produces the 9.0h violation the whole demo turns on |
| 02 | `ShiftCallTime` moved the wrap with the call | Traded a turnaround violation for a permit violation |
| 04 | Descendants walked resource edges as well as prerequisite edges | Made every scene downstream of every other; blast radius meaningless |
| 06 | C008 compared dates loosely | Tightening it exposed two illegal edges in the fixture and a real bug in the deferral family |
| 07 | Relocating a VFX plate was priced near zero | The Pareto frontier collapsed onto the wrong plan |
| 07/08 | Two modules built against an earlier contract | `simulation/planner.py` and `verification/verifier.py` were replaced, not adapted |
| 09 | `/api/demo/reset` assumed a migrated schema | That is the judge's Demo button; it now runs the migrations itself |
