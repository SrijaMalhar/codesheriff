"""
CodeSheriff – Comment Service  (port 8003)
==========================================
Receives a structured review result from the analysis-service and posts
inline review comments back to the GitHub Pull Request.

Shadow mode gate: when shadow_mode is True the findings are logged but
NO GitHub API calls are made, keeping the PR feed clean while you
validate CodeSheriff's output quality.

Environment variables consumed (via shared.config):
  GITHUB_TOKEN   – PAT with `pull_requests: write` permission
  DATABASE_URL   – SQLite / Postgres connection string
"""

import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException
from sqlalchemy.orm import Session

# ---------------------------------------------------------------------------
# Path bootstrap
# ---------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.config import GITHUB_TOKEN
from shared.database import ReviewLog, SessionLocal, create_tables
from shared.models import CommentRequest, Finding, HealthResponse, Severity

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [comment] %(levelname)s %(message)s",
)
logger = logging.getLogger("comment-service")

# GitHub REST API base
GITHUB_API = "https://api.github.com"

# Severity → emoji mapping for human-readable inline comments
SEVERITY_EMOJI: dict[str, str] = {
    Severity.ERROR: "🔴",
    Severity.WARNING: "🟡",
    Severity.INFO: "🔵",
}


# ---------------------------------------------------------------------------
# App lifecycle
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    create_tables()
    logger.info("Comment service started")
    yield


app = FastAPI(
    title="CodeSheriff – Comment Service",
    description="Posts AI review findings as inline GitHub PR comments.",
    version="1.0.0",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# GitHub API helpers
# ---------------------------------------------------------------------------
def _gh_headers() -> dict:
    return {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def post_review_comment(
    owner: str,
    repo_name: str,
    pr_number: int,
    head_sha: str,
    finding: Finding,
) -> bool:
    """Post a single inline review comment on the PR.

    Uses the Pull Request Review Comment API which supports per-line comments.
    Returns True on success, False on failure.
    """
    emoji = SEVERITY_EMOJI.get(finding.severity, "⚪")
    body = (
        f"{emoji} **CodeSheriff [{finding.severity.upper()}]** "
        f"_(source: {finding.source})_\n\n"
        f"{finding.suggestion}"
    )

    url = f"{GITHUB_API}/repos/{owner}/{repo_name}/pulls/{pr_number}/comments"
    payload = {
        "body": body,
        "commit_id": head_sha,
        "path": finding.file,
        "line": finding.line,
        "side": "RIGHT",   # Comment on the new version of the file
    }

    try:
        resp = httpx.post(url, headers=_gh_headers(), json=payload, timeout=20)
        if resp.status_code == 201:
            return True
        logger.warning(
            "GitHub returned %s for comment on %s:%s – %s",
            resp.status_code, finding.file, finding.line, resp.text[:200],
        )
        return False
    except httpx.HTTPError as exc:
        logger.error("HTTP error posting comment: %s", exc)
        return False


def post_review_summary(
    owner: str,
    repo_name: str,
    pr_number: int,
    summary: str,
    findings_count: int,
    shadow_mode: bool,
) -> None:
    """Post a top-level PR review with an overall summary body.

    Uses the Pull Requests Review submission API (distinct from inline
    comments) so the summary appears as a collapsible review block.
    """
    mode_note = (
        "\n\n> ⚠️ **Shadow mode was ON** – these findings were NOT posted as inline comments."
        if shadow_mode
        else ""
    )
    body = (
        f"## 🤠 CodeSheriff Review\n\n"
        f"**{findings_count}** issue(s) found.\n\n"
        f"{summary or '_No summary available._'}"
        f"{mode_note}"
    )

    url = f"{GITHUB_API}/repos/{owner}/{repo_name}/pulls/{pr_number}/reviews"
    payload = {
        "body": body,
        "event": "COMMENT",   # COMMENT = non-blocking; use APPROVE / REQUEST_CHANGES for blocking
    }

    try:
        resp = httpx.post(url, headers=_gh_headers(), json=payload, timeout=20)
        if resp.status_code == 200:
            logger.info("Posted review summary to PR #%s", pr_number)
        else:
            logger.warning("Summary post returned %s: %s", resp.status_code, resp.text[:200])
    except httpx.HTTPError as exc:
        logger.error("HTTP error posting summary: %s", exc)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.get("/health", response_model=HealthResponse, tags=["ops"])
async def health() -> HealthResponse:
    return HealthResponse(service="comment-service")


@app.post("/comment", tags=["comment"])
async def post_comment(payload: CommentRequest) -> dict:
    """Receive a ReviewResult and post findings to GitHub (or skip if shadow).

    Steps:
    1. If shadow_mode is True – log and return without calling GitHub.
    2. Post each inline Finding as a separate review comment.
    3. Post an overall review summary block.
    4. Update the ReviewLog status (comment-service is the final stage).
    """
    review = payload.review
    owner = review.owner
    repo_name = review.repo.split("/")[-1]
    pr_number = review.pr_number
    head_sha = review.head_sha
    shadow_mode = review.shadow_mode
    findings = review.findings
    summary = review.summary or ""
    log_id = payload.review_log_id

    logger.info(
        "comment-service called for PR #%s in %s/%s  shadow=%s  findings=%d",
        pr_number, owner, repo_name, shadow_mode, len(findings),
    )

    if shadow_mode:
        # Shadow mode: record the decision but post nothing to GitHub
        logger.info(
            "Shadow mode ON – skipping GitHub posting for PR #%s (log_id=%s)",
            pr_number, log_id,
        )
        return {
            "status": "shadow",
            "message": "Review logged but not posted (shadow mode active)",
            "findings_count": len(findings),
        }

    if not GITHUB_TOKEN:
        logger.error("GITHUB_TOKEN not configured – cannot post comments")
        raise HTTPException(status_code=503, detail="GITHUB_TOKEN not set")

    # --- Post inline comments for each finding ---------------------------
    posted, failed = 0, 0
    for finding in findings:
        success = post_review_comment(owner, repo_name, pr_number, head_sha, finding)
        if success:
            posted += 1
        else:
            failed += 1

    # --- Post overall summary --------------------------------------------
    post_review_summary(owner, repo_name, pr_number, summary, len(findings), shadow_mode)

    logger.info(
        "Finished PR #%s: %d comments posted, %d failed",
        pr_number, posted, failed,
    )

    return {
        "status": "completed",
        "comments_posted": posted,
        "comments_failed": failed,
        "findings_count": len(findings),
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8003, reload=True)
