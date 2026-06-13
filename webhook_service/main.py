import hashlib
import hmac
import json
import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from shared.config import ANALYSIS_SERVICE_URL, SHADOW_MODE, WEBHOOK_SECRET
from shared.database import ReviewLog, WebhookEvent, SessionLocal, create_tables
from shared.models import HealthResponse, ReviewRequest

logging.basicConfig(level=logging.INFO, format="%(asctime)s [webhook] %(levelname)s %(message)s")
logger = logging.getLogger("webhook-service")


@asynccontextmanager
async def lifespan(app):
    create_tables()
    yield


app = FastAPI(title="CodeSheriff – Webhook Service", lifespan=lifespan)


def verify_signature(payload: bytes, signature_header: str) -> bool:
    if not WEBHOOK_SECRET:
        logger.warning("WEBHOOK_SECRET not set – skipping signature check")
        return True
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(WEBHOOK_SECRET.encode(), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header)


def _log_event(*, raw_body, event_type, action, repo, pr_number, sender, outcome, review_log_id=None):
    db: Session = SessionLocal()
    try:
        ev = WebhookEvent(event_type=event_type, action=action, repo=repo,
                          pr_number=pr_number, sender=sender, outcome=outcome, review_log_id=review_log_id)
        ev.set_payload(raw_body)
        db.add(ev)
        db.commit()
    except Exception as exc:
        logger.warning("Failed to log webhook event: %s", exc)
    finally:
        db.close()


async def dispatch_analysis(review_log_id: int, review_request: ReviewRequest):
    payload = review_request.model_dump()
    payload["review_log_id"] = review_log_id
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(f"{ANALYSIS_SERVICE_URL}/analyze", json=payload)
            resp.raise_for_status()
    except httpx.HTTPError as exc:
        logger.error("Failed to reach analysis-service for log_id=%s: %s", review_log_id, exc)
        db: Session = SessionLocal()
        try:
            log = db.get(ReviewLog, review_log_id)
            if log:
                log.status = "failed"
                db.commit()
        finally:
            db.close()


@app.get("/health", response_model=HealthResponse)
async def health():
    return HealthResponse(service="webhook-service")


@app.post("/webhook")
async def receive_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_hub_signature_256: str = Header(default=""),
    x_github_event: str = Header(default=""),
) -> dict:
    raw_body = await request.body()

    if not verify_signature(raw_body, x_hub_signature_256):
        logger.warning("Rejected webhook – invalid signature")
        _log_event(raw_body=raw_body, event_type=x_github_event, action="", repo="", pr_number=0, sender="", outcome="rejected")
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    if x_github_event != "pull_request":
        _log_event(raw_body=raw_body, event_type=x_github_event, action="", repo="", pr_number=0, sender="", outcome="ignored")
        return {"status": "ignored", "event": x_github_event}

    payload = json.loads(raw_body)
    action = payload.get("action", "")
    sender = payload.get("sender", {}).get("login", "")

    if action not in ("opened", "synchronize"):
        pr_data = payload.get("pull_request", {})
        _log_event(raw_body=raw_body, event_type=x_github_event, action=action,
                   repo=payload.get("repository", {}).get("full_name", ""),
                   pr_number=pr_data.get("number", 0), sender=sender, outcome="ignored")
        return {"status": "ignored", "action": action}

    pr = payload.get("pull_request", {})
    repo_data = payload.get("repository", {})
    pr_number = pr.get("number", 0)
    owner = repo_data.get("owner", {}).get("login", "")
    repo = repo_data.get("full_name", "")
    head_sha = pr.get("head", {}).get("sha", "")

    if not (pr_number and owner and repo):
        _log_event(raw_body=raw_body, event_type=x_github_event, action=action,
                   repo=repo, pr_number=pr_number, sender=sender, outcome="error")
        raise HTTPException(status_code=422, detail="Malformed pull_request payload")

    db: Session = SessionLocal()
    try:
        log = ReviewLog(pr_id=pr_number, repo=repo, owner=owner, head_sha=head_sha,
                        mode="shadow" if SHADOW_MODE else "live", status="pending")
        db.add(log)
        db.commit()
        db.refresh(log)
        log_id = log.id
    finally:
        db.close()

    _log_event(raw_body=raw_body, event_type=x_github_event, action=action,
               repo=repo, pr_number=pr_number, sender=sender, outcome="queued", review_log_id=log_id)

    review_request = ReviewRequest(pr_number=pr_number, repo=repo, owner=owner,
                                   head_sha=head_sha, shadow_mode=SHADOW_MODE)
    background_tasks.add_task(dispatch_analysis, log_id, review_request)

    return {"status": "queued", "pr_number": pr_number, "repo": repo,
            "review_log_id": log_id, "shadow_mode": SHADOW_MODE}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8001, reload=True)
