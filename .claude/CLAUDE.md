# Botbaki Project Instructions

## Sync Behavior

When testing the daemon after clearing database records:
- Clearing `issue_comments` or `processed_triggers` doesn't trigger a re-sync
- The PR's `updated_at` must be newer than `last_sync` for incremental sync to fetch it
- Use `botbaki sync <repo> --pr <number>` to force-sync a specific PR after clearing test data
