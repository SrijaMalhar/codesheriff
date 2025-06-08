"""
CodeSheriff – Shared Configuration
Loads all environment variables via python-dotenv and exposes them
as typed module-level constants used by every service.
"""

import os
from dotenv import load_dotenv

# Load .env from the project root (one level above this file's package)
load_dotenv()

# ---------------------------------------------------------------------------
# GitHub
# ---------------------------------------------------------------------------
# Personal-access token (or GitHub App installation token) used to call the
# GitHub REST API (fetch diffs, post review comments).
GITHUB_TOKEN: str = os.getenv("GITHUB_TOKEN", "")

# Shared secret configured in the GitHub webhook settings.
# Used to verify HMAC-SHA256 signatures on incoming payloads.
WEBHOOK_SECRET: str = os.getenv("WEBHOOK_SECRET", "")

# ---------------------------------------------------------------------------
# Google Gemini
# ---------------------------------------------------------------------------
GOOGLE_API_KEY: str = os.getenv("GOOGLE_API_KEY", "")

# Gemini model to use for AI-powered code review
GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")

# ---------------------------------------------------------------------------
# Shadow mode
# ---------------------------------------------------------------------------
# When True, reviews are fully analysed and logged to the DB but the
# comment-service will NOT post anything back to GitHub.  Useful for
# validating output quality before enabling live reviews.
SHADOW_MODE: bool = os.getenv("SHADOW_MODE", "false").lower() == "true"

# ---------------------------------------------------------------------------
# Inter-service URLs  (override in .env when not running on localhost)
# ---------------------------------------------------------------------------
ANALYSIS_SERVICE_URL: str = os.getenv("ANALYSIS_SERVICE_URL", "http://localhost:8002")
COMMENT_SERVICE_URL: str = os.getenv("COMMENT_SERVICE_URL", "http://localhost:8003")

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
# SQLite by default; swap for Postgres / MySQL by changing this variable.
DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./codesheriff.db")
