"""Feedback analysis for prompt improvement."""

from __future__ import annotations

import os
import subprocess
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import database
from .github_client import GitHubClient, GitHubError
from .review import format_diff_with_line_numbers, format_timeline_for_review
from .sync import parse_repo_full_name


def get_feedback_since(
    since: Optional[str] = None,
    include_handled: bool = False
) -> List[Dict]:
    """
    Get feedback entries with their associated review data.

    Args:
        since: Optional date string (YYYY-MM-DD) to filter feedback from
        include_handled: If False (default), only return unhandled feedback

    Returns:
        List of dicts with feedback and review information
    """
    db = database.get_database()

    # Ensure handled_at column exists (backwards compatibility)
    try:
        db.execute("ALTER TABLE review_feedback ADD COLUMN handled_at TEXT")
        db.commit()
    except Exception:
        pass  # Column already exists

    # Check if new columns exist (backwards compatibility)
    try:
        db.execute("SELECT comment_type FROM review_feedback LIMIT 1")
        has_new_columns = True
    except Exception:
        has_new_columns = False

    # Build WHERE clause
    conditions = []
    params = []
    if since:
        conditions.append("rf.created_at >= ?")
        params.append(since)
    if not include_handled:
        conditions.append("rf.handled_at IS NULL")

    where_clause = "WHERE " + " AND ".join(conditions) if conditions else ""

    if has_new_columns:
        query = f"""
            SELECT
                rf.id,
                rf.pr_number,
                rf.github_login,
                rf.feedback_text,
                rf.created_at,
                rf.comment_type,
                rf.path as feedback_path,
                rf.line as feedback_line,
                pr.id as review_id,
                pr.commit_sha,
                pr.prompt_path,
                pr.review_text,
                pr.raw_metadata,
                pull.id as pr_id,
                pull.title as pr_title,
                pull.author_login as pr_author,
                pull.state as pr_state,
                repo.full_name as repo_full_name
            FROM review_feedback rf
            LEFT JOIN pr_reviews pr ON rf.review_id = pr.id
            LEFT JOIN pull_requests pull ON rf.pr_id = pull.id
            LEFT JOIN repositories repo ON pull.repo_id = repo.id
            {where_clause}
            ORDER BY rf.created_at ASC
        """
    else:
        query = f"""
            SELECT
                rf.id,
                rf.pr_number,
                rf.github_login,
                rf.feedback_text,
                rf.created_at,
                NULL as comment_type,
                NULL as feedback_path,
                NULL as feedback_line,
                pr.id as review_id,
                pr.commit_sha,
                pr.prompt_path,
                pr.review_text,
                pr.raw_metadata,
                pull.id as pr_id,
                pull.title as pr_title,
                pull.author_login as pr_author,
                pull.state as pr_state,
                repo.full_name as repo_full_name
            FROM review_feedback rf
            LEFT JOIN pr_reviews pr ON rf.review_id = pr.id
            LEFT JOIN pull_requests pull ON rf.pr_id = pull.id
            LEFT JOIN repositories repo ON pull.repo_id = repo.id
            {where_clause}
            ORDER BY rf.created_at ASC
        """

    cursor = db.execute(query, params)
    return [dict(row) for row in cursor.fetchall()]


def load_prompt_template(path: str) -> str:
    """Load a prompt template from file."""
    prompt_path = Path(__file__).parent.parent / path
    if not prompt_path.exists():
        raise FileNotFoundError(f"Prompt template not found: {prompt_path}")
    return prompt_path.read_text()


def get_latest_prompt_path(repo_name: str = "mathlib4") -> str:
    """Get the path to the latest prompt for a repo."""
    prompt_info = database.get_latest_prompt_for_repo(repo_name)
    if not prompt_info:
        raise ValueError(f"No prompts found for {repo_name}")
    return prompt_info['path']


