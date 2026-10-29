# Feedback Loop

`session-storyboard` is bundled with `industrial-agent-builder`. Performance findings can flow back into the builder only through an explicit, evidence-backed issue.

## Candidate rules

A candidate must be:

- general across industrial domains;
- backed by at least one trace evidence reference;
- actionable in scaffold, contract, reference, test, or tooling;
- verifiable with a metric and a new DeepWorks test session.

Do not submit one-off platform incidents, business-specific slowness, missing evidence, or speculative architecture changes.

## Consent

After reading a generated story:

1. collect all candidates that pass the rules above;
2. show them together with observation, evidence, mechanism, recommendation, and validation;
3. let the user select individual candidates;
4. write one JSON file per selected candidate;
5. run `scripts/feedback_issue.py` only for the selected candidates.

Never create an issue before the user has selected that candidate.

## Candidate JSON

```json
{
  "schema_version": "session-storyboard-feedback/1",
  "scope": "general",
  "title": "Avoid a duplicate member hop for confirm-and-execute",
  "observation": "The trace shows two member tasks for the same business root; the second task adds more platform wait than script time.",
  "mechanism": "Each member hop pays claim, start, model turns, and tool round trips; splitting confirmation from execution amplifies that floor cost.",
  "recommendation": "When confirmation and execution share one business root and one idempotency key, generate them as one self-contained member execution.",
  "validation": "Generate a team, run a new DeepWorks test session, and verify dispatched-child count and end-to-end duration decrease while the business result stays unchanged.",
  "affected_areas": ["scaffold", "contract", "test"],
  "evidence": [
    {
      "ref": "task:t1",
      "summary": "Two member tasks share child issue child-1 and have overlapping business roots."
    },
    {
      "ref": "round:R2",
      "summary": "The slower round includes both member tasks and a leader summary tail."
    }
  ]
}
```

The script derives a SHA-256 idempotency fingerprint from the normalized finding content and embeds it in the issue body. It searches existing open and closed issues before creating a new one. GitHub issue creation is not an atomic idempotent operation, so a rare concurrent race can still produce a duplicate; automation must close duplicates that carry the same fingerprint.

## Safety

- Keep trace output in `tmp/` and never commit it.
- Use only redacted evidence in candidate JSON.
- Do not include credentials, cookies, production ticket attachments, or private user input.
- Do not use an LLM to perform the GitHub side effect; the deterministic script is the only submission path.
