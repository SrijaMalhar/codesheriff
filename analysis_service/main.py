"""
CodeSheriff – Analysis Service  (port 8002)
===========================================
For a given PR, this service:
  1. Fetches the list of changed files and their raw content via the GitHub API.
  2. Runs flake8 on every modified Python file to gather static-analysis findings.
  3. Sends the full unified diff to Google Gemini and asks for a structured JSON
     review (issues with file, line, severity, suggestion).
  4. Merges both finding sets and calls the comment-service.
  5. Updates the ReviewLog row in the DB with the final findings and status.

Environment variables consumed (via shared.config):
  GITHUB_TOKEN        – PAT with repo read + PR write permissions
  GOOGLE_API_KEY      – Google AI Studio / Gemini API key
  GEMINI_MODEL        – model name (default: gemini-1.5-flash)
  COMMENT_SERVICE_URL – URL of the comment-service (default: localhost:8003)
  DATABASE_URL        – SQLite / Postgres connection string
"""

import json
import logging
import subprocess
import sys
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

import google.generativeai as genai
import httpx
from fastapi import FastAPI, HTTPException
from sqlalchemy.orm import Session

# ---------------------------------------------------------------------------
# Path bootstrap
# ---------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.config import GOOGLE_API_KEY, GEMINI_MODEL, COMMENT_SERVICE_URL, GITHUB_TOKEN
from shared.database import ReviewLog, SessionLocal, create_tables
from shared.models import CommentRequest, Finding, HealthResponse, ReviewRequest, ReviewResult, Severity

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [analysis] %(levelname)s %(message)s",
)
logger = logging.getLogger("analysis-service")

# ---------------------------------------------------------------------------
# GitHub API base URL
# ---------------------------------------------------------------------------
GITHUB_API = "https://api.github.com"


# ---------------------------------------------------------------------------
# App lifecycle
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    create_tables()
    logger.info("Analysis service started")
    yield


