-- GitHub PR Tracking Database Schema
-- SQLite database for tracking PRs, comments, commits, and reviews

-- Organizations (e.g., leanprover-community, leanprover)
CREATE TABLE IF NOT EXISTS organizations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  org_login TEXT UNIQUE NOT NULL,
  org_id INTEGER NOT NULL,
  org_type TEXT NOT NULL,
  html_url TEXT NOT NULL,
  last_sync TEXT
);

-- Repositories within organizations
CREATE TABLE IF NOT EXISTS repositories (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  org_id INTEGER NOT NULL REFERENCES organizations(id),
  repo_name TEXT NOT NULL,
  repo_id INTEGER NOT NULL,
  full_name TEXT NOT NULL,
  default_branch TEXT NOT NULL,
  html_url TEXT NOT NULL,
  last_sync TEXT,
  UNIQUE(org_id, repo_name)
);

-- Pull requests
CREATE TABLE IF NOT EXISTS pull_requests (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  repo_id INTEGER NOT NULL REFERENCES repositories(id),
  pr_number INTEGER NOT NULL,
  pr_id INTEGER NOT NULL,
  node_id TEXT NOT NULL,
  title TEXT NOT NULL,
  body TEXT,
  state TEXT NOT NULL,
  is_draft INTEGER NOT NULL,
  author_login TEXT NOT NULL,
  author_id INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  closed_at TEXT,
  merged_at TEXT,
  base_ref TEXT NOT NULL,
  head_ref TEXT NOT NULL,
  head_sha TEXT,
  comments_count INTEGER NOT NULL DEFAULT 0,
  review_comments_count INTEGER NOT NULL DEFAULT 0,
  commits_count INTEGER NOT NULL DEFAULT 0,
  last_sync TEXT NOT NULL,
  raw_json TEXT,
  UNIQUE(repo_id, pr_number)
);

-- PR labels (many-to-many)
CREATE TABLE IF NOT EXISTS pr_labels (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  pr_id INTEGER NOT NULL REFERENCES pull_requests(id),
  label_name TEXT NOT NULL,
  label_color TEXT NOT NULL,
  label_description TEXT,
  UNIQUE(pr_id, label_name)
);

-- Commits on PR branches
CREATE TABLE IF NOT EXISTS commits (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  pr_id INTEGER NOT NULL REFERENCES pull_requests(id),
  sha TEXT NOT NULL,
  node_id TEXT NOT NULL,
  author_login TEXT,
  author_email TEXT NOT NULL,
  author_name TEXT NOT NULL,
  committer_login TEXT,
  committer_email TEXT NOT NULL,
  committer_name TEXT NOT NULL,
  message_headline TEXT NOT NULL,
  message_body TEXT,
  authored_date TEXT NOT NULL,
  committed_date TEXT NOT NULL,
  html_url TEXT NOT NULL,
  raw_json TEXT,
  UNIQUE(pr_id, sha)
);

-- Review comments (inline code comments)
CREATE TABLE IF NOT EXISTS review_comments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  pr_id INTEGER NOT NULL REFERENCES pull_requests(id),
  comment_id INTEGER NOT NULL,
  node_id TEXT NOT NULL,
  review_id INTEGER,
  author_login TEXT NOT NULL,
  author_id INTEGER NOT NULL,
  body TEXT NOT NULL,
  path TEXT NOT NULL,
  commit_id TEXT NOT NULL,
  original_commit_id TEXT NOT NULL,
  diff_hunk TEXT,
  position INTEGER,
  original_position INTEGER,
  line INTEGER,
  original_line INTEGER,
  start_line INTEGER,
  side TEXT NOT NULL,
  start_side TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  html_url TEXT NOT NULL,
  raw_json TEXT,
  UNIQUE(pr_id, comment_id)
);

-- Issue comments (general PR discussion comments)
CREATE TABLE IF NOT EXISTS issue_comments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  pr_id INTEGER NOT NULL REFERENCES pull_requests(id),
  comment_id INTEGER NOT NULL,
  node_id TEXT NOT NULL,
  author_login TEXT NOT NULL,
  author_id INTEGER NOT NULL,
  author_association TEXT NOT NULL,
  body TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  html_url TEXT NOT NULL,
  raw_json TEXT,
  UNIQUE(pr_id, comment_id)
);

-- Reviews (grouped review comments + approval state)
CREATE TABLE IF NOT EXISTS reviews (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  pr_id INTEGER NOT NULL REFERENCES pull_requests(id),
  review_id INTEGER NOT NULL,
  node_id TEXT NOT NULL,
  author_login TEXT NOT NULL,
  author_id INTEGER NOT NULL,
  author_association TEXT NOT NULL,
  body TEXT,
  state TEXT NOT NULL,
  commit_id TEXT NOT NULL,
  submitted_at TEXT NOT NULL,
  html_url TEXT NOT NULL,
  raw_json TEXT,
  UNIQUE(pr_id, review_id)
);

-- Timeline events (for reconstructing PR state history)
CREATE TABLE IF NOT EXISTS timeline_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  pr_id INTEGER NOT NULL REFERENCES pull_requests(id),
  event_type TEXT NOT NULL,
  actor_login TEXT,
  actor_id INTEGER,
  created_at TEXT NOT NULL,
  event_data TEXT,
  raw_json TEXT NOT NULL,
  UNIQUE(pr_id, event_type, created_at, actor_login)
);