def reconstruct_review_context(entry: Dict, verbose: bool = False) -> str:
    """
    Reconstruct the full prompt that was sent for a review.

    This fetches the diff and timeline at the specific commit that was reviewed,
    using the same prompt template that was used at review time.
    """
    pr_number = entry['pr_number']
    commit_sha = entry['commit_sha']
    prompt_path = entry['prompt_path']
    repo_full_name = entry['repo_full_name']

    if not all([pr_number, commit_sha, prompt_path, repo_full_name]):
        raise ValueError(f"Missing data for feedback entry {entry['id']}: "
                        f"pr={pr_number}, commit={commit_sha}, prompt={prompt_path}, repo={repo_full_name}")

    owner, repo = parse_repo_full_name(repo_full_name)

    # Load the prompt template that was used
    prompt_template = load_prompt_template(prompt_path)

    # Fetch diff at the reviewed commit
    client = GitHubClient()
    if verbose:
        print(f"  Fetching diff for PR #{pr_number}...", file=sys.stderr)

    files = client.get_pull_files(owner, repo, pr_number)
    diff = format_diff_with_line_numbers(files)

    # Get timeline from database
    db = database.get_database()
    pr_id = entry['pr_id']

    # Check if timeline was included in original review
    import json
    metadata = json.loads(entry['raw_metadata']) if entry['raw_metadata'] else {}
    include_timeline = metadata.get('include_timeline', True)

    if include_timeline:
        timeline_events = database.get_pr_timeline(pr_id)
        timeline = format_timeline_for_review(timeline_events)
    else:
        timeline = "(Timeline excluded per --diff-only flag)"

    # Build PR data for template
    title = entry['pr_title'] or f"PR #{pr_number}"
    if title.startswith('Merged by bors: '):
        title = title[len('Merged by bors: '):]

    pr_data = {
        'repo_full_name': repo_full_name,
        'pr_number': pr_number,
        'title': title,
        'author': entry['pr_author'] or 'unknown',
        'commit_sha': commit_sha,
        'state': entry['pr_state'] or 'unknown',
        'current_date': entry['created_at'][:10] if entry['created_at'] else datetime.now().strftime('%Y-%m-%d')
    }

    # Fill in the template
    full_prompt = prompt_template.format(
        repo_full_name=pr_data['repo_full_name'],
        pr_number=pr_data['pr_number'],
        title=pr_data['title'],
        author=pr_data['author'],
        commit_sha=pr_data['commit_sha'],
        state=pr_data['state'],
        current_date=pr_data['current_date'],
        diff=diff,
        timeline=timeline
    )

    return full_prompt


def extract_issue_summary(
    full_context: str,
    review_text: str,
    feedback_text: str,
    verbose: bool = False
) -> str:
    """
    Call LLM to extract a concise issue summary from one feedback entry.

    Returns a 2-4 sentence summary of what went wrong and why.
    """
    import anthropic

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY environment variable is required.")

    client = anthropic.Anthropic(api_key=api_key)

    # Load the extraction prompt template
    extract_prompt_template = load_prompt_template("prompts/analysis/extract-issues.md")

    prompt = extract_prompt_template.format(
        full_prompt=full_context,
        review_text=review_text,
        feedback_text=feedback_text
    )

    response = client.messages.create(
        model="claude-sonnet-4-5-20250929",  # Use Sonnet for extraction (cheaper)
        max_tokens=500,
        messages=[{"role": "user", "content": prompt}]
    )

    return response.content[0].text


def synthesize_new_prompt(
    current_prompt: str,
    issue_summaries: List[Dict],
    verbose: bool = False
) -> str:
    """
    Call LLM to produce an improved prompt based on collected issues.

    Args:
        current_prompt: The current prompt template content
        issue_summaries: List of dicts with pr_number, date, summary

    Returns:
        The new prompt template content
    """
    import anthropic

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY environment variable is required.")

    client = anthropic.Anthropic(api_key=api_key)

    # Format issue summaries
    summaries_text = ""
    for item in issue_summaries:
        summaries_text += f"### PR #{item['pr_number']} ({item['date']})\n"
        summaries_text += f"{item['summary']}\n\n"

    # Load the synthesis prompt template
    synth_prompt_template = load_prompt_template("prompts/analysis/synthesize-prompt.md")

    prompt = synth_prompt_template.format(
        prompt_template=current_prompt,
        issue_summaries=summaries_text
    )

    response = client.messages.create(
        model="claude-sonnet-4-5-20250929",  # Use Sonnet for synthesis
        max_tokens=8000,
        messages=[{"role": "user", "content": prompt}]
    )

    return response.content[0].text


