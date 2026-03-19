---
name: session-workflow
description: >-
  Project session workflow using Beads (bd) for issue tracking.
  Use when starting a session, ending a session, committing work,
  or when the user asks to land/wrap up/finish. Ensures work is
  tracked, committed, and pushed before the session ends.
---

# Session Workflow

This project uses **bd** (beads) for issue tracking.

## Quick Reference

```bash
bd ready              # Find available work
bd show <id>          # View issue details
bd update <id> --status in_progress  # Claim work
bd close <id>         # Complete work
bd sync               # Sync with git
```

## Landing the Plane (Session Completion)

**When ending a work session**, complete ALL steps. Work is NOT done until `git push` succeeds.

1. **File issues** for remaining work
2. **Run quality gates** if code changed — tests, linters, builds
3. **Update issue status** — close finished, update in-progress
4. **Push to remote:**
   ```bash
   git pull --rebase
   bd sync
   git push
   git status  # MUST show "up to date with origin"
   ```
5. **Clean up** — clear stashes, prune branches
6. **Verify** — all changes committed AND pushed
7. **Hand off** — provide context for next session

## Critical Rules

- Work is NOT complete until `git push` succeeds
- NEVER stop before pushing — that leaves work stranded locally
- NEVER say "ready to push when you are" — YOU must push
- If push fails, resolve and retry until it succeeds
