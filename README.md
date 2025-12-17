# Botbaki - AI Code Review for GitHub PRs

AI-powered code review tool for GitHub pull requests, with built-in PR tracking and search capabilities.

## Features

### AI Code Review
- **Automated PR reviews**: Generate detailed code reviews using Claude (Opus or Sonnet)
- **Mathlib4-aware**: Reviews check style, naming conventions, and mathlib-specific patterns
- **Customizable prompts**: Date-based prompt templates with full diff and timeline context
- **Smart caching**: Reviews are stored and reused for identical PR state + prompt combinations
- **Flexible API**: Use Anthropic API or local Claude CLI with automatic quota management

### PR Tracking & Search
- **Comprehensive tracking**: Store PRs, commits, inline code comments, and reviews in SQLite
- **Full-text search**: Search across all comments using FTS5
- **Timeline view**: See all PR activity chronologically with diff context
- **Analytics**: Statistics on authors, reviewers, comment patterns
- **Auto-sync**: Commands automatically fetch PRs that aren't in the database yet

## Requirements

- Python 3.7+
- [GitHub CLI](https://cli.github.com) installed and authenticated (`gh auth login`)
- For AI reviews (optional):
  - Anthropic API key (`ANTHROPIC_API_KEY` env var), OR
  - Local Claude CLI with quota

## Installation

```bash
# Clone or navigate to botbaki directory
cd /path/to/botbaki

# Install dependencies (for AI reviews)
pip install anthropic

# Make sure gh CLI is authenticated
gh auth login

# Run botbaki
./botbaki --help
```

## Usage

### AI Code Review

Generate an AI review of a pull request:

```bash
# Basic review (uses latest prompt, includes full context)
./botbaki review leanprover-community/mathlib4 32904

# Review only the diff, exclude timeline/comments
./botbaki review leanprover-community/mathlib4 32904 --diff-only

# Review at a specific commit
./botbaki review leanprover-community/mathlib4 32904 --commit abc123

# Use a specific prompt version
./botbaki review leanprover-community/mathlib4 32904 --prompt-date 2025-12-15

# Force regeneration (bypass cache)
./botbaki review leanprover-community/mathlib4 32904 --force
```

**How it works:**
1. Fetches PR diff and timeline from GitHub
2. Loads the prompt template from `prompts/{repo}/YYYY-MM-DD.md`
3. Substitutes diff, timeline, and PR metadata into the template
4. Generates review using Claude (via Anthropic API or local CLI)
5. Stores review in database for future reference
6. Returns cached review on subsequent runs (unless `--force`)

**API Methods:**
- **Anthropic SDK** (recommended): Set `ANTHROPIC_API_KEY` environment variable
- **Local Claude CLI**: Falls back automatically if no API key; uses quota checking

**Customizing Prompts:**

Edit or create prompt files in `prompts/{repo}/YYYY-MM-DD.md`:
- Use `{diff}`, `{timeline}`, `{title}`, `{author}`, etc. as placeholders
- Reference files in `prompts/{repo}/references/` for style guides
- Latest prompt (by filename) is used by default

### PR Tracking

### Initial Sync

Sync last 2 months of PRs from mathlib4:

```bash
./botbaki sync leanprover-community/mathlib4 --since 2025-10-16
```

This will:
- Fetch all PRs updated since the specified date
- Store PR metadata, commits, comments, and reviews
- Use ~0.9 second delay between requests (default pacing: ~4000 req/hour, leaving 1000 spare)
- Take approximately 3-4 hours for 2 months of mathlib4 data (~13,500 API requests)

**Rate Limiting Options:**

By default, botbaki paces requests at 0.9s intervals (~4000 req/hour) to stay well under GitHub's 5000 req/hour limit and leave spare quota for other work. You can adjust this:

```bash
# Faster sync (use more of your quota): 0.72s = ~5000 req/hour (no spare)
./botbaki sync leanprover-community/mathlib4 --since 2025-10-16 --delay 0.72

# More conservative (leave more spare): 1.2s = ~3000 req/hour (2000 spare)
./botbaki sync leanprover-community/mathlib4 --since 2025-10-16 --delay 1.2
```

### Incremental Updates

Update only changed PRs:

```bash
./botbaki sync leanprover-community/mathlib4 --incremental
```

This is fast (~5 minutes) and should be run periodically to keep data fresh.

### View PR Timeline (with auto-sync)

Just view any PR - it will automatically sync if not in the database:

```bash
./botbaki show leanprover-community/mathlib4 32904
```

This displays:
- PR metadata (author, state, dates, branch)
- Full timeline with commits, comments, and reviews
- **Diff context** for each inline code comment showing the actual changes
- File paths and line numbers for every review comment

Example output:
```
[2025-12-15T19:06:05] 💬 grunweg @ Mathlib/Geometry/Manifold/VectorField/Pullback.lean:311
           @@ -308,7 +308,7 @@ lemma _root_.MDifferentiableWithinAt...
           ...
           -      (fun (y : M) ↦ (mpullbackWithin I I' f V s y : TangentBundle I M)) (s ∩ f⁻¹' t) x₀ := by
           +      (fun (y : M) ↦ (mpullbackWithin I I' f V s y : TangentBundle I M)) (s ∩ f ⁻¹' t) x₀ := by
           → This one and the next change are good!

[2025-12-15T21:25:11] 💬 harahu @ Mathlib/Geometry/Manifold/VectorField/Pullback.lean:311
           ...
           → I kept them :)
```

### Sync Single PR

Or explicitly sync just one PR:

```bash
./botbaki sync leanprover-community/mathlib4 --pr 12345
```

### List PRs

```bash
# List all PRs
./botbaki list leanprover-community/mathlib4

# Filter by state
./botbaki list leanprover-community/mathlib4 --state open

# Filter by author
./botbaki list leanprover-community/mathlib4 --author username

# Limit results
./botbaki list leanprover-community/mathlib4 --limit 20
```

### Search Comments

Full-text search across all comments:

```bash
# Simple search
./botbaki search "simp lemma"

# FTS5 syntax (AND, OR, quotes)
./botbaki search "performance AND optimization"
./botbaki search '"type class"'

# Limit results
./botbaki search "refactor" --limit 20
```

### Statistics

```bash
# Overall repository stats
./botbaki stats leanprover-community/mathlib4

# Per-author stats
./botbaki stats leanprover-community/mathlib4 --author username
```

Shows:
- PR counts by state
- Top authors
- Top reviewers (last 60 days)
- Comment counts
- Review patterns (approvals, changes requested)

### Check Rate Limit

```bash
./botbaki rate-limit
```

## Database Schema

The SQLite database (`data/github_prs.db`) contains:

**AI Reviews:**
- **pr_reviews** - Generated reviews with prompt hash, model used, and full review text

**PR Tracking:**
- **organizations** - GitHub organizations
- **repositories** - Repositories within organizations
- **pull_requests** - PR metadata with counts
- **commits** - Commits on PR branches with full messages
- **review_comments** - Inline code comments with file path, line number, and diff hunk
- **issue_comments** - General PR discussion
- **reviews** - Review submissions (approved, changes requested)
- **pr_labels** - PR labels (many-to-many)
- **timeline_events** - State change history
- **FTS5 tables** - Full-text search indexes on all comment text

Sync state is tracked per-PR and per-repository for efficient incremental updates.

## Example Queries

Access the database directly with `sqlite3`:

```bash
sqlite3 data/github_prs.db
```

### View Stored Reviews

```sql
SELECT
  pr_number,
  substr(commit_sha, 1, 8) as commit,
  reviewed_at,
  model_used,
  prompt_path,
  substr(review_text, 1, 200) as preview
FROM pr_reviews
ORDER BY reviewed_at DESC
LIMIT 10;
```

### Timeline for a PR

```sql
SELECT
  created_at as ts,
  'commit' as type,
  author_login,
  message_headline as content
FROM commits WHERE pr_id = 123
UNION ALL
SELECT created_at, 'review_comment', author_login, body
FROM review_comments WHERE pr_id = 123
UNION ALL
SELECT created_at, 'issue_comment', author_login, body
FROM issue_comments WHERE pr_id = 123
UNION ALL
SELECT submitted_at, 'review', author_login, state || ': ' || body
FROM reviews WHERE pr_id = 123
ORDER BY ts ASC;
```

### Most Active Reviewers

```sql
SELECT
  author_login,
  COUNT(*) as review_count,
  SUM(CASE WHEN state = 'APPROVED' THEN 1 ELSE 0 END) as approvals
FROM reviews
WHERE submitted_at >= date('now', '-60 days')
GROUP BY author_login
ORDER BY review_count DESC
LIMIT 10;
```

### Average Time to First Review

```sql
SELECT
  AVG(julianday(first_review) - julianday(created_at)) * 24 as avg_hours
FROM (
  SELECT
    pr.created_at,
    MIN(r.submitted_at) as first_review
  FROM pull_requests pr
  JOIN reviews r ON r.pr_id = pr.id
  WHERE r.state IN ('APPROVED', 'CHANGES_REQUESTED')
  GROUP BY pr.id
);
```

## Architecture

**AI Review System:**
- **review.py** - Review generation with template substitution
- **quota.py** - Claude CLI quota checking and management
- **prompts/** - Template-based prompts with style guide references

**PR Tracking:**
- **sync.py** - PR sync logic (full and incremental)
- **database.py** - SQLite operations, schema, CRUD
- **github_client.py** - GitHub API client with rate limiting
- **cli.py** - Command-line interface

Design principles:
- Review caching: Deduplication by PR + commit + prompt hash + flags
- Template-based prompts: Reusable with `{diff}`, `{timeline}` placeholders
- Dual API support: Anthropic SDK or local Claude CLI with quota checking
- Rate limiting: 900ms between requests (~4000 req/hour), exponential backoff
- Idempotent inserts: `INSERT OR IGNORE` for safe re-syncing
- FTS5 integration: Automatic full-text indexing via triggers

## Generalizing to Other Repositories

While designed for mathlib4, botbaki works with any GitHub repository:

```bash
# Review any repository PR (create custom prompts in prompts/{repo}/)
./botbaki review owner/repo 123

# Sync any repository
./botbaki sync owner/repo --since 2024-01-01

# Works with multiple repos simultaneously
./botbaki review leanprover/lean4 456
./botbaki review leanprover-community/batteries 789
```

To add a new repository:
1. Create `prompts/{repo}/YYYY-MM-DD.md` with your review prompt template
2. Optionally add reference files in `prompts/{repo}/references/`
3. Run `./botbaki review {repo} {pr_number}`

## Troubleshooting

### "gh CLI not authenticated"

```bash
gh auth login
```

### "Rate limited"

The tool automatically handles rate limiting with exponential backoff. If you hit the limit:

```bash
# Check remaining quota
./botbaki rate-limit

# Wait until reset time, then resume
./botbaki sync leanprover-community/mathlib4 --incremental
```

### Database locked error

Only one sync can run at a time. Make sure no other botbaki processes are running.

## Development

Project structure:
```
botbaki/
├── src/
│   ├── cli.py           # Command-line interface
│   ├── review.py        # AI review generation
│   ├── quota.py         # Claude quota checking
│   ├── database.py      # SQLite operations
│   ├── github_client.py # GitHub API client
│   └── sync.py          # Sync logic
├── prompts/
│   └── mathlib4/
│       ├── YYYY-MM-DD.md        # Prompt templates
│       └── references/
│           ├── style.md         # Mathlib style guide
│           └── naming.md        # Naming conventions
├── bin/
│   ├── claude-available-model   # Quota checker
│   └── claude-usage             # Quota data fetcher
├── data/                # SQLite database
├── schema.sql          # Database schema
├── botbaki             # Launcher script
└── README.md
```

Dependencies:
- Python stdlib for core functionality
- `anthropic` package for AI reviews (optional)

## License

MIT