def file_has_uncommitted_changes(path: Path) -> bool:
    """Check if file exists with uncommitted changes.

    Returns True if the file:
    - Is untracked (new file not yet committed), OR
    - Has modifications not yet committed

    Returns False if the file:
    - Doesn't exist, OR
    - Exists and is committed with no local changes
    """
    result = subprocess.run(
        ['git', 'status', '--porcelain', str(path)],
        capture_output=True,
        text=True,
        cwd=path.parent
    )
    # Non-empty output means file has uncommitted changes (modified, added, untracked, etc.)
    return bool(result.stdout.strip())


def analyze_feedback(
    since: Optional[str] = None,
    prompt_path: Optional[str] = None,
    dry_run: bool = False,
    verbose: bool = False,
    include_handled: bool = False
) -> int:
    """
    Main entry point for feedback analysis.

    Generates an improved prompt based on user feedback.

    Args:
        since: Filter feedback since this date (YYYY-MM-DD)
        prompt_path: Override prompt template to improve (default: latest)
        dry_run: Just show what would be analyzed
        verbose: Show progress
        include_handled: Include already-handled feedback (default: False)

    Returns:
        Exit code (0 for success)
    """
    # 1. Determine output path
    today = datetime.now().strftime('%Y-%m-%d')
    output_path = Path(__file__).parent.parent / "prompts" / "mathlib4" / f"{today}.md"

    # 2. Check early: if file exists with uncommitted changes, stop
    if output_path.exists() and file_has_uncommitted_changes(output_path):
        print(f"Error: {output_path} has uncommitted changes.", file=sys.stderr)
        print("Commit or discard changes before running analyze-feedback.", file=sys.stderr)
        return 1

    # 3. Load feedback entries
    feedback_entries = get_feedback_since(since, include_handled=include_handled)

    if not feedback_entries:
        print("No feedback entries found.")
        return 0

    # Filter to entries with associated reviews
    entries_with_reviews = [e for e in feedback_entries if e['review_id']]
    if not entries_with_reviews:
        print("No feedback entries with associated reviews found.")
        return 0

    if dry_run:
        print(f"Would analyze {len(entries_with_reviews)} feedback entries:\n")
        for entry in entries_with_reviews:
            date = entry['created_at'][:10] if entry['created_at'] else 'N/A'
            print(f"  PR #{entry['pr_number']} ({date}) - {entry['feedback_text'][:60]}...")
        print(f"\nWould write to: {output_path}")
        return 0

    # 4. Pass 1: Extract issues for each entry
    if verbose:
        print(f"Pass 1: Extracting issues from {len(entries_with_reviews)} feedback entries...",
              file=sys.stderr)

    issue_summaries = []
    for i, entry in enumerate(entries_with_reviews, 1):
        if verbose:
            print(f"  [{i}/{len(entries_with_reviews)}] PR #{entry['pr_number']}...", file=sys.stderr)

        try:
            # Re-fetch diff/timeline at the reviewed commit
            context = reconstruct_review_context(entry, verbose=verbose)

            # Call LLM to extract issue summary
            summary = extract_issue_summary(
                context,
                entry['review_text'] or '',
                entry['feedback_text'] or '',
                verbose=verbose
            )

            issue_summaries.append({
                'pr_number': entry['pr_number'],
                'date': entry['created_at'][:10] if entry['created_at'] else 'N/A',
                'summary': summary
            })

            if verbose:
                print(f"    Summary: {summary[:80]}...", file=sys.stderr)

        except Exception as e:
            print(f"  Warning: Failed to analyze PR #{entry['pr_number']}: {e}", file=sys.stderr)
            continue

    if not issue_summaries:
        print("No issues could be extracted from feedback.", file=sys.stderr)
        return 1

    # 5. Pass 2: Synthesize new prompt
    if verbose:
        print(f"\nPass 2: Synthesizing new prompt from {len(issue_summaries)} issues...",
              file=sys.stderr)

    # Load current prompt template
    if prompt_path:
        current_prompt = load_prompt_template(prompt_path)
    else:
        prompt_info = database.get_latest_prompt_for_repo("mathlib4")
        if not prompt_info:
            print("Error: No prompts found for mathlib4", file=sys.stderr)
            return 1
        current_prompt = prompt_info['content']

    new_prompt = synthesize_new_prompt(current_prompt, issue_summaries, verbose=verbose)

    # 6. Write to file
    output_path.write_text(new_prompt)
    print(f"Wrote {output_path}", file=sys.stderr)

    # Show follow-up command
    print(f"\nTo mark this feedback as handled:", file=sys.stderr)
    if since:
        print(f"  botbaki mark-feedback-handled --since {since}", file=sys.stderr)
    else:
        print(f"  botbaki mark-feedback-handled", file=sys.stderr)

    # Also print to stdout
    print(new_prompt)

    return 0