-- Sync state tracking per PR
CREATE TABLE IF NOT EXISTS pr_sync_state (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  pr_id INTEGER UNIQUE NOT NULL REFERENCES pull_requests(id),
  last_synced_at TEXT NOT NULL,
  commits_synced INTEGER NOT NULL,
  review_comments_synced INTEGER NOT NULL,
  issue_comments_synced INTEGER NOT NULL,
  reviews_synced INTEGER NOT NULL,
  timeline_events_synced INTEGER NOT NULL
);

-- Sync state tracking per repository
CREATE TABLE IF NOT EXISTS repo_sync_state (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  repo_id INTEGER UNIQUE NOT NULL REFERENCES repositories(id),
  last_synced_at TEXT NOT NULL,
  initial_sync_complete INTEGER NOT NULL DEFAULT 0,
  oldest_pr_synced INTEGER,
  newest_pr_synced INTEGER,
  total_prs_synced INTEGER NOT NULL DEFAULT 0
);

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS idx_repositories_org ON repositories(org_id);
CREATE INDEX IF NOT EXISTS idx_pull_requests_repo ON pull_requests(repo_id);
CREATE INDEX IF NOT EXISTS idx_pull_requests_state ON pull_requests(state);
CREATE INDEX IF NOT EXISTS idx_pull_requests_updated ON pull_requests(updated_at);
CREATE INDEX IF NOT EXISTS idx_pull_requests_author ON pull_requests(author_login);

CREATE INDEX IF NOT EXISTS idx_pr_labels_pr ON pr_labels(pr_id);

CREATE INDEX IF NOT EXISTS idx_commits_pr ON commits(pr_id);
CREATE INDEX IF NOT EXISTS idx_commits_date ON commits(committed_date);

CREATE INDEX IF NOT EXISTS idx_review_comments_pr ON review_comments(pr_id);
CREATE INDEX IF NOT EXISTS idx_review_comments_review ON review_comments(review_id);
CREATE INDEX IF NOT EXISTS idx_review_comments_created ON review_comments(created_at);

CREATE INDEX IF NOT EXISTS idx_issue_comments_pr ON issue_comments(pr_id);
CREATE INDEX IF NOT EXISTS idx_issue_comments_created ON issue_comments(created_at);

CREATE INDEX IF NOT EXISTS idx_reviews_pr ON reviews(pr_id);
CREATE INDEX IF NOT EXISTS idx_reviews_state ON reviews(state);
CREATE INDEX IF NOT EXISTS idx_reviews_submitted ON reviews(submitted_at);

CREATE INDEX IF NOT EXISTS idx_timeline_events_pr ON timeline_events(pr_id);
CREATE INDEX IF NOT EXISTS idx_timeline_events_type ON timeline_events(event_type);
CREATE INDEX IF NOT EXISTS idx_timeline_events_created ON timeline_events(created_at);

-- Full-text search virtual tables
CREATE VIRTUAL TABLE IF NOT EXISTS review_comments_fts USING fts5(
  body,
  author_login,
  path,
  content='review_comments',
  content_rowid='id'
);

CREATE VIRTUAL TABLE IF NOT EXISTS issue_comments_fts USING fts5(
  body,
  author_login,
  content='issue_comments',
  content_rowid='id'
);

CREATE VIRTUAL TABLE IF NOT EXISTS pull_requests_fts USING fts5(
  title,
  body,
  author_login,
  content='pull_requests',
  content_rowid='id'
);

-- Triggers to keep FTS in sync
CREATE TRIGGER IF NOT EXISTS review_comments_ai AFTER INSERT ON review_comments BEGIN
  INSERT INTO review_comments_fts(rowid, body, author_login, path)
  VALUES (new.id, new.body, new.author_login, new.path);
END;

CREATE TRIGGER IF NOT EXISTS issue_comments_ai AFTER INSERT ON issue_comments BEGIN
  INSERT INTO issue_comments_fts(rowid, body, author_login)
  VALUES (new.id, new.body, new.author_login);
END;

CREATE TRIGGER IF NOT EXISTS pull_requests_ai AFTER INSERT ON pull_requests BEGIN
  INSERT INTO pull_requests_fts(rowid, title, body, author_login)
  VALUES (new.id, new.title, COALESCE(new.body, ''), new.author_login);
END;

-- AI-generated PR reviews
CREATE TABLE IF NOT EXISTS pr_reviews (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  pr_id INTEGER NOT NULL REFERENCES pull_requests(id),
  pr_number INTEGER NOT NULL,
  commit_sha TEXT NOT NULL,
  reviewed_at TEXT NOT NULL,
  prompt_path TEXT NOT NULL,
  prompt_hash TEXT NOT NULL,
  review_text TEXT NOT NULL,
  model_used TEXT NOT NULL,
  generation_method TEXT NOT NULL,
  raw_metadata TEXT,
  UNIQUE(pr_id, commit_sha, prompt_hash)
);

CREATE INDEX IF NOT EXISTS idx_pr_reviews_pr ON pr_reviews(pr_id);
CREATE INDEX IF NOT EXISTS idx_pr_reviews_commit ON pr_reviews(commit_sha);
CREATE INDEX IF NOT EXISTS idx_pr_reviews_timestamp ON pr_reviews(reviewed_at);
