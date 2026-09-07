# PROMPT 20 — README, Evidence, Compliance

## Task

Produce the **judged surface** of the repository.

## `README.md`, in this order

1. One-line pitch + a 20-second "what you are looking at" paragraph
2. **The live demo URL and the demo video link, above the fold**
3. An animated GIF or screenshot of the **invalid-plan to replan** moment
4. Architecture diagram (a **single** Mermaid, the runtime flow — not eleven diagrams)
5. "How PRI works" — event, twin, impact, counterfactuals, deterministic
   validation, governance, approval, execution, verification
6. "Why the numbers are trustworthy" — the Gemini/deterministic split, stated
   plainly, with a pointer to `validator.py`
7. Quickstart: local (docker-compose + `make seed` + `make demo`) and cloud
8. Technology table: what each service does and **the file where it is called**
9. Compliance section: Google Cloud SDK imported at `src/pri/agent/root_agent.py`,
   Confluent at `src/pri/events/consumer.py`, IBM Bob evidence at `ibm/bob/`
10. Limitations and what we would build next — state them honestly, judges reward
    it and dishonesty is detectable

## `docs/evidence/`

| Folder | Contents |
|---|---|
| `ibm-bob/` | every prompt + Bob artifact + screen recordings + a `development-log.md` narrating which components Bob built |
| `confluent/` | topic config screenshot, consumer logs showing a real message |
| `google-cloud/` | Cloud Run services, a Gemini call trace |
| `runtime/` | a full pipeline log from event to verification |

## `docs/hackathon/compliance-matrix.md`

Each requirement, the implementation, the **exact file and line**, and the
evidence path. **Mark nothing complete without evidence.**

## Licensing

`LICENSE` must remain stock Apache-2.0 so GitHub's detector shows it in the About box.

## Acceptance Criteria

A stranger who reads only the README understands the product in **60 seconds** and
can run it in **10 minutes**.
