#!/usr/bin/env python3
"""Command-line interface for GitHub PR tracking."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from typing import Optional

from . import database, sync
from .github_client import GitHubClient


def cmd_sync(args: argparse.Namespace) -> int:
    """Handle sync command."""
    try:
        if args.incremental:
            sync.incremental_sync(args.repository, request_delay=args.delay, verbose=True)
        elif args.pr:
            sync.sync_single_pr(args.repository, args.pr, request_delay=args.delay, verbose=True)
        else:
            # Full sync
            since = None
            if args.since:
                try:
                    since = datetime.fromisoformat(args.since)
                except ValueError:
                    print(f"Error: Invalid date format: {args.since}", file=sys.stderr)
                    print("Use ISO format: YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS", file=sys.stderr)
                    return 1

            sync.full_sync(args.repository, since=since, request_delay=args.delay,
                          force=args.force, verbose=True)

        return 0

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def cmd_list(args: argparse.Namespace) -> int:
    """Handle list command."""
    try:
        db = database.get_database()

        # Get repository
        repo_row = database.get_repository_by_full_name(args.repository)
        if not repo_row:
            print(f"Error: Repository {args.repository} not found in database", file=sys.stderr)
            print("Run 'botbaki sync' first", file=sys.stderr)
            return 1

        repo_id = repo_row['id']

        # Build query
        query = "SELECT pr_number, title, state, author_login, created_at, updated_at FROM pull_requests WHERE repo_id = ?"
        params = [repo_id]

        if args.state:
            query += " AND state = ?"
            params.append(args.state.upper())

        if args.author:
            query += " AND author_login = ?"
            params.append(args.author)

        query += " ORDER BY pr_number DESC"

        if args.limit:
            query += f" LIMIT {args.limit}"

        # Execute query
        cursor = db.execute(query, params)
        rows = cursor.fetchall()

        if not rows:
            print("No PRs found")
            return 0

        # Print results
        print(f"{'PR':<8} {'State':<8} {'Author':<20} {'Title':<50} {'Updated':<20}")
        print("-" * 120)

        for row in rows:
            pr_number = row['pr_number']
            title = row['title'][:48] + ".." if len(row['title']) > 50 else row['title']
            state = row['state']
            author = row['author_login'][:18] + ".." if len(row['author_login']) > 20 else row['author_login']
            updated = row['updated_at'][:19]  # Trim milliseconds

            print(f"#{pr_number:<7} {state:<8} {author:<20} {title:<50} {updated:<20}")

        print(f"\nTotal: {len(rows)} PRs")
        return 0

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def cmd_show(args: argparse.Namespace) -> int:
    """Handle show command."""
    try:
        db = database.get_database()

        # Get repository
        repo_row = database.get_repository_by_full_name(args.repository)
        if not repo_row:
            # Auto-sync: Repository not in database, sync this PR
            print(f"Repository {args.repository} not in database, syncing PR #{args.pr_number}...\n")
            sync.sync_single_pr(args.repository, args.pr_number, verbose=True)
            print()  # Blank line before showing PR

            # Get repository again
            repo_row = database.get_repository_by_full_name(args.repository)
            if not repo_row:
                print(f"Error: Failed to sync repository {args.repository}", file=sys.stderr)
                return 1

        repo_id = repo_row['id']

        # Get PR
        pr_row = database.get_pr_by_number(repo_id, args.pr_number)
        if not pr_row:
            # Auto-sync: PR not in database, sync it
            print(f"PR #{args.pr_number} not in database, syncing...\n")
            sync.sync_single_pr(args.repository, args.pr_number, verbose=True)
            print()  # Blank line before showing PR

            # Get PR again
            pr_row = database.get_pr_by_number(repo_id, args.pr_number)
            if not pr_row:
                print(f"Error: Failed to sync PR #{args.pr_number}", file=sys.stderr)
                return 1

        pr_id = pr_row['id']

        # Print PR info
        print(f"\n{'='*80}")
        print(f"PR #{pr_row['pr_number']}: {pr_row['title']}")
        print(f"{'='*80}")
        print(f"State:      {pr_row['state']}")
        print(f"Author:     {pr_row['author_login']}")
        print(f"Created:    {pr_row['created_at']}")
        print(f"Updated:    {pr_row['updated_at']}")
        if pr_row['merged_at']:
            print(f"Merged:     {pr_row['merged_at']}")
        elif pr_row['closed_at']:
            print(f"Closed:     {pr_row['closed_at']}")
        print(f"Branch:     {pr_row['head_ref']} → {pr_row['base_ref']}")
        print(f"Commits:    {pr_row['commits_count']}")
        print(f"Comments:   {pr_row['comments_count']}")
        print(f"Reviews:    (see timeline)")

        if pr_row['body']:
            # Truncate at horizontal rule (---) if present
            body = pr_row['body']
            if '\n---' in body:
                body = body.split('\n---')[0].rstrip()

            # Limit to 500 characters
            print(f"\nDescription:\n{body[:500]}")
            if len(body) > 500:
                print("... (truncated)")

        # Get timeline
        print(f"\n{'='*80}")
        print("Timeline")
        print(f"{'='*80}\n")

        timeline = database.get_pr_timeline(pr_id)

        if not timeline:
            print("(No activity)")
        else:
            for event in timeline:
                timestamp = event['timestamp'][:19]  # Trim milliseconds
                event_type = event['type']
                actor = event['actor']
                content = event['content']

                # Format based on type
                if event_type == 'commit':
                    print(f"[{timestamp}] 🔨 {actor}: {content}")
                elif event_type == 'review_comment':
                    # Show file and line for inline code comments
                    path = event.get('path', '')
                    line = event.get('line')
                    location = f"{path}:{line}" if line else path
                    print(f"[{timestamp}] 💬 {actor} @ {location}")

                    # Show diff context if available
                    diff_hunk = event.get('diff_hunk')
                    if diff_hunk:
                        # Indent the diff hunk
                        for diff_line in diff_hunk.split('\n'):
                            print(f"           {diff_line}")

                    # Show the full comment (indent continuation lines)
                    print(f"           → {content}")
                    print()  # Blank line after each review comment
                elif event_type == 'issue_comment':
                    # Truncate bot messages, show full text for humans
                    if actor.endswith('[bot]'):
                        preview = content[:80].replace('\n', ' ')
                        if len(content) > 80:
                            preview += "..."
                        print(f"[{timestamp}] 💭 {actor}: {preview}")
                    else:
                        print(f"[{timestamp}] 💭 {actor}:")
                        # Indent the comment body
                        for line in content.split('\n'):
                            print(f"           {line}")
                        print()  # Blank line after
                elif event_type == 'review':
                    # Skip reviews with only state and no comment (e.g., "COMMENTED" with no text)
                    # These are typically submissions of inline review comments
                    if content.strip() and not content.strip().endswith(':'):
                        print(f"[{timestamp}] ✅ {actor}:")
                        for line in content.split('\n'):
                            print(f"           {line}")
                        print()  # Blank line after

        print(f"\n{'='*80}\n")
        return 0

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def cmd_search(args: argparse.Namespace) -> int:
    """Handle search command."""
    try:
        results = database.search_comments(args.query, limit=args.limit or 50)

        if not results:
            print("No results found")
            return 0

        print(f"Found {len(results)} results:\n")

        for result in results:
            print(f"PR #{result['pr_number']}: {result['pr_title']}")
            print(f"  {result['source']} by {result['author_login']} ({result['created_at'][:10]})")
            preview = result['body'][:150].replace('\n', ' ')
            if len(result['body']) > 150:
                preview += "..."
            print(f"  {preview}")
            print(f"  {result['html_url']}\n")

        return 0

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def cmd_stats(args: argparse.Namespace) -> int:
    """Handle stats command."""
    try:
        db = database.get_database()

        # Get repository
        repo_row = database.get_repository_by_full_name(args.repository)
        if not repo_row:
            print(f"Error: Repository {args.repository} not found in database", file=sys.stderr)
            return 1

        repo_id = repo_row['id']

        print(f"\n{'='*80}")
        print(f"Statistics for {args.repository}")
        print(f"{'='*80}\n")

        if args.author:
            # Per-author stats
            print(f"Stats for author: {args.author}\n")

            # PR counts by state
            cursor = db.execute("""
                SELECT state, COUNT(*) as count
                FROM pull_requests
                WHERE repo_id = ? AND author_login = ?
                GROUP BY state
            """, (repo_id, args.author))

            print("PRs by state:")
            for row in cursor.fetchall():
                print(f"  {row['state']}: {row['count']}")

            # Total comments
            cursor = db.execute("""
                SELECT COUNT(*) as count
                FROM issue_comments ic
                JOIN pull_requests pr ON pr.id = ic.pr_id
                WHERE pr.repo_id = ? AND ic.author_login = ?
            """, (repo_id, args.author))
            comment_count = cursor.fetchone()['count']

            cursor = db.execute("""
                SELECT COUNT(*) as count
                FROM review_comments rc
                JOIN pull_requests pr ON pr.id = rc.pr_id
                WHERE pr.repo_id = ? AND rc.author_login = ?
            """, (repo_id, args.author))
            review_comment_count = cursor.fetchone()['count']

            print(f"\nComments:")
            print(f"  Issue comments: {comment_count}")
            print(f"  Review comments: {review_comment_count}")
            print(f"  Total: {comment_count + review_comment_count}")

            # Reviews
            cursor = db.execute("""
                SELECT state, COUNT(*) as count
                FROM reviews r
                JOIN pull_requests pr ON pr.id = r.pr_id
                WHERE pr.repo_id = ? AND r.author_login = ?
                GROUP BY state
            """, (repo_id, args.author))

            print(f"\nReviews:")
            for row in cursor.fetchall():
                print(f"  {row['state']}: {row['count']}")

        else:
            # Overall stats
            # PR counts
            cursor = db.execute("""
                SELECT state, COUNT(*) as count
                FROM pull_requests
                WHERE repo_id = ?
                GROUP BY state
            """, (repo_id,))

            print("PRs by state:")
            total_prs = 0
            for row in cursor.fetchall():
                count = row['count']
                total_prs += count
                print(f"  {row['state']}: {count}")
            print(f"  Total: {total_prs}")

            # Top authors
            cursor = db.execute("""
                SELECT author_login, COUNT(*) as count
                FROM pull_requests
                WHERE repo_id = ?
                GROUP BY author_login
                ORDER BY count DESC
                LIMIT 10
            """, (repo_id,))

            print(f"\nTop PR authors:")
            for row in cursor.fetchall():
                print(f"  {row['author_login']}: {row['count']} PRs")

            # Top reviewers (last 60 days)
            cutoff = (datetime.now() - timedelta(days=60)).isoformat()
            cursor = db.execute("""
                SELECT
                  r.author_login,
                  COUNT(*) as review_count,
                  SUM(CASE WHEN r.state = 'APPROVED' THEN 1 ELSE 0 END) as approvals,
                  SUM(CASE WHEN r.state = 'CHANGES_REQUESTED' THEN 1 ELSE 0 END) as changes_requested
                FROM reviews r
                JOIN pull_requests pr ON pr.id = r.pr_id
                WHERE pr.repo_id = ? AND r.submitted_at >= ?
                GROUP BY r.author_login
                ORDER BY review_count DESC
                LIMIT 10
            """, (repo_id, cutoff))

            print(f"\nTop reviewers (last 60 days):")
            for row in cursor.fetchall():
                print(f"  {row['author_login']}: {row['review_count']} reviews "
                      f"({row['approvals']} approved, {row['changes_requested']} changes requested)")

            # Comment stats
            cursor = db.execute("""
                SELECT COUNT(*) as count
                FROM issue_comments ic
                JOIN pull_requests pr ON pr.id = ic.pr_id
                WHERE pr.repo_id = ?
            """, (repo_id,))
            total_issue_comments = cursor.fetchone()['count']

            cursor = db.execute("""
                SELECT COUNT(*) as count
                FROM review_comments rc
                JOIN pull_requests pr ON pr.id = rc.pr_id
                WHERE pr.repo_id = ?
            """, (repo_id,))
            total_review_comments = cursor.fetchone()['count']

            print(f"\nComments:")
            print(f"  Issue comments: {total_issue_comments}")
            print(f"  Review comments: {total_review_comments}")
            print(f"  Total: {total_issue_comments + total_review_comments}")

        print(f"\n{'='*80}\n")
        return 0

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def cmd_rate_limit(args: argparse.Namespace) -> int:
    """Handle rate-limit command."""
    try:
        client = GitHubClient()
        client.print_rate_limit_status()
        return 0
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def cmd_daemon(args: argparse.Namespace) -> int:
    """Handle daemon command."""
    try:
        from . import daemon
        return daemon.run_daemon(
            repository=args.repo,
            poll_interval=args.interval,
            verbose=not args.quiet
        )
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1


def cmd_review(args: argparse.Namespace) -> int:
    """Handle review command."""
    try:
        from . import review as review_module

        result = review_module.generate_pr_review(
            full_name=args.repository,
            pr_number=args.pr_number,
            commit_sha=args.commit,
            prompt_date=args.prompt_date,
            include_timeline=not args.diff_only,
            force=args.force,
            verbose=True
        )

        inline_comments = result.get('inline_comments', [])

        # Print review to stdout
        print("\n" + "="*80)
        print(f"AI Review for PR #{args.pr_number}")
        print(f"Commit: {result['commit_sha'][:8]}")
        print(f"Model: {result['model_used']} ({result['generation_method']})")
        print(f"Prompt: {result['prompt_path']}")
        print(f"Inline comments: {len(inline_comments)}")
        if result.get('cached'):
            print(f"Status: CACHED (returning existing review)")
        print("="*80 + "\n")
        print(result['review_text'])

        # Show inline comments unless --single
        if inline_comments and not getattr(args, 'single', False):
            print("\n" + "-"*80)
            print("INLINE COMMENTS:")
            print("-"*80)
            for i, c in enumerate(inline_comments, 1):
                line_info = f"{c['path']}:{c['line']}"
                if c.get('start_line'):
                    line_info = f"{c['path']}:{c['start_line']}-{c['line']}"
                print(f"\n[{i}] {line_info}")
                print(c['body'][:500] + ("..." if len(c['body']) > 500 else ""))
            print("-"*80)

        print("\n" + "="*80)
        if result.get('cached'):
            print(f"Cached review (ID: {result['review_id']})")
        else:
            print(f"Review saved to database (ID: {result['review_id']})")
        print("="*80 + "\n")

        return 0
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1


def cmd_feedback_report(args: argparse.Namespace) -> int:
    """Handle feedback-report command."""
    import re
    from collections import Counter

    try:
        db = database.get_database()

        # Check if new columns exist (backwards compatibility)
        try:
            db.execute("SELECT comment_type FROM review_feedback LIMIT 1")
            has_new_columns = True
        except Exception:
            has_new_columns = False

        # Build WHERE clause for --since filter
        since_filter = ""
        params = []
        if args.since:
            since_filter = "WHERE rf.created_at >= ?"
            params.append(args.since)

        # Query all feedback with review context
        if has_new_columns:
            query = f"""
                SELECT
                    rf.id,
                    rf.pr_number,
                    rf.github_login,
                    rf.feedback_text,
                    rf.created_at,
                    pr.commit_sha,
                    pr.model_used,
                    pr.prompt_path,
                    pr.review_text,
                    rf.comment_type,
                    rf.path,
                    rf.line
                FROM review_feedback rf
                LEFT JOIN pr_reviews pr ON rf.review_id = pr.id
                {since_filter}
                ORDER BY rf.created_at DESC
            """
        else:
            query = f"""
                SELECT
                    rf.id,
                    rf.pr_number,
                    rf.github_login,
                    rf.feedback_text,
                    rf.created_at,
                    pr.commit_sha,
                    pr.model_used,
                    pr.prompt_path,
                    pr.review_text,
                    NULL as comment_type,
                    NULL as path,
                    NULL as line
                FROM review_feedback rf
                LEFT JOIN pr_reviews pr ON rf.review_id = pr.id
                {since_filter}
                ORDER BY rf.created_at DESC
            """
        cursor = db.execute(query, params)
        rows = cursor.fetchall()

        if not rows:
            print("No feedback collected yet.")
            return 0

        # Parse feedback for patterns
        # Match: "Issue 1 was helpful", "Suggestion 2 is wrong", "Issues 3 and 4 were unhelpful"
        helpful_words = r'helpful|great|good|useful|valuable|very helpful'
        unhelpful_words = r'unhelpful|wrong|bad|incorrect|useless|not helpful'
        partial_words = r'fine|ok|okay|reasonable|interesting|redundant'

        # Single item: "Issue 1 was helpful"
        helpful_pattern = re.compile(rf'(issue|suggestion)\s*(\d+)\s*(?:was\s+|is\s+)?(?:{helpful_words})', re.I)
        unhelpful_pattern = re.compile(rf'(issue|suggestion)\s*(\d+)\s*(?:was\s+|is\s+)?(?:{unhelpful_words})', re.I)
        partial_pattern = re.compile(rf'(issue|suggestion)\s*(\d+)\s*(?:was\s+|is\s+)?(?:{partial_words})', re.I)

        # Multi-item: "Issues 3 and 4 were unhelpful"
        multi_helpful = re.compile(rf'(issues?|suggestions?)\s*(\d+)\s*(?:and|,)\s*(\d+)\s*(?:were\s+|are\s+)?(?:{helpful_words})', re.I)
        multi_unhelpful = re.compile(rf'(issues?|suggestions?)\s*(\d+)\s*(?:and|,)\s*(\d+)\s*(?:were\s+|are\s+)?(?:{unhelpful_words})', re.I)
        multi_partial = re.compile(rf'(issues?|suggestions?)\s*(\d+)\s*(?:and|,)\s*(\d+)\s*(?:were\s+|are\s+)?(?:{partial_words})', re.I)

        helpful_items = []
        unhelpful_items = []
        partial_items = []
        total_issues_mentioned = 0
        total_suggestions_mentioned = 0

        print("=" * 80)
        print("FEEDBACK REPORT")
        print("=" * 80)
        print(f"\nTotal feedback entries: {len(rows)}")
        print()

        for row in rows:
            fb_id = row['id']
            pr_number = row['pr_number']
            author = row['github_login']
            text = row['feedback_text']
            created = row['created_at'][:10] if row['created_at'] else 'N/A'
            commit = row['commit_sha'][:8] if row['commit_sha'] else 'N/A'
            model = row['model_used'] or 'N/A'
            prompt = row['prompt_path'] or 'N/A'
            review_text = row['review_text'] or ''
            comment_type = row['comment_type'] or 'issue'
            path = row['path']
            line = row['line']

            print("-" * 80)
            if comment_type == 'review' and path:
                print(f"PR #{pr_number} | {created} | by @{author} | INLINE: {path}:{line}")
            else:
                print(f"PR #{pr_number} | {created} | by @{author}")
            print(f"Review: commit={commit}, model={model}")
            if prompt != 'N/A':
                prompt_name = prompt.split('/')[-1] if '/' in prompt else prompt
                print(f"Prompt: {prompt_name}")

            # Show the review that this feedback is about
            if review_text:
                print()
                print("REVIEW:")
                print()
                # Indent the review text for clarity
                for review_line in review_text.split('\n'):
                    print(f"  {review_line}")

            print()
            print("FEEDBACK:")
            print()
            print(text)
            print()

            # Extract patterns - single items
            for match in helpful_pattern.finditer(text):
                item_type = match.group(1).lower()
                item_num = match.group(2)
                helpful_items.append(f"{item_type} {item_num}")
                if item_type == 'issue':
                    total_issues_mentioned += 1
                else:
                    total_suggestions_mentioned += 1

            for match in unhelpful_pattern.finditer(text):
                item_type = match.group(1).lower()
                item_num = match.group(2)
                unhelpful_items.append(f"{item_type} {item_num}")
                if item_type == 'issue':
                    total_issues_mentioned += 1
                else:
                    total_suggestions_mentioned += 1

            for match in partial_pattern.finditer(text):
                item_type = match.group(1).lower()
                item_num = match.group(2)
                partial_items.append(f"{item_type} {item_num}")
                if item_type == 'issue':
                    total_issues_mentioned += 1
                else:
                    total_suggestions_mentioned += 1

            # Extract patterns - multi items ("Issues 3 and 4 were unhelpful")
            for match in multi_helpful.finditer(text):
                item_type = match.group(1).lower().rstrip('s')  # issues -> issue
                for num in [match.group(2), match.group(3)]:
                    helpful_items.append(f"{item_type} {num}")
                    if item_type == 'issue':
                        total_issues_mentioned += 1
                    else:
                        total_suggestions_mentioned += 1

            for match in multi_unhelpful.finditer(text):
                item_type = match.group(1).lower().rstrip('s')
                for num in [match.group(2), match.group(3)]:
                    unhelpful_items.append(f"{item_type} {num}")
                    if item_type == 'issue':
                        total_issues_mentioned += 1
                    else:
                        total_suggestions_mentioned += 1

            for match in multi_partial.finditer(text):
                item_type = match.group(1).lower().rstrip('s')
                for num in [match.group(2), match.group(3)]:
                    partial_items.append(f"{item_type} {num}")
                    if item_type == 'issue':
                        total_issues_mentioned += 1
                    else:
                        total_suggestions_mentioned += 1

        # Summary
        print("=" * 80)
        print("SUMMARY")
        print("=" * 80)

        total_mentioned = len(helpful_items) + len(unhelpful_items) + len(partial_items)
        if total_mentioned > 0:
            print(f"\nItems mentioned in feedback: {total_mentioned}")
            print(f"  Issues mentioned: {total_issues_mentioned}")
            print(f"  Suggestions mentioned: {total_suggestions_mentioned}")
            print()

            if helpful_items:
                print(f"Helpful ({len(helpful_items)}):")
                for item, count in Counter(helpful_items).most_common():
                    print(f"  - {item}" + (f" (x{count})" if count > 1 else ""))

            if partial_items:
                print(f"\nPartially helpful ({len(partial_items)}):")
                for item, count in Counter(partial_items).most_common():
                    print(f"  - {item}" + (f" (x{count})" if count > 1 else ""))

            if unhelpful_items:
                print(f"\nUnhelpful ({len(unhelpful_items)}):")
                for item, count in Counter(unhelpful_items).most_common():
                    print(f"  - {item}" + (f" (x{count})" if count > 1 else ""))

            # Calculate helpfulness ratio
            if helpful_items or unhelpful_items:
                helpful_count = len(helpful_items)
                unhelpful_count = len(unhelpful_items)
                total = helpful_count + unhelpful_count
                ratio = helpful_count / total if total > 0 else 0
                print(f"\nHelpfulness ratio: {helpful_count}/{total} ({ratio:.0%})")
        else:
            print("\nNo structured feedback patterns detected.")
            print("(Looking for patterns like 'Issue 1 was helpful', 'Suggestion 2 was wrong')")

        print()
        return 0

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1


def cmd_analyze_feedback(args: argparse.Namespace) -> int:
    """Handle analyze-feedback command."""
    try:
        from . import analysis
        return analysis.analyze_feedback(
            since=args.since,
            prompt_path=args.prompt,
            dry_run=args.dry_run,
            verbose=args.verbose,
            force=args.force
        )
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Track GitHub PRs, comments, and reviews in SQLite"
    )
    subparsers = parser.add_subparsers(dest='command', help='Command to run')

    # sync command
    sync_parser = subparsers.add_parser('sync', help='Sync PR data from GitHub')
    sync_parser.add_argument('repository', help='Repository in format owner/repo')
    sync_parser.add_argument('--since', help='Full sync: PRs updated since this date (ISO format)')
    sync_parser.add_argument('--incremental', action='store_true', help='Incremental sync (update only changed PRs)')
    sync_parser.add_argument('--pr', type=int, help='Sync a specific PR number')
    sync_parser.add_argument('--force', action='store_true', help='Force re-sync of all PRs even if unchanged')
    sync_parser.add_argument('--delay', type=float, default=0.9,
                             help='Delay between requests in seconds (default: 0.9 = ~4000 req/hour, leaving 1000 spare)')

    # list command
    list_parser = subparsers.add_parser('list', help='List PRs')
    list_parser.add_argument('repository', help='Repository in format owner/repo')
    list_parser.add_argument('--state', choices=['open', 'closed', 'merged'], help='Filter by state')
    list_parser.add_argument('--author', help='Filter by author')
    list_parser.add_argument('--limit', type=int, help='Limit number of results')

    # show command
    show_parser = subparsers.add_parser('show', help='Show PR details and timeline')
    show_parser.add_argument('repository', help='Repository in format owner/repo')
    show_parser.add_argument('pr_number', type=int, help='PR number')

    # search command
    search_parser = subparsers.add_parser('search', help='Search comments')
    search_parser.add_argument('query', help='Search query (FTS5 syntax)')
    search_parser.add_argument('--limit', type=int, help='Limit number of results (default 50)')

    # stats command
    stats_parser = subparsers.add_parser('stats', help='Show statistics')
    stats_parser.add_argument('repository', help='Repository in format owner/repo')
    stats_parser.add_argument('--author', help='Show stats for specific author')

    # rate-limit command
    subparsers.add_parser('rate-limit', help='Show GitHub API rate limit status')

    # review command
    review_parser = subparsers.add_parser('review', help='Generate AI review for a PR')
    review_parser.add_argument('repository', help='Repository (owner/repo)')
    review_parser.add_argument('pr_number', type=int, help='PR number')
    review_parser.add_argument('--commit', help='Review at specific commit SHA')
    review_parser.add_argument('--prompt-date', help='Use prompt from date (YYYY-MM-DD)')
    review_parser.add_argument('--diff-only', action='store_true',
                              help='Review only the diff, exclude timeline/comments')
    review_parser.add_argument('--force', action='store_true',
                              help='Regenerate review even if one exists')
    review_parser.add_argument('--single', action='store_true',
                              help='Show only summary (no inline comments info)')

    # daemon command
    daemon_parser = subparsers.add_parser('daemon', help='Run polling daemon for @botbaki triggers')
    daemon_parser.add_argument('--repo', default='leanprover-community/mathlib4',
                               help='Repository to monitor (default: leanprover-community/mathlib4)')
    daemon_parser.add_argument('--interval', type=int, default=120,
                               help='Poll interval in seconds (default: 120)')
    daemon_parser.add_argument('--quiet', '-q', action='store_true',
                               help='Reduce output verbosity')

    # feedback-report command
    feedback_report_parser = subparsers.add_parser('feedback-report', help='Show collected feedback on reviews')
    feedback_report_parser.add_argument('--since', help='Only show feedback since this date (YYYY-MM-DD)')

    # analyze-feedback command
    analyze_parser = subparsers.add_parser('analyze-feedback',
                                           help='Analyze feedback and generate improved prompt')
    analyze_parser.add_argument('--since', help='Only analyze feedback since this date (YYYY-MM-DD)')
    analyze_parser.add_argument('--prompt', help='Override prompt template to improve (default: latest)')
    analyze_parser.add_argument('--dry-run', action='store_true',
                               help='Show what would be analyzed without calling API')
    analyze_parser.add_argument('--verbose', '-v', action='store_true',
                               help='Show progress during analysis')
    analyze_parser.add_argument('--force', action='store_true',
                               help='Overwrite output even if it has uncommitted git changes')

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 1

    # Dispatch to command handler
    if args.command == 'sync':
        return cmd_sync(args)
    elif args.command == 'list':
        return cmd_list(args)
    elif args.command == 'show':
        return cmd_show(args)
    elif args.command == 'search':
        return cmd_search(args)
    elif args.command == 'stats':
        return cmd_stats(args)
    elif args.command == 'rate-limit':
        return cmd_rate_limit(args)
    elif args.command == 'review':
        return cmd_review(args)
    elif args.command == 'daemon':
        return cmd_daemon(args)
    elif args.command == 'feedback-report':
        return cmd_feedback_report(args)
    elif args.command == 'analyze-feedback':
        return cmd_analyze_feedback(args)
    else:
        parser.print_help()
        return 1


if __name__ == '__main__':
    sys.exit(main())