app = FastAPI(
    title="CodeSheriff – Analysis Service",
    description="Analyses PR diffs with flake8 and Gemini, then triggers the comment-service.",
    version="1.0.0",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# GitHub API helpers
# ---------------------------------------------------------------------------
def _gh_headers() -> dict:
    """Return auth headers for the GitHub REST API."""
    return {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def fetch_pr_diff(owner: str, repo_name: str, pr_number: int) -> str:
    """Return the unified diff for the PR (GitHub diff media type)."""
    url = f"{GITHUB_API}/repos/{owner}/{repo_name}/pulls/{pr_number}"
    resp = httpx.get(
        url,
        headers={**_gh_headers(), "Accept": "application/vnd.github.v3.diff"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.text


def fetch_pr_files(owner: str, repo_name: str, pr_number: int) -> list[dict]:
    """Return metadata for every file changed in the PR."""
    url = f"{GITHUB_API}/repos/{owner}/{repo_name}/pulls/{pr_number}/files"
    resp = httpx.get(url, headers=_gh_headers(), timeout=30)
    resp.raise_for_status()
    return resp.json()


def fetch_file_content(owner: str, repo_name: str, path: str, ref: str) -> str | None:
    """Fetch raw file content at a specific commit ref; returns None on 404."""
    url = f"{GITHUB_API}/repos/{owner}/{repo_name}/contents/{path}"
    resp = httpx.get(url, headers=_gh_headers(), params={"ref": ref}, timeout=30)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    data = resp.json()
    # GitHub returns base64-encoded content for small files
    import base64
    return base64.b64decode(data["content"]).decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Static analysis with flake8
# ---------------------------------------------------------------------------
def run_flake8(file_path: str, source_code: str) -> list[Finding]:
    """Write source to a temp file, run flake8, and parse its output.

    Returns a list of Finding objects with source='flake8'.
    """
    findings: list[Finding] = []

    with tempfile.NamedTemporaryFile(
        suffix=".py", mode="w", delete=False, encoding="utf-8"
    ) as tmp:
        tmp.write(source_code)
        tmp_path = tmp.name

    try:
        result = subprocess.run(
            ["flake8", "--format=%(row)d:%(col)d:%(code)s:%(text)s", tmp_path],
            capture_output=True,
            text=True,
            timeout=30,
        )
        for line in result.stdout.splitlines():
            parts = line.split(":", 3)
            if len(parts) < 4:
                continue
            try:
                row, _col, code, text = parts
                # Map flake8 error categories to our severity enum
                if code.startswith("E") or code.startswith("F"):
                    severity = Severity.ERROR
                elif code.startswith("W"):
                    severity = Severity.WARNING
                else:
                    severity = Severity.INFO

                findings.append(
                    Finding(
                        file=file_path,
                        line=int(row),
                        severity=severity,
                        suggestion=f"[{code}] {text.strip()}",
                        source="flake8",
                    )
                )
            except (ValueError, IndexError):
                continue
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        logger.warning("flake8 run failed for %s: %s", file_path, exc)
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    return findings


# ---------------------------------------------------------------------------
# AI review via Google Gemini
# ---------------------------------------------------------------------------
def run_gemini_review(diff: str) -> tuple[list[Finding], str]:
    """Send the diff to Gemini and parse the structured JSON response.

    Uses gemini-1.5-flash (or the model set in GEMINI_MODEL).
    Returns (findings, summary).
    """
    if not GOOGLE_API_KEY:
        logger.warning("GOOGLE_API_KEY not set – skipping AI review")
        return [], "AI review skipped (no API key configured)"

    # Configure the Gemini client with the provided API key
    genai.configure(api_key=GOOGLE_API_KEY)
    model = genai.GenerativeModel(GEMINI_MODEL)

    # Truncate very large diffs to stay within the model's context window
    truncated_diff = diff[:12000]

    prompt = f"""You are CodeSheriff, a senior software engineer reviewing a GitHub Pull Request.

Analyse the following unified diff carefully and return a structured JSON review.

DIFF:
{truncated_diff}

Respond ONLY with valid JSON — no markdown fences, no extra text.

Required format:
{{
  "issues": [
    {{
      "file": "relative/path/to/file.py",
      "line": 42,
      "severity": "error",
      "suggestion": "Clear description of the problem and how to fix it."
    }}
  ],
  "summary": "One-paragraph overall assessment of the PR quality and key concerns."
}}

Severity values: "error" (bugs, security, crashes), "warning" (bad practice, maintainability),
"info" (style, minor suggestions).

Focus on: bugs, security vulnerabilities, data races, resource leaks, logic errors,
poor error handling, hardcoded secrets, SQL injection, and code-quality issues.
Skip purely stylistic nitpicks already covered by linters unless they are significant."""

    try:
        response = model.generate_content(
            prompt,
            generation_config=genai.types.GenerationConfig(
                temperature=0.2,       # Low temperature for consistent, precise output
                max_output_tokens=4096,
            ),
        )
        raw = response.text.strip()

        # Strip markdown code fences if the model wrapped the JSON
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()

        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.error("Gemini returned non-JSON response: %s", exc)
        return [], "AI review returned an unparseable response."
    except Exception as exc:
        logger.error("Gemini API error: %s", exc)
        return [], f"AI review failed: {exc}"

    findings: list[Finding] = []
    for issue in data.get("issues", []):
        try:
            findings.append(
                Finding(
                    file=issue["file"],
                    line=max(1, int(issue.get("line", 1))),
                    severity=Severity(issue.get("severity", "info")),
                    suggestion=issue.get("suggestion", ""),
                    source="ai",
                )
            )
        except (KeyError, ValueError) as exc:
            logger.debug("Skipping malformed finding: %s – %s", issue, exc)

    summary: str = data.get("summary", "")
    return findings, summary


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.get("/health", response_model=HealthResponse, tags=["ops"])
async def health() -> HealthResponse:
    return HealthResponse(service="analysis-service")


class AnalyzePayload(ReviewRequest):
    """Extends ReviewRequest with the DB log ID injected by webhook-service."""
    review_log_id: int = 0


@app.post("/analyze", tags=["analysis"])
async def analyze(payload: AnalyzePayload) -> dict:
    """Full PR analysis pipeline.

    1. Fetch PR diff and changed file list from GitHub.
    2. Run flake8 on each modified Python file.
    3. Send diff to Claude for AI review.
    4. Merge findings, update DB, call comment-service.
    """
    owner = payload.owner
    repo_name = payload.repo.split("/")[-1]   # strip owner prefix if present
    pr_number = payload.pr_number
    head_sha = payload.head_sha
    review_log_id = payload.review_log_id

    logger.info("Starting analysis for PR #%s in %s/%s", pr_number, owner, repo_name)

    # --- 1. Fetch the unified diff ----------------------------------------
    try:
        diff = fetch_pr_diff(owner, repo_name, pr_number)
    except httpx.HTTPError as exc:
        logger.error("GitHub API error fetching diff: %s", exc)
        _mark_failed(review_log_id)
        raise HTTPException(status_code=502, detail=f"GitHub API error: {exc}")

    # --- 2. Fetch changed files and run flake8 on Python ones -------------
    flake8_findings: list[Finding] = []
    try:
        changed_files = fetch_pr_files(owner, repo_name, pr_number)
        python_files = [
            f for f in changed_files
            if f["filename"].endswith(".py") and f.get("status") != "removed"
        ]
        logger.info("Found %d Python files to lint", len(python_files))

        for file_meta in python_files:
            file_path: str = file_meta["filename"]
            source = fetch_file_content(owner, repo_name, file_path, head_sha)
            if source is None:
                continue
            findings = run_flake8(file_path, source)
            flake8_findings.extend(findings)
            logger.info("flake8: %d issues in %s", len(findings), file_path)
    except httpx.HTTPError as exc:
        logger.warning("Could not fetch file list for flake8: %s", exc)

    # --- 3. Gemini AI review ---------------------------------------------
    ai_findings, summary = run_gemini_review(diff)
    logger.info("Gemini review: %d issues found", len(ai_findings))

    # --- 4. Merge all findings -------------------------------------------
    all_findings = flake8_findings + ai_findings
    findings_dicts = [f.model_dump() for f in all_findings]

    # --- 5. Update the ReviewLog row -------------------------------------
    db: Session = SessionLocal()
    try:
        log = db.get(ReviewLog, review_log_id)
        if log:
            log.set_findings(findings_dicts)
            log.status = "completed"
            db.commit()
            logger.info("Updated ReviewLog id=%s with %d findings", review_log_id, len(all_findings))
    finally:
        db.close()

    # --- 6. Call comment-service (skip if no log_id) ---------------------
    review_result = ReviewResult(
        pr_number=pr_number,
        repo=payload.repo,
        owner=owner,
        head_sha=head_sha,
        shadow_mode=payload.shadow_mode,
        findings=all_findings,
        summary=summary,
    )
    comment_payload = CommentRequest(
        review=review_result,
        review_log_id=review_log_id,
    )

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{COMMENT_SERVICE_URL}/comment",
                json=comment_payload.model_dump(),
            )
            resp.raise_for_status()
    except httpx.HTTPError as exc:
        logger.error("Failed to reach comment-service: %s", exc)

    return {
        "status": "completed",
        "pr_number": pr_number,
        "findings_count": len(all_findings),
        "shadow_mode": payload.shadow_mode,
    }


def _mark_failed(log_id: int) -> None:
    """Set a ReviewLog row's status to 'failed'."""
    if not log_id:
        return
    db: Session = SessionLocal()
    try:
        log = db.get(ReviewLog, log_id)
        if log:
            log.status = "failed"
            db.commit()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8002, reload=True)
