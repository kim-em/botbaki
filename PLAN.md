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

### Deployment (v0.2.1)

Deployed to `chonk.lean-fro.org` as a systemd service:
- Service: `botbaki.service`
- Logs: `journalctl -u botbaki -f`
- Config: `~/.config/botbaki/env` (contains `ANTHROPIC_API_KEY`)
- Data: `~/projects/botbaki/data/botbaki.db`

**Management:**
```bash
sudo systemctl status botbaki   # Check status
sudo systemctl restart botbaki  # Restart
sudo journalctl -u botbaki -f   # Follow logs
```

## Bugs to Fix (2026-01-30)

Issues discovered during first live test on PR #34594.

### Critical

1. **`-f body=@-` doesn't work for stdin** - Posts literal `@-` instead of reading stdin content. Need to use `-F body=@-` (capital F) or pipe JSON to `--input`.

2. **Help text triggers self-responses** - The help table contains `` `@botbaki review` `` which triggers a review. Need to:
   - Ignore mentions inside code blocks/backticks
   - Ignore mentions in tables
   - Or ignore comments from botbaki's own responses (track response_comment_id)

3. **Duplicate triggers processed** - Same PR got multiple reviews due to #2. Need deduplication per PR+commit.

### UX Issues

4. **Error messages include full review text** - When posting fails, the "Sorry, error..." message includes the entire review that failed to post. Should truncate or omit.

5. **Multiple error comments posted** - Error handling posted "Sorry..." twice for the same failure. Need to track failures better.

6. **No bot account** - Botbaki posts as `kim-em`, confusing for other users. Consider:
   - Creating a dedicated GitHub bot account
   - Or clearly prefixing messages with "[Botbaki]"

### Missing Features

7. **No trigger deduplication** - Multiple `@botbaki review` comments on same commit should only generate one review.

8. **No response threading** - Should reply to the triggering comment, not just post a new top-level comment.

### Review Quality

9. **Copyright year hallucination** - Review said "2026 appears incorrect (should be 2025)" but we're in 2026. Need to pass current date to prompt or fix prompt to not make assumptions.

10. **"Missing module keyword" wrong** - Mathlib doesn't use `module` keyword. This is bad Lean 4 advice. Need better mathlib-specific prompting.

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
