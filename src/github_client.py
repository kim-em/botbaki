"""GitHub API client with rate limiting and retry logic."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional, Union

# Rate limiting configuration
# GitHub allows 5000 requests/hour for authenticated users
# Default to 0.9s delay = ~4000 req/hour, leaving 1000 spare for other use
DEFAULT_REQUEST_DELAY = 0.9  # 900ms between requests (~4000 req/hour)
MAX_RETRIES = 5
INITIAL_RETRY_DELAY = 1.0  # Start with 1 second on rate limit


class GitHubError(Exception):
    """Base exception for GitHub API errors."""
    pass


class NetworkError(GitHubError):
    """Network-related errors (timeouts, connection failures)."""
    pass


class RateLimitError(GitHubError):
    """Rate limit exceeded."""
    pass


class GitHubClient:
    """Client for interacting with the GitHub API via gh CLI."""

    def __init__(self, request_delay: float = DEFAULT_REQUEST_DELAY):
        """
        Initialize GitHub client.

        Args:
            request_delay: Minimum seconds between requests (default 0.9s = ~4000 req/hour)
        """
        self._last_request_time: float = 0
        self.request_delay = request_delay
        self._check_gh_installed()

    def _check_gh_installed(self) -> None:
        """Check if gh CLI is installed and authenticated."""
        try:
            result = subprocess.run(
                ['gh', 'auth', 'status'],
                capture_output=True,
                text=True,
                check=False
            )
            if result.returncode != 0:
                raise GitHubError(
                    "gh CLI not authenticated. Run 'gh auth login' first."
                )
        except FileNotFoundError:
            raise GitHubError(
                "gh CLI not found. Please install GitHub CLI from https://cli.github.com"
            )

    def request(
        self,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        paginate: bool = False
    ) -> Union[Dict[str, Any], List[Dict[str, Any]]]:
        """
        Make an API request with rate limiting and retry logic.

        Args:
            endpoint: API endpoint (e.g., "/repos/owner/repo/pulls")
            params: Query parameters
            paginate: Whether to automatically fetch all pages

        Returns:
            API response (dict or list if paginated)

        Raises:
            NetworkError: On network failures
            RateLimitError: If rate limited after retries
            GitHubError: On other API errors
        """
        # Ensure minimum delay between requests
        elapsed = time.time() - self._last_request_time
        if elapsed < self.request_delay:
            time.sleep(self.request_delay - elapsed)

        retry_delay = INITIAL_RETRY_DELAY

        for attempt in range(MAX_RETRIES + 1):
            try:
                self._last_request_time = time.time()

                # Build endpoint with query parameters
                full_endpoint = endpoint
                if params:
                    # Construct query string
                    query_parts = [f"{key}={value}" for key, value in params.items()]
                    separator = '&' if '?' in endpoint else '?'
                    full_endpoint = f"{endpoint}{separator}{'&'.join(query_parts)}"

                # Build gh command
                cmd = ['gh', 'api', full_endpoint]

                # Add pagination flag if requested
                if paginate:
                    cmd.append('--paginate')

                # Execute command
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=30
                )

                # Parse JSON response
                response_text = result.stdout.strip()
                if not response_text:
                    return [] if paginate else {}

                return json.loads(response_text)

            except subprocess.TimeoutExpired:
                if attempt < MAX_RETRIES:
                    sys.stderr.write(
                        f"Request timeout, retrying (attempt {attempt + 1}/{MAX_RETRIES})...\n"
                    )
                    sys.stderr.flush()
                    time.sleep(retry_delay)
                    retry_delay *= 2
                    continue
                else:
                    raise NetworkError(f"Request timeout after {MAX_RETRIES} retries")

            except subprocess.CalledProcessError as e:
                stderr = e.stderr.lower()

                # Check if rate limited
                if 'rate limit' in stderr or 'api rate limit exceeded' in stderr:
                    if attempt < MAX_RETRIES:
                        # Get rate limit info
                        try:
                            rate_info = self.get_rate_limit()
                            reset_time = rate_info['resources']['core']['reset']
                            remaining = rate_info['resources']['core']['remaining']

                            # If no requests remaining, wait until reset
                            if remaining == 0:
                                wait_time = max(reset_time - time.time() + 1, retry_delay)
                            else:
                                wait_time = retry_delay

                            sys.stderr.write(
                                f"Rate limited (remaining: {remaining}), "
                                f"waiting {wait_time:.1f}s "
                                f"(attempt {attempt + 1}/{MAX_RETRIES})...\n"
                            )
                            sys.stderr.flush()
                            time.sleep(wait_time)
                            retry_delay *= 2
                            continue
                        except Exception:
                            # Fallback if rate limit check fails
                            sys.stderr.write(
                                f"Rate limited, waiting {retry_delay:.1f}s "
                                f"(attempt {attempt + 1}/{MAX_RETRIES})...\n"
                            )
                            sys.stderr.flush()
                            time.sleep(retry_delay)
                            retry_delay *= 2
                            continue
                    else:
                        raise RateLimitError(
                            f"Rate limited after {MAX_RETRIES} retries"
                        )

                # Check for network errors
                elif 'connection' in stderr or 'network' in stderr:
                    if attempt < MAX_RETRIES:
                        sys.stderr.write(
                            f"Network error, retrying (attempt {attempt + 1}/{MAX_RETRIES})...\n"
                        )
                        sys.stderr.flush()
                        time.sleep(retry_delay)
                        retry_delay *= 2
                        continue
                    else:
                        raise NetworkError(f"Network error after {MAX_RETRIES} retries: {e.stderr}")

                # Other API error
                else:
                    raise GitHubError(f"GitHub API error: {e.stderr}")

            except json.JSONDecodeError as e:
                raise GitHubError(f"Invalid JSON response: {e}")

        raise GitHubError("Unexpected error in request retry loop")

    def get_rate_limit(self) -> Dict[str, Any]:
        """
        Get current rate limit status.

        Returns:
            Rate limit information with 'resources' dict
        """
        try:
            result = subprocess.run(
                ['gh', 'api', '/rate_limit'],
                capture_output=True,
                text=True,
                check=True,
                timeout=10
            )
            return json.loads(result.stdout)
        except Exception as e:
            raise GitHubError(f"Failed to get rate limit: {e}")

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
        """
        Get pull requests for a repository.

        Args:
            owner: Repository owner
            repo: Repository name
            state: PR state ('open', 'closed', 'all')
            sort: Sort by ('created', 'updated', 'popularity', 'long-running')
            direction: Sort direction ('asc', 'desc')
            per_page: Results per page (max 100)
            page: Page number

        Returns:
            List of PR objects
        """
        endpoint = f'/repos/{owner}/{repo}/pulls'
        params = {
            'state': state,
            'sort': sort,
            'direction': direction,
            'per_page': str(per_page),
            'page': str(page)
        }
        return self.request(endpoint, params)

    def get_pull_commits(
        self,
        owner: str,
        repo: str,
        pr_number: int,
        per_page: int = 100
    ) -> List[Dict[str, Any]]:
        """Get commits for a pull request."""
        endpoint = f'/repos/{owner}/{repo}/pulls/{pr_number}/commits'
        params = {'per_page': str(per_page)}
        return self.request(endpoint, params, paginate=True)

    def get_pull_review_comments(
        self,
        owner: str,
        repo: str,
        pr_number: int,
        per_page: int = 100
    ) -> List[Dict[str, Any]]:
        """Get review comments (inline code comments) for a pull request."""
        endpoint = f'/repos/{owner}/{repo}/pulls/{pr_number}/comments'
        params = {'per_page': str(per_page)}
        return self.request(endpoint, params, paginate=True)

    def get_issue_comments(
        self,
        owner: str,
        repo: str,
        pr_number: int,
        per_page: int = 100
    ) -> List[Dict[str, Any]]:
        """Get issue comments (general discussion) for a pull request."""
        endpoint = f'/repos/{owner}/{repo}/issues/{pr_number}/comments'
        params = {'per_page': str(per_page)}
        return self.request(endpoint, params, paginate=True)

    def get_pull_reviews(
        self,
        owner: str,
        repo: str,
        pr_number: int,
        per_page: int = 100
    ) -> List[Dict[str, Any]]:
        """Get reviews for a pull request."""
        endpoint = f'/repos/{owner}/{repo}/pulls/{pr_number}/reviews'
        params = {'per_page': str(per_page)}
        return self.request(endpoint, params, paginate=True)

    def get_pull_files(
        self,
        owner: str,
        repo: str,
        pr_number: int,
        per_page: int = 100
    ) -> List[Dict[str, Any]]:
        """
        Get files changed in a pull request with diffs.

        Returns list of file objects with:
        - filename, status, additions, deletions, changes, patch
        """
        endpoint = f'/repos/{owner}/{repo}/pulls/{pr_number}/files'
        params = {'per_page': str(per_page)}
        return self.request(endpoint, params, paginate=True)

    def get_repository(self, owner: str, repo: str) -> Dict[str, Any]:
        """Get repository information."""
        endpoint = f'/repos/{owner}/{repo}'
        return self.request(endpoint)

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
        """
        Post a comment on an issue or PR.

        Args:
            owner: Repository owner
            repo: Repository name
            issue_number: Issue/PR number
            body: Comment body (markdown)

        Returns:
            Created comment object with 'id', 'html_url', etc.
        """
        # Ensure minimum delay between requests
        elapsed = time.time() - self._last_request_time
        if elapsed < self.request_delay:
            time.sleep(self.request_delay - elapsed)

        self._last_request_time = time.time()

        endpoint = f'/repos/{owner}/{repo}/issues/{issue_number}/comments'

        # Use gh api with JSON input via stdin (avoids command-line length limits)
        cmd = [
            'gh', 'api', endpoint,
            '--method', 'POST',
            '--input', '-'  # Read JSON body from stdin
        ]

        # Prepare JSON payload
        import json as json_module
        payload = json_module.dumps({"body": body})

        try:
            result = subprocess.run(
                cmd,
                input=payload,
                capture_output=True,
                text=True,
                check=True,
                timeout=30
            )
            return json.loads(result.stdout)
        except subprocess.CalledProcessError as e:
            raise GitHubError(f"Failed to post comment: {e.stderr}")
        except json.JSONDecodeError as e:
            raise GitHubError(f"Invalid JSON response: {e}")

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
        """
        Create a pull request review with optional inline comments.

        Args:
            owner: Repository owner
            repo: Repository name
            pr_number: PR number
            body: Review body (summary)
            commit_id: SHA of commit being reviewed
            comments: List of inline comments, each with:
                - path: file path
                - line: line number in new file
                - start_line: (optional) for multi-line comments
                - body: comment text
            event: Review event type (COMMENT, APPROVE, REQUEST_CHANGES)

        Returns:
            Created review object
        """
        # Ensure minimum delay between requests
        elapsed = time.time() - self._last_request_time
        if elapsed < self.request_delay:
            time.sleep(self.request_delay - elapsed)

        self._last_request_time = time.time()

        endpoint = f'/repos/{owner}/{repo}/pulls/{pr_number}/reviews'

        payload = {
            "commit_id": commit_id,
            "body": body,
            "event": event,
        }

        if comments:
            # Convert to GitHub API format
            api_comments = []
            for c in comments:
                comment = {
                    "path": c["path"],
                    "body": c["body"],
                    "line": c["line"],
                    "side": "RIGHT",  # Always comment on new file
                }
                if c.get("start_line"):
                    comment["start_line"] = c["start_line"]
                    comment["start_side"] = "RIGHT"
                api_comments.append(comment)
            payload["comments"] = api_comments

        # Use gh api with JSON input via stdin
        cmd = [
            'gh', 'api', endpoint,
            '--method', 'POST',
            '--input', '-'
        ]

        import json as json_module
        payload_json = json_module.dumps(payload)

        try:
            result = subprocess.run(
                cmd,
                input=payload_json,
                capture_output=True,
                text=True,
                check=True,
                timeout=60  # Longer timeout for reviews with many comments
            )
            return json.loads(result.stdout)
        except subprocess.CalledProcessError as e:
            raise GitHubError(f"Failed to create PR review: {e.stderr}")
        except json.JSONDecodeError as e:
            raise GitHubError(f"Invalid JSON response: {e}")
