# CodeSheriff

Automatic GitHub PR reviewer with inline comments, multi-file context analysis,
shadow mode, async queue, and a thumbs-up/down feedback loop.

## Features

| Feature | Details |
|---|---|
| **Inline comments** | Posted on the exact diff line, not just a summary |
| **Multi-file review** | All changed `.py` files linted together; cross-file summary |
| **Shadow mode** | Set `SHADOW_MODE=true` to analyse PRs without posting anything |
| **Async queue** | Webhook returns instantly; review runs in a background worker |
| **Feedback loop** | `POST /feedback` records 👍/👎 votes per comment for future tuning |

## Quick Start

```bash
git clone https://github.com/SrijaMalhar/codesheriff.git
cd codesheriff
pip install -r requirements.txt
cp .env.example .env        # fill in your values
uvicorn app:app --port 8001
```

## Environment Variables

| Variable | Description |
|---|---|
| `GITHUB_TOKEN` | GitHub PAT with `repo` scope |
| `WEBHOOK_SECRET` | Shared secret for HMAC webhook verification |
| `SHADOW_MODE` | `true` = analyse but never post (default: `false`) |
| `DB_PATH` | Path to SQLite feedback database (default: `feedback.db`) |

## Webhook Setup

1. Go to **Settings → Webhooks → Add webhook** in your GitHub repo
2. **Payload URL**: `https://<your-host>/webhook`
3. **Content type**: `application/json`
4. **Secret**: your `WEBHOOK_SECRET`
5. **Events**: Pull requests only

## Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Status, shadow mode flag, queue depth |
| `POST` | `/webhook` | GitHub webhook receiver |
| `POST` | `/feedback` | Record `{"comment_id":…,"vote":"up","pr_id":…}` |
| `GET` | `/feedback/summary` | Aggregate 👍/👎 counts |

## Shadow Mode Testing

```bash
SHADOW_MODE=true uvicorn app:app --port 8001
# Reviews run silently — check worker stdout for results
```
