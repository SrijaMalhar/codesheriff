"""
CodeSheriff – Dashboard  (port 8004)
=====================================
A lightweight Flask web application that reads the shared SQLite DB
and visualises review history.

Pages
-----
  GET  /                        HTML dashboard with Chart.js charts
  GET  /api/stats               JSON data consumed by the charts
  GET  /api/reviews/<id>        Full finding details for a single review
  POST /api/reviews/<id>/rerun  Re-trigger analysis for an existing review
"""

import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from flask import Flask, jsonify, render_template, abort

# ---------------------------------------------------------------------------
# Path bootstrap
# ---------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.config import ANALYSIS_SERVICE_URL, SHADOW_MODE
from shared.database import ReviewLog, WebhookEvent, SessionLocal, create_tables

app = Flask(__name__)


# ---------------------------------------------------------------------------
# Bootstrap DB tables on first request
# ---------------------------------------------------------------------------
with app.app_context():
    create_tables()


# ---------------------------------------------------------------------------
# API – JSON stats endpoint
# ---------------------------------------------------------------------------
@app.route("/api/stats")
def api_stats():
    """Return all chart data as a single JSON payload.

    Response shape
    --------------
    {
      "timeline": {
        "labels": ["2024-06-01", ...],   // last 30 days
        "shadow": [0, 2, ...],
        "live":   [1, 0, ...]
      },
      "severity": {
        "error": 12,
        "warning": 34,
        "info": 5
      },
      "mode_split": {
        "shadow": 8,
        "live": 15
      },
      "recent": [
        {
          "id": 1,
          "pr_id": 42,
          "repo": "owner/repo",
          "timestamp": "2024-06-01T12:00:00",
          "mode": "live",
          "status": "completed",
          "findings_count": 3
        }, ...
      ]
    }
    """
    db = SessionLocal()
    try:
        logs: list[ReviewLog] = (
            db.query(ReviewLog)
            .order_by(ReviewLog.timestamp.desc())
            .all()
        )

        # --- Timeline (last 30 days) ---
        cutoff = datetime.now(timezone.utc) - timedelta(days=30)
        timeline_shadow: dict[str, int] = defaultdict(int)
        timeline_live: dict[str, int] = defaultdict(int)

        # Pre-fill every day so the chart has a complete x-axis
        for i in range(30, -1, -1):
            day = (datetime.now(timezone.utc) - timedelta(days=i)).strftime("%Y-%m-%d")
            timeline_shadow[day] = 0
            timeline_live[day] = 0

        severity_counts: dict[str, int] = {"error": 0, "warning": 0, "info": 0}
        mode_split: dict[str, int] = {"shadow": 0, "live": 0}

        for log in logs:
            ts = log.timestamp
            # Ensure tz-aware for comparison
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)

            day_str = ts.strftime("%Y-%m-%d")

            if ts >= cutoff:
                if log.mode == "shadow":
                    timeline_shadow[day_str] += 1
                else:
                    timeline_live[day_str] += 1

            # Mode split (all time)
            if log.mode in mode_split:
                mode_split[log.mode] += 1

            # Severity breakdown – parse the stored findings JSON
            for finding in log.get_findings():
                sev = finding.get("severity", "info")
                if sev in severity_counts:
                    severity_counts[sev] += 1

        timeline_labels = sorted(timeline_shadow.keys())

        # --- Recent reviews table (newest 20) ---
        recent = []
        for log in logs[:20]:
            recent.append({
                "id": log.id,
                "pr_id": log.pr_id,
                "repo": log.repo,
                "timestamp": log.timestamp.isoformat(),
                "mode": log.mode,
                "status": log.status,
                "findings_count": log.findings_count,
            })

        return jsonify({
            "timeline": {
                "labels": timeline_labels,
                "shadow": [timeline_shadow[d] for d in timeline_labels],
                "live": [timeline_live[d] for d in timeline_labels],
            },
            "severity": severity_counts,
            "mode_split": mode_split,
            "recent": recent,
        })
    finally:
        db.close()


