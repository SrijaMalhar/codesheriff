import asyncio
import csv
import io
import json
import os
import sqlite3
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse

from review import process_pr, verify_sig

load_dotenv()

SHADOW_MODE = os.getenv("SHADOW_MODE", "false").lower() == "true"
DB_PATH = os.getenv("DB_PATH", "feedback.db")

_queue: asyncio.Queue = asyncio.Queue()

_DASHBOARD = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>CodeSheriff</title>
<style>
  body{font-family:sans-serif;max-width:960px;margin:40px auto;padding:0 20px}
  h1{color:#333}table{border-collapse:collapse;width:100%}
  th,td{border:1px solid #ddd;padding:10px;text-align:left}
  tr:nth-child(even){background:#f9f9f9}
  .live{color:green;font-weight:bold}.shadow{color:#999}
  .err{color:#c00}
</style></head>
<body>
<h1>🤠 CodeSheriff Dashboard</h1>
<table id="t">
  <thead><tr><th>PR</th><th>Repo</th><th>Issues</th><th>Inline</th><th>Mode</th><th>When</th></tr></thead>
  <tbody></tbody>
</table>
<script>
fetch('/reviews').then(r=>r.json()).then(rows=>rows.forEach(r=>{
  const tr=document.createElement('tr');
  const mode=r.shadow?'<span class="shadow">shadow</span>':'<span class="live">live</span>';
  tr.innerHTML=`<td>#${r.pr_num}</td><td>${r.owner}/${r.repo}</td>`
    +`<td class="err">${r.issues}</td><td>${r.inline_comments}</td>`
    +`<td>${mode}</td><td>${r.ts}</td>`;
  document.querySelector('#t tbody').appendChild(tr);
}));
</script>
</body></html>"""


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            comment_id TEXT, vote TEXT, pr_id TEXT,
            ts DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner TEXT, repo TEXT, pr_num INTEGER,
            issues INTEGER, inline_comments INTEGER, shadow INTEGER,
            ts DATETIME DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.commit()
    return conn


async def _worker():
    while True:
        job = await _queue.get()
        try:
            result = await asyncio.to_thread(process_pr, **job, shadow=SHADOW_MODE)
            conn = _db()
            conn.execute(
                "INSERT INTO reviews (owner,repo,pr_num,issues,inline_comments,shadow)"
                " VALUES (?,?,?,?,?,?)",
                (job["owner"], job["repo"], job["pr_num"],
                 result.get("issues", 0), result.get("inline", 0), int(SHADOW_MODE)),
            )
            conn.commit()
            conn.close()
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


@app.get("/health")
def health():
    return {"status": "ok", "shadow_mode": SHADOW_MODE, "queue": _queue.qsize()}


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard():
    return _DASHBOARD


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
    data = await request.json()
    conn = _db()
    conn.execute(
        "INSERT INTO feedback (comment_id, vote, pr_id) VALUES (?, ?, ?)",
        (str(data.get("comment_id", "")), data.get("vote", ""), str(data.get("pr_id", ""))),
    )
    conn.commit()
    conn.close()
    return {"status": "recorded", "vote": data.get("vote")}


@app.get("/feedback/summary")
def feedback_summary():
    conn = _db()
    rows = conn.execute("SELECT vote, COUNT(*) FROM feedback GROUP BY vote").fetchall()
    conn.close()
    counts = {r[0]: r[1] for r in rows}
    return {"thumbs_up": counts.get("up", 0), "thumbs_down": counts.get("down", 0)}


@app.get("/feedback/export")
def feedback_export(vote: str = "down"):
    conn = _db()
    rows = conn.execute(
        "SELECT id, comment_id, vote, pr_id, ts FROM feedback WHERE vote=? ORDER BY ts DESC",
        (vote,),
    ).fetchall()
    conn.close()
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["id", "comment_id", "vote", "pr_id", "recorded_at"])
    writer.writerows(rows)
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]), media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=feedback_{vote}.csv"},
    )


@app.get("/reviews")
def reviews(limit: int = 20):
    conn = _db()
    rows = conn.execute(
        "SELECT owner,repo,pr_num,issues,inline_comments,shadow,ts"
        " FROM reviews ORDER BY ts DESC LIMIT ?", (limit,),
    ).fetchall()
    conn.close()
    keys = ["owner", "repo", "pr_num", "issues", "inline_comments", "shadow", "ts"]
    return [dict(zip(keys, r)) for r in rows]
