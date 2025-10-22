import asyncio
import csv
import io
import json
import os
import sqlite3
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import StreamingResponse

from review import process_pr, verify_sig

load_dotenv()

SHADOW_MODE = os.getenv("SHADOW_MODE", "false").lower() == "true"
DB_PATH = os.getenv("DB_PATH", "feedback.db")

_queue: asyncio.Queue = asyncio.Queue()


# ---------- tiny SQLite feedback store ----------

def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            comment_id TEXT,
            vote       TEXT,
            pr_id      TEXT,
            ts         DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    return conn


# ---------- background worker ----------

async def _worker():
    while True:
        job = await _queue.get()
        try:
            await asyncio.to_thread(process_pr, **job, shadow=SHADOW_MODE)
        except Exception as exc:
            print(f"[worker] {exc}")
        finally:
            _queue.task_done()


@asynccontextmanager
async def lifespan(app: "FastAPI"):
    _db().close()
    asyncio.create_task(_worker())
    yield


app = FastAPI(title="CodeSheriff", lifespan=lifespan)


# ---------- endpoints ----------

@app.get("/health")
def health():
    return {"status": "ok", "shadow_mode": SHADOW_MODE, "queue": _queue.qsize()}


@app.post("/webhook")
async def webhook(
    request: Request,
    x_hub_signature_256: str = Header(default=""),
    x_github_event: str = Header(default=""),
):
    body = await request.body()
    if not verify_sig(body, x_hub_signature_256):
        raise HTTPException(401, "Invalid signature")
    if x_github_event != "pull_request":
        return {"status": "ignored"}
    data = json.loads(body)
    if data.get("action") not in ("opened", "synchronize"):
        return {"status": "ignored"}
    pr = data["pull_request"]
    repo = data["repository"]
    await _queue.put({
        "owner": repo["owner"]["login"],
        "repo": repo["name"],
        "pr_num": pr["number"],
        "sha": pr["head"]["sha"],
    })
    return {"status": "queued", "pr": pr["number"], "queue_size": _queue.qsize()}


@app.post("/feedback")
async def feedback(request: Request):
    """Record a thumbs-up or thumbs-down vote on a review comment."""
    data = await request.json()
    conn = _db()
    conn.execute(
        "INSERT INTO feedback (comment_id, vote, pr_id) VALUES (?, ?, ?)",
        (str(data.get("comment_id", "")),
         data.get("vote", ""),
         str(data.get("pr_id", ""))),
    )
    conn.commit()
    conn.close()
    return {"status": "recorded", "vote": data.get("vote")}


@app.get("/feedback/summary")
def feedback_summary():
    conn = _db()
    rows = conn.execute(
        "SELECT vote, COUNT(*) FROM feedback GROUP BY vote"
    ).fetchall()
    conn.close()
    counts = {r[0]: r[1] for r in rows}
    return {"thumbs_up": counts.get("up", 0), "thumbs_down": counts.get("down", 0)}


@app.get("/feedback/export")
def feedback_export(vote: str = "down"):
    """Export feedback rows as CSV — defaults to thumbs-down for rule-improvement review."""
    conn = _db()
    rows = conn.execute(
        "SELECT id, comment_id, vote, pr_id, ts FROM feedback WHERE vote = ? ORDER BY ts DESC",
        (vote,),
    ).fetchall()
    conn.close()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["id", "comment_id", "vote", "pr_id", "recorded_at"])
    writer.writerows(rows)
    buf.seek(0)

    filename = f"codesheriff_feedback_{vote}.csv"
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
