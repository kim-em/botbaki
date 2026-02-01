"""AI-powered PR review generation."""

from __future__ import annotations

import os
import re
import sys
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from . import database
from .github_client import GitHubClient
from .sync import parse_repo_full_name


# --- Pydantic models for structured output ---

class InlineComment(BaseModel):
    """A single inline comment on a specific line or line range."""
    path: str = Field(description="File path relative to repository root")
    line: int = Field(description="Line number in the NEW file where the comment ends")
    start_line: Optional[int] = Field(
        default=None,
        description="Start line for multi-line comments. If omitted, comment is single-line."
    )
    body: str = Field(
        description="Comment text. Use ```suggestion blocks for code suggestions."
    )


class PRReview(BaseModel):
    """Structured PR review with summary and inline comments."""
    summary: str = Field(
        description="Overall review summary with Issues and Suggestions sections."
    )
    inline_comments: List[InlineComment] = Field(
        default_factory=list,
        description="Specific inline comments on code lines. Only include if you have specific line feedback."
    )


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


def annotate_patch_with_line_numbers(patch: str) -> List[str]:
    """
    Annotate each line of a unified diff patch with line numbers.

    Returns lines in format:
    - "@@ -10,5 +15,7 @@" (hunk header, unchanged)
    - "  15:  | context line" (context, line 15 in new file)
    - "  16:+ | added line" (addition, line 16 in new file)
    - "    :- | deleted line" (deletion, no new file line)
    """
    result = []
    new_line_num = None

    for line in patch.split('\n'):
        if line.startswith('@@'):
            # Parse hunk header: @@ -old_start,old_count +new_start,new_count @@
            match = re.match(r'@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@', line)
            if match:
                new_line_num = int(match.group(1))
            result.append(line)
        elif line.startswith('+') and not line.startswith('+++'):
            # Added line
            result.append(f"{new_line_num:4d}:+ | {line[1:]}")
            new_line_num += 1
        elif line.startswith('-') and not line.startswith('---'):
            # Deleted line - no line number in new file
            result.append(f"    :- | {line[1:]}")
        elif line.startswith('\\'):
            # "\ No newline at end of file" - pass through
            result.append(line)
        else:
            # Context line (or file header like +++ or ---)
            if new_line_num is not None and not line.startswith('+++') and not line.startswith('---'):
                # Remove the leading space from context lines for consistency
                content = line[1:] if line.startswith(' ') else line
                result.append(f"{new_line_num:4d}:  | {content}")
                new_line_num += 1
            else:
                result.append(line)

    return result


