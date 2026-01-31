"""GitHub API client using PyGithub with GitHub App authentication."""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from github import Github, Auth, GithubException
from github.GithubException import RateLimitExceededException

# Rate limiting configuration
DEFAULT_REQUEST_DELAY = 0.5  # 500ms between requests
MAX_RETRIES = 5
INITIAL_RETRY_DELAY = 1.0


class GitHubError(Exception):
    """Base exception for GitHub API errors."""
    pass


class NetworkError(GitHubError):
    """Network-related errors (timeouts, connection failures)."""
    pass


class RateLimitError(GitHubError):
    """Rate limit exceeded."""
    pass


def _load_github_app_credentials() -> tuple[int, str, int]:
    """
    Load GitHub App credentials from config directory.

    Returns:
        Tuple of (app_id, private_key, installation_id)

    Raises:
        GitHubError: If credentials are missing or invalid
    """
    config_dir = Path.home() / ".config" / "botbaki"

    try:
        app_id_file = config_dir / "github-app-id"
        private_key_file = config_dir / "github-app-key.pem"
        installation_id_file = config_dir / "github-installation-id"

        if not app_id_file.exists():
            raise GitHubError(f"Missing GitHub App ID file: {app_id_file}")
        if not private_key_file.exists():
            raise GitHubError(f"Missing GitHub App private key: {private_key_file}")
        if not installation_id_file.exists():
            raise GitHubError(f"Missing GitHub installation ID: {installation_id_file}")

        app_id = int(app_id_file.read_text().strip())
        private_key = private_key_file.read_text()
        installation_id = int(installation_id_file.read_text().strip())

        return (app_id, private_key, installation_id)

    except ValueError as e:
        raise GitHubError(f"Invalid credentials format: {e}")


