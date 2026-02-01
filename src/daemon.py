"""Polling daemon for botbaki - watches for @botbaki triggers and responds."""

from __future__ import annotations

import re
import signal
import sys
import time
from datetime import datetime
from typing import List, Optional, Tuple

from . import database, sync, review as review_module
from .github_client import GitHubClient, GitHubError


# Configuration
POLL_INTERVAL = 120  # 2 minutes
DEFAULT_REPOSITORY = "leanprover-community/mathlib4"
TRIGGER_PATTERN = re.compile(r'@botbaki\s+(\w+)(?:\s+(.*))?', re.IGNORECASE)

# Supported commands
COMMANDS = {
    'review': 'Generate a review for this PR',
    'feedback': 'Provide feedback on a review',
    'help': 'Show available commands',
}


class Daemon:
    """Polling daemon that watches for @botbaki triggers."""

    def __init__(self, repository: str = DEFAULT_REPOSITORY,
                 poll_interval: int = POLL_INTERVAL, verbose: bool = True):
        self.poll_interval = poll_interval
        self.verbose = verbose
        self.running = True
        self.client = GitHubClient()
        self.repository = repository
        self.owner, self.repo = repository.split('/')

        # Set up signal handlers for graceful shutdown
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    def _signal_handler(self, signum, frame):
        """Handle shutdown signals gracefully."""
        self.log(f"Received signal {signum}, shutting down...")
        self.running = False

    def log(self, message: str) -> None:
        """Log a message with timestamp."""
        if self.verbose:
            timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            print(f"[{timestamp}] {message}", flush=True)

    def run(self) -> None:
        """Main daemon loop."""
        self.log(f"Starting botbaki daemon for {self.repository}")
        self.log(f"Poll interval: {self.poll_interval}s")

        while self.running:
            try:
                self._poll_cycle()
            except Exception as e:
                self.log(f"Error in poll cycle: {e}")
                import traceback
                traceback.print_exc()

            if self.running:
                self.log(f"Sleeping for {self.poll_interval}s...")
                # Sleep in small increments to respond to signals faster
                for _ in range(self.poll_interval):
                    if not self.running:
                        break
                    time.sleep(1)

        self.log("Daemon stopped")

    def _poll_cycle(self) -> None:
        """Single poll cycle: sync and process triggers."""
        self.log("Starting poll cycle")

        # First, efficiently check for new @botbaki mentions
        # This is faster than full incremental sync
        prs_to_sync = self._find_prs_with_new_mentions()

        if prs_to_sync:
            self.log(f"Found {len(prs_to_sync)} PRs with new @botbaki mentions")
            for pr_num in prs_to_sync:
                try:
                    sync.sync_single_pr(self.repository, pr_num, request_delay=0.5, verbose=False)
                except Exception as e:
                    self.log(f"Failed to sync PR #{pr_num}: {e}")
        else:
            # Fallback: do a lighter incremental sync
            self.log("No new mentions, running incremental sync...")
            try:
                sync.incremental_sync(self.repository, request_delay=0.5, verbose=False)
            except Exception as e:
                self.log(f"Sync failed: {e}")
                return

        # Find and process triggers
        triggers = self._find_unprocessed_triggers()
        self.log(f"Found {len(triggers)} unprocessed triggers")

        for trigger in triggers:
            if not self.running:
                break
            self._process_trigger(trigger)

        self.log("Poll cycle complete")

    def _find_prs_with_new_mentions(self) -> List[int]:
        """Find PRs that have new @botbaki mentions since last sync.

        Uses the efficient repo-wide issue comments endpoint.
        Returns list of PR numbers that need syncing.
        """
        # Get last sync time
        repo_row = database.get_repository_by_full_name(self.repository)
        if not repo_row:
            return []

        last_sync = database.get_last_sync_time(repo_row['id'])
        if not last_sync:
            return []

        try:
            # Fetch comments updated since last sync
            comments = self.client.get_repo_issue_comments_since(
                self.owner, self.repo, since=last_sync
            )

            # Find PRs with @botbaki mentions
            prs_with_mentions = set()
            for c in comments:
                body = c.get('body', '')
                pr_number = c.get('pr_number')
                if pr_number and '@botbaki' in body.lower():
                    prs_with_mentions.add(pr_number)

            return list(prs_with_mentions)

        except Exception as e:
            self.log(f"Failed to fetch recent comments: {e}")
            return []

    def _find_unprocessed_triggers(self) -> List[dict]:
        """Find @botbaki comments that haven't been processed yet.

        Checks both issue_comments (general PR discussion) and
        review_comments (inline code comments / replies).
        """
        db = database.get_database()

        # Get repository
        repo_row = database.get_repository_by_full_name(self.repository)
        if not repo_row:
            return []

        repo_id = repo_row['id']

        # Find comments mentioning @botbaki that aren't in processed_triggers
        # Union of issue_comments and review_comments
        query = """
            SELECT
                ic.id as db_id,
                ic.comment_id,
                ic.pr_id,
                ic.author_login,
                ic.body,
                ic.created_at as created_at,
                pr.pr_number,
                'issue' as comment_type,
                NULL as path,
                NULL as line
            FROM issue_comments ic
            JOIN pull_requests pr ON pr.id = ic.pr_id
            LEFT JOIN processed_triggers pt ON pt.comment_id = ic.comment_id
            WHERE pr.repo_id = ?
              AND LOWER(ic.body) LIKE '%@botbaki%'
              AND pt.id IS NULL

            UNION ALL

            SELECT
                rc.id as db_id,
                rc.comment_id,
                rc.pr_id,
                rc.author_login,
                rc.body,
                rc.created_at as created_at,
                pr.pr_number,
                'review' as comment_type,
                rc.path,
                rc.line
            FROM review_comments rc
            JOIN pull_requests pr ON pr.id = rc.pr_id
            LEFT JOIN processed_triggers pt ON pt.comment_id = rc.comment_id
            WHERE pr.repo_id = ?
              AND LOWER(rc.body) LIKE '%@botbaki%'
              AND pt.id IS NULL

            ORDER BY 6 ASC
        """

        cursor = db.execute(query, (repo_id, repo_id))
        return [dict(row) for row in cursor.fetchall()]

    def _strip_code_blocks(self, text: str) -> str:
        """Remove code blocks and inline code from text.

        This prevents triggers inside code examples from being detected.
        """
        # Remove fenced code blocks (```...```)
        text = re.sub(r'```[\s\S]*?```', '', text)
        # Remove inline code (`...`)
        text = re.sub(r'`[^`]+`', '', text)
        # Remove markdown tables (lines starting with |)
        text = re.sub(r'^\|.*\|$', '', text, flags=re.MULTILINE)
        return text

    def _parse_trigger(self, body: str) -> Optional[Tuple[str, str]]:
        """Parse @botbaki command from comment body.

        Returns:
            Tuple of (command, args) or None if no valid command found.
        """
        # Strip code blocks to avoid triggering on examples
        clean_body = self._strip_code_blocks(body)

        match = TRIGGER_PATTERN.search(clean_body)
        if not match:
            return None

        command = match.group(1).lower()
        args = match.group(2) or ''

        if command not in COMMANDS:
            return None

        return (command, args.strip())

    def _process_trigger(self, trigger: dict) -> None:
        """Process a single trigger comment."""
        comment_id = trigger['comment_id']
        pr_id = trigger['pr_id']
        pr_number = trigger['pr_number']
        author = trigger['author_login']
        body = trigger['body']

        self.log(f"Processing trigger from @{author} on PR #{pr_number}")

        # Parse the command
        parsed = self._parse_trigger(body)
        if not parsed:
            self.log(f"  No valid command found, skipping")
            self._mark_processed(comment_id, pr_id, author, "invalid", error="No valid command")
            return

        command, args = parsed
        self.log(f"  Command: {command} {args}")

        try:
            if command == 'review':
                self._handle_review(trigger, args)
            elif command == 'feedback':
                self._handle_feedback(trigger, args)
            elif command == 'help':
                self._handle_help(trigger)
            else:
                self.log(f"  Unknown command: {command}")
                self._mark_processed(comment_id, pr_id, author, command, error=f"Unknown command: {command}")

        except Exception as e:
            self.log(f"  Error processing: {e}")
            self._mark_processed(comment_id, pr_id, author, command, error=str(e))

    def _handle_review(self, trigger: dict, args: str) -> None:
        """Handle @botbaki review command."""
        comment_id = trigger['comment_id']
        pr_id = trigger['pr_id']
        pr_number = trigger['pr_number']
        author = trigger['author_login']

        # Parse args
        diff_only = '--diff-only' in args or '-d' in args
        single_comment = '--single' in args  # Post as issue comment instead of PR review

        # Get current commit SHA to check for duplicate triggers
        pr_row = database.get_pr_by_id(pr_id)
        if not pr_row:
            self.log(f"  PR not found in database")
            self._mark_processed(comment_id, pr_id, author, "review", error="PR not found")
            return

        current_commit = pr_row['head_sha']

        # Check if we already posted a review for this commit
        existing_response = self._get_existing_review_for_commit(pr_id, current_commit)
        if existing_response:
            self.log(f"  Review already posted for commit {current_commit[:8]}, skipping")
            # Mark as processed but don't post again - just acknowledge
            response = (
                f"@{author} A review was already posted for commit `{current_commit[:8]}`. "
                f"See the earlier comment in this thread."
            )
            try:
                ack_result = self.client.post_issue_comment(
                    self.owner, self.repo, pr_number, response
                )
                self._mark_processed(
                    comment_id, pr_id, author, "review-duplicate",
                    response_comment_id=ack_result.get('id')
                )
            except GitHubError:
                self._mark_processed(comment_id, pr_id, author, "review-duplicate")
            return

        self.log(f"  Generating review (diff_only={diff_only}, single={single_comment})...")

        try:
            # Generate review
            result = review_module.generate_pr_review(
                full_name=self.repository,
                pr_number=pr_number,
                include_timeline=not diff_only,
                force=False,  # Use cached if available
                verbose=False
            )

            inline_comments = result.get('inline_comments', [])

            # Decide posting method: --single or no inline comments -> issue comment
            # Otherwise -> PR review with inline comments
            if single_comment or not inline_comments:
                # Post as single issue comment (legacy mode)
                response = self._format_review_response(result, trigger)
                self.log(f"  Posting as issue comment...")
                comment_result = self.client.post_issue_comment(
                    self.owner, self.repo, pr_number, response
                )
                response_id = comment_result.get('id')
                self.log(f"  Posted comment: {comment_result.get('html_url')}")
            else:
                # Post as PR review with inline comments
                summary = self._format_review_response(result, trigger)
                self.log(f"  Posting as PR review with {len(inline_comments)} inline comments...")
                review_result = self.client.create_pull_request_review(
                    owner=self.owner,
                    repo=self.repo,
                    pr_number=pr_number,
                    body=summary,
                    commit_id=current_commit,
                    comments=inline_comments,
                    event="COMMENT"
                )
                response_id = review_result.get('id')
                self.log(f"  Posted PR review: {review_result.get('html_url')}")

            # Mark as processed
            flags = []
            if diff_only:
                flags.append('--diff-only')
            if single_comment:
                flags.append('--single')
            command_str = 'review' + (''.join(flags) if flags else '')

            self._mark_processed(
                comment_id, pr_id, author,
                command_str,
                review_id=result['review_id'],
                response_comment_id=response_id
            )

        except Exception as e:
            self.log(f"  Review generation failed: {e}")
            # Post error response (truncate error message to avoid posting huge text)
            error_str = str(e)
            if len(error_str) > 500:
                error_str = error_str[:500] + "... [truncated]"
            error_response = f"Sorry @{author}, I encountered an error generating the review:\n\n```\n{error_str}\n```"
            # Only post error if we haven't already posted one for this trigger
            if not self._has_error_response(comment_id):
                try:
                    error_result = self.client.post_issue_comment(
                        self.owner, self.repo, pr_number, error_response
                    )
                    self._mark_processed(
                        comment_id, pr_id, author, "review",
                        response_comment_id=error_result.get('id'),
                        error=str(e)[:1000]  # Also truncate stored error
                    )
                except GitHubError:
                    self._mark_processed(comment_id, pr_id, author, "review", error=str(e)[:1000])
            else:
                self._mark_processed(comment_id, pr_id, author, "review", error=str(e)[:1000])

    def _handle_help(self, trigger: dict) -> None:
        """Handle @botbaki help command."""
        comment_id = trigger['comment_id']
        pr_id = trigger['pr_id']
        pr_number = trigger['pr_number']
        author = trigger['author_login']

        response = """**Botbaki Commands**

| Command | Description |
|---------|-------------|
| `@botbaki review` | Generate an AI review with inline comments |
| `@botbaki review --single` | Post review as a single comment (no inline) |
| `@botbaki review --diff-only` | Review only the diff (no timeline context) |
| `@botbaki feedback <text>` | Provide feedback on the last review |
| `@botbaki help` | Show this help message |

Reviews are generated by Claude and cached per commit+prompt version.
"""

        try:
            comment_result = self.client.post_issue_comment(
                self.owner, self.repo, pr_number, response
            )
            self._mark_processed(
                comment_id, pr_id, author, "help",
                response_comment_id=comment_result.get('id')
            )
            self.log(f"  Posted help: {comment_result.get('html_url')}")
        except GitHubError as e:
            self._mark_processed(comment_id, pr_id, author, "help", error=str(e))

    def _handle_feedback(self, trigger: dict, args: str) -> None:
        """Handle @botbaki feedback command."""
        comment_id = trigger['comment_id']
        pr_id = trigger['pr_id']
        pr_number = trigger['pr_number']
        author = trigger['author_login']
        body = trigger['body']
        created_at = trigger['created_at']
        comment_type = trigger.get('comment_type', 'issue')
        path = trigger.get('path')
        line = trigger.get('line')

        # Extract feedback text (everything after "@botbaki feedback")
        # The body includes the full comment
        match = re.search(r'@botbaki\s+feedback\s*(.*)', body, re.IGNORECASE | re.DOTALL)
        feedback_text = match.group(1).strip() if match else body

        context = f" on {path}:{line}" if path else ""
        self.log(f"  Storing {comment_type} feedback ({len(feedback_text)} chars){context}")

        # Find the most recent review for this PR to link feedback to
        db = database.get_database()
        cursor = db.execute("""
            SELECT id FROM pr_reviews
            WHERE pr_id = ?
            ORDER BY reviewed_at DESC
            LIMIT 1
        """, (pr_id,))
        review_row = cursor.fetchone()
        review_id = review_row[0] if review_row else None

        # Ensure the new columns exist (for backwards compatibility)
        try:
            db.execute("ALTER TABLE review_feedback ADD COLUMN comment_type TEXT")
        except Exception:
            pass  # Column already exists
        try:
            db.execute("ALTER TABLE review_feedback ADD COLUMN path TEXT")
        except Exception:
            pass
        try:
            db.execute("ALTER TABLE review_feedback ADD COLUMN line INTEGER")
        except Exception:
            pass

        # Store the feedback
        db.execute("""
            INSERT OR REPLACE INTO review_feedback
            (review_id, pr_id, pr_number, comment_id, feedback_text, github_login,
             created_at, processed_at, comment_type, path, line)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            review_id,
            pr_id,
            pr_number,
            comment_id,
            feedback_text,
            author,
            created_at,
            datetime.now().isoformat(),
            comment_type,
            path,
            line
        ))
        db.commit()

        # Add +1 reaction to acknowledge feedback
        try:
            if comment_type == 'review':
                self.client.create_review_comment_reaction(
                    self.owner, self.repo, comment_id, content="+1"
                )
            else:
                self.client.create_issue_comment_reaction(
                    self.owner, self.repo, comment_id, content="+1"
                )
            self._mark_processed(comment_id, pr_id, author, "feedback")
            self.log(f"  Added +1 reaction to acknowledge feedback")
        except GitHubError as e:
            # Still mark as processed even if reaction fails
            self._mark_processed(comment_id, pr_id, author, "feedback", error=str(e))
            self.log(f"  Stored feedback but failed to add reaction: {e}")

    def _format_review_response(self, result: dict, trigger: dict) -> str:
        """Format the review result as a GitHub comment."""
        author = trigger['author_login']
        cached = result.get('cached', False)
        commit_sha = result['commit_sha'][:8]
        model = result['model_used']

        header = f"@{author} Here's my review of this PR at commit `{commit_sha}`:\n\n"

        if cached:
            header += f"*Using cached review (model: {model})*\n\n"

        footer = ""

        return header + result['review_text'] + footer

    def _has_error_response(self, comment_id: int) -> bool:
        """Check if we've already posted an error response for this trigger."""
        db = database.get_database()
        cursor = db.execute("""
            SELECT 1 FROM processed_triggers
            WHERE comment_id = ? AND error_message IS NOT NULL
        """, (comment_id,))
        return cursor.fetchone() is not None

    def _get_existing_review_for_commit(self, pr_id: int, commit_sha: str) -> Optional[int]:
        """Check if there's already a review for this PR at this commit.

        Returns the response_comment_id if a review was already posted.
        """
        db = database.get_database()
        cursor = db.execute("""
            SELECT pt.response_comment_id
            FROM processed_triggers pt
            WHERE pt.pr_id = ?
              AND pt.command LIKE 'review%'
              AND pt.error_message IS NULL
              AND pt.response_comment_id IS NOT NULL
              AND EXISTS (
                  SELECT 1 FROM pr_reviews r
                  WHERE r.id = pt.review_id
                    AND r.commit_sha = ?
              )
            LIMIT 1
        """, (pr_id, commit_sha))
        row = cursor.fetchone()
        return row[0] if row else None

    def _mark_processed(
        self,
        comment_id: int,
        pr_id: int,
        triggered_by: str,
        command: str,
        review_id: Optional[int] = None,
        response_comment_id: Optional[int] = None,
        error: Optional[str] = None
    ) -> None:
        """Mark a trigger comment as processed."""
        db = database.get_database()

        db.execute("""
            INSERT OR REPLACE INTO processed_triggers
            (comment_id, pr_id, command, triggered_by, processed_at, review_id, response_comment_id, error_message)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            comment_id,
            pr_id,
            command,
            triggered_by,
            datetime.now().isoformat(),
            review_id,
            response_comment_id,
            error
        ))
        db.commit()


def run_daemon(repository: str = DEFAULT_REPOSITORY,
               poll_interval: int = POLL_INTERVAL, verbose: bool = True) -> int:
    """Entry point for daemon command.

    Args:
        repository: Repository to monitor (default: leanprover-community/mathlib4)
        poll_interval: Seconds between polls (default: 120)
        verbose: Print status messages (default: True)

    Returns:
        Exit code (0 for clean shutdown)
    """
    daemon = Daemon(repository=repository, poll_interval=poll_interval, verbose=verbose)
    daemon.run()
    return 0