def format_diff_with_line_numbers(files: List[Dict]) -> str:
    """
    Format GitHub files with line numbers for inline comment generation.

    Each line is prefixed with its line number in the new file (RIGHT side),
    or blank for deleted lines.
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

        if 'patch' not in file_info:
            output.append("(binary or no diff available)\n")
            continue

        # Parse the patch and add line numbers
        annotated_lines = annotate_patch_with_line_numbers(file_info['patch'])
        output.append("```diff")
        output.extend(annotated_lines)
        output.append("```\n")

    full_diff = "\n".join(output)

    # Truncate if too large
    MAX_DIFF_LENGTH = 200_000

    if len(full_diff) > MAX_DIFF_LENGTH:
        full_diff = full_diff[:MAX_DIFF_LENGTH]
        full_diff += f"\n\n... [TRUNCATED: diff too large, showing first {MAX_DIFF_LENGTH} chars] ...\n"

    return full_diff


def extract_new_file_lines(patch: str) -> set:
    """Extract the set of line numbers present in the new file from a patch."""
    lines = set()
    new_line_num = None

    for line in patch.split('\n'):
        if line.startswith('@@'):
            match = re.match(r'@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@', line)
            if match:
                new_line_num = int(match.group(1))
        elif line.startswith('+') and not line.startswith('+++'):
            if new_line_num is not None:
                lines.add(new_line_num)
                new_line_num += 1
        elif line.startswith('-') and not line.startswith('---'):
            pass  # Deleted line, no new line number
        elif line.startswith('\\'):
            pass  # "\ No newline at end of file"
        elif not line.startswith('+++') and not line.startswith('---'):
            # Context line
            if new_line_num is not None:
                lines.add(new_line_num)
                new_line_num += 1

    return lines


def validate_inline_comments(
    comments: List[InlineComment],
    files: List[Dict]
) -> Tuple[List[InlineComment], List[InlineComment]]:
    """
    Validate that inline comments reference valid lines in the diff.

    Returns:
        Tuple of (valid_comments, invalid_comments)
    """
    # Build a map of file -> set of valid new-file line numbers
    valid_lines = {}
    for file_info in files:
        filename = file_info['filename']
        if 'patch' not in file_info:
            valid_lines[filename] = set()
            continue

        lines = extract_new_file_lines(file_info['patch'])
        valid_lines[filename] = lines

    valid = []
    invalid = []

    for comment in comments:
        if comment.path not in valid_lines:
            invalid.append(comment)
            continue

        file_lines = valid_lines[comment.path]

        # Check line is valid
        if comment.line not in file_lines:
            invalid.append(comment)
            continue

        # Check start_line if present
        if comment.start_line and comment.start_line not in file_lines:
            invalid.append(comment)
            continue

        valid.append(comment)

    return (valid, invalid)


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


def generate_review(
    prompt_template: str,
    diff: str,
    timeline: str,
    pr_data: Dict,
    model_preference: str = "opus"
) -> Tuple[PRReview, str]:
    """
    Generate structured review using Anthropic Python SDK.

    Returns:
        Tuple of (PRReview object, model_used)

    Raises:
        ValueError: If ANTHROPIC_API_KEY is not set
    """
    import anthropic

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError(
            "ANTHROPIC_API_KEY environment variable is required. "
            "Set it to your Anthropic API key."
        )

    client = anthropic.Anthropic(api_key=api_key)

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
        current_date=pr_data['current_date'],
        diff=diff,
        timeline=timeline
    )

    response = client.messages.parse(
        model=model_id,
        max_tokens=16000,
        messages=[
            {"role": "user", "content": full_prompt}
        ],
        output_format=PRReview,
    )

    return (response.parsed_output, model_preference)


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
    Generate AI review for a PR using structured output.

    Args:
        full_name: Repository in format 'owner/repo'
        pr_number: PR number
        commit_sha: Specific commit to review (default: HEAD of PR)
        prompt_date: Use prompt from specific date YYYY-MM-DD (default: latest)
        include_timeline: Include botbaki show output (default: True)
        force: Regenerate even if review exists (default: False)
        verbose: Print progress

    Returns:
        Dict with review data including:
        - review_text: Summary text
        - inline_comments: List of validated inline comments (dicts)
        - model_used, generation_method, prompt_path, commit_sha, cached
    """
    import json

    owner, repo = parse_repo_full_name(full_name)

    # 1. Ensure PR is in database
    database.get_database()
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

    prompt_path = prompt_info['path']
    prompt_content = prompt_info['content']
    prompt_hash = prompt_info['hash']

    if verbose:
        print(f"\nUsing prompt: {prompt_path}", file=sys.stderr)
        print(f"Reviewing PR #{pr_number} at commit {commit_sha[:8]}", file=sys.stderr)

    # 3.5. Check if review already exists for this PR/commit/prompt combination
    if not force:
        existing_reviews = database.get_pr_reviews(pr_id)
        for existing in existing_reviews:
            # Parse metadata to check include_timeline flag
            existing_metadata = json.loads(existing['raw_metadata']) if existing['raw_metadata'] else {}
            existing_include_timeline = existing_metadata.get('include_timeline', True)

            if (existing['commit_sha'] == commit_sha and
                existing['prompt_hash'] == prompt_hash and
                existing_include_timeline == include_timeline):
                if verbose:
                    print(f"\nReview already exists (ID: {existing['id']})! Returning cached review.",
                          file=sys.stderr)
                    print(f"Use --force to regenerate.", file=sys.stderr)

                # Extract inline comments from metadata if present
                inline_comments = existing_metadata.get('inline_comments', [])

                return {
                    'review_id': existing['id'],
                    'review_text': existing['review_text'],
                    'inline_comments': inline_comments,
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

    # Always use line-annotated diff for structured output
    diff = format_diff_with_line_numbers(files)

    # 5. Get timeline if requested
    if include_timeline:
        if verbose:
            print("Fetching PR timeline...", file=sys.stderr)
        timeline_events = database.get_pr_timeline(pr_id)
        timeline = format_timeline_for_review(timeline_events)
    else:
        timeline = "(Timeline excluded per --diff-only flag)"

    if verbose:
        print("Generating review with Anthropic SDK (structured output)...", file=sys.stderr)

    # 6. Generate review
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
        'state': pr_row['state'],
        'current_date': datetime.now().strftime('%Y-%m-%d')
    }

    review_obj, model_used = generate_review(
        prompt_content, diff, timeline, pr_data
    )
    generation_method = "anthropic_sdk_structured"

    # 7. Validate inline comments
    valid_comments, invalid_comments = validate_inline_comments(
        review_obj.inline_comments, files
    )

    if invalid_comments and verbose:
        print(f"Warning: {len(invalid_comments)} inline comments had invalid line numbers",
              file=sys.stderr)

    # Convert invalid comments to summary addendum
    review_text = review_obj.summary
    if invalid_comments:
        addendum = "\n\n---\n**Additional notes (couldn't place inline):**\n"
        for c in invalid_comments:
            addendum += f"\n- **{c.path}**: {c.body}\n"
        review_text = review_text + addendum

    # Convert valid comments to dicts for storage and return
    inline_comments = [c.model_dump() for c in valid_comments]

    # 8. Store review in database
    metadata = {
        'files_count': len(files),
        'diff_length': len(diff),
        'timeline_length': len(timeline),
        'include_timeline': include_timeline,
        'inline_comments': inline_comments,
        'invalid_comments_count': len(invalid_comments)
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
        'inline_comments': inline_comments,
        'model_used': model_used,
        'generation_method': generation_method,
        'prompt_path': prompt_path,
        'commit_sha': commit_sha,
        'cached': False
    }