class GitHubClient:
    """Client for interacting with GitHub API using GitHub App authentication."""

    def __init__(self, request_delay: float = DEFAULT_REQUEST_DELAY):
        """
        Initialize GitHub client with App authentication.

        Args:
            request_delay: Minimum seconds between requests (default 0.5s)
        """
        self._last_request_time: float = 0
        self.request_delay = request_delay

        # Load credentials and authenticate
        app_id, private_key, installation_id = _load_github_app_credentials()

        # Create GitHub App authentication
        app_auth = Auth.AppAuth(app_id, private_key)
        installation_auth = app_auth.get_installation_auth(installation_id)

        # Create authenticated GitHub client
        self._github = Github(auth=installation_auth)
        self._installation_id = installation_id

    def _rate_limit_delay(self) -> None:
        """Ensure minimum delay between requests."""
        elapsed = time.time() - self._last_request_time
        if elapsed < self.request_delay:
            time.sleep(self.request_delay - elapsed)
        self._last_request_time = time.time()

    def _handle_rate_limit(self, e: RateLimitExceededException, attempt: int) -> None:
        """Handle rate limit exception with exponential backoff."""
        if attempt >= MAX_RETRIES:
            raise RateLimitError(f"Rate limited after {MAX_RETRIES} retries")

        # Get reset time from the exception or rate limit API
        try:
            rate_limit = self._github.get_rate_limit()
            reset_time = rate_limit.core.reset.timestamp()
            wait_time = max(reset_time - time.time() + 1, INITIAL_RETRY_DELAY * (2 ** attempt))
        except Exception:
            wait_time = INITIAL_RETRY_DELAY * (2 ** attempt)

        sys.stderr.write(
            f"Rate limited, waiting {wait_time:.1f}s (attempt {attempt + 1}/{MAX_RETRIES})...\n"
        )
        sys.stderr.flush()
        time.sleep(wait_time)

    def get_rate_limit(self) -> Dict[str, Any]:
        """Get current rate limit status."""
        self._rate_limit_delay()
        rate_limit = self._github.get_rate_limit()
        return {
            'resources': {
                'core': {
                    'limit': rate_limit.core.limit,
                    'remaining': rate_limit.core.remaining,
                    'reset': int(rate_limit.core.reset.timestamp())
                }
            }
        }

    def get_pulls(
        self,
        owner: str,
        repo: str,
        state: str = 'all',
        sort: str = 'updated',
        direction: str = 'desc',
        per_page: int = 100,
        page: int = 1
    ) -> List[Dict[str, Any]]:
        """Get pull requests for a repository."""
        self._rate_limit_delay()

        for attempt in range(MAX_RETRIES):
            try:
                repository = self._github.get_repo(f"{owner}/{repo}")
                pulls = repository.get_pulls(state=state, sort=sort, direction=direction)

                # Manual pagination since PyGithub's pagination is different
                start = (page - 1) * per_page
                results = []
                for i, pr in enumerate(pulls):
                    if i < start:
                        continue
                    if i >= start + per_page:
                        break
                    results.append(self._pr_to_dict(pr))

                return results

            except RateLimitExceededException as e:
                self._handle_rate_limit(e, attempt)
            except GithubException as e:
                raise GitHubError(f"GitHub API error: {e}")

        raise GitHubError("Unexpected error in request retry loop")

    def get_pull_commits(
        self,
        owner: str,
        repo: str,
        pr_number: int,
        per_page: int = 100
    ) -> List[Dict[str, Any]]:
        """Get commits for a pull request."""
        self._rate_limit_delay()

        for attempt in range(MAX_RETRIES):
            try:
                repository = self._github.get_repo(f"{owner}/{repo}")
                pr = repository.get_pull(pr_number)
                commits = pr.get_commits()

                results = []
                for commit in commits:
                    results.append({
                        'sha': commit.sha,
                        'node_id': commit.raw_data.get('node_id', ''),
                        'commit': {
                            'message': commit.commit.message,
                            'author': {
                                'name': commit.commit.author.name if commit.commit.author else '',
                                'email': commit.commit.author.email if commit.commit.author else '',
                                'date': commit.commit.author.date.isoformat() if commit.commit.author and commit.commit.author.date else ''
                            },
                            'committer': {
                                'name': commit.commit.committer.name if commit.commit.committer else '',
                                'email': commit.commit.committer.email if commit.commit.committer else '',
                                'date': commit.commit.committer.date.isoformat() if commit.commit.committer and commit.commit.committer.date else ''
                            }
                        },
                        'author': {'login': commit.author.login} if commit.author else None,
                        'committer': {'login': commit.committer.login} if commit.committer else None,
                        'html_url': commit.html_url
                    })
                return results

            except RateLimitExceededException as e:
                self._handle_rate_limit(e, attempt)
            except GithubException as e:
                raise GitHubError(f"GitHub API error: {e}")

        raise GitHubError("Unexpected error in request retry loop")

    def get_pull_review_comments(
        self,
        owner: str,
        repo: str,
        pr_number: int,
        per_page: int = 100
    ) -> List[Dict[str, Any]]:
        """Get review comments (inline code comments) for a pull request."""
        self._rate_limit_delay()

        for attempt in range(MAX_RETRIES):
            try:
                repository = self._github.get_repo(f"{owner}/{repo}")
                pr = repository.get_pull(pr_number)
                comments = pr.get_review_comments()

                results = []
                for c in comments:
                    results.append({
                        'id': c.id,
                        'node_id': c.raw_data.get('node_id', ''),
                        'pull_request_review_id': c.raw_data.get('pull_request_review_id'),
                        'user': {'login': c.user.login, 'id': c.user.id},
                        'body': c.body,
                        'path': c.path,
                        'commit_id': c.commit_id,
                        'original_commit_id': c.original_commit_id,
                        'diff_hunk': c.diff_hunk,
                        'position': c.position,
                        'original_position': c.original_position,
                        'line': c.raw_data.get('line'),
                        'original_line': c.raw_data.get('original_line'),
                        'start_line': c.raw_data.get('start_line'),
                        'side': c.raw_data.get('side', 'RIGHT'),
                        'start_side': c.raw_data.get('start_side'),
                        'created_at': c.created_at.isoformat() if c.created_at else '',
                        'updated_at': c.updated_at.isoformat() if c.updated_at else '',
                        'html_url': c.html_url
                    })
                return results

            except RateLimitExceededException as e:
                self._handle_rate_limit(e, attempt)
            except GithubException as e:
                raise GitHubError(f"GitHub API error: {e}")

        raise GitHubError("Unexpected error in request retry loop")

    def get_issue_comments(
        self,
        owner: str,
        repo: str,
        pr_number: int,
        per_page: int = 100
    ) -> List[Dict[str, Any]]:
        """Get issue comments (general discussion) for a pull request."""
        self._rate_limit_delay()

        for attempt in range(MAX_RETRIES):
            try:
                repository = self._github.get_repo(f"{owner}/{repo}")
                issue = repository.get_issue(pr_number)
                comments = issue.get_comments()

                results = []
                for c in comments:
                    results.append({
                        'id': c.id,
                        'node_id': c.raw_data.get('node_id', ''),
                        'user': {'login': c.user.login, 'id': c.user.id},
                        'author_association': c.raw_data.get('author_association', 'NONE'),
                        'body': c.body,
                        'created_at': c.created_at.isoformat() if c.created_at else '',
                        'updated_at': c.updated_at.isoformat() if c.updated_at else '',
                        'html_url': c.html_url
                    })
                return results

            except RateLimitExceededException as e:
                self._handle_rate_limit(e, attempt)
            except GithubException as e:
                raise GitHubError(f"GitHub API error: {e}")

        raise GitHubError("Unexpected error in request retry loop")

    def get_pull_reviews(
        self,
        owner: str,
        repo: str,
        pr_number: int,
        per_page: int = 100
    ) -> List[Dict[str, Any]]:
        """Get reviews for a pull request."""
        self._rate_limit_delay()

        for attempt in range(MAX_RETRIES):
            try:
                repository = self._github.get_repo(f"{owner}/{repo}")
                pr = repository.get_pull(pr_number)
                reviews = pr.get_reviews()

                results = []
                for r in reviews:
                    results.append({
                        'id': r.id,
                        'node_id': r.raw_data.get('node_id', ''),
                        'user': {'login': r.user.login, 'id': r.user.id},
                        'author_association': r.raw_data.get('author_association', 'NONE'),
                        'body': r.body,
                        'state': r.state,
                        'commit_id': r.commit_id,
                        'submitted_at': r.submitted_at.isoformat() if r.submitted_at else '',
                        'html_url': r.html_url
                    })
                return results

            except RateLimitExceededException as e:
                self._handle_rate_limit(e, attempt)
            except GithubException as e:
                raise GitHubError(f"GitHub API error: {e}")

        raise GitHubError("Unexpected error in request retry loop")

    def get_pull_files(
        self,
        owner: str,
        repo: str,
        pr_number: int,
        per_page: int = 100
    ) -> List[Dict[str, Any]]:
        """Get files changed in a pull request with diffs."""
        self._rate_limit_delay()

        for attempt in range(MAX_RETRIES):
            try:
                repository = self._github.get_repo(f"{owner}/{repo}")
                pr = repository.get_pull(pr_number)
                files = pr.get_files()

                results = []
                for f in files:
                    results.append({
                        'filename': f.filename,
                        'status': f.status,
                        'additions': f.additions,
                        'deletions': f.deletions,
                        'changes': f.changes,
                        'patch': f.patch if f.patch else ''
                    })
                return results

            except RateLimitExceededException as e:
                self._handle_rate_limit(e, attempt)
            except GithubException as e:
                raise GitHubError(f"GitHub API error: {e}")

        raise GitHubError("Unexpected error in request retry loop")

    def get_repository(self, owner: str, repo: str) -> Dict[str, Any]:
        """Get repository information."""
        self._rate_limit_delay()

        for attempt in range(MAX_RETRIES):
            try:
                repository = self._github.get_repo(f"{owner}/{repo}")
                return {
                    'id': repository.id,
                    'name': repository.name,
                    'full_name': repository.full_name,
                    'default_branch': repository.default_branch,
                    'html_url': repository.html_url,
                    'owner': {
                        'login': repository.owner.login,
                        'id': repository.owner.id,
                        'type': repository.owner.type,
                        'html_url': repository.owner.html_url
                    }
                }

            except RateLimitExceededException as e:
                self._handle_rate_limit(e, attempt)
            except GithubException as e:
                raise GitHubError(f"GitHub API error: {e}")

        raise GitHubError("Unexpected error in request retry loop")

    def get_pull(self, owner: str, repo: str, pr_number: int) -> Dict[str, Any]:
        """Get a specific pull request with full details."""
        self._rate_limit_delay()

        for attempt in range(MAX_RETRIES):
            try:
                repository = self._github.get_repo(f"{owner}/{repo}")
                pr = repository.get_pull(pr_number)
                return self._pr_to_dict(pr)

            except RateLimitExceededException as e:
                self._handle_rate_limit(e, attempt)
            except GithubException as e:
                raise GitHubError(f"GitHub API error: {e}")

        raise GitHubError("Unexpected error in request retry loop")

    def _pr_to_dict(self, pr) -> Dict[str, Any]:
        """Convert PyGithub PullRequest to dict matching our schema."""
        return {
            'number': pr.number,
            'id': pr.id,
            'node_id': pr.raw_data.get('node_id', ''),
            'title': pr.title,
            'body': pr.body,
            'state': pr.state,
            'draft': pr.draft,
            'user': {'login': pr.user.login, 'id': pr.user.id},
            'created_at': pr.created_at.isoformat() if pr.created_at else '',
            'updated_at': pr.updated_at.isoformat() if pr.updated_at else '',
            'closed_at': pr.closed_at.isoformat() if pr.closed_at else None,
            'merged_at': pr.merged_at.isoformat() if pr.merged_at else None,
            'base': {'ref': pr.base.ref},
            'head': {'ref': pr.head.ref, 'sha': pr.head.sha},
            'comments': pr.comments,
            'review_comments': pr.review_comments,
            'commits': pr.commits,
            'html_url': pr.html_url
        }

    def print_rate_limit_status(self) -> None:
        """Print current rate limit status to stderr."""
        try:
            rate_info = self.get_rate_limit()
            core = rate_info['resources']['core']
            reset_time = time.strftime(
                '%Y-%m-%d %H:%M:%S',
                time.localtime(core['reset'])
            )
            sys.stderr.write(
                f"Rate limit: {core['remaining']}/{core['limit']} "
                f"(resets at {reset_time})\n"
            )
            sys.stderr.flush()
        except Exception as e:
            sys.stderr.write(f"Failed to get rate limit: {e}\n")
            sys.stderr.flush()

    def post_issue_comment(
        self,
        owner: str,
        repo: str,
        issue_number: int,
        body: str
    ) -> Dict[str, Any]:
        """Post a comment on an issue or PR."""
        self._rate_limit_delay()

        for attempt in range(MAX_RETRIES):
            try:
                repository = self._github.get_repo(f"{owner}/{repo}")
                issue = repository.get_issue(issue_number)
                comment = issue.create_comment(body)

                return {
                    'id': comment.id,
                    'node_id': comment.raw_data.get('node_id', ''),
                    'html_url': comment.html_url,
                    'user': {'login': comment.user.login, 'id': comment.user.id},
                    'body': comment.body,
                    'created_at': comment.created_at.isoformat() if comment.created_at else ''
                }

            except RateLimitExceededException as e:
                self._handle_rate_limit(e, attempt)
            except GithubException as e:
                raise GitHubError(f"Failed to post comment: {e}")

        raise GitHubError("Unexpected error in request retry loop")

    def create_pull_request_review(
        self,
        owner: str,
        repo: str,
        pr_number: int,
        body: str,
        commit_id: str,
        comments: Optional[List[Dict[str, Any]]] = None,
        event: str = "COMMENT"
    ) -> Dict[str, Any]:
        """Create a pull request review with optional inline comments.

        Uses the raw GitHub API to support multi-line comments with
        start_line/line and side parameters.
        """
        self._rate_limit_delay()

        for attempt in range(MAX_RETRIES):
            try:
                # Build the request payload for the raw API
                # PyGithub's create_review doesn't support all parameters
                # (start_line, side, start_side) so we use the requester directly
                payload: Dict[str, Any] = {
                    'body': body,
                    'event': event
                }

                if commit_id:
                    payload['commit_id'] = commit_id

                if comments:
                    review_comments = []
                    for c in comments:
                        comment_dict = {
                            'path': c['path'],
                            'body': c['body'],
                            'line': c['line'],
                            'side': 'RIGHT'
                        }
                        if c.get('start_line'):
                            comment_dict['start_line'] = c['start_line']
                            comment_dict['start_side'] = 'RIGHT'
                        review_comments.append(comment_dict)
                    payload['comments'] = review_comments

                # Use the raw API via PyGithub's requester
                headers, data = self._github._Github__requester.requestJsonAndCheck(
                    "POST",
                    f"/repos/{owner}/{repo}/pulls/{pr_number}/reviews",
                    input=payload
                )

                return {
                    'id': data.get('id'),
                    'node_id': data.get('node_id', ''),
                    'html_url': data.get('html_url', ''),
                    'user': data.get('user', {}),
                    'body': data.get('body', ''),
                    'state': data.get('state', ''),
                    'submitted_at': data.get('submitted_at', '')
                }

            except RateLimitExceededException as e:
                self._handle_rate_limit(e, attempt)
            except GithubException as e:
                raise GitHubError(f"Failed to create PR review: {e}")

        raise GitHubError("Unexpected error in request retry loop")
