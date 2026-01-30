# Botbaki Development Plan

## Current State

Botbaki is a CLI tool that:
- Syncs PR data from GitHub to SQLite
- Generates AI reviews using Claude (Sonnet or Opus)
- Caches reviews to avoid regenerating for the same commit/prompt
- **NEW:** Runs as a polling daemon watching for `@botbaki` triggers

**Cost per review:** ~$0.05 (Sonnet) or ~$0.20 (Opus)

## Completed

### Polling Daemon (v0.2)

- `botbaki daemon` command runs a polling loop
- Checks for new `@botbaki` comments every 2 minutes
- Supported commands:
  - `@botbaki review` - generate review for current PR
  - `@botbaki review --diff-only` - review without timeline context
  - `@botbaki help` - show available commands
- Posts reviews as issue comments
- Tracks processed triggers to avoid duplicate responses
- Systemd service file for deployment (`service/botbaki.service`)

**Configuration:**
- Hardcoded to `leanprover-community/mathlib4`
- Anyone can trigger reviews
- Uses `gh` CLI for GitHub API access

## Next Steps

### 1. Inline Review Comments

Currently reviews are posted as a single issue comment. Future improvement:
- Parse review output to identify file-specific suggestions
- Post as a GitHub PR review with inline comments on specific lines
- Benefits: easier for authors to see suggestions in context

**Implementation notes:**
- Use `POST /repos/{owner}/{repo}/pulls/{pull_number}/reviews` API
- Include `comments` array with `path`, `line`, `body` for each inline comment
- Need to map suggestions to actual diff lines (tricky with context)

### 2. Webhooks (Future)

For real-time response instead of 2-minute polling delay:
- Requires public endpoint (Cloudflare Tunnel, ngrok, or cloud hosting)
- Subscribe to `issue_comment` webhook events
- Keep polling as fallback for missed webhooks

### 3. Opt-in System for PR Authors

Proactively offer reviews to PR authors who want them.

**User preference storage:**
- Database table: `user_preferences(github_login, wants_auto_review, created_at, updated_at)`
- Default: no auto-review (opt-in required)

**Opt-in flow:**
- When a new PR is opened by unknown user, comment asking if they'd like reviews
- User replies `@botbaki yes` or `@botbaki no` to set preference
- Preference persists for future PRs
- User can change preference anytime with `@botbaki opt-in` / `@botbaki opt-out`

**Auto-review behavior:**
- For opted-in users: automatically review new PRs and force-pushes
- Respect rate limits (max N reviews per PR? per day?)

### 4. Feedback Collection

Allow users to rate botbaki's suggestions for prompt improvement.

**Inline feedback mechanism:**
- Each suggestion in the review gets a unique ID
- Users can react or reply:
  - 👍 / 👎 reactions on the comment
  - `@botbaki +1 <id>` / `@botbaki -1 <id>` for specific suggestions
  - `@botbaki feedback <id> <text>` for detailed feedback

**Database schema:**
```sql
CREATE TABLE review_feedback (
    id INTEGER PRIMARY KEY,
    review_id INTEGER REFERENCES pr_reviews(id),
    suggestion_id TEXT,           -- e.g., "issue-1", "suggestion-3"
    suggestion_text TEXT,         -- the actual suggestion text
    feedback_type TEXT,           -- 'positive', 'negative', 'detailed'
    feedback_text TEXT,           -- user's detailed feedback if any
    github_login TEXT,
    created_at TEXT
);
```

**Feedback review process:**
- Periodic export of feedback for human review
- `botbaki feedback-report` command to summarize:
  - Most upvoted/downvoted suggestion patterns
  - Common complaints
  - Suggestions that were wrong vs. unhelpful vs. good
- Update prompts/templates based on patterns
- Track prompt versions and measure improvement over time

## Architecture Sketch

```
┌─────────────────┐     ┌──────────────┐
│  GitHub         │────▶│  Webhook     │
│  (PRs/Comments) │     │  Receiver    │
└─────────────────┘     └──────┬───────┘
                               │
                               ▼
                        ┌──────────────┐
                        │  Event       │
                        │  Queue       │
                        └──────┬───────┘
                               │
                               ▼
                        ┌──────────────┐
                        │  Worker      │
                        │  (reviews,   │
                        │   feedback)  │
                        └──────┬───────┘
                               │
              ┌────────────────┼────────────────┐
              ▼                ▼                ▼
       ┌──────────┐     ┌──────────┐     ┌──────────┐
       │ SQLite   │     │ Claude   │     │ GitHub   │
       │ Database │     │ API      │     │ API      │
       └──────────┘     └──────────┘     └──────────┘
```

## Open Questions

- Should reviews be posted as PR comments or GitHub review objects (with inline comments)?
- How to handle very large PRs that exceed context limits?
- Should botbaki have any "memory" of previous reviews on the same PR?
- Cost controls: daily/monthly budget caps?
- Multiple repos: mathlib4 only, or configurable per-repo prompts?

## Priority Order

1. **GitHub comment triggers** - most useful for testing with real users
2. **Feedback collection** - start gathering data early
3. **Online service** - needed for production use
4. **Opt-in system** - nice to have once service is stable