# ---------------------------------------------------------------------------
# API – single review detail endpoint
# ---------------------------------------------------------------------------
@app.route("/api/reviews/<int:review_id>")
def api_review_detail(review_id: int):
    """Return full details for a single review, including every finding.

    Response shape
    --------------
    {
      "id": 1,
      "pr_id": 42,
      "repo": "owner/repo",
      "owner": "owner",
      "head_sha": "abc123",
      "timestamp": "2024-06-01T12:00:00",
      "mode": "live",
      "status": "completed",
      "findings_count": 3,
      "findings": [
        {
          "file": "src/app.py",
          "line": 17,
          "severity": "error",
          "suggestion": "...",
          "source": "flake8"
        },
        ...
      ]
    }
    """
    db = SessionLocal()
    try:
        log: ReviewLog | None = db.get(ReviewLog, review_id)
        if log is None:
            abort(404, description=f"Review #{review_id} not found")

        return jsonify({
            "id": log.id,
            "pr_id": log.pr_id,
            "repo": log.repo,
            "owner": log.owner,
            "head_sha": log.head_sha,
            "timestamp": log.timestamp.isoformat(),
            "mode": log.mode,
            "status": log.status,
            "findings_count": log.findings_count,
            "findings": log.get_findings(),   # full list of Finding dicts
        })
    finally:
        db.close()


# ---------------------------------------------------------------------------
# API – re-run analysis for an existing review
# ---------------------------------------------------------------------------
@app.route("/api/reviews/<int:review_id>/rerun", methods=["POST"])
def api_review_rerun(review_id: int):
    """Re-trigger the full analysis pipeline for an existing review.

    Reads the stored repo / PR / SHA from the DB and POSTs the same
    payload to the analysis-service so findings are refreshed in-place.

    Response
    --------
    202  {"status": "queued", "review_id": <id>}         – analysis accepted
    404  {"error": "Review #N not found"}
    502  {"error": "Analysis service unreachable: …"}
    """
    db = SessionLocal()
    try:
        log: ReviewLog | None = db.get(ReviewLog, review_id)
        if log is None:
            return jsonify({"error": f"Review #{review_id} not found"}), 404

        payload = {
            "pr_number":     log.pr_id,
            "repo":          log.repo,
            "owner":         log.owner,
            "head_sha":      log.head_sha,
            "shadow_mode":   SHADOW_MODE,
            "review_log_id": log.id,     # analysis-service updates this row
        }
    finally:
        db.close()

    try:
        resp = httpx.post(
            f"{ANALYSIS_SERVICE_URL}/analyze",
            json=payload,
            timeout=10,   # just enough to confirm the service accepted it
        )
        resp.raise_for_status()
    except httpx.TimeoutException:
        # Analysis accepted but slow — treat as queued
        pass
    except httpx.HTTPError as exc:
        return jsonify({"error": f"Analysis service unreachable: {exc}"}), 502

    return jsonify({"status": "queued", "review_id": review_id}), 202


# ---------------------------------------------------------------------------
# API – webhook event log
# ---------------------------------------------------------------------------
@app.route("/api/events")
def api_events():
    """Return the 100 most recent webhook events as JSON.

    Response shape
    --------------
    [
      {
        "id": 1,
        "received_at": "2024-06-01T12:00:00",
        "event_type": "pull_request",
        "action": "opened",
        "repo": "owner/repo",
        "pr_number": 42,
        "sender": "octocat",
        "outcome": "queued",
        "review_log_id": 7
      },
      ...
    ]
    """
    db = SessionLocal()
    try:
        events: list[WebhookEvent] = (
            db.query(WebhookEvent)
            .order_by(WebhookEvent.received_at.desc())
            .limit(100)
            .all()
        )
        return jsonify([
            {
                "id":            ev.id,
                "received_at":   ev.received_at.isoformat(),
                "event_type":    ev.event_type,
                "action":        ev.action,
                "repo":          ev.repo,
                "pr_number":     ev.pr_number,
                "sender":        ev.sender,
                "outcome":       ev.outcome,
                "review_log_id": ev.review_log_id,
            }
            for ev in events
        ])
    finally:
        db.close()


# ---------------------------------------------------------------------------
# API – raw payload for a single webhook event
# ---------------------------------------------------------------------------
@app.route("/api/events/<int:event_id>/payload")
def api_event_payload(event_id: int):
    """Return the raw JSON payload stored for one webhook event.

    Response shape
    --------------
    {
      "id": 1,
      "received_at": "2024-06-01T12:00:00",
      "event_type": "pull_request",
      "action": "opened",
      "payload": { ...raw GitHub payload dict... }
    }
    """
    db = SessionLocal()
    try:
        ev: WebhookEvent | None = db.get(WebhookEvent, event_id)
        if ev is None:
            abort(404, description=f"Event #{event_id} not found")
        return jsonify({
            "id":           ev.id,
            "received_at":  ev.received_at.isoformat(),
            "event_type":   ev.event_type,
            "action":       ev.action,
            "payload":      ev.get_payload(),
        })
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Main page
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    """Render the dashboard HTML page."""
    return render_template("index.html")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8004, debug=True)
