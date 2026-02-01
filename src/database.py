"""SQLite database operations for GitHub PR tracking.

This module provides the database layer for botbaki, handling:
- PR metadata storage and retrieval
- Comment and review storage
- Sync state tracking
- Full-text search across comments
- AI review caching
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, TypedDict


# TypedDicts for structured returns

class TimelineEvent(TypedDict):
    """A single event in a PR timeline."""
    timestamp: str
    type: str  # 'commit', 'review_comment', 'issue_comment', 'review'
    actor: str
    content: str
    url: str
    path: Optional[str]
    line: Optional[int]
    diff_hunk: Optional[str]


class SearchResult(TypedDict):
    """A search result from full-text search."""
    pr_number: int
    pr_title: str
    source: str  # 'review_comment' or 'issue_comment'
    body: str
    author_login: str
    created_at: str
    html_url: str


class PromptInfo(TypedDict):
    """Information about a prompt file."""
    path: str
    content: str
    hash: str

# Database location
DATA_DIR = Path(__file__).parent.parent / "data"
DB_PATH = DATA_DIR / "github_prs.db"

# Load schema from schema.sql
SCHEMA_FILE = Path(__file__).parent.parent / "schema.sql"
with open(SCHEMA_FILE, 'r') as f:
    SCHEMA = f.read()


class Database:
    """Database connection manager with lazy initialization.

    Supports both singleton usage (for CLI) and explicit connection management
    (for future web/concurrent usage).

    Usage:
        # Singleton (backwards compatible)
        db = get_database()

        # Explicit management
        database = Database(custom_path)
        conn = database.connect()
        database.close()

        # Context manager (future)
        with Database() as conn:
            conn.execute(...)
    """

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None

    def connect(self) -> sqlite3.Connection:
        """Get or create database connection."""
        if self._conn is not None:
            return self._conn

        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)

        return self._conn

    def close(self) -> None:
        """Close database connection."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> sqlite3.Connection:
        return self.connect()

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()


# Global singleton for backwards compatibility
_default_db = Database()


def get_database() -> sqlite3.Connection:
    """Get the default database connection (backwards compatible)."""
    return _default_db.connect()


def close_database() -> None:
    """Close the default database connection."""
    _default_db.close()


# Keep old global for any direct access (deprecated)
_db: Optional[sqlite3.Connection] = None


def _legacy_close_database() -> None:
    """Legacy close function - deprecated, use close_database() instead."""
    global _db
    if _db is not None:
        _db.close()
        _db = None


# Organization operations

def get_or_create_organization(org_data: Dict[str, Any]) -> int:
    """Get or create organization, return database ID."""
    db = get_database()
    cursor = db.execute(
        "SELECT id FROM organizations WHERE org_login = ?",
        (org_data['login'],)
    )
    row = cursor.fetchone()
    if row:
        return row['id']

    cursor = db.execute("""
        INSERT INTO organizations (org_login, org_id, org_type, html_url)
        VALUES (?, ?, ?, ?)
    """, (
        org_data['login'],
        org_data['id'],
        org_data.get('type', 'Organization'),
        org_data['html_url']
    ))
    db.commit()
    return cursor.lastrowid


# Repository operations

