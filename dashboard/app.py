import json
import secrets as _secrets
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from flask import Flask, jsonify, render_template, abort

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from shared.config import ANALYSIS_SERVICE_URL, SHADOW_MODE
from shared.database import ReviewLog, WebhookEvent, SessionLocal, create_tables

app = Flask(__name__)

with app.app_context():
    create_tables()


@app.route("/api/stats")
def api_stats():
    db = SessionLocal()
    try:
        logs = db.query(ReviewLog).order_by(ReviewLog.timestamp.desc()).all()

        cutoff = datetime.now(timezone.utc) - timedelta(days=30)
        timeline_shadow: dict[str, int] = defaultdict(int)
        timeline_live: dict[str, int] = defaultdict(int)

        for i in range(30, -1, -1):
            day = (datetime.now(timezone.utc) - timedelta(days=i)).strftime("%Y-%m-%d")
            timeline_shadow[day] = 0
            timeline_live[day] = 0

        severity_counts = {"error": 0, "warning": 0, "info": 0}
        mode_split = {"shadow": 0, "live": 0}

        for log in logs:
            ts = log.timestamp
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            day_str = ts.strftime("%Y-%m-%d")
            if ts >= cutoff:
                (timeline_shadow if log.mode == "shadow" else timeline_live)[day_str] += 1
            if log.mode in mode_split:
                mode_split[log.mode] += 1
            for finding in log.get_findings():
                sev = finding.get("severity", "info")
                if sev in severity_counts:
                    severity_counts[sev] += 1

        labels = sorted(timeline_shadow.keys())
        recent = [
            {"id": l.id, "pr_id": l.pr_id, "repo": l.repo, "timestamp": l.timestamp.isoformat(),
             "mode": l.mode, "status": l.status, "findings_count": l.findings_count}
            for l in logs[:20]
        ]
        return jsonify({
            "timeline": {"labels": labels, "shadow": [timeline_shadow[d] for d in labels], "live": [timeline_live[d] for d in labels]},
            "severity": severity_counts,
            "mode_split": mode_split,
            "recent": recent,
        })
    finally:
        db.close()


@app.route("/api/reviews/<int:review_id>")
def api_review_detail(review_id: int):
    db = SessionLocal()
    try:
        log = db.get(ReviewLog, review_id)
        if log is None:
            abort(404, description=f"Review #{review_id} not found")
        return jsonify({
            "id": log.id, "pr_id": log.pr_id, "repo": log.repo, "owner": log.owner,
            "head_sha": log.head_sha, "timestamp": log.timestamp.isoformat(),
            "mode": log.mode, "status": log.status,
            "findings_count": log.findings_count, "findings": log.get_findings(),
        })
    finally:
        db.close()


@app.route("/api/reviews/<int:review_id>/rerun", methods=["POST"])
def api_review_rerun(review_id: int):
    db = SessionLocal()
    try:
        log = db.get(ReviewLog, review_id)
        if log is None:
            return jsonify({"error": f"Review #{review_id} not found"}), 404
        payload = {"pr_number": log.pr_id, "repo": log.repo, "owner": log.owner,
                   "head_sha": log.head_sha, "shadow_mode": SHADOW_MODE, "review_log_id": log.id}
    finally:
        db.close()

    try:
        resp = httpx.post(f"{ANALYSIS_SERVICE_URL}/analyze", json=payload, timeout=10)
        resp.raise_for_status()
    except httpx.TimeoutException:
        pass
    except httpx.HTTPError as exc:
        return jsonify({"error": f"Analysis service unreachable: {exc}"}), 502

    return jsonify({"status": "queued", "review_id": review_id}), 202


@app.route("/api/events")
def api_events():
    db = SessionLocal()
    try:
        events = db.query(WebhookEvent).order_by(WebhookEvent.received_at.desc()).limit(100).all()
        return jsonify([
            {"id": ev.id, "received_at": ev.received_at.isoformat(), "event_type": ev.event_type,
             "action": ev.action, "repo": ev.repo, "pr_number": ev.pr_number,
             "sender": ev.sender, "outcome": ev.outcome, "review_log_id": ev.review_log_id}
            for ev in events
        ])
    finally:
        db.close()


@app.route("/api/events/<int:event_id>/payload")
def api_event_payload(event_id: int):
    db = SessionLocal()
    try:
        ev = db.get(WebhookEvent, event_id)
        if ev is None:
            abort(404, description=f"Event #{event_id} not found")
        return jsonify({"id": ev.id, "received_at": ev.received_at.isoformat(),
                        "event_type": ev.event_type, "action": ev.action, "payload": ev.get_payload()})
    finally:
        db.close()


@app.route("/api/setup/info")
def api_setup_info():
    import os
    return jsonify({"webhook_secret_set": bool(os.getenv("WEBHOOK_SECRET", "")),
                    "shadow_mode": SHADOW_MODE, "webhook_port": 8001})


@app.route("/api/setup/generate-secret")
def api_generate_secret():
    return jsonify({"secret": _secrets.token_hex(32)})


@app.route("/")
def index():
    return render_template("index.html")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8004, debug=True)
