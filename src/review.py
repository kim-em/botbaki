"""AI-powered PR review generation."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import database, quota
from .github_client import GitHubClient
from .sync import parse_repo_full_name


def format_diff_for_review(files: List[Dict]) -> str:
    """
    Format GitHub files API response into readable diff.

    Truncates very large diffs to stay within token limits.
    """
    output = []
    total_changes = sum(f.get('changes', 0) for f in files)

    output.append(f"# Changed Files ({len(files)} files, {total_changes} changes)\n")

    for file_info in files:
        filename = file_info['filename']
        status = file_info['status']
        additions = file_info.get('additions', 0)
        deletions = file_info.get('deletions', 0)

        output.append(f"\n## {filename}")
        output.append(f"Status: {status} (+{additions}/-{deletions})\n")

        # Include the diff patch if available
        if 'patch' in file_info:
            output.append("```diff")
            output.append(file_info['patch'])
            output.append("```\n")

    full_diff = "\n".join(output)

    # Truncate if too large (rough estimate: 1 token ~= 4 chars)
    # Aim for ~50k tokens = ~200k chars for diff section
    MAX_DIFF_LENGTH = 200_000

    if len(full_diff) > MAX_DIFF_LENGTH:
        full_diff = full_diff[:MAX_DIFF_LENGTH]
        full_diff += f"\n\n... [TRUNCATED: diff too large, showing first {MAX_DIFF_LENGTH} chars] ...\n"

    return full_diff


def format_timeline_for_review(timeline: List[Dict]) -> str:
    """Format PR timeline (from botbaki show) for review context."""
    output = ["# PR Timeline\n"]

    for event in timeline:
        timestamp = event['timestamp'][:19]
        event_type = event['type']
        actor = event['actor']
        content = event['content']

        if event_type == 'commit':
            output.append(f"[{timestamp}] Commit by {actor}: {content}")
        elif event_type == 'review_comment':
            path = event.get('path', '')
            line = event.get('line')
            location = f"{path}:{line}" if line else path
            output.append(f"[{timestamp}] Review comment by {actor} @ {location}")
            output.append(f"  {content[:200]}..." if len(content) > 200 else f"  {content}")
        elif event_type == 'issue_comment':
            output.append(f"[{timestamp}] Comment by {actor}")
            output.append(f"  {content[:200]}..." if len(content) > 200 else f"  {content}")
        elif event_type == 'review':
            output.append(f"[{timestamp}] Review by {actor}: {content}")

    # Limit timeline length
    timeline_text = "\n".join(output)
    MAX_TIMELINE_LENGTH = 50_000

    if len(timeline_text) > MAX_TIMELINE_LENGTH:
        timeline_text = timeline_text[:MAX_TIMELINE_LENGTH]
        timeline_text += f"\n\n... [TRUNCATED: timeline too long] ...\n"

    return timeline_text


def generate_review_via_anthropic_sdk(
    prompt_template: str,
    diff: str,
    timeline: str,
    pr_data: Dict,
    model_preference: str = "opus"
) -> Tuple[str, str]:
    """
    Generate review using Anthropic Python SDK.

    Returns:
        Tuple of (review_text, model_used)
    """
    import anthropic

    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

    # Map preference to actual model IDs
    model_map = {
        "opus": "claude-opus-4-5-20251101",
        "sonnet": "claude-sonnet-4-5-20250929"
    }

    model_id = model_map.get(model_preference, model_map["sonnet"])

    # Fill in template with PR data
    full_prompt = prompt_template.format(
        repo_full_name=pr_data['repo_full_name'],
        pr_number=pr_data['pr_number'],
        title=pr_data['title'],
        author=pr_data['author'],
        commit_sha=pr_data['commit_sha'],
        state=pr_data['state'],
        diff=diff,
        timeline=timeline
    )

    response = client.messages.create(
        model=model_id,
        max_tokens=16000,
        messages=[
            {"role": "user", "content": full_prompt}
        ]
    )

    review_text = response.content[0].text

    return (review_text, model_preference)


def generate_review_via_claude_cli(
    prompt_template: str,
    diff: str,
    timeline: str,
    pr_data: Dict,
    model_preference: str = "opus",
    verbose: bool = True
) -> Tuple[str, str]:
    """
    Generate review using local Claude CLI with quota checking.

    If quota exhausted, sleeps for 1 hour and retries.

    Returns:
        Tuple of (review_text, model_used)
    """
    # Fill in template with PR data
    full_prompt = prompt_template.format(
        repo_full_name=pr_data['repo_full_name'],
        pr_number=pr_data['pr_number'],
        title=pr_data['title'],
        author=pr_data['author'],
        commit_sha=pr_data['commit_sha'],
        state=pr_data['state'],
        diff=diff,
        timeline=timeline
    )

    while True:
        # Check quota
        available_model = quota.check_claude_quota(verbose=verbose)

        if available_model is None:
            # No quota available
            if verbose:
                print("\nNo Claude quota available. Sleeping for 1 hour...", file=sys.stderr)
                retry_time = (datetime.now() + timedelta(hours=1)).strftime('%H:%M:%S')
                print(f"Will retry at {retry_time}", file=sys.stderr)

            time.sleep(3600)  # Sleep for 1 hour
            continue

        # We have quota - use the available model
        actual_model = available_model if model_preference == "opus" else "sonnet"

        if verbose and actual_model != model_preference:
            print(f"Note: Requested {model_preference} but using {actual_model} due to quota",
                  file=sys.stderr)

        # Run claude -p with prompt via stdin
        cmd = ['claude', '-p']

        if verbose:
            print(f"\nGenerating review with Claude {actual_model}...", file=sys.stderr)

        result = subprocess.run(
            cmd,
            input=full_prompt,
            capture_output=True,
            text=True,
            check=False
        )

        if result.returncode == 0:
            # Success
            return (result.stdout.strip(), actual_model)
        else:
            # Check if quota error
            stderr_lower = result.stderr.lower()
            if 'quota' in stderr_lower or 'limit' in stderr_lower:
                if verbose:
                    print(f"Quota exhausted during generation. Sleeping for 1 hour...",
                          file=sys.stderr)
                time.sleep(3600)
                continue
            else:
                # Other error
                raise RuntimeError(f"Claude CLI failed: {result.stderr}")


def generate_pr_review(
    full_name: str,
    pr_number: int,
    commit_sha: Optional[str] = None,
    prompt_date: Optional[str] = None,
    include_timeline: bool = True,
    force: bool = False,
    verbose: bool = True
) -> Dict:
    """
    Generate AI review for a PR.

    Args:
        full_name: Repository in format 'owner/repo'
        pr_number: PR number
        commit_sha: Specific commit to review (default: HEAD of PR)
        prompt_date: Use prompt from specific date YYYY-MM-DD (default: latest)
        include_timeline: Include botbaki show output (default: True)
        force: Regenerate even if review exists (default: False)
        verbose: Print progress

    Returns:
        Dict with review data (review_text, model_used, etc.)
    """
    owner, repo = parse_repo_full_name(full_name)

    # 1. Ensure PR is in database
    db = database.get_database()
    repo_row = database.get_repository_by_full_name(full_name)

    if not repo_row:
        if verbose:
            print(f"Repository {full_name} not in database, syncing...", file=sys.stderr)
        from .sync import sync_single_pr
        sync_single_pr(full_name, pr_number, verbose=verbose)
        repo_row = database.get_repository_by_full_name(full_name)

    repo_id = repo_row['id']

    pr_row = database.get_pr_by_number(repo_id, pr_number)
    if not pr_row:
        if verbose:
            print(f"PR #{pr_number} not in database, syncing...", file=sys.stderr)
        from .sync import sync_single_pr
        sync_single_pr(full_name, pr_number, verbose=verbose)
        pr_row = database.get_pr_by_number(repo_id, pr_number)

    pr_id = pr_row['id']

    # 2. Get commit SHA (default to head_sha)
    if commit_sha is None:
        commit_sha = pr_row['head_sha']

    # 3. Load prompt
    if prompt_date:
        prompt_info = database.get_prompt_by_date(repo, prompt_date)
        if not prompt_info:
            raise ValueError(f"No prompt found for {repo} on {prompt_date}")
    else:
        prompt_info = database.get_latest_prompt_for_repo(repo)
        if not prompt_info:
            raise ValueError(f"No prompts found for {repo}. Create prompts/{repo}/YYYY-MM-DD.md")

    prompt_path, prompt_content, prompt_hash = prompt_info

    if verbose:
        print(f"\nUsing prompt: {prompt_path}", file=sys.stderr)
        print(f"Reviewing PR #{pr_number} at commit {commit_sha[:8]}", file=sys.stderr)

    # 3.5. Check if review already exists for this PR/commit/prompt combination
    if not force:
        existing_reviews = database.get_pr_reviews(pr_id)
        for existing in existing_reviews:
            # Parse metadata to check include_timeline flag
            import json
            existing_metadata = json.loads(existing['raw_metadata']) if existing['raw_metadata'] else {}
            existing_include_timeline = existing_metadata.get('include_timeline', True)

            if (existing['commit_sha'] == commit_sha and
                existing['prompt_hash'] == prompt_hash and
                existing_include_timeline == include_timeline):
                if verbose:
                    print(f"\nReview already exists (ID: {existing['id']})! Returning cached review.",
                          file=sys.stderr)
                    print(f"Use --force to regenerate.", file=sys.stderr)
                return {
                    'review_id': existing['id'],
                    'review_text': existing['review_text'],
                    'model_used': existing['model_used'],
                    'generation_method': existing['generation_method'],
                    'prompt_path': existing['prompt_path'],
                    'commit_sha': existing['commit_sha'],
                    'cached': True
                }
    elif verbose:
        print("\n--force flag set, regenerating review...", file=sys.stderr)

    # 4. Fetch diff from GitHub
    client = GitHubClient()

    if verbose:
        print("Fetching diff from GitHub...", file=sys.stderr)

    files = client.get_pull_files(owner, repo, pr_number)
    diff = format_diff_for_review(files)

    # 5. Get timeline if requested
    if include_timeline:
        if verbose:
            print("Fetching PR timeline...", file=sys.stderr)
        timeline_events = database.get_pr_timeline(pr_id)
        timeline = format_timeline_for_review(timeline_events)
    else:
        timeline = "(Timeline excluded per --diff-only flag)"

    # 6. Choose generation method
    use_sdk = quota.has_anthropic_api_key()

    if verbose:
        method = "Anthropic SDK" if use_sdk else "Claude CLI"
        print(f"Generation method: {method}", file=sys.stderr)

    # 7. Generate review
    # Strip "Merged by bors" from title - not helpful for review
    title = pr_row['title']
    if title.startswith('Merged by bors: '):
        title = title[len('Merged by bors: '):]

    pr_data = {
        'repo_full_name': full_name,
        'pr_number': pr_number,
        'title': title,
        'author': pr_row['author_login'],
        'commit_sha': commit_sha,
        'state': pr_row['state']
    }

    if use_sdk:
        review_text, model_used = generate_review_via_anthropic_sdk(
            prompt_content, diff, timeline, pr_data
        )
        generation_method = "anthropic_sdk"
    else:
        review_text, model_used = generate_review_via_claude_cli(
            prompt_content, diff, timeline, pr_data, verbose=verbose
        )
        generation_method = "claude_cli"

    # 8. Store review in database
    metadata = {
        'files_count': len(files),
        'diff_length': len(diff),
        'timeline_length': len(timeline),
        'include_timeline': include_timeline
    }

    review_id = database.store_pr_review(
        pr_id=pr_id,
        pr_number=pr_number,
        commit_sha=commit_sha,
        prompt_path=prompt_path,
        prompt_hash=prompt_hash,
        review_text=review_text,
        model_used=model_used,
        generation_method=generation_method,
        metadata=metadata
    )

    return {
        'review_id': review_id,
        'review_text': review_text,
        'model_used': model_used,
        'generation_method': generation_method,
        'prompt_path': prompt_path,
        'commit_sha': commit_sha,
        'cached': False
    }
