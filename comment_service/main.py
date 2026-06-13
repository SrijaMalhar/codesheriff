import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from shared.config import GITHUB_TOKEN
from shared.database import ReviewLog, SessionLocal, create_tables
from shared.models import CommentRequest, Finding, HealthResponse, Severity

logging.basicConfig(level=logging.INFO, format="%(asctime)s [comment] %(levelname)s %(message)s")
logger = logging.getLogger("comment-service")

GITHUB_API = "https://api.github.com"
SEVERITY_EMOJI = {Severity.ERROR: "🔴", Severity.WARNING: "🟡", Severity.INFO: "🔵"}


@asynccontextmanager
async def lifespan(app):
    create_tables()
    yield


app = FastAPI(title="CodeSheriff – Comment Service", lifespan=lifespan)


def _gh_headers():
    return {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def post_review_comment(owner, repo_name, pr_number, head_sha, finding: Finding) -> bool:
    emoji = SEVERITY_EMOJI.get(finding.severity, "⚪")
    body = f"{emoji} **CodeSheriff [{finding.severity.upper()}]** _(source: {finding.source})_\n\n{finding.suggestion}"
    url = f"{GITHUB_API}/repos/{owner}/{repo_name}/pulls/{pr_number}/comments"
    payload = {"body": body, "commit_id": head_sha, "path": finding.file, "line": finding.line, "side": "RIGHT"}
    try:
        resp = httpx.post(url, headers=_gh_headers(), json=payload, timeout=20)
        if resp.status_code == 201:
            return True
        logger.warning("GitHub returned %s for comment on %s:%s", resp.status_code, finding.file, finding.line)
        return False
    except httpx.HTTPError as exc:
        logger.error("HTTP error posting comment: %s", exc)
        return False


def post_review_summary(owner, repo_name, pr_number, summary, findings_count, shadow_mode):
    mode_note = "\n\n> ⚠️ **Shadow mode was ON** – findings were NOT posted as inline comments." if shadow_mode else ""
    body = f"## 🤠 CodeSheriff Review\n\n**{findings_count}** issue(s) found.\n\n{summary or '_No summary available._'}{mode_note}"
    url = f"{GITHUB_API}/repos/{owner}/{repo_name}/pulls/{pr_number}/reviews"
    try:
        resp = httpx.post(url, headers=_gh_headers(), json={"body": body, "event": "COMMENT"}, timeout=20)
        if resp.status_code != 200:
            logger.warning("Summary post returned %s", resp.status_code)
    except httpx.HTTPError as exc:
        logger.error("HTTP error posting summary: %s", exc)


@app.get("/health", response_model=HealthResponse)
async def health():
    return HealthResponse(service="comment-service")


@app.post("/comment")
async def post_comment(payload: CommentRequest) -> dict:
    review = payload.review
    owner = review.owner
    repo_name = review.repo.split("/")[-1]
    shadow_mode = review.shadow_mode

    logger.info("comment-service: PR #%s in %s/%s shadow=%s findings=%d",
                review.pr_number, owner, repo_name, shadow_mode, len(review.findings))

    if shadow_mode:
        return {"status": "shadow", "message": "Review logged but not posted", "findings_count": len(review.findings)}

    if not GITHUB_TOKEN:
        raise HTTPException(status_code=503, detail="GITHUB_TOKEN not set")

    posted, failed = 0, 0
    for finding in review.findings:
        if post_review_comment(owner, repo_name, review.pr_number, review.head_sha, finding):
            posted += 1
        else:
            failed += 1

    post_review_summary(owner, repo_name, review.pr_number, review.summary or "", len(review.findings), shadow_mode)

    return {"status": "completed", "comments_posted": posted, "comments_failed": failed}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8003, reload=True)
