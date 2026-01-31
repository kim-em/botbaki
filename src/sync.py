"""Sync logic for GitHub PR data."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from . import database
from .github_client import GitHubClient


def parse_repo_full_name(full_name: str) -> Tuple[str, str]:
    """Parse 'owner/repo' into (owner, repo)."""
    parts = full_name.split('/', 1)
    if len(parts) != 2:
        raise ValueError(f"Invalid repository format: {full_name}. Expected 'owner/repo'")
    return parts[0], parts[1]


def full_sync(
    full_name: str,
    since: Optional[datetime] = None,
    request_delay: float = 0.9,
    force: bool = False,
    verbose: bool = True
) -> None:
    """
    Perform full sync of PRs since a given date.

    Args:
        full_name: Repository in format 'owner/repo'
        since: Sync PRs updated since this date (default: 2 months ago)
        request_delay: Delay between API requests in seconds (default: 0.9s = ~4000 req/hour)
        force: Force re-sync of all PRs even if they appear unchanged
        verbose: Print progress messages

    Raises:
        ValueError: If repository format is invalid
        GitHubError: On API errors
    """
    owner, repo = parse_repo_full_name(full_name)

    # Default to 2 months ago if not specified
    if since is None:
        since = datetime.now() - timedelta(days=60)

    since_str = since.isoformat()

    if verbose:
        print(f"Starting full sync for {full_name} (PRs updated since {since_str})")
        print(f"Rate limit: {request_delay:.2f}s between requests (~{3600/request_delay:.0f} req/hour)")

    client = GitHubClient(request_delay=request_delay)

    # Get or create repository in database
    repo_data = client.get_repository(owner, repo)
    org_id = database.get_or_create_organization(repo_data['owner'])
    repo_id = database.get_or_create_repository(org_id, repo_data)

    if verbose:
        print(f"Repository ID: {repo_id}")
        client.print_rate_limit_status()

    # Phase 1: Fetch all PR metadata
    if verbose:
        print("\n=== Phase 1: Fetching PR list ===")

    all_prs = []
    page = 1

    while True:
        if verbose:
            print(f"Fetching page {page}...", end=' ', flush=True)

        prs = client.get_pulls(
            owner, repo,
            state='all',
            sort='updated',
            direction='desc',
            per_page=100,
            page=page
        )

        if not prs:
            if verbose:
                print("(empty)")
            break

        # Filter to PRs updated since cutoff
        recent_prs = [pr for pr in prs if pr['updated_at'] >= since_str]

        # Check if any PRs on this page are new or updated (unless force=True)
        if force:
            # Force mode: sync all PRs regardless of whether they appear unchanged
            changed_prs = recent_prs
            if verbose:
                print(f"got {len(prs)} PRs, {len(recent_prs)} since cutoff (force mode)")
        else:
            changed_prs = []
            for pr in recent_prs:
                existing = database.get_pr_by_number(repo_id, pr['number'])
                if not existing or existing['updated_at'] != pr['updated_at']:
                    changed_prs.append(pr)

            if verbose:
                if changed_prs:
                    print(f"got {len(prs)} PRs, {len(recent_prs)} since cutoff, {len(changed_prs)} new/updated")
                else:
                    print(f"got {len(prs)} PRs, {len(recent_prs)} since cutoff, all unchanged")

        all_prs.extend(changed_prs)

        # Stop if we've gone past our date threshold
        if len(recent_prs) < len(prs):
            if verbose:
                print("Reached PRs older than cutoff date")
            break

        if len(prs) < 100:  # Last page
            break

        page += 1

    if verbose:
        print(f"\nFound {len(all_prs)} PRs to sync")

    # Phase 2: Store PRs and fetch sub-resources
    if verbose:
        print("\n=== Phase 2: Syncing PR details ===")

    # all_prs already contains only new/updated PRs from Phase 1
    for i, pr in enumerate(all_prs, 1):
        pr_number = pr['number']
        existing_pr = database.get_pr_by_number(repo_id, pr_number)

        if verbose:
            status = "(updated)" if existing_pr else "(new)"
            print(f"[{i}/{len(all_prs)}] PR #{pr_number}: {pr['title'][:60]}... {status}")

        # Store PR metadata
        pr_id = database.store_or_update_pr(repo_id, pr)

        # Fetch and store sub-resources (force_fetch=True to ignore unreliable GitHub counts)
        sync_pr_details(client, owner, repo, pr_id, pr_number, pr, verbose=False, force_fetch=True)

        # Update sync state
        database.update_pr_sync_state(
            pr_id,
            datetime.now().isoformat(),
            commits=pr.get('commits', 0),
            review_comments=pr.get('review_comments', 0),
            issue_comments=pr.get('comments', 0),
            reviews=0  # We don't have a count for reviews
        )

        # Print progress every 10 PRs
        if verbose and i % 10 == 0:
            client.print_rate_limit_status()

    # Update repository sync state
    database.update_repo_sync_state(
        repo_id,
        datetime.now().isoformat(),
        pr_count=len(all_prs)
    )

    if verbose:
        print(f"\n=== Sync complete: {len(all_prs)} PRs synced ===")
        client.print_rate_limit_status()


def sync_pr_details(
    client: GitHubClient,
    owner: str,
    repo: str,
    pr_id: int,
    pr_number: int,
    pr_data: Dict,
    verbose: bool = True,
    force_fetch: bool = False
) -> None:
    """
    Sync all details for a single PR.

    Args:
        client: GitHub API client
        owner: Repository owner
        repo: Repository name
        pr_id: Database PR ID
        pr_number: GitHub PR number
        pr_data: PR data dict (must have 'commits', 'review_comments', 'comments' counts)
        verbose: Print progress messages
        force_fetch: If True, fetch all resources regardless of counts
    """
    # Fetch commits
    if force_fetch or pr_data.get('commits', 0) > 0:
        if verbose:
            print(f"  Fetching {pr_data['commits']} commits...", end=' ', flush=True)

        try:
            commits = client.get_pull_commits(owner, repo, pr_number)
            for commit in commits:
                database.store_commit(pr_id, commit)
            if verbose:
                print(f"✓ {len(commits)}")
        except Exception as e:
            if verbose:
                print(f"✗ Error: {e}")
            sys.stderr.write(f"Error fetching commits for PR #{pr_number}: {e}\n")

    # Fetch review comments (inline code comments)
    if force_fetch or pr_data.get('review_comments', 0) > 0:
        if verbose:
            print(f"  Fetching {pr_data['review_comments']} review comments...", end=' ', flush=True)

        try:
            comments = client.get_pull_review_comments(owner, repo, pr_number)
            for comment in comments:
                database.store_review_comment(pr_id, comment)
            if verbose:
                print(f"✓ {len(comments)}")
        except Exception as e:
            if verbose:
                print(f"✗ Error: {e}")
            sys.stderr.write(f"Error fetching review comments for PR #{pr_number}: {e}\n")

    # Fetch issue comments (general discussion)
    if force_fetch or pr_data.get('comments', 0) > 0:
        if verbose:
            print(f"  Fetching {pr_data['comments']} issue comments...", end=' ', flush=True)

        try:
            comments = client.get_issue_comments(owner, repo, pr_number)
            for comment in comments:
                database.store_issue_comment(pr_id, comment)
            if verbose:
                print(f"✓ {len(comments)}")
        except Exception as e:
            if verbose:
                print(f"✗ Error: {e}")
            sys.stderr.write(f"Error fetching issue comments for PR #{pr_number}: {e}\n")

    # Fetch reviews (always fetch unless PR is brand new draft, or if force_fetch)
    if force_fetch or pr_data['state'] != 'draft' or pr_data.get('comments', 0) > 0:
        if verbose:
            print("  Fetching reviews...", end=' ', flush=True)

        try:
            reviews = client.get_pull_reviews(owner, repo, pr_number)
            for review in reviews:
                database.store_review(pr_id, review)
            if verbose:
                print(f"✓ {len(reviews)}")
        except Exception as e:
            if verbose:
                print(f"✗ Error: {e}")
            sys.stderr.write(f"Error fetching reviews for PR #{pr_number}: {e}\n")


def incremental_sync(full_name: str, request_delay: float = 0.9, verbose: bool = True) -> None:
    """
    Perform incremental sync - update only changed PRs.

    Args:
        full_name: Repository in format 'owner/repo'
        request_delay: Delay between API requests in seconds (default: 0.9s = ~4000 req/hour)
        verbose: Print progress messages

    Raises:
        ValueError: If repository format is invalid or not found in DB
        GitHubError: On API errors
    """
    owner, repo = parse_repo_full_name(full_name)

    if verbose:
        print(f"Starting incremental sync for {full_name}")
        print(f"Rate limit: {request_delay:.2f}s between requests (~{3600/request_delay:.0f} req/hour)")

    # Get repository from database
    repo_row = database.get_repository_by_full_name(full_name)
    if not repo_row:
        raise ValueError(
            f"Repository {full_name} not found in database. "
            "Run full sync first."
        )

    repo_id = repo_row['id']

    # Get last sync time
    last_sync = database.get_last_sync_time(repo_id)
    if not last_sync:
        raise ValueError(
            f"No sync history for {full_name}. Run full sync first."
        )

    if verbose:
        print(f"Last sync: {last_sync}")

    client = GitHubClient(request_delay=request_delay)

    if verbose:
        client.print_rate_limit_status()

    # Fetch PRs updated since last sync
    if verbose:
        print("\n=== Fetching changed PRs ===")

    changed_prs = []
    page = 1

    while True:
        if verbose:
            print(f"Fetching page {page}...", end=' ', flush=True)

        prs = client.get_pulls(
            owner, repo,
            state='all',
            sort='updated',
            direction='desc',
            per_page=100,
            page=page
        )

        if not prs:
            if verbose:
                print("(empty)")
            break

        # Filter to PRs updated since last sync
        recent_prs = [pr for pr in prs if pr['updated_at'] > last_sync]

        if verbose:
            print(f"got {len(prs)} PRs, {len(recent_prs)} changed since last sync")

        changed_prs.extend(recent_prs)

        # Stop if we've gone past our last sync time
        if len(recent_prs) < len(prs):
            if verbose:
                print("Reached PRs older than last sync")
            break

        if len(prs) < 100:
            break

        page += 1

    if verbose:
        print(f"\nFound {len(changed_prs)} changed PRs")

    if not changed_prs:
        if verbose:
            print("No changes to sync")
        return

    # Update each changed PR
    if verbose:
        print("\n=== Syncing changed PRs ===")

    for i, pr in enumerate(changed_prs, 1):
        pr_number = pr['number']

        if verbose:
            print(f"[{i}/{len(changed_prs)}] PR #{pr_number}: {pr['title'][:60]}...")

        # Get existing PR from database
        existing_pr = database.get_pr_by_number(repo_id, pr_number)

        # Store updated PR metadata
        pr_id = database.store_or_update_pr(repo_id, pr)

        # Determine what needs updating
        needs_update = False

        if not existing_pr:
            # New PR - fetch everything
            needs_update = True
            if verbose:
                print("  (New PR)")
        else:
            # PR was found because updated_at changed, so always fetch details
            # (GitHub's comment counts in PR list can be stale)
            needs_update = True
            if verbose:
                print("  (Updated)")

        if needs_update:
            # Always force_fetch since PR listing API doesn't include comment counts
            sync_pr_details(client, owner, repo, pr_id, pr_number, pr, verbose=verbose, force_fetch=True)

            # Update sync state
            database.update_pr_sync_state(
                pr_id,
                datetime.now().isoformat(),
                commits=pr.get('commits', 0),
                review_comments=pr.get('review_comments', 0),
                issue_comments=pr.get('comments', 0),
                reviews=0
            )
        else:
            if verbose:
                print("  (No changes)")

        # Print rate limit every 10 PRs
        if verbose and i % 10 == 0:
            client.print_rate_limit_status()

    # Update repository sync state
    database.update_repo_sync_state(
        repo_id,
        datetime.now().isoformat()
    )

    if verbose:
        print(f"\n=== Sync complete: {len(changed_prs)} PRs updated ===")
        client.print_rate_limit_status()


def sync_single_pr(full_name: str, pr_number: int, request_delay: float = 0.9, verbose: bool = True) -> None:
    """
    Sync a single PR by number.

    Args:
        full_name: Repository in format 'owner/repo'
        pr_number: PR number to sync
        request_delay: Delay between API requests in seconds (default: 0.9s = ~4000 req/hour)
        verbose: Print progress messages
    """
    owner, repo = parse_repo_full_name(full_name)

    if verbose:
        print(f"Syncing PR #{pr_number} from {full_name}")
        print(f"Rate limit: {request_delay:.2f}s between requests (~{3600/request_delay:.0f} req/hour)")

    client = GitHubClient(request_delay=request_delay)

    # Get repository
    repo_data = client.get_repository(owner, repo)
    org_id = database.get_or_create_organization(repo_data['owner'])
    repo_id = database.get_or_create_repository(org_id, repo_data)

    # Get PR data
    prs = client.get_pulls(owner, repo, state='all', per_page=1)
    if not prs:
        raise ValueError(f"PR #{pr_number} not found")

    # Find the specific PR (gh API doesn't support getting by number directly for pulls)
    # We need to use a different approach - fetch from issues endpoint
    pr_endpoint = f'/repos/{owner}/{repo}/pulls/{pr_number}'
    pr_data = client.request(pr_endpoint)

    # Store PR
    pr_id = database.store_or_update_pr(repo_id, pr_data)

    # Sync details
    if verbose:
        print(f"PR #{pr_number}: {pr_data['title']}")

    # Always fetch all resources for single PR sync
    sync_pr_details(client, owner, repo, pr_id, pr_number, pr_data, verbose=verbose, force_fetch=True)

    # Update sync state
    database.update_pr_sync_state(
        pr_id,
        datetime.now().isoformat(),
        commits=pr_data.get('commits', 0),
        review_comments=pr_data.get('review_comments', 0),
        issue_comments=pr_data.get('comments', 0),
        reviews=0
    )

    if verbose:
        print("\n✓ Sync complete")
        client.print_rate_limit_status()
