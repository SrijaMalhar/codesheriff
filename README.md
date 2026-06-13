# CodeSheriff

An AI-powered GitHub Pull Request reviewer. CodeSheriff watches for new or updated PRs, analyses the diffs with **flake8** and **Google Gemini**, and posts inline review comments back to GitHub.

## Architecture

```
GitHub Webhook
      │
      ▼
webhook-service  :8001  Verifies HMAC-SHA256 signature, queues PR review
      │
      ▼
analysis-service :8002  Fetches diff, runs flake8 + Gemini AI review
      │
      ▼
comment-service  :8003  Posts inline review comments to GitHub PR

dashboard        :8004  Flask + Chart.js — review history and stats
```

## Quick Start

```bash
git clone https://github.com/YOUR_USERNAME/codesheriff.git
cd codesheriff
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in your values
bash run_all.sh
```

## Environment Variables

| Variable               | Required | Description                                    |
|------------------------|----------|------------------------------------------------|
| `GITHUB_TOKEN`         | ✅        | GitHub PAT with `repo` scope                   |
| `WEBHOOK_SECRET`       | ✅        | Shared secret for webhook HMAC verification    |
| `GOOGLE_API_KEY`       | ✅        | Google Gemini API key (aistudio.google.com)    |
| `SHADOW_MODE`          | ❌        | `true` to log only, skip posting to GitHub     |
| `GEMINI_MODEL`         | ❌        | Gemini model (default: `gemini-1.5-flash`)     |
| `DATABASE_URL`         | ❌        | SQLite (default) or PostgreSQL URL             |

## Setting Up a Webhook

1. Go to your repo → **Settings → Webhooks → Add webhook**
2. Set **Payload URL** to your public URL + `/webhook` (use ngrok for local dev)
3. Set **Content type** to `application/json`
4. Set **Secret** to your `WEBHOOK_SECRET` value
5. Select **Pull requests** events only

## Shadow Mode

Set `SHADOW_MODE=true` in `.env` to analyse PRs without posting any comments to GitHub. Reviews are fully logged to the database — useful for validating output quality before going live.

## Stack

Python 3.12 · FastAPI · Flask · SQLAlchemy · Google Gemini · flake8 · httpx

## License

MIT
