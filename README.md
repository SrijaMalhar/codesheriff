# 🤠 CodeSheriff

An AI-powered GitHub Pull Request reviewer built as a Python microservices
application.  CodeSheriff watches your repositories for new or updated PRs,
analyses the diffs with **flake8** (static analysis) and **Anthropic Claude**
(AI review), and posts structured inline review comments back to GitHub.

---

## Architecture

```
GitHub Webhook
      │
      ▼
┌─────────────────┐
│ webhook-service │  :8001  Verifies HMAC-SHA256 signature, queues PR review
└────────┬────────┘
         │ HTTP POST /analyze
         ▼
┌─────────────────────┐
│  analysis-service   │  :8002  Fetches diff via GitHub API
│  • flake8 (Python)  │         Runs static analysis
│  • Claude AI review │         Sends diff to Claude for structured JSON review
└──────────┬──────────┘
           │ HTTP POST /comment
           ▼
┌─────────────────┐
│ comment-service │  :8003  Posts inline review comments to GitHub PR
│  (shadow gate)  │         Skips posting when shadow mode is active
└─────────────────┘

┌───────────────┐
│   Dashboard   │  :8004  Flask + Chart.js – reviews over time,
│  (Flask app)  │         severity breakdown, shadow-vs-live counts
└───────────────┘

            ▼
     SQLite DB (shared)
     codesheriff.db
```

---

## Services

| Service            | Port | Purpose                                           |
|--------------------|------|---------------------------------------------------|
| `webhook-service`  | 8001 | Receives GitHub webhook events                    |
| `analysis-service` | 8002 | Static analysis + Claude AI review                |
| `comment-service`  | 8003 | Posts inline comments to GitHub PRs               |
| `dashboard`        | 8004 | Web UI showing review history and stats           |

---

## Quick Start

### 1. Clone and install dependencies

```bash
git clone https://github.com/YOUR_USERNAME/codesheriff.git
cd codesheriff
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure environment variables

```bash
cp .env.example .env
# Edit .env and fill in your values:
#   GITHUB_TOKEN      – PAT with repo read + PR write permissions
#   WEBHOOK_SECRET    – random hex string (same as GitHub webhook secret)
#   ANTHROPIC_API_KEY – Claude API key
```

### 3. Start all services

```bash
mkdir -p logs
bash run_all.sh
```

Or start services individually:

```bash
# In separate terminals:
python -m uvicorn webhook_service.main:app --port 8001 --reload
python -m uvicorn analysis_service.main:app --port 8002 --reload
python -m uvicorn comment_service.main:app --port 8003 --reload
python dashboard/app.py
```

### 4. Configure your GitHub webhook

1. Go to your repository → **Settings → Webhooks → Add webhook**
2. Set the **Payload URL** to your public URL + `/webhook`
   (use [ngrok](https://ngrok.com) for local development: `ngrok http 8001`)
3. Set **Content type** to `application/json`
4. Set **Secret** to the same value as `WEBHOOK_SECRET` in your `.env`
5. Select **Pull requests** events only
6. Click **Add webhook**

---

## Shadow Mode

Set `SHADOW_MODE=true` in your `.env` to run CodeSheriff without posting
any comments to GitHub.  Reviews are fully analysed and logged to the DB —
perfect for validating output quality before going live.

The dashboard clearly shows which reviews ran in shadow vs live mode.

---

## Environment Variables

| Variable               | Required | Description                                              |
|------------------------|----------|----------------------------------------------------------|
| `GITHUB_TOKEN`         | ✅        | GitHub PAT with `repo` scope                             |
| `WEBHOOK_SECRET`       | ✅        | Shared secret for webhook HMAC verification              |
| `GOOGLE_API_KEY`       | ✅        | Google Gemini API key (aistudio.google.com)              |
| `SHADOW_MODE`          | ❌        | `true` to log-only, `false` (default) to post to GitHub  |
| `GEMINI_MODEL`         | ❌        | Gemini model (default: `gemini-1.5-flash`)               |
| `DATABASE_URL`         | ❌        | SQLite (default) or PostgreSQL connection string         |
| `ANALYSIS_SERVICE_URL` | ❌        | URL of analysis-service (default: `http://localhost:8002`) |
| `COMMENT_SERVICE_URL`  | ❌        | URL of comment-service (default: `http://localhost:8003`) |

---

## API Reference

### webhook-service (:8001)

| Method | Path       | Description                                    |
|--------|------------|------------------------------------------------|
| GET    | `/health`  | Liveness probe                                 |
| POST   | `/webhook` | GitHub PR webhook receiver (HMAC-verified)     |

### analysis-service (:8002)

| Method | Path       | Description                                    |
|--------|------------|------------------------------------------------|
| GET    | `/health`  | Liveness probe                                 |
| POST   | `/analyze` | Analyse a PR (flake8 + Claude) and post review |

### comment-service (:8003)

| Method | Path       | Description                                    |
|--------|------------|------------------------------------------------|
| GET    | `/health`  | Liveness probe                                 |
| POST   | `/comment` | Post review findings to a GitHub PR            |

---

## Tech Stack

- **Python 3.12**
- **FastAPI** – webhook, analysis, and comment microservices
- **Flask** – dashboard
- **SQLAlchemy** – ORM (SQLite by default)
- **Google Generative AI Python SDK** – Gemini AI integration
- **flake8** – Python static analysis
- **httpx** – async HTTP client for GitHub API and inter-service calls
- **Chart.js** – dashboard visualisations
- **python-dotenv** – environment variable management

---

## License

MIT
