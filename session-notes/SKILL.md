---
name: session-notes
description: "Standards and workflow for creating and managing session notes. Use when starting a new session, recording major decisions, or when the user asks to 'take notes'."
---
# Session Notes

Record session notes as Markdown files in `${PROJECT_DIR}/.agents/notes/`.

## Filename Convention

**Format**: `YYYY-MM-DD-n.md`

- `YYYY-MM-DD` — current date
- `n` — incrementing session number for the day (starting at `1`)

## Template

```markdown
# Session Note: [Date] - Session [N]

## Goals
[Main objectives for this session]

## Key Activities
[Tasks performed, files edited, research done]

## Decisions Made
[Critical choices, architectural decisions, user approvals]

## Outcomes / Next Steps
[What was accomplished, what remains]
```

## Workflow

1. Ensure `${PROJECT_DIR}/.agents/notes/` exists (create it if not).
2. List existing files matching today's date to determine the next session number.
3. Create the file and populate it with the template above.
