# Botbaki - GitHub PR Tracking Database

SQLite-based tool for tracking GitHub pull requests, comments, commits, and reviews.

## Features

- **Comprehensive PR tracking**: Store PRs, commits, inline code comments, general discussion, and reviews
- **Full context**: Every inline review comment includes file path, line number, and diff hunk showing the actual code change
- **Auto-sync**: `show` command automatically fetches PRs that aren't in the database yet
- **Timeline view**: See all PR activity chronologically with full diff context for each code comment
- **Full-text search**: Search across all comments using SQLite FTS5
- **Analytics**: Statistics on authors, reviewers, comment patterns
- **Incremental sync**: Efficiently update only changed PRs
- **Zero dependencies**: Uses Python stdlib + `gh` CLI

## Requirements

- Python 3.7+
- [GitHub CLI](https://cli.github.com) installed and authenticated (`gh auth login`)

## Installation

```bash
# Clone or navigate to botbaki directory
cd /path/to/botbaki

# Make sure gh CLI is authenticated
gh auth login

# Run botbaki
./botbaki --help
```

## Usage

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

- **organizations** - GitHub organizations
- **repositories** - Repositories within organizations
- **pull_requests** - PR metadata with counts
- **commits** - Commits on PR branches with full messages
- **review_comments** - Inline code comments with file path, line number, and diff hunk (the actual code context)
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

- **database.py** - SQLite operations, schema, CRUD
- **github_client.py** - GitHub API client with rate limiting
- **sync.py** - Sync logic (full and incremental)
- **cli.py** - Command-line interface

Design principles:
- Rate limiting: 900ms between requests (~4000 req/hour, leaving spare quota), exponential backoff on errors
- Idempotent inserts: `INSERT OR IGNORE` for safe re-syncing
- FTS5 integration: Automatic full-text indexing via triggers
- Checkpoint-based recovery: Resume from failures

## Generalizing to Other Repositories

While designed for mathlib4, botbaki works with any GitHub repository:

```bash
# Sync any repository
./botbaki sync owner/repo --since 2024-01-01

# Works with multiple repos simultaneously (separate databases per repo)
./botbaki sync leanprover/lean4 --since 2024-10-01
./botbaki sync leanprover-community/batteries --since 2024-10-01
```

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
│   ├── database.py      # Database operations
│   ├── github_client.py # GitHub API client
│   ├── sync.py         # Sync logic
│   └── cli.py          # CLI interface
├── data/               # SQLite database
├── schema.sql         # Reference schema
├── botbaki            # Launcher script
└── README.md
```

No external dependencies required - uses Python stdlib only.

## License

MIT
