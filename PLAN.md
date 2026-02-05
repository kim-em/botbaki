# Botbaki Development Plan

## Current State

Botbaki is a GitHub App that:
- Syncs PR data from GitHub to SQLite
- Generates AI reviews using Claude (Sonnet) with inline comments
- Caches reviews to avoid regenerating for the same commit/prompt
- Runs as a polling daemon watching for `@botbaki` triggers
- Collects user feedback on review quality

**Identity:** `botbaki-review[bot]` (GitHub App)
**Cost per review:** ~$0.05 (Sonnet)

## Completed

### Inline Review Comments (v0.3)

- Reviews posted as GitHub PR reviews with inline comments on specific lines
- Uses Claude's structured output API with Pydantic models
- Line-annotated diffs help Claude reference correct line numbers
- Validation filters out invalid line references before posting
- `--single` flag available for legacy single-comment mode

### GitHub App Authentication (v0.3)

- Converted from `gh` CLI to PyGithub with GitHub App auth
- Bot identity: `botbaki-review[bot]` (not personal account)
- Higher rate limits, no abuse detection issues
- Credentials in `~/.config/botbaki/`:
  - `github-app-id`
  - `github-app-key.pem`
  - `github-installation-id`

### Efficient Comment Polling (v0.3)

- Polls repo-wide issue comments endpoint for @botbaki mentions
- Only syncs PRs that have new mentions (not all updated PRs)
- Falls back to incremental sync when no mentions found
- Fixes issue where comments don't update PR's `updated_at`

### Feedback Collection (v0.3)

- `@botbaki feedback <text>` stores feedback linked to most recent review
- Feedback stored in `review_feedback` table
- Bot acknowledges with "Thanks for the feedback!"
- `botbaki feedback-report` command shows collected feedback with pattern analysis:
  - Lists all feedback with review context (PR, commit, model, prompt)
  - Extracts helpful/unhelpful/partial mentions from text
  - Handles multi-item patterns ("Issues 3 and 4 were unhelpful")
  - Calculates helpfulness ratio

### Polling Daemon (v0.2)

- `botbaki daemon` command runs a polling loop
- Checks for new `@botbaki` comments every 2 minutes
- Supported commands:
  - `@botbaki review` - generate review with inline comments
  - `@botbaki review --single` - post as single comment (no inline)
  - `@botbaki review --diff-only` - review without timeline context
  - `@botbaki feedback <text>` - provide feedback on last review
  - `@botbaki help` - show available commands
- Tracks processed triggers to avoid duplicate responses

### Deployment (v0.2.1)

Deployed to `chonk.lean-fro.org` as a systemd service:
- Service: `botbaki.service`
- Logs: `journalctl -u botbaki -f`
- Config: `~/.config/botbaki/env` (contains `ANTHROPIC_API_KEY`)
- GitHub App credentials: `~/.config/botbaki/github-app-*`
- Data: `~/projects/botbaki/data/github_prs.db`

**Management:**
```bash
sudo systemctl status botbaki   # Check status
sudo systemctl restart botbaki  # Restart
sudo journalctl -u botbaki -f   # Follow logs
```

## Known Limitations

- **No response threading**: Reviews post as new top-level comments rather than replies to the trigger comment.
- **Polling delay**: 2-minute poll interval (webhooks would be real-time)

## Next Steps

### 1. Webhooks

For real-time response instead of 2-minute polling delay:
- Requires public endpoint (Cloudflare Tunnel, ngrok, or cloud hosting)
- Subscribe to `issue_comment` webhook events
- Keep polling as fallback for missed webhooks

### 2. Opt-in System for PR Authors

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

### 3. Advanced Feedback Collection

Further improvements to feedback system:

**Per-suggestion IDs:**
- Each issue/suggestion in review gets a unique ID
- `@botbaki +1 <id>` / `@botbaki -1 <id>` for quick feedback
- Track reactions (👍/👎) on review comments

**Prompt improvement tracking:**
- Compare helpfulness ratios across prompt versions
- Identify patterns in wrong/unhelpful suggestions
- A/B test prompt changes

## Architecture

```
┌─────────────────┐     ┌──────────────┐
│  GitHub         │────▶│  Polling     │
│  (PRs/Comments) │     │  Daemon      │
└─────────────────┘     └──────┬───────┘
                               │
                               ▼
                        ┌──────────────┐
                        │  Trigger     │
                        │  Processing  │
                        └──────┬───────┘
                               │
              ┌────────────────┼────────────────┐
              ▼                ▼                ▼
       ┌──────────┐     ┌──────────┐     ┌──────────┐
       │ SQLite   │     │ Claude   │     │ GitHub   │
       │ Database │     │ API      │     │ API      │
       └──────────┘     └──────────┘     └──────────┘
                                         (PyGithub +
                                          App Auth)
```

## Open Questions

- How to handle very large PRs that exceed context limits?
- Should botbaki have any "memory" of previous reviews on the same PR?
- Cost controls: daily/monthly budget caps?
- Multiple repos: mathlib4 only, or configurable per-repo prompts?

## Priority Order

1. ~~GitHub comment triggers~~ ✓
2. ~~Inline review comments~~ ✓
3. ~~Feedback collection~~ ✓ (including feedback-report)
4. **Webhooks** - for real-time response
5. **Opt-in system** - proactive reviews for interested users
6. **Advanced feedback** - per-suggestion IDs and reaction tracking
