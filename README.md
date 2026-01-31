# Botbaki - AI Code Review for GitHub PRs

AI-powered code review bot for GitHub pull requests, deployed as a GitHub App with inline review comments.

## Features

- **Inline review comments**: Posts GitHub PR reviews with comments on specific lines
- **Mathlib4-aware**: Reviews check style, naming conventions, and mathlib-specific patterns
- **Smart caching**: Reviews stored and reused for identical PR state + prompt combinations
- **Feedback collection**: Users can provide feedback on review quality
- **Polling daemon**: Watches for `@botbaki` triggers every 2 minutes

## Bot Identity

Botbaki runs as a GitHub App: **botbaki-review[bot]**

This provides:
- Separate identity (not impersonating a user)
- Higher rate limits (5000+ requests/hour)
- No abuse detection issues

## Commands

Trigger botbaki by commenting on a PR:

| Command | Description |
|---------|-------------|
| `@botbaki review` | Generate review with inline comments |
| `@botbaki review --single` | Post as single comment (no inline) |
| `@botbaki review --diff-only` | Review without timeline context |
| `@botbaki feedback <text>` | Provide feedback on the last review |
| `@botbaki help` | Show available commands |

## Requirements

- Python 3.10+
- GitHub App credentials (for GitHub API access)
- Anthropic API key (for Claude reviews)

## Installation

### 1. Clone and Install Dependencies

```bash
git clone https://github.com/kim-em/botbaki.git
cd botbaki
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Create GitHub App

1. Go to https://github.com/settings/apps/new (or org settings for org app)
2. Set permissions:
   - Contents: Read-only
   - Issues: Read and write
   - Pull requests: Read and write
3. Disable webhooks (we use polling)
4. Generate and download private key
5. Install the app on your repository

### 3. Configure Credentials

```bash
mkdir -p ~/.config/botbaki

# GitHub App credentials
echo "YOUR_APP_ID" > ~/.config/botbaki/github-app-id
cp path/to/private-key.pem ~/.config/botbaki/github-app-key.pem
echo "YOUR_INSTALLATION_ID" > ~/.config/botbaki/github-installation-id
chmod 600 ~/.config/botbaki/github-app-key.pem

# Anthropic API key
echo "ANTHROPIC_API_KEY=sk-ant-..." > ~/.config/botbaki/env
chmod 600 ~/.config/botbaki/env
```

### 4. Run the Daemon

```bash
# Manual run
source .venv/bin/activate
python3 -m src.cli daemon

# Or install as systemd service
cd service && ./install.sh
```

## Usage

### CLI Commands

```bash
# Generate a review (outputs to stdout)
./botbaki review leanprover-community/mathlib4 32904

# Review without timeline context
./botbaki review leanprover-community/mathlib4 32904 --diff-only

# Force regeneration (bypass cache)
./botbaki review leanprover-community/mathlib4 32904 --force

# Sync PR data
./botbaki sync leanprover-community/mathlib4 --pr 32904

# Run daemon
./botbaki daemon
```

### Daemon Mode

The daemon polls for `@botbaki` comments every 2 minutes:

```bash
./botbaki daemon
```

Management (when installed as systemd service):
```bash
sudo systemctl status botbaki   # Check status
sudo systemctl restart botbaki  # Restart
sudo journalctl -u botbaki -f   # Follow logs
```

## Configuration

### Credentials

All credentials stored in `~/.config/botbaki/`:

| File | Description |
|------|-------------|
| `env` | Anthropic API key (`ANTHROPIC_API_KEY=...`) |
| `github-app-id` | GitHub App ID |
| `github-app-key.pem` | GitHub App private key |
| `github-installation-id` | Installation ID for target repo |

### Prompts

Review prompts are in `prompts/{repo}/YYYY-MM-DD.md`:

- Use `{diff}`, `{timeline}`, `{title}`, `{author}`, etc. as placeholders
- Reference files in `prompts/{repo}/references/` for style guides
- Latest prompt (by filename) is used by default

## Database

SQLite database (`data/botbaki.db`) contains:

- **pull_requests** - PR metadata
- **commits** - Commits on PR branches
- **review_comments** - Inline code comments
- **issue_comments** - General PR discussion
- **reviews** - Review submissions
- **pr_reviews** - Generated AI reviews (cached)
- **review_feedback** - User feedback on reviews
- **processed_triggers** - Tracked bot triggers

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
                                         (PyGithub)
```

**Key modules:**
- `src/cli.py` - Command-line interface
- `src/daemon.py` - Polling daemon
- `src/review.py` - AI review generation with structured output
- `src/github_client.py` - GitHub App authentication via PyGithub
- `src/sync.py` - PR data synchronization
- `src/database.py` - SQLite operations

## Deployment

Currently deployed to `chonk.lean-fro.org`:

```bash
# SSH to chonk
ssh chonk

# Check status
sudo systemctl status botbaki
sudo journalctl -u botbaki -f

# Deploy updates
cd ~/projects/botbaki
git pull
sudo systemctl restart botbaki
```

## License

MIT
