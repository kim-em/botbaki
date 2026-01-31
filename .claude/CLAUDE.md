# Botbaki Project Instructions

## Architecture

- **GitHub App**: `botbaki-review[bot]` - uses PyGithub with App authentication
- **Polling daemon**: Checks for @botbaki mentions every 2 minutes
- **Inline comments**: Reviews posted as GitHub PR reviews with line-specific comments
- **Structured output**: Uses Claude's Pydantic-based structured output for reviews

## Credentials

All in `~/.config/botbaki/`:
- `github-app-id` - GitHub App ID
- `github-app-key.pem` - Private key
- `github-installation-id` - Installation ID
- `env` - Contains `ANTHROPIC_API_KEY`

## Efficient Comment Polling

The daemon efficiently finds new @botbaki mentions:
1. Polls `GET /repos/{owner}/{repo}/issues/comments?since=LAST_SYNC`
2. Filters for comments containing `@botbaki`
3. Syncs only those specific PRs
4. Falls back to incremental sync when no mentions found

This solves the issue where new comments don't update PR's `updated_at`.

## Testing Tips

When testing the daemon after clearing database records:
- Use `botbaki sync <repo> --pr <number>` to force-sync a specific PR
- Or wait for the next poll cycle to pick up @botbaki mentions

## Deployment

On chonk:
```bash
cd ~/projects/botbaki
git pull
sudo systemctl restart botbaki
sudo journalctl -u botbaki -f
```