def mark_feedback_handled(
    since: Optional[str] = None,
    dry_run: bool = False
) -> int:
    """
    Mark feedback entries as handled (incorporated into prompt updates).

    Args:
        since: Only mark feedback since this date (YYYY-MM-DD)
        dry_run: Just show what would be marked

    Returns:
        Exit code (0 for success)
    """
    db = database.get_database()

    # Ensure handled_at column exists
    try:
        db.execute("ALTER TABLE review_feedback ADD COLUMN handled_at TEXT")
        db.commit()
    except Exception:
        pass

    # Build WHERE clause
    conditions = ["handled_at IS NULL"]
    params = []
    if since:
        conditions.append("created_at >= ?")
        params.append(since)

    where_clause = " AND ".join(conditions)

    # Count entries
    cursor = db.execute(
        f"SELECT COUNT(*) FROM review_feedback WHERE {where_clause}",
        params
    )
    count = cursor.fetchone()[0]

    if count == 0:
        print("No unhandled feedback entries to mark.")
        return 0

    if dry_run:
        print(f"Would mark {count} feedback entries as handled.")
        return 0

    # Mark as handled
    now = datetime.now().isoformat()
    db.execute(
        f"UPDATE review_feedback SET handled_at = ? WHERE {where_clause}",
        [now] + params
    )
    db.commit()

    print(f"Marked {count} feedback entries as handled.")
    return 0


