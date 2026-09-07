# Appendix B — Guardrail Block

Append this to any prompt where Bob starts drifting.

```
CONSTRAINTS FOR THIS TASK:
- Touch only the files I listed. If you believe another file must change, say so
  and wait.
- No new dependencies without naming them and why.
- No LLM call in deterministic code paths.
- Do not write a number into code that is not in config/*.yaml or a fixture.
- If a library signature is uncertain, read the installed package rather than
  guessing, and tell me what you found.
- End with: files created, files modified, how to run it, what is not covered.
```
