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
| **GitHub Actions** | Drop `pr-review.yml` in any repo — no server needed |

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
| `GOOGLE_API_KEY` | Gemini API key — get one at aistudio.google.com |
| `SHADOW_MODE` | `true` = analyse but never post (default: `false`) |
| `DB_PATH` | Path to SQLite feedback database (default: `feedback.db`) |
| `GEMINI_MODEL` | Gemini model to use (default: `gemini-2.5-flash`) |

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
| `GET` | `/dashboard` | Web UI showing recent reviews |
| `POST` | `/webhook` | GitHub webhook receiver |
| `POST` | `/feedback` | Record `{"comment_id":…,"vote":"up","pr_id":…}` |
| `GET` | `/feedback/summary` | Aggregate 👍/👎 counts |
| `GET` | `/feedback/export` | Download thumbs-down feedback as CSV |
| `POST` | `/feedback/retrain` | Ask Gemini for suggestions based on bad feedback |
| `GET` | `/reviews` | Recent PR review log |
| `GET` | `/suggestions` | Gemini-generated review improvement suggestions |

## Shadow Mode Testing

```bash
SHADOW_MODE=true uvicorn app:app --port 8001
# Reviews run silently — check worker stdout for results
```

## GitHub Actions (no server required)

Add `GOOGLE_API_KEY` to your repo secrets, then copy `.github/workflows/pr-review.yml`
into any Python repo. CodeSheriff will review every pull request automatically.
