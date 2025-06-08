"""
CodeSheriff – Webhook Service  (port 8001)
==========================================
Receives GitHub webhook events for pull_request actions (opened /
synchronize), verifies the HMAC-SHA256 signature, persists a review
record in the DB, and dispatches a background task to the
analysis-service.

Environment variables consumed (via shared.config):
  WEBHOOK_SECRET      – shared secret configured in GitHub webhook settings
  ANALYSIS_SERVICE_URL – URL of the analysis-service (default: localhost:8002)
  SHADOW_MODE         – "true" to log-only, skip posting comments to GitHub
  DATABASE_URL        – SQLite / Postgres connection string
"""

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

# ---------------------------------------------------------------------------
# Path bootstrap – allow "shared" to be found when running as a module or
# via `python webhook_service/main.py` from the codesheriff/ directory.
# ---------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.config import ANALYSIS_SERVICE_URL, SHADOW_MODE, WEBHOOK_SECRET
from shared.database import ReviewLog, WebhookEvent, SessionLocal, create_tables
from shared.models import HealthResponse, ReviewRequest

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [webhook] %(levelname)s %(message)s",
)
logger = logging.getLogger("webhook-service")


# ---------------------------------------------------------------------------
# App lifecycle
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create DB tables on startup."""
    create_tables()
    logger.info("Webhook service started – DB tables ready")
    yield


app = FastAPI(
    title="CodeSheriff – Webhook Service",
    description="Receives and validates GitHub PR webhooks.",
    version="1.0.0",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# HMAC signature verification
# ---------------------------------------------------------------------------
def verify_signature(payload: bytes, signature_header: str) -> bool:
    """Return True if the X-Hub-Signature-256 header matches the payload HMAC.

    GitHub computes:  HMAC-SHA256(secret, raw_body)  and sends it as
    'sha256=<hex_digest>'.  We recompute and compare with a constant-time
    equality check to prevent timing attacks.
    """
    if not WEBHOOK_SECRET:
        logger.warning("WEBHOOK_SECRET not set – skipping signature check (insecure!)")
        return True

    if not signature_header or not signature_header.startswith("sha256="):
        return False

    expected = "sha256=" + hmac.new(
        WEBHOOK_SECRET.encode(), payload, hashlib.sha256
    ).hexdigest()

    return hmac.compare_digest(expected, signature_header)


# ---------------------------------------------------------------------------
# Helper: persist a WebhookEvent row
# ---------------------------------------------------------------------------
def _log_event(
    *,
    raw_body: bytes,
    event_type: str,
    action: str,
    repo: str,
    pr_number: int,
    sender: str,
    outcome: str,
    review_log_id: int | None = None,
) -> None:
    """Write one WebhookEvent row; swallows DB errors so the main handler
    is never blocked by a logging failure."""
    db: Session = SessionLocal()
    try:
        ev = WebhookEvent(
            event_type=event_type,
            action=action,
            repo=repo,
            pr_number=pr_number,
            sender=sender,
            outcome=outcome,
            review_log_id=review_log_id,
        )
        ev.set_payload(raw_body)
        db.add(ev)
        db.commit()
    except Exception as exc:           # pragma: no cover
        logger.warning("Failed to log webhook event: %s", exc)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Background task: call analysis-service
# ---------------------------------------------------------------------------
async def dispatch_analysis(review_log_id: int, review_request: ReviewRequest) -> None:
    """Fire-and-forget: send the review request to the analysis-service.

    Runs in FastAPI's background task executor so the webhook response
    can be returned to GitHub quickly (GitHub times out after 10 s).
    """
    payload = review_request.model_dump()
    payload["review_log_id"] = review_log_id

    logger.info(
        "Dispatching analysis for PR #%s in %s (log_id=%s, shadow=%s)",
        review_request.pr_number,
        review_request.repo,
        review_log_id,
        review_request.shadow_mode,
    )

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                f"{ANALYSIS_SERVICE_URL}/analyze",
                json=payload,
            )
            resp.raise_for_status()
            logger.info("Analysis complete for log_id=%s – status %s", review_log_id, resp.status_code)
    except httpx.HTTPError as exc:
        logger.error("Failed to reach analysis-service for log_id=%s: %s", review_log_id, exc)

        # Mark the DB record as failed so the dashboard reflects reality
        db: Session = SessionLocal()
        try:
            log = db.get(ReviewLog, review_log_id)
            if log:
                log.status = "failed"
                db.commit()
        finally:
            db.close()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.get("/health", response_model=HealthResponse, tags=["ops"])
async def health() -> HealthResponse:
    """Liveness probe – returns 200 when the service is up."""
    return HealthResponse(service="webhook-service")


@app.post("/webhook", tags=["webhook"])
async def receive_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_hub_signature_256: str = Header(default=""),
    x_github_event: str = Header(default=""),
) -> dict:
    """GitHub webhook receiver.

    1. Reads the raw request body (needed for HMAC verification).
    2. Verifies the X-Hub-Signature-256 header.
    3. Ignores non-pull_request events silently (GitHub sends many event types).
    4. For opened / synchronize actions: creates a ReviewLog row and
       enqueues a background analysis task.
    """
    raw_body = await request.body()

    # --- Signature check -------------------------------------------------
    if not verify_signature(raw_body, x_hub_signature_256):
        logger.warning("Rejected webhook – invalid signature")
        _log_event(
            raw_body=raw_body,
            event_type=x_github_event,
            action="",
            repo="", pr_number=0, sender="",
            outcome="rejected",
        )
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    # --- Event filter ----------------------------------------------------
    if x_github_event != "pull_request":
        logger.debug("Ignoring event type: %s", x_github_event)
        _log_event(
            raw_body=raw_body,
            event_type=x_github_event,
            action="",
            repo="", pr_number=0, sender="",
            outcome="ignored",
        )
        return {"status": "ignored", "event": x_github_event}

    payload = json.loads(raw_body)
    action = payload.get("action", "")
    sender: str = payload.get("sender", {}).get("login", "")

    if action not in ("opened", "synchronize"):
        logger.debug("Ignoring pull_request action: %s", action)
        pr_data = payload.get("pull_request", {})
        _log_event(
            raw_body=raw_body,
            event_type=x_github_event,
            action=action,
            repo=payload.get("repository", {}).get("full_name", ""),
            pr_number=pr_data.get("number", 0),
            sender=sender,
            outcome="ignored",
        )
        return {"status": "ignored", "action": action}

    # --- Extract PR metadata ---------------------------------------------
    pr = payload.get("pull_request", {})
    repo_data = payload.get("repository", {})

    pr_number: int = pr.get("number", 0)
    owner: str = repo_data.get("owner", {}).get("login", "")
    repo: str = repo_data.get("full_name", "")        # "owner/name"
    head_sha: str = pr.get("head", {}).get("sha", "")

    if not (pr_number and owner and repo):
        _log_event(
            raw_body=raw_body,
            event_type=x_github_event,
            action=action,
            repo=repo, pr_number=pr_number, sender=sender,
            outcome="error",
        )
        raise HTTPException(status_code=422, detail="Malformed pull_request payload")

    # --- Determine shadow mode (env var default, overridable per-request) --
    shadow_mode: bool = SHADOW_MODE

    # --- Persist a pending ReviewLog row ---------------------------------
    db: Session = SessionLocal()
    try:
        log = ReviewLog(
            pr_id=pr_number,
            repo=repo,
            owner=owner,
            head_sha=head_sha,
            mode="shadow" if shadow_mode else "live",
            status="pending",
        )
        db.add(log)
        db.commit()
        db.refresh(log)
        log_id = log.id
        logger.info("Created ReviewLog id=%s for PR #%s in %s", log_id, pr_number, repo)
    finally:
        db.close()

    # --- Record the webhook event ----------------------------------------
    _log_event(
        raw_body=raw_body,
        event_type=x_github_event,
        action=action,
        repo=repo,
        pr_number=pr_number,
        sender=sender,
        outcome="queued",
        review_log_id=log_id,
    )

    # --- Enqueue analysis as a background task ---------------------------
    review_request = ReviewRequest(
        pr_number=pr_number,
        repo=repo,
        owner=owner,
        head_sha=head_sha,
        shadow_mode=shadow_mode,
    )
    background_tasks.add_task(dispatch_analysis, log_id, review_request)

    return {
        "status": "queued",
        "pr_number": pr_number,
        "repo": repo,
        "review_log_id": log_id,
        "shadow_mode": shadow_mode,
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8001, reload=True)
