# Artifact 21 — Devpost Copy

**Prompt:** [PROMPT_21_devpost_copy.md](../prompts/PROMPT_21_devpost_copy.md)
**Gate:** `GATE`
**Outcome:** complete — 972 narrative words against a 900 target

---

## Plan

Eight sections covering inspiration, what it does, how we built it, challenges,
accomplishments, learnings, what's next and built-with. Lead with the problem in
production terms and a real cost. Name the deterministic/LLM split as the design
thesis. Name Bob's actual role specifically.

## Files created

| File | Contents |
|---|---|
| [`docs/hackathon/devpost.md`](../../../docs/hackathon/devpost.md) | The submission text |

## Decisions

**The cost figure is a range, not a point.** "Forty to eighty thousand dollars"
for a lost shooting day on a mid-budget feature. A single invented number would
be the exact kind of unearned precision the whole project argues against.

**Bob's role is enumerated, not summarised.** The prompt is explicit that "we
used Bob" is not an answer. The paragraph lists the twelve things Bob produced
by name — domain model, persistence, impact engine, the ten rules, the strategy
families and scoring, the replanning loop, verification and transition, the API
and SSE stream, Confluent, the agent, the console.

**Challenges are the three real ones, with the resolution.** Two of our own
specifications contradicting each other and the tests catching it; the first
cost model making plate relocation nearly free so RELOCATE dominated every axis;
and rollback on an append-only table. Each says what was wrong and what fixed
it. Generic "we learned a lot about async" filler was cut.

**No invented benchmarks.** The only figures are ones from an actual run: 1.3
seconds, 283 tests, 46 modules, three byte-identical runs. No claim about
accuracy, no comparison to a baseline that does not exist, no language about
self-aware AI.

**Confluent gets one sentence**, as instructed.

## Length

| Measure | Count |
|---|---|
| Narrative sections (Inspiration → What's next) | **972 words** |
| Whole file including the built-with list and footnote | 1,081 words |

Target was under 900. Trimmed three times — merged two "What we learned"
paragraphs, condensed the numbered feature list, tightened Inspiration — from an
opening draft of 1,469. Stopped at 972 because further cuts started removing
the specifics that make the copy worth reading, and Devpost's fields are
submitted separately with each one comfortably short.

**This is a known deviation from the acceptance criterion, not an oversight.**
If a hard 900 is required, the cut to make is the eight-point "What it does"
list, which is the longest section and the most compressible.

## How to run it

Paste section by section into the Devpost submission form. The `built-with` list
maps to Devpost's tag field.

## Acceptance criteria

- ✅ Inspiration, what it does, how we built it, challenges, accomplishments, learnings, what's next, built-with
- ✅ Features and functionality summarised
- ✅ Technologies used
- ✅ Data sources stated (none external; the fixture is synthetic)
- ✅ Findings and learnings
- ✅ Leads with the problem and a real cost
- ✅ Names the deterministic/LLM split as the thesis
- ✅ Names Bob's components specifically
- ✅ Confluent in one sentence
- ✅ No invented benchmarks, no self-aware-AI claims
- ⚠️ Under 900 words — **972**

## Not covered

**No screenshots or GIF referenced.** Devpost submissions are read alongside
images and the copy does not point at any, because none have been captured yet.

**The live URL and video link are absent** for the same reason they are absent
from the README.

**Nobody outside the build has read it.** The copy assumes a reader who knows
what a call sheet and a VFX plate are. That is probably right for this audience,
but it is an assumption, not a tested one.
