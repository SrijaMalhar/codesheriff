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
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from shared.config import COMMENT_SERVICE_URL, GEMINI_MODEL, GITHUB_TOKEN, GOOGLE_API_KEY
from shared.database import ReviewLog, SessionLocal, create_tables
from shared.models import CommentRequest, Finding, HealthResponse, ReviewRequest, ReviewResult, Severity

logging.basicConfig(level=logging.INFO, format="%(asctime)s [analysis] %(levelname)s %(message)s")
logger = logging.getLogger("analysis-service")

GITHUB_API = "https://api.github.com"


@asynccontextmanager
async def lifespan(app):
    create_tables()
    yield


app = FastAPI(title="CodeSheriff – Analysis Service", lifespan=lifespan)


def _gh_headers():
    return {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type(httpx.HTTPError),
    reraise=True,
)
def fetch_pr_diff(owner, repo_name, pr_number):
    url = f"{GITHUB_API}/repos/{owner}/{repo_name}/pulls/{pr_number}"
    resp = httpx.get(url, headers={**_gh_headers(), "Accept": "application/vnd.github.v3.diff"}, timeout=30)
    resp.raise_for_status()
    return resp.text


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type(httpx.HTTPError),
    reraise=True,
)
def fetch_pr_files(owner, repo_name, pr_number):
    resp = httpx.get(f"{GITHUB_API}/repos/{owner}/{repo_name}/pulls/{pr_number}/files",
                     headers=_gh_headers(), timeout=30)
    resp.raise_for_status()
    return resp.json()


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type(httpx.HTTPError),
    reraise=False,
)
def fetch_file_content(owner, repo_name, path, ref):
    import base64
    resp = httpx.get(f"{GITHUB_API}/repos/{owner}/{repo_name}/contents/{path}",
                     headers=_gh_headers(), params={"ref": ref}, timeout=30)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return base64.b64decode(resp.json()["content"]).decode("utf-8", errors="replace")


def run_flake8(file_path, source_code):
    findings = []
    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False, encoding="utf-8") as tmp:
        tmp.write(source_code)
        tmp_path = tmp.name
    try:
        result = subprocess.run(
            ["flake8", "--format=%(row)d:%(col)d:%(code)s:%(text)s", tmp_path],
            capture_output=True, text=True, timeout=30,
        )
        for line in result.stdout.splitlines():
            parts = line.split(":", 3)
            if len(parts) < 4:
                continue
            try:
                row, _col, code, text = parts
                sev = (Severity.ERROR if code.startswith(("E", "F"))
                       else Severity.WARNING if code.startswith("W")
                       else Severity.INFO)
                findings.append(Finding(file=file_path, line=int(row), severity=sev,
                                        suggestion=f"[{code}] {text.strip()}", source="flake8"))
            except (ValueError, IndexError):
                continue
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        logger.warning("flake8 failed for %s: %s", file_path, exc)
    finally:
        Path(tmp_path).unlink(missing_ok=True)
    return findings


@retry(
    stop=stop_after_attempt(2),
    wait=wait_exponential(multiplier=2, min=5, max=30),
    retry=retry_if_exception_type(Exception),
    reraise=False,
)
def _call_gemini(model, prompt):
    return model.generate_content(
        prompt,
        generation_config=genai.types.GenerationConfig(temperature=0.2, max_output_tokens=4096),
    )


def run_gemini_review(diff):
    if not GOOGLE_API_KEY:
        return [], "AI review skipped (no API key)"
    genai.configure(api_key=GOOGLE_API_KEY)
    model = genai.GenerativeModel(GEMINI_MODEL)
    prompt = f"""You are CodeSheriff, a senior software engineer reviewing a GitHub Pull Request.
Analyse the following unified diff and return a structured JSON review.

DIFF:
{diff[:12000]}

Respond ONLY with valid JSON (no markdown fences).
Format:
{{
  "issues": [{{"file": "path/file.py", "line": 42, "severity": "error", "suggestion": "..."}}],
  "summary": "One-paragraph overall assessment."
}}
Severity: "error", "warning", or "info"."""
    try:
        response = _call_gemini(model, prompt)
        raw = response.text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        data = json.loads(raw.strip())
    except json.JSONDecodeError as exc:
        logger.error("Gemini returned non-JSON: %s", exc)
        return [], "AI review returned unparseable response."
    except Exception as exc:
        logger.error("Gemini error: %s", exc)
        return [], f"AI review failed: {exc}"

    findings = []
    for issue in data.get("issues", []):
        try:
            findings.append(Finding(
                file=issue["file"],
                line=max(1, int(issue.get("line", 1))),
                severity=Severity(issue.get("severity", "info")),
                suggestion=issue.get("suggestion", ""),
                source="ai",
            ))
        except (KeyError, ValueError):
            pass
    return findings, data.get("summary", "")


@app.get("/health", response_model=HealthResponse)
async def health():
    return HealthResponse(service="analysis-service")


class AnalyzePayload(ReviewRequest):
    review_log_id: int = 0


@app.post("/analyze")
async def analyze(payload: AnalyzePayload):
    owner = payload.owner
    repo_name = payload.repo.split("/")[-1]

    try:
        diff = fetch_pr_diff(owner, repo_name, payload.pr_number)
    except httpx.HTTPError as exc:
        _mark_failed(payload.review_log_id)
        raise HTTPException(status_code=502, detail=f"GitHub API error: {exc}")

    flake8_findings = []
    try:
        for f in fetch_pr_files(owner, repo_name, payload.pr_number):
            if f["filename"].endswith(".py") and f.get("status") != "removed":
                source = fetch_file_content(owner, repo_name, f["filename"], payload.head_sha)
                if source:
                    flake8_findings.extend(run_flake8(f["filename"], source))
    except httpx.HTTPError as exc:
        logger.warning("Could not fetch files for flake8: %s", exc)

    ai_findings, summary = run_gemini_review(diff)
    all_findings = flake8_findings + ai_findings

    db: Session = SessionLocal()
    try:
        log = db.get(ReviewLog, payload.review_log_id)
        if log:
            log.set_findings([f.model_dump() for f in all_findings])
            log.status = "completed"
            db.commit()
    finally:
        db.close()

    comment_payload = CommentRequest(
        review=ReviewResult(
            pr_number=payload.pr_number, repo=payload.repo, owner=owner,
            head_sha=payload.head_sha, shadow_mode=payload.shadow_mode,
            findings=all_findings, summary=summary,
        ),
        review_log_id=payload.review_log_id,
    )
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(f"{COMMENT_SERVICE_URL}/comment", json=comment_payload.model_dump())
            resp.raise_for_status()
    except httpx.HTTPError as exc:
        logger.error("Failed to reach comment-service: %s", exc)

    return {"status": "completed", "pr_number": payload.pr_number, "findings_count": len(all_findings)}


def _mark_failed(log_id):
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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8002, reload=True)