def get_reaction_feedback(
    repository: str = "leanprover-community/mathlib4",
    since: Optional[str] = None,
    verbose: bool = False
) -> List[Dict[str, Any]]:
    """
    Collect emoji reactions on bot's review comments as implicit feedback.

    Fetches reactions on:
    - Review comments posted by the bot (inline code comments)
    - Issue comments posted by the bot (review summaries, help messages)

    Args:
        repository: Repository in format owner/repo
        since: Only collect reactions since this date (YYYY-MM-DD)
        verbose: Print progress

    Returns:
        List of dicts with reaction info:
        - pr_number: PR where reaction was posted
        - comment_type: 'review' or 'issue'
        - comment_id: GitHub comment ID
        - reaction: The reaction type (+1, -1, laugh, confused, etc.)
        - reactor: GitHub login of person who reacted
        - created_at: When reaction was posted
        - path: For review comments, the file path
        - line: For review comments, the line number
    """
    owner, repo = parse_repo_full_name(repository)
    client = GitHubClient()

    # Get bot's login
    try:
        bot_info = client.get_authenticated_user()
        bot_login = bot_info['login']
        if verbose:
            print(f"Bot login: {bot_login}", file=sys.stderr)
    except GitHubError as e:
        print(f"Failed to get bot info: {e}", file=sys.stderr)
        return []

    db = database.get_database()

    # Get repository
    repo_row = database.get_repository_by_full_name(repository)
    if not repo_row:
        print(f"Repository {repository} not found in database", file=sys.stderr)
        return []

    repo_id = repo_row['id']

    # Build query for bot's comments
    since_filter = ""
    params = [repo_id, bot_login]
    if since:
        since_filter = " AND rc.created_at >= ?"
        params.append(since)

    # Find review comments by bot
    review_comments_query = f"""
        SELECT
            rc.comment_id,
            rc.path,
            rc.line,
            rc.created_at,
            pr.pr_number
        FROM review_comments rc
        JOIN pull_requests pr ON pr.id = rc.pr_id
        WHERE pr.repo_id = ?
          AND rc.author_login = ?
          {since_filter}
    """

    cursor = db.execute(review_comments_query, params)
    review_comments = [dict(row) for row in cursor.fetchall()]

    # Find issue comments by bot
    params = [repo_id, bot_login]
    if since:
        params.append(since)

    issue_comments_query = f"""
        SELECT
            ic.comment_id,
            ic.created_at,
            pr.pr_number
        FROM issue_comments ic
        JOIN pull_requests pr ON pr.id = ic.pr_id
        WHERE pr.repo_id = ?
          AND ic.author_login = ?
          {since_filter.replace('rc.', 'ic.')}
    """

    cursor = db.execute(issue_comments_query, params)
    issue_comments = [dict(row) for row in cursor.fetchall()]

    if verbose:
        print(f"Found {len(review_comments)} review comments and {len(issue_comments)} issue comments by bot",
              file=sys.stderr)

    reactions = []

    # Fetch reactions on review comments
    for comment in review_comments:
        try:
            comment_reactions = client.get_review_comment_reactions(
                owner, repo, comment['comment_id']
            )
            for r in comment_reactions:
                reactions.append({
                    'pr_number': comment['pr_number'],
                    'comment_type': 'review',
                    'comment_id': comment['comment_id'],
                    'reaction': r['content'],
                    'reactor': r['user'],
                    'created_at': r['created_at'],
                    'path': comment['path'],
                    'line': comment['line']
                })
        except GitHubError as e:
            if verbose:
                print(f"  Failed to get reactions for review comment {comment['comment_id']}: {e}",
                      file=sys.stderr)

    # Fetch reactions on issue comments
    for comment in issue_comments:
        try:
            comment_reactions = client.get_issue_comment_reactions(
                owner, repo, comment['comment_id']
            )
            for r in comment_reactions:
                reactions.append({
                    'pr_number': comment['pr_number'],
                    'comment_type': 'issue',
                    'comment_id': comment['comment_id'],
                    'reaction': r['content'],
                    'reactor': r['user'],
                    'created_at': r['created_at'],
                    'path': None,
                    'line': None
                })
        except GitHubError as e:
            if verbose:
                print(f"  Failed to get reactions for issue comment {comment['comment_id']}: {e}",
                      file=sys.stderr)

    if verbose:
        print(f"Total reactions collected: {len(reactions)}", file=sys.stderr)

    return reactions


def format_reaction_summary(reactions: List[Dict[str, Any]]) -> str:
    """
    Format reactions into a summary string.

    Args:
        reactions: List of reaction dicts from get_reaction_feedback()

    Returns:
        Formatted summary string
    """
    if not reactions:
        return "No reactions found on bot's comments."

    # Count by reaction type
    reaction_counts = Counter(r['reaction'] for r in reactions)

    # Map reaction codes to emoji
    emoji_map = {
        '+1': '👍',
        '-1': '👎',
        'laugh': '😄',
        'confused': '😕',
        'heart': '❤️',
        'hooray': '🎉',
        'rocket': '🚀',
        'eyes': '👀'
    }

    lines = []
    lines.append(f"Total reactions: {len(reactions)}")
    lines.append("")

    # Show counts with emoji
    for reaction, count in reaction_counts.most_common():
        emoji = emoji_map.get(reaction, reaction)
        lines.append(f"  {emoji} {reaction}: {count}")

    # Break down positive vs negative
    positive = sum(1 for r in reactions if r['reaction'] in ['+1', 'heart', 'hooray', 'rocket'])
    negative = sum(1 for r in reactions if r['reaction'] in ['-1', 'confused'])
    neutral = len(reactions) - positive - negative

    lines.append("")
    lines.append(f"Sentiment: +{positive} / -{negative} / ~{neutral}")

    if positive + negative > 0:
        ratio = positive / (positive + negative)
        lines.append(f"Positive ratio: {ratio:.0%}")

    # Show breakdown by PR
    pr_reactions = Counter(r['pr_number'] for r in reactions)
    if len(pr_reactions) > 1:
        lines.append("")
        lines.append("By PR:")
        for pr_num, count in pr_reactions.most_common(5):
            lines.append(f"  PR #{pr_num}: {count} reactions")
        if len(pr_reactions) > 5:
            lines.append(f"  ... and {len(pr_reactions) - 5} more PRs")

    return "\n".join(lines)