def get_or_create_repository(org_id: int, repo_data: Dict[str, Any]) -> int:
    """Get or create repository, return database ID."""
    db = get_database()
    cursor = db.execute(
        "SELECT id FROM repositories WHERE org_id = ? AND repo_name = ?",
        (org_id, repo_data['name'])
    )
    row = cursor.fetchone()
    if row:
        return row['id']

    cursor = db.execute("""
        INSERT INTO repositories
        (org_id, repo_name, repo_id, full_name, default_branch, html_url)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        org_id,
        repo_data['name'],
        repo_data['id'],
        repo_data['full_name'],
        repo_data.get('default_branch', 'master'),
        repo_data['html_url']
    ))
    db.commit()
    return cursor.lastrowid


def get_repository_by_full_name(full_name: str) -> Optional[sqlite3.Row]:
    """Get repository by full_name (e.g., 'leanprover-community/mathlib4')."""
    db = get_database()
    cursor = db.execute(
        "SELECT * FROM repositories WHERE full_name = ?",
        (full_name,)
    )
    return cursor.fetchone()


# Pull request operations

def store_or_update_pr(repo_id: int, pr_data: Dict[str, Any]) -> int:
    """Store or update pull request, return database ID."""
    db = get_database()

    # Check if PR exists
    cursor = db.execute(
        "SELECT id FROM pull_requests WHERE repo_id = ? AND pr_number = ?",
        (repo_id, pr_data['number'])
    )
    row = cursor.fetchone()

    pr_values = (
        repo_id,
        pr_data['number'],
        pr_data['id'],
        pr_data['node_id'],
        pr_data['title'],
        pr_data.get('body'),
        pr_data['state'],
        1 if pr_data.get('draft', False) else 0,
        pr_data['user']['login'],
        pr_data['user']['id'],
        pr_data['created_at'],
        pr_data['updated_at'],
        pr_data.get('closed_at'),
        pr_data.get('merged_at'),
        pr_data['base']['ref'],
        pr_data['head']['ref'],
        pr_data['head']['sha'],
        pr_data.get('comments', 0),
        pr_data.get('review_comments', 0),
        pr_data.get('commits', 0),
        datetime.now().isoformat(),
        json.dumps(pr_data)
    )

    if row:
        # Update existing PR
        pr_id = row['id']
        db.execute("""
            UPDATE pull_requests SET
                pr_id = ?, node_id = ?, title = ?, body = ?, state = ?,
                is_draft = ?, author_login = ?, author_id = ?,
                created_at = ?, updated_at = ?, closed_at = ?, merged_at = ?,
                base_ref = ?, head_ref = ?, head_sha = ?,
                comments_count = ?, review_comments_count = ?, commits_count = ?,
                last_sync = ?, raw_json = ?
            WHERE id = ?
        """, pr_values[2:] + (pr_id,))
    else:
        # Insert new PR
        cursor = db.execute("""
            INSERT INTO pull_requests
            (repo_id, pr_number, pr_id, node_id, title, body, state, is_draft,
             author_login, author_id, created_at, updated_at, closed_at, merged_at,
             base_ref, head_ref, head_sha, comments_count, review_comments_count,
             commits_count, last_sync, raw_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, pr_values)
        pr_id = cursor.lastrowid

    db.commit()
    return pr_id


def get_pr_by_number(repo_id: int, pr_number: int) -> Optional[sqlite3.Row]:
    """Get PR by repository ID and PR number."""
    db = get_database()
    cursor = db.execute(
        "SELECT * FROM pull_requests WHERE repo_id = ? AND pr_number = ?",
        (repo_id, pr_number)
    )
    return cursor.fetchone()


def get_pr_by_id(pr_id: int) -> Optional[sqlite3.Row]:
    """Get PR by its database ID."""
    db = get_database()
    cursor = db.execute(
        "SELECT * FROM pull_requests WHERE id = ?",
        (pr_id,)
    )
    return cursor.fetchone()


# Commit operations

def store_commit(pr_id: int, commit_data: Dict[str, Any]) -> None:
    """Store commit (idempotent)."""
    db = get_database()

    commit = commit_data.get('commit', commit_data)
    author = commit.get('author', {})
    committer = commit.get('committer', {})

    # Split message into headline and body
    message = commit.get('message', '')
    lines = message.split('\n', 1)
    headline = lines[0]
    body = lines[1].strip() if len(lines) > 1 else None

    db.execute("""
        INSERT OR IGNORE INTO commits
        (pr_id, sha, node_id, author_login, author_email, author_name,
         committer_login, committer_email, committer_name,
         message_headline, message_body, authored_date, committed_date,
         html_url, raw_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        pr_id,
        commit_data['sha'],
        commit_data.get('node_id', ''),
        commit_data.get('author', {}).get('login'),
        author.get('email', ''),
        author.get('name', ''),
        commit_data.get('committer', {}).get('login'),
        committer.get('email', ''),
        committer.get('name', ''),
        headline,
        body,
        author.get('date', ''),
        committer.get('date', ''),
        commit_data.get('html_url', ''),
        json.dumps(commit_data)
    ))
    db.commit()


# Review comment operations

def store_review_comment(pr_id: int, comment_data: Dict[str, Any]) -> None:
    """Store review comment (idempotent)."""
    db = get_database()

    db.execute("""
        INSERT OR IGNORE INTO review_comments
        (pr_id, comment_id, node_id, review_id, author_login, author_id,
         body, path, commit_id, original_commit_id, diff_hunk,
         position, original_position, line, original_line, start_line,
         side, start_side, created_at, updated_at, html_url, raw_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        pr_id,
        comment_data['id'],
        comment_data['node_id'],
        comment_data.get('pull_request_review_id'),
        comment_data['user']['login'],
        comment_data['user']['id'],
        comment_data['body'],
        comment_data['path'],
        comment_data['commit_id'],
        comment_data['original_commit_id'],
        comment_data.get('diff_hunk'),
        comment_data.get('position'),
        comment_data.get('original_position'),
        comment_data.get('line'),
        comment_data.get('original_line'),
        comment_data.get('start_line'),
        comment_data.get('side', 'RIGHT'),
        comment_data.get('start_side'),
        comment_data['created_at'],
        comment_data['updated_at'],
        comment_data['html_url'],
        json.dumps(comment_data)
    ))
    db.commit()


# Issue comment operations

def store_issue_comment(pr_id: int, comment_data: Dict[str, Any]) -> None:
    """Store issue comment (idempotent)."""
    db = get_database()

    db.execute("""
        INSERT OR IGNORE INTO issue_comments
        (pr_id, comment_id, node_id, author_login, author_id,
         author_association, body, created_at, updated_at, html_url, raw_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        pr_id,
        comment_data['id'],
        comment_data['node_id'],
        comment_data['user']['login'],
        comment_data['user']['id'],
        comment_data.get('author_association', 'NONE'),
        comment_data['body'],
        comment_data['created_at'],
        comment_data['updated_at'],
        comment_data['html_url'],
        json.dumps(comment_data)
    ))
    db.commit()


# Review operations

def store_review(pr_id: int, review_data: Dict[str, Any]) -> None:
    """Store review (idempotent)."""
    db = get_database()

    db.execute("""
        INSERT OR IGNORE INTO reviews
        (pr_id, review_id, node_id, author_login, author_id,
         author_association, body, state, commit_id, submitted_at,
         html_url, raw_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        pr_id,
        review_data['id'],
        review_data['node_id'],
        review_data['user']['login'],
        review_data['user']['id'],
        review_data.get('author_association', 'NONE'),
        review_data.get('body'),
        review_data['state'],
        review_data['commit_id'],
        review_data['submitted_at'],
        review_data['html_url'],
        json.dumps(review_data)
    ))
    db.commit()


# Sync state operations

def get_last_sync_time(repo_id: int) -> Optional[str]:
    """Get last sync timestamp for repository."""
    db = get_database()
    cursor = db.execute(
        "SELECT last_synced_at FROM repo_sync_state WHERE repo_id = ?",
        (repo_id,)
    )
    row = cursor.fetchone()
    return row['last_synced_at'] if row else None


def update_repo_sync_state(repo_id: int, timestamp: str, pr_count: int = None) -> None:
    """Update repository sync state."""
    db = get_database()

    cursor = db.execute(
        "SELECT id FROM repo_sync_state WHERE repo_id = ?",
        (repo_id,)
    )
    row = cursor.fetchone()

    if row:
        if pr_count is not None:
            db.execute("""
                UPDATE repo_sync_state
                SET last_synced_at = ?, total_prs_synced = ?
                WHERE repo_id = ?
            """, (timestamp, pr_count, repo_id))
        else:
            db.execute("""
                UPDATE repo_sync_state
                SET last_synced_at = ?
                WHERE repo_id = ?
            """, (timestamp, repo_id))
    else:
        db.execute("""
            INSERT INTO repo_sync_state
            (repo_id, last_synced_at, total_prs_synced)
            VALUES (?, ?, ?)
        """, (repo_id, timestamp, pr_count or 0))

    db.commit()


def update_pr_sync_state(pr_id: int, timestamp: str,
                         commits: int = 0, review_comments: int = 0,
                         issue_comments: int = 0, reviews: int = 0) -> None:
    """Update PR sync state."""
    db = get_database()

    cursor = db.execute(
        "SELECT id FROM pr_sync_state WHERE pr_id = ?",
        (pr_id,)
    )
    row = cursor.fetchone()

    if row:
        db.execute("""
            UPDATE pr_sync_state
            SET last_synced_at = ?, commits_synced = ?,
                review_comments_synced = ?, issue_comments_synced = ?,
                reviews_synced = ?
            WHERE pr_id = ?
        """, (timestamp, commits, review_comments, issue_comments,
              reviews, pr_id))
    else:
        db.execute("""
            INSERT INTO pr_sync_state
            (pr_id, last_synced_at, commits_synced, review_comments_synced,
             issue_comments_synced, reviews_synced)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (pr_id, timestamp, commits, review_comments, issue_comments,
              reviews))

    db.commit()


# Query operations

def get_pr_timeline(pr_id: int) -> List[TimelineEvent]:
    """Get all activity for a PR in chronological order."""
    db = get_database()

    # Union of all activity types
    cursor = db.execute("""
        SELECT
          committed_date as timestamp,
          'commit' as type,
          author_login as actor,
          message_headline as content,
          html_url as url,
          NULL as path,
          NULL as line,
          NULL as diff_hunk
        FROM commits WHERE pr_id = ?
        UNION ALL
        SELECT
          created_at,
          'review_comment',
          author_login,
          body,
          html_url,
          path,
          COALESCE(line, original_line) as line,
          diff_hunk
        FROM review_comments WHERE pr_id = ?
        UNION ALL
        SELECT
          created_at,
          'issue_comment',
          author_login,
          body,
          html_url,
          NULL as path,
          NULL as line,
          NULL as diff_hunk
        FROM issue_comments WHERE pr_id = ?
        UNION ALL
        SELECT
          submitted_at,
          'review',
          author_login,
          state || COALESCE(': ' || body, ''),
          html_url,
          NULL as path,
          NULL as line,
          NULL as diff_hunk
        FROM reviews WHERE pr_id = ?
        ORDER BY timestamp ASC
    """, (pr_id, pr_id, pr_id, pr_id))

    return [dict(row) for row in cursor.fetchall()]


def search_comments(query: str, limit: int = 50) -> List[SearchResult]:
    """Full-text search across all comments."""
    db = get_database()

    cursor = db.execute("""
        SELECT
          pr.pr_number,
          pr.title as pr_title,
          'review_comment' as source,
          rc.body,
          rc.author_login,
          rc.created_at as created_at,
          rc.html_url
        FROM review_comments_fts rcf
        JOIN review_comments rc ON rc.id = rcf.rowid
        JOIN pull_requests pr ON pr.id = rc.pr_id
        WHERE review_comments_fts MATCH ?
        UNION ALL
        SELECT
          pr.pr_number,
          pr.title as pr_title,
          'issue_comment' as source,
          ic.body,
          ic.author_login,
          ic.created_at as created_at,
          ic.html_url
        FROM issue_comments_fts icf
        JOIN issue_comments ic ON ic.id = icf.rowid
        JOIN pull_requests pr ON pr.id = ic.pr_id
        WHERE issue_comments_fts MATCH ?
        ORDER BY created_at DESC
        LIMIT ?
    """, (query, query, limit))

    return [dict(row) for row in cursor.fetchall()]


# PR Review operations

def store_pr_review(
    pr_id: int,
    pr_number: int,
    commit_sha: str,
    prompt_path: str,
    prompt_hash: str,
    review_text: str,
    model_used: str,
    generation_method: str,
    metadata: Optional[Dict[str, Any]] = None
) -> int:
    """Store PR review (idempotent via UNIQUE constraint)."""
    db = get_database()
    cursor = db.execute("""
        INSERT OR IGNORE INTO pr_reviews
        (pr_id, pr_number, commit_sha, reviewed_at, prompt_path,
         prompt_hash, review_text, model_used, generation_method, raw_metadata)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        pr_id, pr_number, commit_sha, datetime.now().isoformat(),
        prompt_path, prompt_hash, review_text, model_used,
        generation_method, json.dumps(metadata) if metadata else None
    ))
    db.commit()
    return cursor.lastrowid


