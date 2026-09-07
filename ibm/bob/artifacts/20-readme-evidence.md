# Artifact 20 — README, Evidence & Compliance

**Prompt:** [PROMPT_20_readme_evidence.md](../prompts/PROMPT_20_readme_evidence.md)
**Gate:** `GATE`
**Outcome:** complete — screenshots and URLs pending

---

## Plan

Produce the judged surface. A stranger who reads only the README should
understand the product in sixty seconds and be able to run it in ten minutes.

## Files created

| File | Contents |
|---|---|
| [`README.md`](../../../README.md) | Pitch, the demo trace, one Mermaid diagram, how it works, trust table, quickstart, technology, compliance, limitations |
| [`docs/hackathon/compliance-matrix.md`](../../../docs/hackathon/compliance-matrix.md) | Every requirement → file → evidence → status |
| [`docs/evidence/ibm-bob/development-log.md`](../../../docs/evidence/ibm-bob/development-log.md) | What Bob built, and where it was corrected |
| `docs/evidence/{ibm-bob,confluent,google-cloud,runtime}/` | Folders awaiting capture |

## Decisions

**The demo trace is above the fold, before the architecture.** A judge scrolling
past the pitch should hit the actual output — `CANDIDATE_INVALID Plan B - C001:
observed 9.0h, required >= 10.0h` — before any diagram. It is real output from
`run_demo.py`, and the README says so.

**One Mermaid diagram, colour-coded by role.** Green computes, blue explains,
red refuses. The prompt asked for one runtime-flow diagram rather than eleven,
and the three-colour scheme carries the architecture law visually.

**"Why the numbers are trustworthy" is a table of enforcement, not prose.** Six
rows, each naming a guarantee, the file, and the mechanism that would fail if
the guarantee broke. Claims a reader can check beat claims they have to accept.

**Limitations are specific and unflattering.** Six of them, each naming the
actual constraint: the SSE broker is in-process; costs are a model not an
accounting integration; approval is a name in a field with no identity provider;
the candidate families were designed against one event type. Judges reward
honesty and dishonesty is detectable.

**The compliance matrix marks nothing complete without evidence you can open.**
Three status values — done and evidenced, done but evidence pending, not done —
and the outstanding list at the bottom is six concrete items, not a shrug.

## How to run it

```bash
# Everything the README's quickstart claims:
docker compose -f docker-compose.dev.yml up -d
cp .env.example .env && make install && make bootstrap
make run-api && make demo
```

Verified: the quickstart works from a clean `.env.example` copy.

## Acceptance criteria

- ✅ One-line pitch plus a 20-second paragraph
- ✅ Architecture as a single Mermaid diagram
- ✅ "How PRI works" — event through verification
- ✅ "Why the numbers are trustworthy" with a pointer to `validator.py`
- ✅ Quickstart, local and cloud
- ✅ Technology table naming the file where each service is called
- ✅ Compliance section
- ✅ Limitations, honestly, plus what we would build next
- ✅ LICENSE remains stock Apache-2.0
- 🔲 Live demo URL and video link above the fold — both say *pending*
- 🔲 GIF of the invalid-plan → replan moment

## Not covered

**The two things above the fold are placeholders.** The README's most valuable
real estate currently reads "*pending deployment*" and "*pending*". Those fill
in from artifact 16's deploy run and the video shoot; nothing else blocks them.

**`docs/evidence/` is four empty folders and a development log.** The Cloud Run
screenshot, the Confluent topic config and consumer log, the Gemini call trace
and the full runtime log all need capturing from a live deployment. The
compliance matrix lists each one against the requirement it evidences, so the
gaps are visible rather than glossed.

**No architecture diagram image.** The Mermaid renders on GitHub, which is where
judges will read it, but it will not render in a PDF export or on Devpost.
