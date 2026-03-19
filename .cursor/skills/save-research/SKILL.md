---
name: save-research
description: >-
  Save technical research results to the researches/ folder. Use when completing
  a research task, investigation, hardware/software compatibility study, or any
  exploration that produced reusable findings with external references.
---

# Save Research

When a research/investigation task is complete, persist the findings as a markdown file in `researches/`.

## File naming

```
researches/YYYY-MM-DD_<slug>.md
```

- `YYYY-MM-DD` — date the research was concluded
- `<slug>` — lowercase, hyphens, 2-5 words summarizing the topic

Examples: `2026-03-18_led-feedback-hardware.md`, `2026-04-01_midi-sysex-protocol.md`

## Document structure

```markdown
# YYYY-MM-DD — <Title>

## Goal
One sentence: what question were we trying to answer.

## Context
Device, software versions, environment — whatever scopes the research.

## Findings
The meat: what was discovered, tested, measured. Use tables and code blocks.

## Conclusion
Clear verdict. Was the goal achieved? What's the practical outcome?

## References
- [Label](URL) — one-line description of what the link covers
```

## Rules

- **One file per research topic.** Don't append to old files — create a new dated entry if revisiting.
- **Always include References** with working URLs. These are the main long-term value.
- **Findings should be reproducible** — include exact commands, note numbers, config values that were tested.
- **State negative results explicitly.** "X does not work because Y" is valuable knowledge.
- **No prose filler.** Tables > paragraphs for structured data.
- **Never include sensitive information** — no API keys, tokens, passwords, private URLs, personal data, internal hostnames, or credentials. If a finding depends on a secret, reference it by name (e.g. "set `OPENAI_API_KEY`") without the actual value.
