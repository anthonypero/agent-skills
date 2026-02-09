---
name: session-notes
description: "Standards and workflow for creating and managing session notes. Use when starting a new session, recording major decisions, or when the user asks to 'take notes'."
---
# Session Notes

This skill outlines the process for recording session notes project work.

## Filename Convention

Session notes are stored in the `${PROJECT_DIR}/.agents/notes/` directory.

**Format**: `YYYY-MM-DD-n.md`

* `YYYY-MM-DD`: The current date.
* `n`: Incrementing number for the day (e.g., `1` for the first session, `2` for the second).

## Content Structure

Notes should be written in Markdown and summarize the session's essence.

```markdown
# Session Note: [Date] - Session [N]

## Goals
[What were the main objectives?]

## Key Activities
[Major tasks performed, files edited, research done]

## Decisions Made
[Critical choices, architectural decisions, user approvals]

## Outcomes / Next Steps
[What was accomplished? What is left for next time?]
```

## Workflow

1. **Verify Directory**: Check if `${PROJECT_DIR}/.agents/notes/` exists.
    * *If it does not exist*: Create the directory.
    * *If it exists*: Proceed to the next step.
2. **Determine Session Number (`n`)**: Run `ls ${PROJECT_DIR}/.agents/notes/YYYY-MM-DD-*` (using today's date).
    * *If files exist*: Extract the `n` values, find the maximum, and set the new `n` to `max + 1`.
    * *If no files exist*: Set `n` to `1`.
3. **Create File**: Create the new note file at `${PROJECT_DIR}/.agents/notes/YYYY-MM-DD-n.md`.
4. **Populate**: Write the [Content Structure](#content-structure) template into the new file.