def get_pr_reviews(pr_id: int) -> List[Dict[str, Any]]:
    """Get all reviews for a PR, ordered by newest first."""
    db = get_database()
    cursor = db.execute("""
        SELECT * FROM pr_reviews
        WHERE pr_id = ?
        ORDER BY reviewed_at DESC
    """, (pr_id,))
    return [dict(row) for row in cursor.fetchall()]


def get_latest_prompt_for_repo(repo_name: str) -> Optional[PromptInfo]:
    """
    Find most recent prompt file for a repository.
    Returns PromptInfo dict with path, content, and hash, or None.
    """
    import hashlib
    prompts_dir = Path(__file__).parent.parent / "prompts" / repo_name
    if not prompts_dir.exists():
        return None

    # YYYY-MM-DD format sorts chronologically
    prompt_files = sorted(prompts_dir.glob("*.md"), reverse=True)
    if not prompt_files:
        return None

    latest = prompt_files[0]
    content = latest.read_text()
    content_hash = hashlib.sha256(content.encode()).hexdigest()[:16]

    return PromptInfo(
        path=str(latest.relative_to(Path(__file__).parent.parent)),
        content=content,
        hash=content_hash
    )


def get_prompt_by_date(repo_name: str, date: str) -> Optional[PromptInfo]:
    """Get prompt for specific date (YYYY-MM-DD format)."""
    import hashlib
    prompt_file = Path(__file__).parent.parent / "prompts" / repo_name / f"{date}.md"
    if not prompt_file.exists():
        return None

    content = prompt_file.read_text()
    content_hash = hashlib.sha256(content.encode()).hexdigest()[:16]

    return PromptInfo(
        path=str(prompt_file.relative_to(Path(__file__).parent.parent)),
        content=content,
        hash=content_hash
    )
